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
  :root {
    color-scheme: dark light;
    --bg: #08090d; --panel: #0f1118; --panel2: #161926; --raise: #1c2030;
    --line: #232838; --line2: #2e3446;
    --txt: #e9ebf3; --dim: #8e94ac; --faint: #5b6178;
    --cyan: #5ee7ff; --violet: #a78bfa; --green: #4ade80; --amber: #fbbf24; --red: #fb7185;
    --accent: var(--cyan);
    --radius: 12px;
    --shadow: 0 1px 2px rgba(0,0,0,.5), 0 8px 28px -12px rgba(0,0,0,.7);
    --mono: ui-monospace, "SF Mono", "Cascadia Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f6f7fb; --panel: #ffffff; --panel2: #f2f4f9; --raise: #eceff6;
      --line: #e2e6ef; --line2: #d3d9e6;
      --txt: #10131c; --dim: #5b6178; --faint: #878ea3;
      --cyan: #0891b2; --violet: #7c3aed; --green: #15803d; --amber: #b45309; --red: #be123c;
      --shadow: 0 1px 2px rgba(16,19,28,.06), 0 8px 24px -14px rgba(16,19,28,.24);
    }
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; background: var(--bg); color: var(--txt);
    font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  body::before {
    content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
    background:
      radial-gradient(900px 480px at 12% -8%, color-mix(in srgb, var(--cyan) 13%, transparent), transparent 70%),
      radial-gradient(760px 420px at 92% 4%, color-mix(in srgb, var(--violet) 12%, transparent), transparent 72%);
  }
  .app { position: relative; z-index: 1; display: grid; grid-template-columns: 236px minmax(0,1fr); min-height: 100vh; }

  /* ---------------------------------------------------------------- rail */
  .rail {
    display: flex; flex-direction: column; gap: 20px; padding: 20px 14px;
    border-right: 1px solid var(--line);
    background: color-mix(in srgb, var(--panel) 76%, transparent);
    backdrop-filter: blur(14px);
    position: sticky; top: 0; height: 100vh;
  }
  .brand { display: flex; align-items: center; gap: 10px; padding: 2px 8px 0; }
  .mark {
    width: 26px; height: 26px; border-radius: 8px; flex: none;
    background: linear-gradient(140deg, var(--cyan), var(--violet));
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--cyan) 32%, transparent), 0 6px 18px -8px var(--violet);
    position: relative;
  }
  .mark::after {
    content: ""; position: absolute; inset: 7px; border-radius: 3px;
    background: var(--bg); opacity: .82;
  }
  .brandname { font-weight: 650; letter-spacing: -.015em; font-size: 14px; }
  .brandname em { font-style: normal; color: var(--dim); font-weight: 500; }

  nav { display: flex; flex-direction: column; gap: 2px; }
  nav button {
    display: flex; align-items: center; gap: 10px; width: 100%; text-align: left;
    font: inherit; color: var(--dim); background: none; border: 0; cursor: pointer;
    padding: 8px 10px; border-radius: 9px; transition: background .16s, color .16s, transform .16s;
  }
  nav button .g { width: 18px; text-align: center; opacity: .75; font-size: 13px; }
  nav button .num { margin-left: auto; font: 11px/1 var(--mono); color: var(--faint); opacity: 0; transition: opacity .16s; }
  nav button:hover { background: var(--panel2); color: var(--txt); }
  nav button:hover .num { opacity: 1; }
  nav button[aria-current="true"] {
    background: linear-gradient(90deg, color-mix(in srgb, var(--cyan) 16%, transparent), transparent 78%);
    color: var(--txt); font-weight: 600;
    box-shadow: inset 2px 0 0 var(--accent);
  }
  .rail-foot { margin-top: auto; display: flex; flex-direction: column; gap: 8px; padding: 0 10px; }
  .pulse { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--dim); }
  .dot {
    width: 7px; height: 7px; border-radius: 50%; background: var(--green); flex: none;
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--green) 70%, transparent);
    animation: ping 2.4s ease-out infinite;
  }
  .dot.stale { background: var(--amber); animation: none; }
  .dot.down { background: var(--red); animation: none; }
  @keyframes ping {
    0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--green) 60%, transparent); }
    70% { box-shadow: 0 0 0 7px transparent; }
    100% { box-shadow: 0 0 0 0 transparent; }
  }
  .hint { font: 11px/1.5 var(--mono); color: var(--faint); }

  /* ---------------------------------------------------------------- header */
  main { min-width: 0; display: flex; flex-direction: column; }
  header.bar {
    display: flex; align-items: flex-end; justify-content: space-between; gap: 20px;
    padding: 22px 28px 16px; border-bottom: 1px solid var(--line);
    position: sticky; top: 0; z-index: 5;
    background: color-mix(in srgb, var(--bg) 84%, transparent); backdrop-filter: blur(14px);
  }
  h1 { margin: 0; font-size: 21px; letter-spacing: -.022em; font-weight: 640; }
  .sub { margin: 3px 0 0; color: var(--dim); font-size: 12.5px; max-width: 68ch; }
  .controls { display: flex; align-items: center; gap: 8px; flex: none; }
  input[type="search"] {
    font: inherit; font-size: 13px; color: var(--txt); width: 148px;
    background: var(--panel2); border: 1px solid var(--line); border-radius: 9px;
    padding: 7px 10px; transition: border-color .16s, width .2s, box-shadow .16s;
  }
  input[type="search"]:focus {
    outline: none; width: 208px; border-color: var(--accent);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent);
  }
  .seg { display: flex; background: var(--panel2); border: 1px solid var(--line); border-radius: 9px; padding: 2px; }
  .seg button {
    font: 12px/1 var(--mono); color: var(--dim); background: none; border: 0; cursor: pointer;
    padding: 6px 9px; border-radius: 7px; transition: background .16s, color .16s;
  }
  .seg button:hover { color: var(--txt); }
  .seg button[aria-pressed="true"] { background: var(--raise); color: var(--txt); }
  .btn {
    font: inherit; font-size: 13px; color: var(--dim); cursor: pointer;
    background: var(--panel2); border: 1px solid var(--line); border-radius: 9px; padding: 7px 12px;
    transition: background .16s, color .16s, border-color .16s;
  }
  .btn:hover { color: var(--txt); border-color: var(--line2); }
  .btn[aria-pressed="true"] { color: var(--bg); background: var(--accent); border-color: var(--accent); font-weight: 600; }

  /* ---------------------------------------------------------------- content */
  #content { padding: 24px 28px 56px; min-height: 60vh; }
  section { animation: rise .3s cubic-bezier(.22,1,.36,1) both; }
  @keyframes rise { from { opacity: 0; transform: translateY(7px); } to { opacity: 1; transform: none; } }
  .stagger > * { animation: rise .34s cubic-bezier(.22,1,.36,1) both; animation-delay: calc(var(--i, 0) * 32ms); }

  h2.sec {
    margin: 28px 0 12px; font-size: 11px; font-weight: 650; letter-spacing: .1em;
    text-transform: uppercase; color: var(--faint);
    display: flex; align-items: center; gap: 10px;
  }
  h2.sec::after { content: ""; flex: 1; height: 1px; background: var(--line); }
  h2.sec:first-child { margin-top: 0; }

  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(178px, 1fr)); gap: 12px; }
  .card {
    background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
    padding: 14px 16px; box-shadow: var(--shadow); position: relative; overflow: hidden;
    transition: border-color .18s, transform .18s;
  }
  .card::before {
    content: ""; position: absolute; inset: 0 0 auto; height: 2px;
    background: linear-gradient(90deg, var(--accent), transparent 60%); opacity: .55;
  }
  .card:hover { border-color: var(--line2); transform: translateY(-1px); }
  .card .k { font-size: 11px; letter-spacing: .06em; text-transform: uppercase; color: var(--faint); }
  .card .v { font: 600 27px/1.15 var(--mono); letter-spacing: -.02em; margin-top: 6px; }
  .card .v small { font-size: 13px; color: var(--dim); font-weight: 500; margin-left: 4px; }
  .card .d { font: 12px/1 var(--mono); margin-top: 7px; color: var(--faint); }
  .d.up { color: var(--green); } .d.down { color: var(--red); } .d.flat { color: var(--faint); }

  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(272px, 1fr)); gap: 12px; }
  .panel {
    background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
    padding: 14px 16px; box-shadow: var(--shadow);
  }
  .panel h3 { margin: 0 0 4px; font-size: 13.5px; font-weight: 620; letter-spacing: -.01em; }
  .panel .why { font-size: 12px; color: var(--dim); margin: 6px 0 0; }

  .tblwrap { border: 1px solid var(--line); border-radius: var(--radius); overflow: auto; background: var(--panel); box-shadow: var(--shadow); }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  thead th {
    position: sticky; top: 0; background: var(--panel2); z-index: 1;
    text-align: left; padding: 9px 14px; font-size: 11px; letter-spacing: .07em;
    text-transform: uppercase; color: var(--faint); font-weight: 600;
    border-bottom: 1px solid var(--line);
  }
  tbody td { padding: 10px 14px; border-bottom: 1px solid color-mix(in srgb, var(--line) 62%, transparent); vertical-align: top; }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr { transition: background .13s; }
  tbody tr:hover { background: color-mix(in srgb, var(--accent) 6%, transparent); }
  tbody tr.clickable { cursor: pointer; }
  tbody tr.flag td:first-child { box-shadow: inset 2px 0 0 var(--amber); }
  td.num, th.num { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
  code, .mono { font-family: var(--mono); font-size: 12.5px; }

  .pill {
    display: inline-flex; align-items: center; gap: 5px; padding: 2px 8px; border-radius: 999px;
    font: 11px/1.7 var(--mono); border: 1px solid transparent; white-space: nowrap;
  }
  .pill::before { content: ""; width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
  .pill.ok { color: var(--green); background: color-mix(in srgb, var(--green) 12%, transparent); border-color: color-mix(in srgb, var(--green) 28%, transparent); }
  .pill.warn { color: var(--amber); background: color-mix(in srgb, var(--amber) 12%, transparent); border-color: color-mix(in srgb, var(--amber) 28%, transparent); }
  .pill.bad { color: var(--red); background: color-mix(in srgb, var(--red) 12%, transparent); border-color: color-mix(in srgb, var(--red) 28%, transparent); }
  .pill.info { color: var(--cyan); background: color-mix(in srgb, var(--cyan) 12%, transparent); border-color: color-mix(in srgb, var(--cyan) 28%, transparent); }
  .pill.mute { color: var(--faint); background: color-mix(in srgb, var(--faint) 10%, transparent); border-color: var(--line); }

  .meter { height: 5px; border-radius: 999px; background: var(--raise); overflow: hidden; margin-top: 7px; }
  .meter i { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--cyan), var(--violet)); transition: width .5s cubic-bezier(.22,1,.36,1); }

  /* pipeline */
  .pipe { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(196px, 1fr); gap: 10px; overflow-x: auto; padding-bottom: 8px; }
  .col { background: var(--panel2); border: 1px solid var(--line); border-radius: var(--radius); padding: 10px; min-width: 0; }
  .col > header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 9px; }
  .col h4 { margin: 0; font: 600 11px/1 var(--mono); letter-spacing: .07em; text-transform: uppercase; color: var(--dim); }
  .col .n { font: 11px/1 var(--mono); color: var(--faint); background: var(--raise); border-radius: 999px; padding: 3px 7px; }
  .item {
    background: var(--panel); border: 1px solid var(--line); border-radius: 9px; padding: 9px 10px;
    margin-bottom: 7px; cursor: default; transition: border-color .16s, transform .16s;
  }
  .item:hover { border-color: var(--line2); transform: translateY(-1px); }
  .item.flag { border-color: color-mix(in srgb, var(--amber) 46%, var(--line)); background: color-mix(in srgb, var(--amber) 6%, var(--panel)); }
  .item .t { font-size: 12.5px; font-weight: 550; line-height: 1.4; }
  .item .m { font: 11px/1.5 var(--mono); color: var(--faint); margin-top: 4px; word-break: break-all; }
  .item .w { font-size: 11.5px; color: var(--amber); margin-top: 5px; }

  /* timeline */
  .tl { position: relative; margin: 4px 0 0; padding-left: 22px; }
  .tl::before { content: ""; position: absolute; left: 5px; top: 4px; bottom: 4px; width: 1px; background: var(--line); }
  .ev { position: relative; padding: 8px 0; }
  .ev::before {
    content: ""; position: absolute; left: -21px; top: 14px; width: 9px; height: 9px;
    border-radius: 50%; background: var(--panel); border: 2px solid var(--faint);
  }
  .ev.ok::before { border-color: var(--green); }
  .ev.bad::before { border-color: var(--red); }
  .ev.info::before { border-color: var(--cyan); }
  .ev .h { display: flex; align-items: baseline; gap: 9px; flex-wrap: wrap; }
  .ev .ty { font: 600 12px/1.5 var(--mono); }
  .ev .at { font: 11px/1.5 var(--mono); color: var(--faint); }
  .ev pre {
    margin: 6px 0 0; padding: 9px 11px; background: var(--panel2); border: 1px solid var(--line);
    border-radius: 8px; overflow-x: auto; font: 11.5px/1.55 var(--mono); color: var(--dim);
    max-height: 220px;
  }

  .note { color: var(--dim); font-size: 12.5px; margin: 8px 0 0; }
  .unavailable { color: var(--faint); font-style: italic; }
  .estimate::after { content: " est."; color: var(--faint); font-style: italic; font-size: .85em; }
  .empty { text-align: center; padding: 56px 20px; color: var(--dim); }
  .empty .big { font-size: 30px; opacity: .35; }
  .empty h3 { margin: 12px 0 4px; font-size: 15px; color: var(--txt); font-weight: 600; }

  .skel { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(178px, 1fr)); }
  .skel div { height: 88px; border-radius: var(--radius); background: linear-gradient(100deg, var(--panel) 30%, var(--panel2) 50%, var(--panel) 70%); background-size: 220% 100%; animation: shim 1.15s linear infinite; border: 1px solid var(--line); }
  @keyframes shim { to { background-position: -220% 0; } }

  .back { font: inherit; font-size: 12.5px; background: none; border: 0; color: var(--accent); cursor: pointer; padding: 0 0 10px; }
  .back:hover { text-decoration: underline; }

  @media (max-width: 880px) {
    .app { grid-template-columns: 1fr; }
    .rail { position: static; height: auto; flex-direction: row; align-items: center; gap: 12px; overflow-x: auto; }
    .rail nav { flex-direction: row; }
    .rail-foot { display: none; }
    header.bar { flex-direction: column; align-items: stretch; }
  }
  @media (prefers-reduced-motion: reduce) {
    * { animation-duration: .001ms !important; transition-duration: .001ms !important; }
  }
