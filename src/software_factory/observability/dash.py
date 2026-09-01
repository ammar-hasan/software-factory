"""`sf dash`: the dashboard, served locally from the ledger (PRD FR-15.8).

Local-first and read-mostly, with **no external dependency** -- no framework, no CDN, no
build step. The entire server is `http.server` from the standard library and the entire
client is one HTML document. That constraint is not minimalism for its own sake: PR-2 says
local is the reference implementation rather than a degraded mode, and a dashboard that
needs `npm install` to look at a factory running offline on a laptop fails that on the first
day somebody tries it.

Read-mostly, and the "mostly" is deliberate. Steering a live run (FR-15.7) is a *decision*
channel and therefore authenticated and capability-checked (FR-25.5); an unauthenticated
steering endpoint is a privilege-escalation path, so this server does not offer one. It
binds to loopback and serves what the ledger already contains.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from software_factory.ledger.log import Ledger
from software_factory.observability.metrics import Window
from software_factory.observability.views import (
    activity_board,
    definition_view,
    evaluation_view,
    overview,
    registry_view,
    run_index,
    run_inspector,
    work_items_from,
)

#: Loopback only. A dashboard reachable from the network is one that has published a
#: factory's whole history to whoever can reach the port, and FR-15.8 asks for a local
#: application rather than an unauthenticated service.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


@dataclass(frozen=True, slots=True)
class DashboardData:
    """Everything the dashboard can show, from one ledger and the tree beside it.

    A snapshot rather than a live connection: each request re-reads, so a page is always a
    fold over the ledger as it stands and never a cache that could disagree with it.

    ``root`` and ``memory_path`` default to the conventional layout -- a ``.factory``
    state directory holding ``ledger.jsonl`` and ``memory.jsonl`` beside the factory tree
    that produced them. They are fields rather than assumptions so a caller with an
    unconventional layout can still reach the definition, and so a test can point them
    somewhere that does not exist and see the view say so.
    """

    ledger_path: Path
    integrations: frozenset[str] = frozenset()
    root: Path | None = None
    memory_path: Path | None = None

    def factory_root(self) -> Path:
        return self.root if self.root is not None else self.ledger_path.parent.parent

    def memory_file(self) -> Path:
        if self.memory_path is not None:
            return self.memory_path
        return self.ledger_path.parent / "memory.jsonl"

    def payload(self, view: str, params: dict[str, list[str]]) -> dict[str, Any]:
        days = _days_from(params)
        if days is None:
            return {
                "error": "days.invalid",
                "message": (
                    f"`days` must be a whole number between 1 and {MAX_WINDOW_DAYS}; "
                    f"got {params.get('days', [''])[0]!r}"
                ),
            }
        if view in ("definition", "evaluation", "registry"):
            # These three do not read the window, and loading the whole ledger to answer
            # `definition` would make the cheapest view the slowest.
            return self._standing(view)

        ledger = Ledger(self.ledger_path)
        entries = list(ledger.read())
        window = Window.last(timedelta(days=days))

        match view:
            case "overview":
                return {
                    **overview(entries, window=window, integrations=self.integrations),
                    "days": days,
                }
            case "activity":
                # Rebuilt from the ledger. This used to serve an empty board with a note
                # explaining that it was "empty by construction" -- but FR-15.2 says derived
                # state is rebuildable from the ledger, and this was the one view that did
                # not do it. The entries carry every field the board shows.
                return {
                    **activity_board(work_items_from(entries)),
                    "note": (
                        "Rebuilt from the ledger. The request body and source permalink are "
                        "not recorded there, so those are absent rather than empty."
                    ),
                }
            case "runs":
                return run_index(entries)
            case "ask":
                return _ask(entries, params.get("q", [""])[0], params.get("item", [""])[0])
            case "run":
                run_id = params.get("run", [""])[0]
                if not run_id:
                    return {"view": "run", "error": "run.missing", "message": "pass ?run=<id>"}
                return run_inspector(entries, run_id)
            case _:
                return {
                    "error": "view.unknown",
                    "message": f"no view {view!r}",
                    "views": list(VIEWS),
                }

    def _standing(self, view: str) -> dict[str, Any]:
        """The three views that describe the factory rather than its history.

        These existed as functions in `views.py` -- the module docstring has always said
        "the dashboard's six views" -- and the server offered three. A view computed and
        reachable from nothing is the failure this codebase keeps finding in itself, so
        they are wired here, each degrading to a stated reason rather than to an empty
        page when the thing it describes is not on disk.
        """
        if view == "evaluation":
            from software_factory.improvement.loop import LoopState

            entries = list(Ledger(self.ledger_path).read())
            state = LoopState.from_ledger(entries)
            return evaluation_view(entries, proposals=state.records)

        definition, reason = self._definition()
        if view == "definition":
            if definition is None:
                return _unavailable("definition", reason)
            return definition_view(definition)

        skills: list[Any] = []
        if definition is not None:
            from software_factory.orchestrator.coordinator import _registry_from

            skills = _registry_from(definition).all()
        return registry_view(memory_stats=self._memory_stats(), skills=skills)

    def _definition(self) -> tuple[Any | None, str]:
        root = self.factory_root()
        try:
            from software_factory.definition.loader import load

            definition, _report = load(root)
        except Exception as exc:  # DefinitionError, and anything a partial tree raises
            return None, f"{type(exc).__name__} loading the factory at {root}: {exc}"
        return definition, ""

    def _memory_stats(self) -> dict[str, Any]:
        path = self.memory_file()
        if not path.exists():
            return {"available": False, "reason": f"no memory log at {path}"}
        try:
            from software_factory.memory import MemoryStore

            store = MemoryStore(path)
            store.load()
        except Exception as exc:
            return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
        return {"available": True, **store.stats()}


#: How many work items one question may be answered across.
#:
#: A question asked without naming an item searches every item the ledger knows, and a
#: page that returns forty answers has answered nothing.
MAX_ANSWERS = 6


def _ask(entries: list[Any], question: str, item: str) -> dict[str, Any]:
    """Answer a question from the record, across the whole factory.

    The same `Explainer` behind `sf explain`, which never calls a model: it quotes what a
    run wrote down at the time and says plainly when the record is silent. That property is
    what makes this safe to put on a page -- an answer box backed by a model would invent a
    plausible history of runs nobody can check, which is the opposite of what a ledger is
    for.

    Naming an item is optional. `sf explain` requires one because a command has a person
    who knows which item they mean; somebody looking at a dashboard usually does not, and
    "you must already know the answer's address" is how a feature goes unused.
    """
    from software_factory.orchestrator.explain import Explainer

    question = question.strip()
    if not question:
        return {
            "view": "ask",
            "error": "question.missing",
            "message": "pass ?q=<question>",
        }

    explainer = Explainer.from_ledger(entries)
    if not explainer.conversations:
        return {
            "view": "ask",
            "question": question,
            "answers": [],
            "note": (
                "No run has recorded a conversation yet, so there is nothing to answer "
                "from. This never re-runs anything to find out."
            ),
        }

    wanted = [item] if item else sorted(explainer.conversations)
    answers = []
    for work_item in wanted:
        answer = explainer.answer(work_item, question)
        if not answer.answered:
            continue
        answers.append({"workItem": work_item, **answer.as_dict()})
    answers.sort(key=lambda a: -len(a.get("citations", [])))

    return {
        "view": "ask",
        "question": question,
        "answers": answers[:MAX_ANSWERS],
        "searched": len(wanted),
        "note": (
            "Quoted from what each run recorded at the time, never reconstructed and never "
            "re-run. Where nothing is returned, the record does not contain the answer."
        ),
    }


def _unavailable(view: str, reason: str) -> dict[str, Any]:
    """A view that cannot be built says why, in the shape the client already renders.

    Not an HTTP error: the factory is fine and the page is fine; one panel has nothing
    behind it, and an operator needs to know which and why. Availability with a reason is
    how every metric in this codebase reports the same situation.
    """
    return {"view": view, "available": False, "reason": reason}


#: The views the server offers, in the order the client lists them.
VIEWS: tuple[str, ...] = (
    "overview",
    "activity",
    "runs",
    "definition",
    "evaluation",
    "registry",
)

#: Reachable, but not a tab: `ask` answers a question rather than showing a page.
EXTRA_VIEWS: tuple[str, ...] = ("run", "ask")


MAX_WINDOW_DAYS = 3650
"""Ten years. Long enough for any real question, short enough that `timedelta` cannot
overflow -- `?days=99999999` used to raise `OverflowError` out of `Window.last` and drop
the connection with no response."""


def _days_from(params: dict[str, list[str]]) -> int | None:
    """The requested window in days, or None when the request does not name a usable one.

    A negative value was the dangerous case: it produced HTTP 200 and a window whose start
    was *after* its end, so `Window.contains` was false for every entry and a fully
    populated factory rendered as `runs=0` with everything else `insufficient_data`. The
    dashboard renders the window nowhere, so nothing on the page hinted at it.
    """
    raw = params.get("days", ["7"])[0]
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return None
    if days < 1 or days > MAX_WINDOW_DAYS:
        return None
    return days


def _script_hash(html: str) -> str:
    """The CSP hash of the page's one inline script.

    Computed from the document rather than pasted beside it, because a hash written by hand
    goes stale the first time the script changes -- and a stale hash means the page silently
    stops working, which is the failure mode a CSP is most often blamed for and least often
    guilty of.
    """
    start = html.index("<script>") + len("<script>")
    end = html.index("</script>", start)
    digest = hashlib.sha256(html[start:end].encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def make_handler(data: DashboardData) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to one dashboard's data.

    A closure rather than a class attribute so two dashboards in one process do not share
    state -- which matters for tests more than for operators, and a design that is awkward
    to test is one whose behaviour nobody checks.
    """

    class Handler(BaseHTTPRequestHandler):
        server_version = "software-factory-dash"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            if parsed.path in ("/", "/index.html"):
                self._respond(200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
                return
            if parsed.path.startswith("/api/"):
                view = parsed.path.removeprefix("/api/").strip("/") or "overview"
                try:
                    result = data.payload(view, params)
                except Exception as exc:
                    # A traceback into the operator's terminal and no response at all is
                    # the outcome `log_message` was overridden to prevent. An error the
                    # page can render is strictly better than a dropped connection.
                    result = {
                        "error": "view.failed",
                        "message": f"{type(exc).__name__} while building {view!r}",
                    }
                status = _status_for(result)
                body = json.dumps(result, indent=2, default=str)
                self._respond(status, "application/json; charset=utf-8", body.encode("utf-8"))
                return
            self._respond(404, "text/plain; charset=utf-8", b"not found")

        def log_message(self, format: str, *args: Any) -> None:
            """Silent by default. A dashboard that prints a line per request buries whatever
            the operator was actually watching in the terminal they started it from."""

        def _respond(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # `connect-src 'self'` because without it the directive falls back to
            # `default-src 'none'` and blocks the page's own fetch -- the only data path it
            # has, so the whole client was inert under an enforcing browser.
            #
            # `script-src` names the hash of the one inline script rather than
            # `'unsafe-inline'`. Inline handlers on *injected* elements are governed by
            # `script-src` too, so `'unsafe-inline'` disabled the single protection that
            # matters here: this page renders text that came from a model.
            self.send_header(
                "Content-Security-Policy",
                (
                    "default-src 'none'; "
                    "connect-src 'self'; "
                    "style-src 'unsafe-inline'; "
                    # `data:` and nothing else. The page draws its own texture from an
                    # inline SVG data URI, which loads nothing from anywhere -- but
                    # `img-src` falls back to `default-src 'none'`, so without naming it
                    # the browser blocks the page's own paint and logs a violation. `data:`
                    # admits no host, so this widens the policy by exactly one scheme that
                    # cannot reach the network.
                    "img-src data:; "
                    f"script-src 'sha256-{SCRIPT_HASH}'; "
                    "base-uri 'none'; "
                    "form-action 'none'"
                ),
            )
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _status_for(result: dict[str, Any]) -> int:
    """HTTP status from the payload's own error code.

    A structured error returned as 200 is one a caller has to inspect to notice, and the
    inverted-window bug was exactly that: a successful-looking response describing an empty
    factory that was not empty.
    """
    error = result.get("error")
    if error in ("days.invalid", "run.missing"):
        return 400
    if error == "view.unknown":
        return 404
    if error == "view.failed":
        return 500
    return 200


def serve(
    ledger_path: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    integrations: frozenset[str] = frozenset(),
    root: Path | None = None,
    ready: Callable[[str], None] | None = None,
) -> ThreadingHTTPServer:
    """Start the dashboard. Returns the server so a caller can shut it down.

    Returning rather than blocking: `sf dash` blocks on `serve_forever`, and a test needs the
    handle. A function that can only be used one way is a function only one caller can have.
    """
    server = ThreadingHTTPServer(
        (host, port),
        make_handler(DashboardData(ledger_path, integrations, root=root)),
    )
    if ready is not None:
        ready(f"http://{host}:{server.server_address[1]}/")
    return server


#: One document, no build step, no external resources.
#:
#: Written plainly rather than prettily. The dashboard's job is to make a factory's state
#: legible to somebody who is trying to decide something, and a page that needs a toolchain
#: to change is a page nobody changes.
INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>software factory</title>
<style>
  /* ---------------------------------------------------------------- tokens */
  :root {
    color-scheme: dark light;
    /* One accent, used sparingly. A palette with three accents has none. */
    --accent: #7c8cff;
    --accent-2: #58e6d0;
    --bg: #0b0c10;
    --raise: #101218;
    --card: #14161e;
    --line: #1e212b;
    --line-2: #2a2e3b;
    --ink: #eceef5;
    --dim: #9096a8;
    --faint: #61667a;
    --good: #4ec9a0;
    --warn: #e8b458;
    --bad: #f2748c;
    --r: 14px;
    --r-sm: 9px;
    --pad: 22px;
    --ease: cubic-bezier(.2, .8, .2, 1);
    --spring: cubic-bezier(.34, 1.56, .64, 1);
    /* A stack, not a download. Each entry is a genuinely good face that ships with its
       platform, ordered so a reader gets the best one their machine has. A dashboard that
       fetches a font is a dashboard that renders differently offline -- and this one is
       for looking at a factory running offline. */
    --sans: "SF Pro Text", -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text",
            "Segoe UI", Inter, Roboto, "Helvetica Neue", system-ui, sans-serif;
    --display: "SF Pro Display", -apple-system, BlinkMacSystemFont,
               "Segoe UI Variable Display", "Segoe UI", Inter, system-ui, sans-serif;
    --mono: "SF Mono", ui-monospace, "JetBrains Mono", "Cascadia Mono", "Roboto Mono",
            Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: light) {
    :root {
      --accent: #5566e8; --accent-2: #1d9f8c;
      --bg: #fbfbfd; --raise: #ffffff; --card: #ffffff;
      --line: #e8e9ef; --line-2: #d6d8e2;
      --ink: #14161d; --dim: #5b6072; --faint: #8b90a0;
      --good: #0f9d76; --warn: #b57414; --bad: #d64560;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 400 14px/1.6 var(--sans);
    font-feature-settings: "cv01","cv03","ss01","calt";
    -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
    letter-spacing: -.006em;
  }
  ::selection { background: color-mix(in oklab, var(--accent) 35%, transparent); }

  .shell { display: grid; grid-template-columns: 232px minmax(0, 1fr); min-height: 100vh; }

  /* ---------------------------------------------------------------- sidebar */
  .side {
    display: flex; flex-direction: column; gap: 26px;
    padding: 20px 14px; border-right: 1px solid var(--line);
    background: var(--raise); position: sticky; top: 0; height: 100vh;
  }
  .brand { display: flex; align-items: center; gap: 10px; padding: 2px 8px; }
  .dot-logo {
    width: 22px; height: 22px; border-radius: 7px; flex: none;
    background: linear-gradient(145deg, var(--accent), var(--accent-2));
    box-shadow: 0 2px 10px -2px color-mix(in oklab, var(--accent) 60%, transparent);
  }
  .brand b { font: 600 14.5px/1 var(--display); letter-spacing: -.02em; }
  .brand span { color: var(--faint); font-weight: 400; }

  .side nav { display: flex; flex-direction: column; gap: 1px; }
  .side nav button {
    display: flex; align-items: center; gap: 11px; width: 100%; text-align: left;
    font: 400 13.5px/1 var(--sans); color: var(--dim); background: none; border: 0;
    cursor: pointer; padding: 9px 11px; border-radius: var(--r-sm);
    transition: background .2s var(--ease), color .2s var(--ease), transform .12s var(--ease);
  }
  .side nav button:hover { background: var(--card); color: var(--ink); }
  .side nav button:active { transform: scale(.975); }
  .side nav button .ic { width: 16px; height: 16px; flex: none; opacity: .8; }
  .side nav button[aria-current="true"] {
    background: color-mix(in oklab, var(--accent) 14%, transparent);
    color: var(--ink); font-weight: 550;
  }
  .side nav button[aria-current="true"] .ic { opacity: 1; color: var(--accent); }

  .side footer { margin-top: auto; padding: 0 11px; display: flex; flex-direction: column; gap: 9px; }
  .pulse { display: flex; align-items: center; gap: 8px; font: 11.5px/1 var(--mono); color: var(--faint); }
  .bead { width: 6px; height: 6px; border-radius: 50%; background: var(--good); flex: none; }
  .bead.live { animation: breathe 2.8s var(--ease) infinite; }
  .bead.stale { background: var(--warn); } .bead.down { background: var(--bad); }
  @keyframes breathe { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: .45; transform: scale(.8); } }
  .keys { font: 11px/1.7 var(--mono); color: var(--faint); }
  .keys kbd { color: var(--dim); }

  /* ---------------------------------------------------------------- main */
  main { min-width: 0; padding: 26px var(--pad) 72px; max-width: 1400px; }
  .titlebar { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
  h1 { margin: 0; font: 600 clamp(22px, 2.6vw, 29px)/1.15 var(--display); letter-spacing: -.033em; }
  .lede { margin: 6px 0 0; color: var(--dim); font-size: 13.5px; max-width: 66ch; }
  .tools { display: flex; align-items: center; gap: 7px; flex: none; }
  .seg { display: flex; background: var(--card); border: 1px solid var(--line); border-radius: var(--r-sm); padding: 2px; }
  .seg button {
    font: 12px/1 var(--mono); color: var(--faint); background: none; border: 0; cursor: pointer;
    padding: 6px 9px; border-radius: 7px; transition: background .18s var(--ease), color .18s;
  }
  .seg button:hover { color: var(--ink); }
  .seg button[aria-pressed="true"] { background: var(--raise); color: var(--ink); box-shadow: 0 1px 3px rgba(0,0,0,.25); }
  .ghost {
    font: 400 13px/1 var(--sans); color: var(--dim); cursor: pointer; padding: 8px 12px;
    background: var(--card); border: 1px solid var(--line); border-radius: var(--r-sm);
    transition: color .18s, border-color .18s, transform .12s var(--ease);
  }
  .ghost:hover { color: var(--ink); border-color: var(--line-2); }
  .ghost:active { transform: scale(.96); }
  .ghost[aria-pressed="true"] { color: var(--bg); background: var(--accent); border-color: var(--accent); }

  /* ---------------------------------------------------------------- ask */
  .ask { margin: 22px 0 26px; position: relative; }
  .ask input {
    width: 100%; font: 400 15px/1.5 var(--sans); color: var(--ink);
    background: var(--card); border: 1px solid var(--line); border-radius: var(--r);
    padding: 15px 17px 15px 44px; outline: none;
    transition: border-color .22s var(--ease), box-shadow .22s var(--ease), background .22s;
  }
  .ask input::placeholder { color: var(--faint); }
  .ask input:focus {
    border-color: color-mix(in oklab, var(--accent) 60%, transparent);
    box-shadow: 0 0 0 4px color-mix(in oklab, var(--accent) 13%, transparent);
    background: var(--raise);
  }
  .ask .ic { position: absolute; left: 15px; top: 50%; transform: translateY(-50%);
             width: 17px; height: 17px; color: var(--faint); pointer-events: none; }
  .ask .hint { position: absolute; right: 14px; top: 50%; transform: translateY(-50%);
               font: 11px/1 var(--mono); color: var(--faint); pointer-events: none; }
  .answers { margin-top: 12px; display: flex; flex-direction: column; gap: 9px; }
  .answer {
    background: var(--card); border: 1px solid var(--line); border-radius: var(--r);
    padding: 14px 16px; animation: slide .34s var(--ease) both;
  }
  .answer .who { font: 11px/1 var(--mono); color: var(--faint); }
  .answer blockquote {
    margin: 8px 0 0; padding-left: 12px; border-left: 2px solid var(--accent);
    color: var(--ink); font-size: 13.5px;
  }
  .answer .from { font: 10.5px/1.7 var(--mono); color: var(--faint); margin-top: 4px; }
  @keyframes slide { from { opacity: 0; transform: translateY(6px); } }

  /* ---------------------------------------------------------------- grid */
  .grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 11px; }
  .cell {
    grid-column: span 3; background: var(--card); border: 1px solid var(--line);
    border-radius: var(--r); padding: 16px 17px; display: flex; flex-direction: column;
    animation: rise .42s var(--ease) both; animation-delay: calc(var(--i, 0) * 38ms);
    transition: border-color .22s var(--ease), transform .22s var(--spring);
  }
  .cell:hover { border-color: var(--line-2); }
  .cell.w4 { grid-column: span 4; } .cell.w6 { grid-column: span 6; }
  .cell.w8 { grid-column: span 8; } .cell.w12 { grid-column: span 12; }
  .cell.bare { padding: 0; overflow: hidden; }
  @keyframes rise { from { opacity: 0; transform: translateY(10px); } }
  @media (max-width: 1150px) { .cell { grid-column: span 6; } .cell.w8, .cell.w12 { grid-column: span 12; } }
  @media (max-width: 760px) {
    .shell { grid-template-columns: 1fr; }
    .side { position: static; height: auto; flex-direction: row; align-items: center; gap: 14px; overflow-x: auto; }
    .side nav { flex-direction: row; } .side footer { display: none; }
    .cell, .cell.w4, .cell.w6, .cell.w8 { grid-column: span 12; }
  }

  .cap { font: 500 10.5px/1 var(--mono); letter-spacing: .1em; text-transform: uppercase; color: var(--faint); }
  .big { font: 600 clamp(26px,3.4vw,38px)/1 var(--display); letter-spacing: -.04em;
         margin-top: 11px; font-variant-numeric: tabular-nums; }
  .big.sm { font-size: clamp(20px, 2.2vw, 25px); }
  .big.id { font: 550 14px/1.4 var(--mono); letter-spacing: -.01em; overflow-wrap: anywhere; }
  .big u { font-size: 13px; font-weight: 400; color: var(--dim); text-decoration: none; margin-left: 6px; }
  .trend { align-self: flex-start; margin-top: 10px; font: 11.5px/1 var(--mono);
           padding: 4px 8px; border-radius: 6px; }
  .trend.up { color: var(--good); background: color-mix(in oklab, var(--good) 12%, transparent); }
  .trend.down { color: var(--bad); background: color-mix(in oklab, var(--bad) 12%, transparent); }
  .trend.flat { color: var(--faint); background: color-mix(in oklab, var(--faint) 10%, transparent); }
  .cell h3 { margin: 0; font: 550 13.5px/1.3 var(--sans); letter-spacing: -.012em; }
  .said { margin: 9px 0 0; color: var(--dim); font-size: 12.5px; }
  .said.q { color: var(--faint); font-style: italic; }

  svg.plot { width: 100%; height: 118px; display: block; margin-top: auto; padding-top: 12px; }
  svg.spark { width: 100%; height: 30px; display: block; margin-top: 14px; }
  .scale { display: flex; justify-content: space-between; margin-top: 7px;
           font: 10.5px/1 var(--mono); color: var(--faint); }

  .ring { display: flex; align-items: center; gap: 15px; margin-top: 12px; }
  .ring .n { font: 600 25px/1 var(--display); letter-spacing: -.035em; font-variant-numeric: tabular-nums; }
  .ring .u { color: var(--dim); font-size: 12px; margin-top: 3px; }

  .lanes { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
           gap: 9px; align-items: start; margin-top: 13px; }
  .lane { background: var(--raise); border: 1px solid var(--line); border-radius: 11px; padding: 10px; }
  .lane.none { opacity: .45; }
  .lane .top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
  .lane h4 { margin: 0; font: 550 10.5px/1 var(--mono); letter-spacing: .09em; text-transform: uppercase; color: var(--dim); }
  .lane .ct { font: 11px/1 var(--mono); color: var(--faint); }
  .lane .bar { height: 2px; border-radius: 2px; background: var(--line); margin-bottom: 9px; overflow: hidden; }
  .lane .bar i { display: block; height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-2)); }
  .card2 { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
           padding: 9px 10px; margin-bottom: 6px; transition: transform .18s var(--spring); }
  .card2:hover { transform: translateY(-1px); }
  .card2.hot { border-color: color-mix(in oklab, var(--warn) 45%, var(--line)); }
  .card2 .t { font-size: 12.5px; font-weight: 500; line-height: 1.4; }
  .card2 .m { font: 10.5px/1.5 var(--mono); color: var(--faint); margin-top: 4px; overflow-wrap: anywhere; }
  .card2 .y { font-size: 11.5px; color: var(--warn); margin-top: 5px; }

  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  thead th { position: sticky; top: 0; z-index: 1; background: var(--card); text-align: left;
             padding: 11px 15px; font: 500 10.5px/1 var(--mono); letter-spacing: .1em;
             text-transform: uppercase; color: var(--faint); border-bottom: 1px solid var(--line); }
  tbody td { padding: 12px 15px; border-bottom: 1px solid var(--line); }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr { transition: background .15s; }
  tbody tr:hover { background: var(--raise); }
  tbody tr.go { cursor: pointer; }
  tbody tr.go:active { transform: scale(.998); }
  td.n, th.n { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
  .scroll { overflow: auto; max-height: 66vh; }
  code, .mono { font-family: var(--mono); font-size: 12.5px; }

  .tag { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 6px;
         font: 11px/1.6 var(--mono); border: 1px solid transparent; white-space: nowrap; }
  .tag.ok { color: var(--good); background: color-mix(in oklab, var(--good) 11%, transparent); }
  .tag.warn { color: var(--warn); background: color-mix(in oklab, var(--warn) 11%, transparent); }
  .tag.bad { color: var(--bad); background: color-mix(in oklab, var(--bad) 11%, transparent); }
  .tag.info { color: var(--accent); background: color-mix(in oklab, var(--accent) 13%, transparent); }
  .tag.mute { color: var(--faint); background: color-mix(in oklab, var(--faint) 11%, transparent); }

  .meter { height: 4px; border-radius: 4px; background: var(--line); overflow: hidden; margin-top: 7px; }
  .meter i { display: block; height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-2));
             transition: width .6s var(--ease); }

  .thread { position: relative; padding-left: 24px; margin-top: 13px; }
  .thread::before { content: ""; position: absolute; left: 5px; top: 10px; bottom: 10px;
                    width: 1px; background: var(--line-2); }
  .beat { position: relative; padding: 9px 0; }
  .beat::before { content: ""; position: absolute; left: -23px; top: 15px; width: 9px; height: 9px;
                  border-radius: 50%; background: var(--bg); border: 2px solid var(--faint); }
  .beat.ok::before { border-color: var(--good); }
  .beat.bad::before { border-color: var(--bad); }
  .beat.info::before { border-color: var(--accent); }
  .beat .hd { display: flex; align-items: baseline; gap: 9px; flex-wrap: wrap; }
  .beat .ty { font: 550 12.5px/1.5 var(--mono); }
  .beat .ts { font: 10.5px/1.5 var(--mono); color: var(--faint); }
  .beat .sm { font-size: 12.5px; color: var(--dim); margin-top: 3px; }
  .beat .sm b { color: var(--ink); font-weight: 550; }
  .beat details { margin-top: 6px; }
  .beat summary { cursor: pointer; font: 10.5px/1.6 var(--mono); color: var(--faint); width: max-content; }
  .beat summary:hover { color: var(--accent); }
  .beat pre { margin: 6px 0 0; padding: 10px 12px; background: var(--raise); border: 1px solid var(--line);
              border-radius: 9px; overflow-x: auto; font: 11.5px/1.6 var(--mono); color: var(--dim); max-height: 300px; }

  .rule { grid-column: 1 / -1; margin: 14px 2px 0; display: flex; align-items: center; gap: 11px;
          font: 500 10.5px/1 var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--faint); }
  .rule::after { content: ""; flex: 1; height: 1px; background: var(--line); }
  .said.note { color: var(--dim); font-size: 12.5px; margin: 11px 2px 0; }
  .nothing { text-align: center; padding: 68px 20px; color: var(--dim); }
  .nothing h3 { margin: 12px 0 5px; font: 550 15.5px/1.3 var(--display); color: var(--ink); }
  .load { grid-column: span 3; height: 118px; border-radius: var(--r); border: 1px solid var(--line);
          background: linear-gradient(100deg, var(--card) 32%, var(--raise) 50%, var(--card) 68%);
          background-size: 220% 100%; animation: sweep 1.25s linear infinite; }
  @keyframes sweep { to { background-position: -220% 0; } }
  .back { font: 400 13px/1 var(--sans); background: none; border: 0; color: var(--accent);
          cursor: pointer; padding: 0 0 13px; }
  .back:hover { text-decoration: underline; }
  @media (prefers-reduced-motion: reduce) { *, *::before, *::after {
    animation-duration: .001ms !important; transition-duration: .001ms !important; } }
</style>
</head>
<body>
<div class="shell">
  <aside class="side">
    <div class="brand"><span class="dot-logo"></span><b>software<span>&#8202;factory</span></b></div>
    <nav id="nav"></nav>
    <footer>
      <div class="pulse"><span class="bead live" id="bead"></span><span id="stamp">reading</span></div>
      <div class="keys"><kbd>/</kbd> ask &middot; <kbd>r</kbd> refresh</div>
    </footer>
  </aside>
  <main>
    <div class="titlebar">
      <div>
        <h1 id="title">Overview</h1>
        <p class="lede" id="lede">Metrics, their trend, and when the work actually happened.</p>
      </div>
      <div class="tools">
        <div class="seg" id="window"></div>
        <button class="ghost" id="auto" aria-pressed="false" title="refresh every 5s">auto</button>
      </div>
    </div>
    <div class="ask">
      <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
        stroke-linecap="round" aria-hidden="true"><path d="M12 17h.01M9.1 9a3 3 0 015.8 1c0 2-3 2.5-3 4"/>
        <circle cx="12" cy="12" r="9"/></svg>
      <input id="ask" type="text" autocomplete="off" spellcheck="false"
        placeholder="Ask what a run decided, tried, or was constrained by">
      <span class="hint">answers from the record, never re-run</span>
    </div>
    <div id="answers" class="answers"></div>
    <div id="content"></div>
  </main>