</style>
</head>
<body>
<div class="app">
  <aside class="rail">
    <div class="brand"><span class="mark"></span><span class="brandname">software<em>&#8202;factory</em></span></div>
    <nav id="nav"></nav>
    <div class="rail-foot">
      <div class="pulse"><span class="dot" id="dot"></span><span id="live">reading ledger</span></div>
      <div class="hint">1&#8211;6 view &middot; r refresh &middot; / filter</div>
    </div>
  </aside>
  <main>
    <header class="bar">
      <div>
        <h1 id="title">Overview</h1>
        <p class="sub" id="subtitle">Metrics and their trend &mdash; the view that must not flatter.</p>
      </div>
      <div class="controls">
        <input id="filter" type="search" placeholder="filter" spellcheck="false" autocomplete="off">
        <div class="seg" id="window"></div>
        <button class="btn" id="refresh" title="refresh (r)">refresh</button>
        <button class="btn" id="auto" aria-pressed="false" title="auto-refresh every 5s">auto</button>
      </div>
    </header>
    <div id="content"><div class="skel"><div></div><div></div><div></div><div></div></div></div>
  </main>
</div>
<script>
const content = document.getElementById('content');
const titleEl = document.getElementById('title');
const subEl = document.getElementById('subtitle');
const filterEl = document.getElementById('filter');
const dotEl = document.getElementById('dot');
const liveEl = document.getElementById('live');