</div>
<script>
const content = document.getElementById('content');
const titleEl = document.getElementById('title');
const ledeEl = document.getElementById('lede');
const bead = document.getElementById('bead');
const stamp = document.getElementById('stamp');
const askEl = document.getElementById('ask');
const answersEl = document.getElementById('answers');

// Everything this page renders came from the ledger, and the ledger is full of text from
// outside the trust boundary: model output, work-item titles written by whoever opened the
// issue, command stderr. Nothing reaches innerHTML without passing through here.
function esc(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// A short, real tap. Long buzzes are the reason people turn haptics off, and a page that
// vibrates on a device that cannot is a page that throws -- so this is guarded and quiet.
function tap(ms) {
  try { if (navigator.vibrate) navigator.vibrate(ms || 6); } catch (e) { /* not available */ }
}

const ICONS = {
  overview: 'M3 13h4l3 7 4-16 3 9h4',
  activity: 'M3 5h18M3 12h18M3 19h18',
  runs: 'M8 5v14l11-7z',
  definition: 'M12 3v3m0 12v3m9-9h-3M6 12H3m13.5-6.5l-2 2m-9 9l-2 2m0-13l2 2m9 9l2 2M15 12a3 3 0 11-6 0 3 3 0 016 0z',
  evaluation: 'M12 3a9 9 0 100 18 9 9 0 000-18zm0 5a4 4 0 100 8 4 4 0 000-8z',
  registry: 'M4 6h16M4 12h16M4 18h10',
};

function icon(name) {
  return `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="${ICONS[name] || ICONS.overview}"/></svg>`;
}

const VIEWS = [
  ['overview',   'Overview',   'Metrics, their trend, and when the work actually happened.'],
  ['activity',   'Activity',   'Work items by stage. Anything needing a person sorts first.'],
  ['runs',       'Runs',       'Every run this ledger records. Open one to inspect it.'],
  ['definition', 'Definition', 'What this factory is configured to be, and who may change it.'],
  ['evaluation', 'Evaluation', 'Scorers, their outcomes, and every improvement proposal.'],
  ['registry',   'Registry',   'Memory and skill health — is what this factory learned still worth carrying?'],
];

let view = 'overview', days = 7, runId = null, timer = null, lastData = null;

const nav = document.getElementById('nav');
VIEWS.forEach(([id, label]) => {
  const b = document.createElement('button');
  b.dataset.view = id;
  b.innerHTML = `${icon(id)}<span>${esc(label)}</span>`;
  b.addEventListener('click', () => { tap(); go(id); });
  nav.appendChild(b);
});

const windowEl = document.getElementById('window');
[1, 7, 30, 90, 365].forEach(d => {
  const b = document.createElement('button');
  b.textContent = d + 'd';
  b.setAttribute('aria-pressed', String(d === days));
  b.addEventListener('click', () => { tap(); days = d; syncWindow(); load(); });
  windowEl.appendChild(b);
});
function syncWindow() {
  windowEl.querySelectorAll('button').forEach(b =>
    b.setAttribute('aria-pressed', String(b.textContent === days + 'd')));
}

const autoBtn = document.getElementById('auto');
autoBtn.addEventListener('click', () => {
  tap();
  const on = autoBtn.getAttribute('aria-pressed') !== 'true';
  autoBtn.setAttribute('aria-pressed', String(on));
  if (timer) { clearInterval(timer); timer = null; }
  if (on) timer = setInterval(() => load({ quiet: true }), 5000);
});

function go(next, id) {
  view = next; runId = id || null;
  const meta = VIEWS.find(v => v[0] === next);
  nav.querySelectorAll('button').forEach(b =>
    b.setAttribute('aria-current', String(b.dataset.view === next)));
  titleEl.textContent = next === 'run' ? 'Run' : (meta ? meta[1] : next);
  ledeEl.textContent = next === 'run'
    ? 'Everything the ledger recorded about one run, in the order it happened.'
    : (meta ? meta[2] : '');
  load();
}

function status(kind, text) {
  bead.className = 'bead' + (kind === 'ok' ? ' live' : ' ' + kind);
  stamp.textContent = text;
}

/* ------------------------------------------------------------------- ask */

askEl.addEventListener('keydown', e => {
  if (e.key === 'Enter') { tap(10); ask(askEl.value); }
  if (e.key === 'Escape') { askEl.value = ''; answersEl.innerHTML = ''; askEl.blur(); }
});

async function ask(question) {
  if (!question.trim()) { answersEl.innerHTML = ''; return; }
  answersEl.innerHTML = `<div class="answer"><div class="who">reading the record…</div></div>`;
  let data;
  try {
    data = await (await fetch(`/api/ask?q=${encodeURIComponent(question)}`)).json();
  } catch (err) {
    answersEl.innerHTML = `<div class="answer"><div class="who">could not reach the factory</div>
      <div class="said">${esc(err)}</div></div>`;
    return;
  }
  if (data.error) {
    answersEl.innerHTML = `<div class="answer"><div class="who">${esc(data.error)}</div>
      <div class="said">${esc(data.message)}</div></div>`;
    return;
  }
  if (!data.answers || !data.answers.length) {
    answersEl.innerHTML = `<div class="answer"><div class="who">the record does not say</div>
      <div class="said q">${esc(data.note)}</div></div>`;
    return;
  }
  answersEl.innerHTML = data.answers.map(a =>
    `<div class="answer"><div class="who">${esc(a.workItem)}</div>`
    + (a.citations || []).map(c =>
        `<blockquote>${esc(c.text)}</blockquote>`
        + `<div class="from">${esc(c.kind)} · ${esc(c.stage)} · ${esc(c.run_id || c.runId || '')}</div>`
      ).join('')
    + `</div>`).join('')
    + `<div class="said note">${esc(data.note)}</div>`;
}

document.addEventListener('keydown', e => {
  if (e.target === askEl) return;
  if (e.key === '/') { e.preventDefault(); askEl.focus(); return; }
  if (e.key === 'r') { tap(); load(); return; }
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= VIEWS.length) { tap(); go(VIEWS[n - 1][0]); }
});