// Everything this page renders came from the ledger, and the ledger is full of text from
// outside the trust boundary: model output, work-item titles written by whoever opened the
// issue, command stderr. Nothing reaches innerHTML without passing through here.
function esc(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const VIEWS = [
  ['overview',   'Overview',   '◔', 'Metrics and their trend — the view that must not flatter.'],
  ['activity',   'Activity',   '▦', 'Work items by stage. Anything needing a person sorts first.'],
  ['runs',       'Runs',       '▶', 'Every run this ledger records, newest first. Open one to inspect it.'],
  ['definition', 'Definition', '⚙', 'What this factory is configured to be, and who may change it.'],
  ['evaluation', 'Evaluation', '◎', 'Scorers, their outcomes, and every improvement proposal.'],
  ['registry',   'Registry',   '☷', 'Memory health and skill health — is what this factory learned still worth carrying?'],
];

let view = 'overview';
let days = 7;
let runId = null;
let timer = null;
let lastData = null;

/* ------------------------------------------------------------------ chrome */

const nav = document.getElementById('nav');
VIEWS.forEach(([id, label, glyph], i) => {
  const b = document.createElement('button');
  b.dataset.view = id;
  b.innerHTML = `<span class="g">${glyph}</span><span>${esc(label)}</span><span class="num">${i + 1}</span>`;
  b.addEventListener('click', () => go(id));
  nav.appendChild(b);
});

const windowEl = document.getElementById('window');
[1, 7, 30, 90, 365].forEach(d => {
  const b = document.createElement('button');
  b.textContent = d + 'd';
  b.setAttribute('aria-pressed', String(d === days));
  b.addEventListener('click', () => { days = d; syncWindow(); load(); });
  windowEl.appendChild(b);
});
function syncWindow() {
  windowEl.querySelectorAll('button').forEach(b =>
    b.setAttribute('aria-pressed', String(b.textContent === days + 'd')));
}

document.getElementById('refresh').addEventListener('click', () => load());
const autoBtn = document.getElementById('auto');
autoBtn.addEventListener('click', () => {
  const on = autoBtn.getAttribute('aria-pressed') !== 'true';
  autoBtn.setAttribute('aria-pressed', String(on));
  if (timer) { clearInterval(timer); timer = null; }
  if (on) timer = setInterval(() => load({ quiet: true }), 5000);
});

filterEl.addEventListener('input', () => applyFilter());
function applyFilter() {
  const q = filterEl.value.trim().toLowerCase();
  document.querySelectorAll('[data-row]').forEach(el => {
    el.hidden = q !== '' && !el.dataset.row.includes(q);
  });
}

document.addEventListener('keydown', e => {
  if (e.target === filterEl) { if (e.key === 'Escape') { filterEl.value = ''; applyFilter(); filterEl.blur(); } return; }
  if (e.key === '/') { e.preventDefault(); filterEl.focus(); return; }
  if (e.key === 'r') { load(); return; }
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= VIEWS.length) go(VIEWS[n - 1][0]);
});

function go(next, id) {
  view = next; runId = id || null;
  const meta = VIEWS.find(v => v[0] === next);
  nav.querySelectorAll('button').forEach(b => b.setAttribute('aria-current', String(b.dataset.view === next)));
  titleEl.textContent = next === 'run' ? 'Run' : (meta ? meta[1] : next);
  subEl.textContent = next === 'run' ? 'Everything the ledger recorded about one run.' : (meta ? meta[3] : '');
  filterEl.value = '';
  load();
}

function status(kind, text) {
  dotEl.className = 'dot' + (kind === 'ok' ? '' : ' ' + kind);
  liveEl.textContent = text;
}

/* ------------------------------------------------------------------ render */

function fmt(v) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
  return String(v);
}

function pct(v) { return v === null || v === undefined ? '—' : Math.round(v * 100) + '%'; }

function delta(v, unit) {
  if (v === null || v === undefined) return `<div class="d flat">no comparison — unavailable in one window</div>`;
  const cls = v > 0 ? 'up' : (v < 0 ? 'down' : 'flat');
  const arrow = v > 0 ? '▲' : (v < 0 ? '▼' : '▬');
  return `<div class="d ${cls}">${arrow} ${esc(fmt(Math.abs(v)))}${unit ? ' ' + esc(unit) : ''} vs previous window</div>`;
}