/* --------------------------------------------------------------- drawing */

function fmt(v) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'number') {
    return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(3)));
  }
  return String(v);
}
function pct(v) { return v === null || v === undefined ? '—' : Math.round(v * 100) + '%'; }

function trend(v, unit) {
  if (v === null || v === undefined) return `<div class="trend flat">no comparison</div>`;
  const cls = v > 0 ? 'up' : (v < 0 ? 'down' : 'flat');
  const arrow = v > 0 ? '↑' : (v < 0 ? '↓' : '→');
  return `<div class="trend ${cls}">${arrow} ${esc(fmt(Math.abs(v)))}${unit ? ' ' + esc(unit) : ''}</div>`;
}

function cell(inner, opts) {
  const o = opts || {};
  return `<div class="cell ${o.cls || ''}" style="--i:${o.i || 0}">${inner}</div>`;
}

function stat(label, value, unit, extra, i, cls) {
  const long = String(value === null || value === undefined ? '' : value).length > 16;
  return cell(`<div class="cap">${esc(label)}</div>`
    + `<div class="big${long ? ' id' : ''}">${esc(value)}`
    + `${unit ? `<u>${esc(unit)}</u>` : ''}</div>` + (extra || ''), { i, cls });
}

/** Charts are arithmetic and SVG path data. This page loads nothing from anywhere. */
function plot(values, key) {
  const w = 1000, h = 118, pad = 6, n = values.length;
  if (!n) return `<div class="said q">no activity in this window</div>`;
  const top = Math.max(1, ...values);
  const x = i => n === 1 ? w / 2 : (i / (n - 1)) * w;
  const y = v => pad + (1 - v / top) * (h - pad * 2);
  const d = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  return `<svg class="plot" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img"
      aria-label="activity per bucket">
    <defs><linearGradient id="f${key}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="var(--accent)" stop-opacity=".34"/>
      <stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
    <path d="${d}L${w},${h}L0,${h}Z" fill="url(#f${key})"/>
    <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2"
      stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>`;
}

function spark(values, colour) {
  const w = 300, h = 30, n = values.length;
  if (!n) return '';
  const top = Math.max(1, ...values);
  const x = i => n === 1 ? w / 2 : (i / (n - 1)) * w;
  const y = v => 2 + (1 - v / top) * (h - 4);
  const d = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <path d="${d}" fill="none" stroke="${colour}" stroke-width="1.6"
      stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
}

function ring(share, caption, key) {
  const size = 68, r = 28, c = 2 * Math.PI * r;
  const filled = share === null || share === undefined ? 0 : Math.max(0, Math.min(1, share));
  return `<div class="ring">
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
      <defs><linearGradient id="r${key}" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stop-color="var(--accent)"/>
        <stop offset="100%" stop-color="var(--accent-2)"/></linearGradient></defs>
      <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="var(--line)" stroke-width="6"/>
      <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="url(#r${key})" stroke-width="6"
        stroke-linecap="round" stroke-dasharray="${(c*filled).toFixed(1)} ${c.toFixed(1)}"
        transform="rotate(-90 ${size/2} ${size/2})"/></svg>
    <div><div class="n">${share === null || share === undefined ? '—' : pct(share)}</div>
    <div class="u">${esc(caption)}</div></div></div>`;
}

function bucketLabel(seconds) {
  if (seconds >= 604800) return `${Math.round(seconds / 604800)}-week buckets`;
  if (seconds >= 86400) return `${Math.round(seconds / 86400)}-day buckets`;
  return `${Math.max(1, Math.round(seconds / 3600))}-hour buckets`;
}