function card(label, value, unit, d, i) {
  return `<div class="card" style="--i:${i}"><div class="k">${esc(label)}</div>`
       + `<div class="v">${esc(value)}${unit ? `<small>${esc(unit)}</small>` : ''}</div>`
       + (d === undefined ? '' : d) + `</div>`;
}

function renderOverview(d) {
  const r = d.current.runs;
  const t = d.trend || {};
  const cards = [
    card('runs in window', fmt(r.total), '', delta(t.runs, ''), 0),
    card('work runs', fmt(r.work), '', '', 1),
    card('measurement share', pct(r.measurementShare), '', '', 2),
    card('improvement runs', fmt(r.improvement), '', '', 3),
  ].join('');

  const available = d.current.measures.filter(m => m.availability === 'available');
  const missing = d.current.measures.filter(m => m.availability !== 'available');

  const metricCards = available.map((m, i) => {
    const cls = m.estimate ? 'estimate' : '';
    const ex = m.excludes && m.excludes.length ? `<p class="why">excludes ${esc(m.excludes.join(', '))}</p>` : '';
    return `<div class="panel" style="--i:${i}" data-row="${esc(String(m.name).toLowerCase())}">`
      + `<h3>${esc(m.name)}</h3>`
      + `<div class="v mono ${cls}" style="font-size:22px;font-weight:600">${esc(fmt(m.value))} <small style="color:var(--dim);font-size:12px">${esc(m.unit || '')}</small></div>`
      + delta(t[m.name], m.unit) + ex + `</div>`;
  }).join('');

  const missingRows = missing.map(m =>
    `<tr data-row="${esc(String(m.name).toLowerCase())}"><td><code>${esc(m.name)}</code></td>`
    + `<td><span class="pill mute">${esc(String(m.availability).replace(/_/g, ' '))}</span></td>`
    + `<td class="note">${esc(m.reason)}</td></tr>`).join('');

  return `<section>`
    + `<h2 class="sec">this window &mdash; ${esc(d.days || days)} days</h2>`
    + `<div class="cards stagger">${cards}</div>`
    + `<p class="note">${esc(r.note)}</p>`
    + (available.length ? `<h2 class="sec">measured</h2><div class="grid stagger">${metricCards}</div>` : '')
    + (missing.length
        ? `<h2 class="sec">not measured, and why</h2><div class="tblwrap"><table><thead><tr>`
          + `<th>metric</th><th>state</th><th>reason</th></tr></thead><tbody>${missingRows}</tbody></table></div>`
          + `<p class="note">A metric with no data reports its absence. "No change" and "we could not look" are different things, and the second must never render as the first.</p>`
        : '')
    + `</section>`;
}

const STAGES = ['intake', 'triage', 'design', 'build', 'review', 'verify', 'handoff'];