/* ------------------------------------------------------------- renderers */

function renderOverview(d) {
  const r = d.current.runs, t = d.trend || {};
  const s = d.series || { runs: [], handoffs: [], gateFailures: [], costUnits: [], bucketSeconds: 3600 };
  const measures = d.current.measures || [];
  const shown = measures.filter(m => m.availability === 'available');
  const absent = measures.filter(m => m.availability !== 'available');
  const gates = shown.find(m => m.name === 'gate_pass_rate');
  const autonomy = measures.find(m => m.name === 'autonomy');

  const rows = absent.map(m =>
    `<tr><td><code>${esc(m.name)}</code></td>`
    + `<td><span class="tag mute">${esc(String(m.availability).replace(/_/g, ' '))}</span></td>`
    + `<td>${esc(m.reason)}</td></tr>`).join('');

  return `<div class="grid">`
    + cell(`<div class="cap">activity — last ${esc(d.days || days)} days</div>`
        + `<div class="big">${esc(r.total)}<u>runs</u></div>` + trend(t.runs, 'vs previous')
        + plot(s.runs, 'runs')
        + `<div class="scale"><span>${esc(String(s.start || '').slice(0, 10))}</span>`
        + `<span>${esc(bucketLabel(s.bucketSeconds || 3600))}</span>`
        + `<span>${esc(String(s.end || '').slice(0, 10))}</span></div>`, { cls: 'w6', i: 0 })
    + cell(`<h3>Gate pass rate</h3><div class="cap">first attempts only</div>`
        + ring(gates ? gates.value : null, gates ? `${gates.sample} evaluated` : 'not measured', 'g')
        + spark(s.gateFailures, 'var(--bad)')
        + `<div class="said q">the line is gate failures per bucket</div>`, { cls: 'w3', i: 1 })
    + cell(`<h3>Autonomy</h3><div class="cap">merged, no human commits</div>`
        + (autonomy && autonomy.availability === 'available'
            ? ring(autonomy.value, `${autonomy.sample} merged`, 'a')
            : ring(null, 'not observable', 'a')
              + `<div class="said q">${esc(autonomy ? autonomy.reason : 'no measurement')}</div>`),
        { cls: 'w3', i: 2 })
    + stat('reached handoff', (s.handoffs || []).reduce((a, b) => a + b, 0), 'changes',
        spark(s.handoffs, 'var(--good)'), 3, 'w3')
    + stat('spend, estimated', (s.costUnits || []).reduce((a, b) => a + b, 0).toFixed(2), 'units',
        spark(s.costUnits, 'var(--accent-2)')
        + `<div class="said q">recorded usage and declared prices, not billing</div>`, 4, 'w3')
    + cell(`<h3>Run mix</h3>`
        + [['work', r.work], ['evaluation', r.evaluation], ['benchmark', r.benchmark],
           ['improvement', r.improvement]].map(([k, n]) =>
          `<div style="margin-top:10px"><div style="display:flex;justify-content:space-between;font-size:12.5px">`
          + `<span>${esc(k)}</span><span class="mono">${esc(n)}</span></div>`
          + `<div class="meter"><i style="width:${r.total ? Math.round((n / r.total) * 100) : 0}%"></i></div></div>`).join('')
        + `<div class="said">${esc(r.note)}</div>`, { cls: 'w6', i: 5 })
    + (shown.filter(m => m.name !== 'gate_pass_rate').length
        ? `<div class="rule">measured</div>` + shown.filter(m => m.name !== 'gate_pass_rate')
            .map((m, i) => stat(m.name, fmt(m.value), m.unit || '',
              trend(t[m.name], m.unit)
              + (m.excludes && m.excludes.length
                  ? `<div class="said q">excludes ${esc(m.excludes.join(', '))}</div>` : ''),
              i, 'w3')).join('')
        : '')
    + (absent.length
        ? `<div class="rule">not measured, and why</div>`
          + cell(`<div class="scroll"><table><thead><tr><th>metric</th><th>state</th>`
            + `<th>reason</th></tr></thead><tbody>${rows}</tbody></table></div>`, { cls: 'w12 bare', i: 0 })
          + `<div class="said note" style="grid-column:1/-1">A metric with no data reports its `
          + `absence. "No change" and "we could not look" are different things, and the `
          + `second must never render as the first.</div>`
        : '')
    + `</div>`;
}

const STAGES = ['intake', 'triage', 'design', 'build', 'review', 'verify', 'handoff'];

function renderActivity(d) {
  if (!d.workItems || !d.workItems.length) {
    return nothing('No work items yet', d.note || 'Run `sf work` and this board fills from the ledger.');
  }
  const byStage = new Map(STAGES.map(s => [s, []]));
  d.workItems.forEach(w => {
    const k = String(w.stage || '').toLowerCase();
    if (!byStage.has(k)) byStage.set(k, []);
    byStage.get(k).push(w);
  });
  const most = Math.max(1, ...[...byStage.values()].map(v => v.length));
  const lanes = [...byStage.entries()].map(([stage, items]) =>
    `<div class="lane ${items.length ? '' : 'none'}">`
    + `<div class="top"><h4>${esc(stage)}</h4><span class="ct">${items.length}</span></div>`
    + `<div class="bar"><i style="width:${Math.round((items.length / most) * 100)}%"></i></div>`
    + (items.length ? items.map(w =>
        `<div class="card2 ${w.needsAttention ? 'hot' : ''}">`
        + `<div class="t">${esc(w.title)}</div>`
        + `<div class="m">${esc(w.id)} · ${esc(w.workClass || '')}`
        + `${w.rework ? ' · rework &times;' + esc(w.rework) : ''}</div>`
        + (w.why ? `<div class="y">${esc(w.why)}</div>` : '') + `</div>`).join('')
      : `<div class="said q" style="font-size:11px">—</div>`) + `</div>`).join('');

  const flagged = d.needingAttention || 0;
  return `<div class="grid">`
    + stat('work items', d.workItems.length, '', '', 0)
    + stat('needing a person', flagged, '',
        flagged ? `<div class="trend down">↑ sorted first</div>`
                : `<div class="trend up">→ nothing waiting on you</div>`, 1)
    + cell(`<h3>Pipeline</h3><div class="lanes">${lanes}</div>`
        + `<div class="said">${esc(d.note || '')}</div>`, { cls: 'w12', i: 2 })
    + `</div>`;
}

function tagFor(s) {
  const v = String(s || '').toLowerCase();
  const cls = /^(ok|pass|passed|succeeded|complete|completed|handoff|merged)$/.test(v) ? 'ok'
    : /^(blocked|failed|fail|refused|violation|closed)$/.test(v) ? 'bad'
    : /^(running|open|opened)$/.test(v) ? 'info' : 'warn';
  return `<span class="tag ${cls}">${esc(v || 'unknown')}</span>`;
}

function renderRuns(d) {
  if (!d.runs || !d.runs.length) {
    return nothing('No runs recorded', 'A run appears the moment `sf work` writes its first ledger entry.');
  }
  const rows = d.runs.map(r =>
    `<tr class="go" data-run="${esc(r.id)}"><td><code>${esc(r.id)}</code></td>`
    + `<td>${esc(r.agent || '—')}</td><td><span class="tag mute">${esc(r.stage || '?')}</span></td>`
    + `<td>${tagFor(r.status)}</td><td class="n">${esc(r.modelCalls)}</td>`
    + `<td class="n">${esc(r.toolCalls)}</td>`
    + `<td class="n">${r.gatesFailed ? `<span class="tag bad">${esc(r.gatesFailed)}</span>` : '0'}</td>`
    + `<td class="n">${esc(r.costUnits)}</td></tr>`).join('');
  const spent = d.runs.reduce((a, r) => a + (r.costUnits || 0), 0);
  return `<div class="grid">`
    + stat('runs recorded', d.total, '', '', 0)
    + stat('spend', spent.toFixed(3), 'units', `<div class="trend flat">estimated</div>`, 1)
    + stat('shown', d.shown, d.truncated ? 'of ' + d.total : '', '', 2)
    + stat('gates failed', d.runs.reduce((a, r) => a + (r.gatesFailed || 0), 0), '', '', 3)
    + cell(`<div class="scroll"><table><thead><tr><th>run</th><th>agent</th><th>stage</th>`
      + `<th>status</th><th class="n">model</th><th class="n">tools</th>`
      + `<th class="n">gates failed</th><th class="n">cost</th></tr></thead>`
      + `<tbody>${rows}</tbody></table></div>`, { cls: 'w12 bare', i: 4 })
    + `<div class="said note" style="grid-column:1/-1">${esc(d.costNote || '')} Click a row to inspect.</div>`
    + `</div>`;
}

const SUMMARY_KEYS = ['stage', 'agent', 'tier', 'gate', 'outcome', 'status', 'tool',
                      'verdict', 'costUnits', 'reason', 'blocker', 'action'];

function summarise(payload) {
  if (!payload || typeof payload !== 'object') return '';
  const parts = SUMMARY_KEYS
    .filter(k => payload[k] !== undefined && payload[k] !== null && payload[k] !== '')
    .map(k => `${esc(k)} <b>${esc(fmt(payload[k]))}</b>`);
  return parts.length ? `<div class="sm">${parts.join(' · ')}</div>` : '';
}

function beatClass(type) {
  if (/violation|escalation/.test(type)) return 'bad';
  if (/gate|finished/.test(type)) return 'ok';
  if (/model|tool|pack/.test(type)) return 'info';
  return '';
}

function renderRun(d) {
  const gates = (d.gates || []).map(g =>
    `<span class="tag ${/^(pass|passed|true)$/i.test(String(g.outcome)) ? 'ok' : 'bad'}">`
    + `${esc(g.gate)}</span>`).join(' ');
  const beats = (d.entries || []).map(e => {
    // A text node, not a string: the inspector returns whole ledger payloads by design,
    // and JSON.stringify escapes JSON metacharacters rather than HTML ones.
    const pre = document.createElement('pre');
    pre.textContent = JSON.stringify(e.payload, null, 2);
    return `<div class="beat ${beatClass(e.type)}"><div class="hd">`
      + `<span class="ty">${esc(e.type)}</span><span class="ts">${esc(e.at)}</span>`
      + `<span class="ts">#${esc(e.seq)}</span></div>${summarise(e.payload)}`
      + `<details><summary>payload</summary>${pre.outerHTML}</details></div>`;
  }).join('');
  const trouble = [...(d.escalations || []), ...(d.violations || [])];
  return `<button class="back" data-back="runs">← all runs</button><div class="grid">`
    + stat('run', d.run, '', '', 0)
    + stat('tool calls', d.toolCalls, '', '', 1)
    + stat('spend', d.costUnits, 'units', `<div class="trend flat">estimate</div>`, 2)
    + stat('ledger entries', (d.entries || []).length, '', '', 3)
    + (gates ? cell(`<h3>Gates</h3><div style="margin-top:11px;display:flex;flex-wrap:wrap;gap:6px">`
        + `${gates}</div>`, { cls: 'w6', i: 4 }) : '')
    + (trouble.length ? cell(`<h3>Escalations and violations</h3>`
        + trouble.map(t => `<div class="said"><b>${esc(t.kind || t.gate || 'event')}</b> — `
          + `${esc(t.reason || t.message || JSON.stringify(t))}</div>`).join(''), { cls: 'w6', i: 5 }) : '')
    + cell(`<h3>Trace</h3><div class="thread">${beats}</div>`
        + `<div class="said note">${esc(d.costNote || '')}</div>`, { cls: 'w12', i: 6 })
    + `</div>`;
}

function tagCell(label, values, i) {
  if (!values || !values.length) {
    return cell(`<h3>${esc(label)}</h3><div class="said q">none declared</div>`, { cls: 'w4', i });
  }
  return cell(`<h3>${esc(label)} <span class="tag mute">${values.length}</span></h3>`
    + `<div style="margin-top:11px;display:flex;flex-wrap:wrap;gap:6px">`
    + values.map(v => `<span class="tag info">${esc(v)}</span>`).join('') + `</div>`, { cls: 'w4', i });
}