function renderActivity(d) {
  if (!d.workItems || !d.workItems.length) {
    return empty('▦', 'No work items yet', esc(d.note || 'Run `sf work` and this board fills from the ledger.'));
  }
  const byStage = new Map(STAGES.map(s => [s, []]));
  d.workItems.forEach(w => {
    const key = String(w.stage || '').toLowerCase();
    if (!byStage.has(key)) byStage.set(key, []);
    byStage.get(key).push(w);
  });

  const cols = [...byStage.entries()].map(([stage, items]) =>
    `<div class="col"><header><h4>${esc(stage)}</h4><span class="n">${items.length}</span></header>`
    + (items.length ? items.map(w =>
        `<div class="item ${w.needsAttention ? 'flag' : ''}" data-row="${esc((w.id + ' ' + w.title + ' ' + stage).toLowerCase())}">`
        + `<div class="t">${esc(w.title)}</div>`
        + `<div class="m">${esc(w.id)} &middot; ${esc(w.workClass || '')}${w.rework ? ' &middot; rework &times;' + esc(w.rework) : ''}</div>`
        + (w.why ? `<div class="w">${esc(w.why)}</div>` : '')
        + `</div>`).join('')
      : `<div class="note" style="font-size:11.5px;color:var(--faint)">empty</div>`)
    + `</div>`).join('');

  const flagged = d.needingAttention || 0;
  return `<section>`
    + `<div class="cards stagger" style="margin-bottom:20px">`
    + card('work items', d.workItems.length, '', '', 0)
    + card('needing a person', flagged, '', flagged
        ? `<div class="d down">▲ sorted first on this board</div>`
        : `<div class="d up">▬ nothing is waiting on you</div>`, 1)
    + `</div>`
    + `<h2 class="sec">pipeline</h2><div class="pipe">${cols}</div>`
    + `<p class="note">${esc(d.note || '')}</p></section>`;
}

function statusPill(s) {
  const v = String(s || '').toLowerCase();
  const cls = /^(ok|passed|pass|succeeded|complete|completed|handoff)$/.test(v) ? 'ok'
    : /^(blocked|failed|fail|refused|violation)$/.test(v) ? 'bad'
    : /^(running|open)$/.test(v) ? 'info' : 'warn';
  return `<span class="pill ${cls}">${esc(v || 'unknown')}</span>`;
}

function renderRuns(d) {
  if (!d.runs || !d.runs.length) {
    return empty('▶', 'No runs recorded', 'A run appears here the moment `sf work` writes its first ledger entry.');
  }
  const rows = d.runs.map(r =>
    `<tr class="clickable ${r.violations || r.gatesFailed ? 'flag' : ''}" data-run="${esc(r.id)}" `
    + `data-row="${esc((r.id + ' ' + r.agent + ' ' + r.stage + ' ' + r.status + ' ' + r.workItem).toLowerCase())}">`
    + `<td><code>${esc(r.id)}</code></td>`
    + `<td>${esc(r.agent || '—')}</td>`
    + `<td><span class="pill mute">${esc(r.stage || '?')}</span></td>`
    + `<td>${statusPill(r.status)}</td>`
    + `<td class="num">${esc(r.modelCalls)}</td>`
    + `<td class="num">${esc(r.toolCalls)}</td>`
    + `<td class="num">${r.gatesFailed ? `<span class="pill bad">${esc(r.gatesFailed)}</span>` : '0'}</td>`
    + `<td class="num">${esc(r.costUnits)}</td>`
    + `</tr>`).join('');

  const spent = d.runs.reduce((a, r) => a + (r.costUnits || 0), 0);
  return `<section>`
    + `<div class="cards stagger" style="margin-bottom:20px">`
    + card('runs recorded', d.total, '', '', 0)
    + card('cost units', spent.toFixed(3), '', `<div class="d flat">estimated from declared prices</div>`, 1)
    + card('shown', d.shown, d.truncated ? 'of ' + d.total : '', '', 2)
    + `</div>`
    + `<h2 class="sec">runs</h2>`
    + `<div class="tblwrap"><table><thead><tr><th>run</th><th>agent</th><th>stage</th><th>status</th>`
    + `<th class="num">model</th><th class="num">tools</th><th class="num">gates failed</th><th class="num">cost</th>`
    + `</tr></thead><tbody>${rows}</tbody></table></div>`
    + `<p class="note">${esc(d.costNote || '')} Click a row to inspect the run.</p></section>`;
}

function evClass(type) {
  if (/violation|escalation/.test(type)) return 'bad';
  if (/gate|finished/.test(type)) return 'ok';
  if (/model|tool|pack/.test(type)) return 'info';
  return '';
}

function renderRun(d) {
  const gates = (d.gates || []).map(g => {
    const ok = /^(pass|passed|true)$/i.test(String(g.outcome));
    return `<span class="pill ${ok ? 'ok' : 'bad'}">${esc(g.gate)}</span>`;
  }).join(' ');

  const events = (d.entries || []).map(e => {
    const pre = document.createElement('pre');
    pre.textContent = JSON.stringify(e.payload, null, 2);
    return `<div class="ev ${evClass(e.type)}" data-row="${esc(String(e.type).toLowerCase())}">`
      + `<div class="h"><span class="ty">${esc(e.type)}</span><span class="at">${esc(e.at)}</span>`
      + `<span class="at">#${esc(e.seq)}</span></div>${pre.outerHTML}</div>`;
  }).join('');

  const trouble = [...(d.escalations || []), ...(d.violations || [])];
  return `<section>`
    + `<button class="back" data-back="runs">&larr; all runs</button>`
    + `<div class="cards stagger" style="margin-bottom:20px">`
    + card('run', d.run, '', '', 0)
    + card('tool calls', d.toolCalls, '', '', 1)
    + card('cost units', d.costUnits, '', `<div class="d flat">estimate, not billing</div>`, 2)
    + card('ledger entries', (d.entries || []).length, '', '', 3)
    + `</div>`
    + (gates ? `<h2 class="sec">gates</h2><div>${gates}</div>` : '')
    + (trouble.length
        ? `<h2 class="sec">escalations and violations</h2><div class="grid stagger">`
          + trouble.map((t, i) => `<div class="panel" style="--i:${i}"><h3>${esc(t.kind || t.gate || 'event')}</h3>`
            + `<p class="why">${esc(t.reason || t.message || JSON.stringify(t))}</p></div>`).join('')
          + `</div>`
        : '')
    + `<h2 class="sec">timeline</h2><div class="tl">${events}</div>`
    + `<p class="note">${esc(d.costNote || '')}</p></section>`;
}