function renderDefinition(d) {
  if (d.available === false) return unavailable('Definition', d.reason);
  return `<div class="grid">`
    + stat('factory', d.factory, '', '', 0) + stat('agents', (d.agents || []).length, '', '', 1)
    + stat('skills', (d.skills || []).length, '', '', 2)
    + stat('repositories', (d.repositories || []).length, '', '', 3)
    + `<div class="rule">components</div>`
    + tagCell('agents', d.agents, 0) + tagCell('automations', d.automations, 1)
    + tagCell('runners', d.runners, 2) + tagCell('scorers', d.scorers, 3)
    + tagCell('skills', d.skills, 4) + tagCell('principals', d.principals, 5)
    + ((d.unloaded || []).length ? tagCell('failed to load', d.unloaded, 6) : '')
    + tagCell('repositories', d.repositories, 7)
    + `<div class="said note" style="grid-column:1/-1">${esc(d.note || '')}</div></div>`;
}

function renderEvaluation(d) {
  const names = Object.keys(d.scorers || {});
  if (!names.length && !(d.proposals || []).length) {
    return nothing('Nothing evaluated yet',
      'Scorers record here once runs are sampled, and proposals once the improvement loop opens one.');
  }
  const scorers = names.map((name, i) => {
    const s = d.scorers[name];
    const outcomes = Object.entries(s.outcomes || {});
    const passed = outcomes.filter(([k]) => /pass|ok/i.test(k)).reduce((a, [, v]) => a + v, 0);
    return cell(`<h3>${esc(name)}</h3>`
      + ring(s.sampled ? passed / s.sampled : null, `${s.sampled} sampled`, 's' + i)
      + `<div style="margin-top:11px;display:flex;flex-wrap:wrap;gap:6px">`
      + outcomes.map(([k, v]) => `<span class="tag ${/pass|ok/i.test(k) ? 'ok' : 'bad'}">`
        + `${esc(k)} ${esc(v)}</span>`).join('') + `</div>`, { cls: 'w4', i });
  }).join('');
  const props = (d.proposals || []).map(p =>
    `<tr><td><code>${esc(p.id)}</code></td><td>${esc(p.target)}</td>`
    + `<td>${tagFor(p.status)}</td>`
    + `<td>${esc((p.evidence || []).join('; ') || 'no evidence recorded')}</td></tr>`).join('');
  return `<div class="grid">`
    + (scorers ? `<div class="rule">scorers</div>${scorers}` : '')
    + `<div class="rule">improvement proposals</div>`
    + cell(props
        ? `<div class="scroll"><table><thead><tr><th>proposal</th><th>target</th><th>status</th>`
          + `<th>evidence</th></tr></thead><tbody>${props}</tbody></table></div>`
        : `<div class="said q">No proposal has been opened. The loop proposes; it never applies.</div>`,
        { cls: props ? 'w12 bare' : 'w12', i: 0 })
    + `</div>`;
}

function renderRegistry(d) {
  const m = d.memory || {};
  const lanes = Object.entries(m).filter(([k]) =>
    !['available', 'reason', 'total', 'quarantined', 'bytes'].includes(k));
  const mem = m.available === false
    ? cell(`<h3>Memory</h3><div class="said q">${esc(m.reason)}</div>`, { cls: 'w6', i: 0 })
    : stat('memories', fmt(m.total), '', '', 0)
      + stat('quarantined', fmt(m.quarantined), '',
          m.quarantined ? `<div class="trend down">↑ held out of retrieval</div>`
                        : `<div class="trend up">→ clean</div>`, 1)
      + stat('size', fmt(Math.round((m.bytes || 0) / 1024)), 'KiB', '', 2)
      + (lanes.length ? cell(`<h3>Lanes</h3>` + lanes.map(([lane, n]) =>
          `<div style="margin-top:10px"><div style="display:flex;justify-content:space-between;font-size:12.5px">`
          + `<span>${esc(lane)}</span><span class="mono">${esc(n)}</span></div>`
          + `<div class="meter"><i style="width:${m.total ? Math.round((n / m.total) * 100) : 0}%"></i>`
          + `</div></div>`).join(''), { cls: 'w3', i: 3 }) : '');
  const skills = (d.skills || []).map(s =>
    `<tr><td><code>${esc(s.name)}</code></td><td>${tagFor(s.status)}</td>`
    + `<td class="n">${esc(pct(s.precision))}</td><td class="n">${esc(pct(s.recall))}</td>`
    + `<td class="n">${esc(s.offered)}</td><td class="n">${esc(s.helped)}</td></tr>`).join('');
  return `<div class="grid"><div class="rule">memory fabric</div>${mem}`
    + `<div class="rule">skills</div>`
    + cell(skills
        ? `<div class="scroll"><table><thead><tr><th>skill</th><th>status</th>`
          + `<th class="n">precision</th><th class="n">recall</th><th class="n">offered</th>`
          + `<th class="n">helped</th></tr></thead><tbody>${skills}</tbody></table></div>`
        : `<div class="said q">No skills are declared in this factory.</div>`,
        { cls: skills ? 'w12 bare' : 'w12', i: 0 })
    + `<div class="said note" style="grid-column:1/-1">Precision is, of the times a skill was `
    + `loaded, how often it helped. Recall counts retrospectively-detected misses and is an `
    + `estimate with a stated derivation, not a measurement.</div></div>`;
}

function unavailable(what, reason) {
  return `<div class="grid">` + cell(`<h3>${esc(what)} is not available</h3>`
    + `<div class="said">${esc(reason)}</div>`
    + `<div class="said q">The factory is fine and the page is fine — this one panel has `
    + `nothing behind it, and an operator needs to know which and why.</div>`,
    { cls: 'w6', i: 0 }) + `</div>`;
}

function nothing(head, body) {
  return `<div class="nothing"><h3>${esc(head)}</h3><div class="said">${esc(body)}</div></div>`;
}

/* ------------------------------------------------------------------ load */

const RENDER = {
  overview: renderOverview, activity: renderActivity, runs: renderRuns, run: renderRun,
  definition: renderDefinition, evaluation: renderEvaluation, registry: renderRegistry,
};

async function load(opts) {
  const quiet = opts && opts.quiet;
  if (!quiet) {
    status('ok', 'reading');
    content.innerHTML = `<div class="grid"><div class="load"></div><div class="load"></div>`
      + `<div class="load"></div><div class="load"></div></div>`;
  }
  const q = new URLSearchParams({ days: String(days) });
  if (view === 'run' && runId) q.set('run', runId);
  let data;
  try {
    data = await (await fetch(`/api/${view}?${q}`)).json();
  } catch (err) {
    status('down', 'unreachable');
    content.innerHTML = unavailable('Could not reach the factory', String(err));
    return;
  }
  lastData = data;
  if (data && data.error) {
    content.innerHTML = unavailable(String(data.error), String(data.message));
    status('stale', String(data.error));
    return;
  }
  const render = RENDER[view];
  content.innerHTML = render ? render(data) : unavailable('view.unknown', view);
  status('ok', new Date().toLocaleTimeString());
}

content.addEventListener('click', e => {
  const row = e.target.closest('tr[data-run]');
  if (row) { tap(); go('run', row.dataset.run); return; }
  const back = e.target.closest('[data-back]');
  if (back) { tap(); go(back.dataset.back); }
});

go('overview');
</script>
</body>
</html>
"""


SCRIPT_HASH = _script_hash(INDEX_HTML)
"""The CSP hash of the page's inline script, computed at import from the document itself.

Derived rather than written down: a hash pasted beside the script goes stale the first time
the script changes, and a stale hash means the page silently stops working -- the failure a
CSP is most often blamed for and least often guilty of.
"""