function chips(label, values) {
  if (!values || !values.length) return `<div class="panel"><h3>${esc(label)}</h3><p class="why unavailable">none declared</p></div>`;
  return `<div class="panel" data-row="${esc((label + ' ' + values.join(' ')).toLowerCase())}"><h3>${esc(label)} <span class="pill mute">${values.length}</span></h3>`
    + `<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:5px">`
    + values.map(v => `<span class="pill info">${esc(v)}</span>`).join('') + `</div></div>`;
}

function renderDefinition(d) {
  if (d.available === false) return unavailablePanel('Definition', d.reason);
  return `<section>`
    + `<div class="cards stagger" style="margin-bottom:20px">`
    + card('factory', d.factory, '', '', 0)
    + card('agents', (d.agents || []).length, '', '', 1)
    + card('skills', (d.skills || []).length, '', '', 2)
    + card('repositories', (d.repositories || []).length, '', '', 3)
    + `</div>`
    + `<h2 class="sec">components</h2><div class="grid stagger">`
    + chips('agents', d.agents) + chips('automations', d.automations) + chips('runners', d.runners)
    + chips('scorers', d.scorers) + chips('skills', d.skills) + chips('principals', d.principals)
    + ((d.unloaded || []).length ? chips('failed to load', d.unloaded) : '')
    + `</div>`
    + `<h2 class="sec">repositories</h2><div class="grid stagger">`
    + ((d.repositories || []).length
        ? d.repositories.map((r, i) => `<div class="panel" style="--i:${i}"><h3><code>${esc(r)}</code></h3></div>`).join('')
        : `<div class="panel"><p class="why unavailable">none declared</p></div>`)
    + `</div><p class="note">${esc(d.note || '')}</p></section>`;
}

function renderEvaluation(d) {
  const names = Object.keys(d.scorers || {});
  const scorerPanels = names.map((name, i) => {
    const s = d.scorers[name];
    const outcomes = Object.entries(s.outcomes || {});
    const passed = outcomes.filter(([k]) => /pass|ok/i.test(k)).reduce((a, [, v]) => a + v, 0);
    const rate = s.sampled ? passed / s.sampled : null;
    return `<div class="panel" style="--i:${i}" data-row="${esc(name.toLowerCase())}"><h3>${esc(name)}</h3>`
      + `<div style="font:600 20px/1.2 var(--mono);margin-top:4px">${rate === null ? '—' : pct(rate)}`
      + `<small style="font-size:12px;color:var(--dim);font-weight:500"> of ${esc(s.sampled)} sampled</small></div>`
      + `<div class="meter"><i style="width:${rate === null ? 0 : Math.round(rate * 100)}%"></i></div>`
      + `<div style="margin-top:9px;display:flex;flex-wrap:wrap;gap:5px">`
      + outcomes.map(([k, v]) => `<span class="pill ${/pass|ok/i.test(k) ? 'ok' : 'bad'}">${esc(k)} ${esc(v)}</span>`).join('')
      + `</div></div>`;
  }).join('');

  const props = (d.proposals || []).map(p =>
    `<tr data-row="${esc((p.id + ' ' + p.target + ' ' + p.status).toLowerCase())}">`
    + `<td><code>${esc(p.id)}</code></td><td>${esc(p.target)}</td><td>${statusPill(p.status)}</td>`
    + `<td class="note">${esc((p.evidence || []).join('; ') || 'no evidence recorded')}</td></tr>`).join('');

  if (!names.length && !(d.proposals || []).length) {
    return empty('◎', 'Nothing evaluated yet', 'Scorers record here once runs are sampled, and proposals once the improvement loop opens one.');
  }
  return `<section>`
    + (names.length ? `<h2 class="sec">scorers</h2><div class="grid stagger">${scorerPanels}</div>` : '')
    + `<h2 class="sec">improvement proposals</h2>`
    + (props
        ? `<div class="tblwrap"><table><thead><tr><th>proposal</th><th>target</th><th>status</th><th>evidence</th>`
          + `</tr></thead><tbody>${props}</tbody></table></div>`
        : `<p class="note unavailable">No proposal has been opened. The loop proposes; it never applies.</p>`)
    + `</section>`;
}

function renderRegistry(d) {
  const m = d.memory || {};
  const memCards = m.available === false
    ? `<div class="panel"><h3>memory</h3><p class="why unavailable">${esc(m.reason)}</p></div>`
    : `<div class="cards stagger">`
      + card('memories', fmt(m.total), '', '', 0)
      + card('quarantined', fmt(m.quarantined), '', m.quarantined
          ? `<div class="d down">▲ held out of retrieval</div>` : `<div class="d up">▬ clean</div>`, 1)
      + card('size', fmt(Math.round((m.bytes || 0) / 1024)), 'KiB', '', 2)
      + `</div>`;

  const lanes = Object.entries(m).filter(([k]) =>
    !['available', 'reason', 'total', 'quarantined', 'bytes'].includes(k));
  const laneRows = lanes.map(([lane, n]) =>
    `<tr data-row="${esc(lane.toLowerCase())}"><td><code>${esc(lane)}</code></td><td class="num">${esc(n)}</td>`
    + `<td style="width:50%"><div class="meter"><i style="width:${m.total ? Math.round((n / m.total) * 100) : 0}%"></i></div></td></tr>`).join('');

  const skills = (d.skills || []).map(s =>
    `<tr data-row="${esc((s.name + ' ' + s.status).toLowerCase())}"><td><code>${esc(s.name)}</code></td>`
    + `<td>${statusPill(s.status)}</td>`
    + `<td class="num">${esc(pct(s.precision))}</td><td class="num">${esc(pct(s.recall))}</td>`
    + `<td class="num">${esc(s.offered)}</td><td class="num">${esc(s.helped)}</td></tr>`).join('');

  return `<section>`
    + `<h2 class="sec">memory fabric</h2>${memCards}`
    + (laneRows ? `<div class="tblwrap" style="margin-top:12px"><table><thead><tr><th>lane</th>`
        + `<th class="num">memories</th><th>share</th></tr></thead><tbody>${laneRows}</tbody></table></div>` : '')
    + `<h2 class="sec">skills</h2>`
    + (skills
        ? `<div class="tblwrap"><table><thead><tr><th>skill</th><th>status</th><th class="num">precision</th>`
          + `<th class="num">recall</th><th class="num">offered</th><th class="num">helped</th>`
          + `</tr></thead><tbody>${skills}</tbody></table></div>`
          + `<p class="note">Precision is of the times it was loaded, how often it helped. `
          + `Recall counts retrospectively-detected misses and is an estimate with a stated derivation, not a measurement.</p>`
        : `<p class="note unavailable">No skills are declared in this factory.</p>`)
    + `</section>`;
}

function unavailablePanel(what, reason) {
  return `<section><div class="panel"><h3>${esc(what)} is not available</h3>`
    + `<p class="why">${esc(reason)}</p>`
    + `<p class="why">The factory is fine and the page is fine — this one panel has nothing behind it, `
    + `and an operator needs to know which and why.</p></div></section>`;
}

function empty(glyph, head, body) {
  return `<section><div class="empty"><div class="big">${glyph}</div><h3>${esc(head)}</h3>`
    + `<p class="note">${esc(body)}</p></div></section>`;
}

function renderError(d) {
  return `<section><div class="panel"><h3>${esc(d.error)}</h3><p class="why">${esc(d.message)}</p></div></section>`;
}

/* -------------------------------------------------------------------- load */

const RENDER = {
  overview: renderOverview, activity: renderActivity, runs: renderRuns,
  run: renderRun, definition: renderDefinition, evaluation: renderEvaluation,
  registry: renderRegistry,
};

async function load(opts) {
  const quiet = opts && opts.quiet;
  if (!quiet) { status('ok', 'reading ledger'); content.setAttribute('aria-busy', 'true'); }
  const q = new URLSearchParams({ days: String(days) });
  if (view === 'run' && runId) q.set('run', runId);
  let data;
  try {
    const res = await fetch(`/api/${view}?${q}`);
    data = await res.json();
  } catch (err) {
    status('down', 'unreachable');
    content.innerHTML = `<section><div class="panel"><h3>Could not reach the factory</h3>`
      + `<p class="why">${esc(err)}</p></div></section>`;
    return;
  }
  content.removeAttribute('aria-busy');
  lastData = data;
  if (data && data.error) { content.innerHTML = renderError(data); status('stale', esc(data.error)); return; }
  const render = RENDER[view];
  content.innerHTML = render ? render(data) : renderError({ error: 'view.unknown', message: view });
  status('ok', new Date().toLocaleTimeString());
  applyFilter();
}

content.addEventListener('click', e => {
  const row = e.target.closest('tr[data-run]');
  if (row) { go('run', row.dataset.run); return; }
  const back = e.target.closest('[data-back]');
  if (back) go(back.dataset.back);
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
