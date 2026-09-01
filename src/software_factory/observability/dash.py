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
  /* ============================================================ tokens */
  :root {
    color-scheme: dark light;
    --bg: #06070b;
    --ink: #f2f4fb;
    --dim: #9aa1bb;
    --faint: #656c86;
    --hair: rgba(255,255,255,.075);
    --hair-2: rgba(255,255,255,.14);
    --glass: rgba(255,255,255,.032);
    --glass-2: rgba(255,255,255,.055);
    --well: rgba(0,0,0,.28);
    --c1: #22d3ee;   /* cyan   */
    --c2: #818cf8;   /* indigo */
    --c3: #e879f9;   /* fuchsia*/
    --good: #34d399;
    --warn: #fbbf24;
    --bad: #fb7185;
    --r: 18px;
    --r-sm: 11px;
    --mono: ui-monospace, "SF Mono", "Cascadia Mono", "JetBrains Mono", Menlo, monospace;
    --ease: cubic-bezier(.16,1,.3,1);
    --spring: linear(0,.286 4.2%,.858 9.6%,1.05 13%,1.11 16.7%,1.06 22%,.99 28.4%,.98 36%,1.01 50%,1 71%,1);
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f4f5fa; --ink: #0b0d15; --dim: #565d78; --faint: #858ca6;
      --hair: rgba(11,13,21,.09); --hair-2: rgba(11,13,21,.16);
      --glass: rgba(255,255,255,.72); --glass-2: rgba(255,255,255,.9);
      --well: rgba(11,13,21,.035);
      --c1: #0891b2; --c2: #4f46e5; --c3: #c026d3;
      --good: #059669; --warn: #b45309; --bad: #e11d48;
    }
  }
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    margin: 0; min-height: 100vh; background: var(--bg); color: var(--ink);
    font: 400 14px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Inter, sans-serif;
    font-feature-settings: "cv02","cv03","cv04","ss01";
    -webkit-font-smoothing: antialiased; letter-spacing: -.008em;
  }
  /* mesh + grain. The grain is an inline SVG turbulence, not an image: nothing here
     loads from anywhere, and a "local-first" dashboard that fetches a texture is making
     the claim it exists to disprove. */
  body::before, body::after { content: ""; position: fixed; inset: 0; pointer-events: none; }
  body::before {
    z-index: 0;
    background:
      radial-gradient(1100px 620px at 8% -12%, color-mix(in oklab, var(--c1) 20%, transparent), transparent 62%),
      radial-gradient(900px 540px at 96% -4%, color-mix(in oklab, var(--c3) 16%, transparent), transparent 60%),
      radial-gradient(1000px 700px at 52% 108%, color-mix(in oklab, var(--c2) 15%, transparent), transparent 64%);
    filter: saturate(1.15);
  }
  body::after {
    z-index: 1; opacity: .5; mix-blend-mode: overlay;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)' opacity='.35'/%3E%3C/svg%3E");
  }
  #shell { position: relative; z-index: 2; }

  /* ============================================================ top bar */
  header.top {
    position: sticky; top: 0; z-index: 40;
    display: flex; align-items: center; gap: 14px;
    padding: 12px 22px;
    background: color-mix(in oklab, var(--bg) 72%, transparent);
    backdrop-filter: blur(22px) saturate(1.6);
    border-bottom: 1px solid var(--hair);
  }
  .logo { display: flex; align-items: center; gap: 10px; flex: none; }
  .glyph {
    width: 28px; height: 28px; border-radius: 9px; position: relative; flex: none;
    background: conic-gradient(from 200deg, var(--c1), var(--c2), var(--c3), var(--c1));
    box-shadow: 0 0 24px -6px color-mix(in oklab, var(--c2) 80%, transparent);
    animation: spin 14s linear infinite;
  }
  .glyph::after { content: ""; position: absolute; inset: 6px; border-radius: 4px; background: var(--bg); }
  @keyframes spin { to { transform: rotate(360deg); } }
  .wordmark { font-weight: 620; font-size: 14.5px; letter-spacing: -.02em; white-space: nowrap; }
  .wordmark span { color: var(--dim); font-weight: 450; }

  nav.tabs { display: flex; gap: 2px; margin-left: 8px; overflow-x: auto; scrollbar-width: none; }
  nav.tabs::-webkit-scrollbar { display: none; }
  nav.tabs button {
    position: relative; font: inherit; font-size: 13px; color: var(--dim);
    background: none; border: 0; cursor: pointer; padding: 7px 13px; border-radius: 10px;
    white-space: nowrap; transition: color .18s var(--ease), background .18s var(--ease);
  }
  nav.tabs button:hover { color: var(--ink); background: var(--glass); }
  nav.tabs button[aria-current="true"] { color: var(--ink); font-weight: 560; background: var(--glass-2); }
  nav.tabs button[aria-current="true"]::after {
    content: ""; position: absolute; left: 13px; right: 13px; bottom: -13px; height: 2px;
    border-radius: 2px; background: linear-gradient(90deg, var(--c1), var(--c3));
  }

  .spacer { flex: 1 1 auto; }
  .kbtn {
    display: flex; align-items: center; gap: 8px; font: inherit; font-size: 12.5px;
    color: var(--dim); cursor: pointer; padding: 7px 10px 7px 12px;
    background: var(--glass); border: 1px solid var(--hair); border-radius: 10px;
    transition: border-color .18s, color .18s, background .18s;
  }
  .kbtn:hover { color: var(--ink); border-color: var(--hair-2); background: var(--glass-2); }
  kbd {
    font: 11px/1 var(--mono); color: var(--faint); background: var(--well);
    border: 1px solid var(--hair); border-radius: 5px; padding: 3px 5px;
  }
  .seg { display: flex; background: var(--glass); border: 1px solid var(--hair); border-radius: 10px; padding: 2px; }
  .seg button {
    font: 12px/1 var(--mono); color: var(--faint); background: none; border: 0; cursor: pointer;
    padding: 6px 9px; border-radius: 8px; transition: color .16s, background .16s;
  }
  .seg button:hover { color: var(--ink); }
  .seg button[aria-pressed="true"] { background: var(--glass-2); color: var(--ink); }
  .live { display: flex; align-items: center; gap: 7px; font: 11px/1 var(--mono); color: var(--faint); }
  .beacon { width: 7px; height: 7px; border-radius: 50%; background: var(--good); flex: none; animation: beat 2.6s var(--ease) infinite; }
  .beacon.stale { background: var(--warn); animation: none; }
  .beacon.down { background: var(--bad); animation: none; }
  @keyframes beat {
    0%,100% { box-shadow: 0 0 0 0 color-mix(in oklab, var(--good) 55%, transparent); }
    60% { box-shadow: 0 0 0 8px transparent; }
  }

  /* ============================================================ page */
  main { padding: 26px 22px 80px; max-width: 1680px; margin: 0 auto; }
  .head { margin: 0 2px 20px; }
  .head h1 { margin: 0; font-size: clamp(24px, 3.2vw, 34px); font-weight: 640; letter-spacing: -.035em; }
  .head p { margin: 5px 0 0; color: var(--dim); font-size: 13.5px; max-width: 74ch; }

  /* ============================================================ bento */
  .bento { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 12px; }
  .tile {
    position: relative; overflow: hidden; grid-column: span 3;
    background: var(--glass); border: 1px solid var(--hair); border-radius: var(--r);
    padding: 16px 18px; backdrop-filter: blur(18px) saturate(1.3);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.05), 0 18px 40px -28px rgba(0,0,0,.85);
    transition: border-color .24s var(--ease), transform .24s var(--ease);
    animation: lift .5s var(--ease) both; animation-delay: calc(var(--i,0) * 45ms);
  }
  .tile:hover { border-color: var(--hair-2); transform: translateY(-2px); }
  .tile.w4 { grid-column: span 4; } .tile.w6 { grid-column: span 6; }
  .tile.w8 { grid-column: span 8; } .tile.w12 { grid-column: span 12; }
  .tile { display: flex; flex-direction: column; }
  .tile > .chart { margin-top: auto; }
  /* A column flex container stretches its children, which turned every inline pill into a
     full-width bar. The pills size to their text; only the full-width things stretch. */
  .tile > .delta, .tile > .gauge, .tile > .chip { align-self: flex-start; }
  .tile.flush { padding: 0; }
  @keyframes lift { from { opacity: 0; transform: translateY(12px) scale(.985); } }
  @media (max-width: 1180px) { .tile { grid-column: span 6; } .tile.w8, .tile.w12 { grid-column: span 12; } }
  @media (max-width: 720px)  { .tile, .tile.w4, .tile.w6, .tile.w8 { grid-column: span 12; } }

  .label { font: 500 10.5px/1 var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--faint); }
  .figure { font: 620 clamp(30px, 4.4vw, 46px)/1 ui-sans-serif, system-ui, sans-serif;
            letter-spacing: -.045em; margin-top: 10px; font-variant-numeric: tabular-nums; }
  .figure.sm { font-size: clamp(22px, 2.6vw, 28px); }
  .figure .u { font-size: 13px; font-weight: 460; color: var(--dim); letter-spacing: -.01em; margin-left: 6px; }
  .figure.id { font: 600 15px/1.35 var(--mono); letter-spacing: -.01em; overflow-wrap: anywhere; }
  .delta { display: inline-flex; align-items: center; gap: 5px; margin-top: 10px;
           font: 11.5px/1 var(--mono); padding: 4px 8px; border-radius: 999px; }
  .delta.up { color: var(--good); background: color-mix(in oklab, var(--good) 13%, transparent); }
  .delta.down { color: var(--bad); background: color-mix(in oklab, var(--bad) 13%, transparent); }
  .delta.flat { color: var(--faint); background: var(--well); }
  .sub { margin: 9px 0 0; color: var(--dim); font-size: 12.5px; }
  .tile h3 { margin: 0 0 2px; font-size: 14px; font-weight: 580; letter-spacing: -.015em; }

  /* chart */
  .chart { width: 100%; height: 132px; display: block; overflow: visible; }
  .chart .grid { stroke: var(--hair); stroke-width: 1; }
  .axis { display: flex; justify-content: space-between; margin-top: 8px;
          font: 10.5px/1 var(--mono); color: var(--faint); }
  .spark { width: 100%; height: 34px; display: block; margin-top: 10px; }

  /* gauge */
  .gauge { display: flex; align-items: center; gap: 16px; margin-top: 12px; }
  .gauge svg { flex: none; }
  .gauge .num { font: 620 27px/1 ui-sans-serif, system-ui, sans-serif; letter-spacing: -.04em;
                font-variant-numeric: tabular-nums; }
  .gauge .cap { color: var(--dim); font-size: 12px; margin-top: 3px; }

  /* pipeline */
  .flow { display: flex; align-items: flex-start; gap: 8px; overflow-x: auto; padding: 2px;
          scrollbar-width: thin; }
  /* Each lane needs a visible edge. Without one the count sits at the far right of an
     invisible column and reads as part of the next stage's title -- "0 TRIAGE". */
  .lane { flex: 1 1 0; min-width: 140px; background: var(--well); border: 1px solid var(--hair);
          border-radius: 14px; padding: 11px 10px; align-self: stretch; }
  .lane header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
  .lane h4 { margin: 0; font: 600 10.5px/1 var(--mono); letter-spacing: .1em; text-transform: uppercase; color: var(--dim); }
  .lane .n { font: 11px/1 var(--mono); color: var(--faint); background: var(--glass-2);
             border-radius: 999px; padding: 3px 7px; }
  .lane .rail { height: 3px; border-radius: 3px; background: var(--hair); overflow: hidden; margin-bottom: 10px; }
  .lane .rail i { display: block; height: 100%; background: linear-gradient(90deg, var(--c1), var(--c2)); }
  .lane.vacant { opacity: .42; }
  .wi {
    background: var(--glass-2); border: 1px solid var(--hair); border-radius: var(--r-sm);
    padding: 10px 11px; margin-bottom: 7px; transition: transform .2s var(--ease), border-color .2s;
  }
  .wi:hover { transform: translateY(-1px); border-color: var(--hair-2); }
  .wi.flag { border-color: color-mix(in oklab, var(--warn) 42%, var(--hair));
             background: color-mix(in oklab, var(--warn) 7%, var(--glass-2)); }
  .wi .t { font-size: 12.5px; font-weight: 540; line-height: 1.4; }
  .wi .m { font: 10.5px/1.5 var(--mono); color: var(--faint); margin-top: 5px; overflow-wrap: anywhere; }
  .wi .w { font-size: 11.5px; color: var(--warn); margin-top: 6px; }
  .empty-lane { font: 10.5px/1 var(--mono); color: var(--faint); padding: 8px 2px; }

  /* table */
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  thead th {
    position: sticky; top: 0; z-index: 1; text-align: left;
    background: color-mix(in oklab, var(--bg) 88%, transparent); backdrop-filter: blur(8px);
    padding: 11px 16px; font: 500 10.5px/1 var(--mono); letter-spacing: .11em;
    text-transform: uppercase; color: var(--faint); border-bottom: 1px solid var(--hair);
  }
  tbody td { padding: 12px 16px; border-bottom: 1px solid var(--hair); vertical-align: top; }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr { transition: background .14s; }
  tbody tr:hover { background: var(--glass); }
  tbody tr.clickable { cursor: pointer; }
  td.num, th.num { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
  .scroll { overflow: auto; max-height: 68vh; }
  code, .mono { font-family: var(--mono); font-size: 12.5px; }

  .chip {
    display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 999px;
    font: 11px/1.6 var(--mono); border: 1px solid transparent; white-space: nowrap;
  }
  .chip::before { content: ""; width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
  .chip.ok   { color: var(--good); background: color-mix(in oklab, var(--good) 12%, transparent); border-color: color-mix(in oklab, var(--good) 26%, transparent); }
  .chip.warn { color: var(--warn); background: color-mix(in oklab, var(--warn) 12%, transparent); border-color: color-mix(in oklab, var(--warn) 26%, transparent); }
  .chip.bad  { color: var(--bad);  background: color-mix(in oklab, var(--bad) 12%, transparent);  border-color: color-mix(in oklab, var(--bad) 26%, transparent); }
  .chip.info { color: var(--c1);   background: color-mix(in oklab, var(--c1) 12%, transparent);   border-color: color-mix(in oklab, var(--c1) 26%, transparent); }
  .chip.mute { color: var(--faint); background: var(--well); border-color: var(--hair); }
  .chip.plain::before { display: none; }

  .meter { height: 5px; border-radius: 999px; background: var(--well); overflow: hidden; margin-top: 8px; }
  .meter i { display: block; height: 100%; border-radius: 999px;
             background: linear-gradient(90deg, var(--c1), var(--c2), var(--c3)); }

  /* trace */
  .trace { position: relative; padding-left: 26px; }
  .trace::before { content: ""; position: absolute; left: 6px; top: 8px; bottom: 8px; width: 1px;
                   background: linear-gradient(180deg, transparent, var(--hair-2) 12%, var(--hair-2) 88%, transparent); }
  .ev { position: relative; padding: 10px 0; }
  .ev::before { content: ""; position: absolute; left: -24px; top: 17px; width: 9px; height: 9px;
                border-radius: 50%; background: var(--bg); border: 2px solid var(--faint); }
  .ev.ok::before   { border-color: var(--good); box-shadow: 0 0 12px -2px var(--good); }
  .ev.bad::before  { border-color: var(--bad);  box-shadow: 0 0 12px -2px var(--bad); }
  .ev.info::before { border-color: var(--c1);   box-shadow: 0 0 12px -2px var(--c1); }
  .ev .h { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .ev .ty { font: 600 12.5px/1.5 var(--mono); letter-spacing: -.01em; }
  .ev .at { font: 10.5px/1.5 var(--mono); color: var(--faint); }
  .ev .sum { font-size: 12.5px; color: var(--dim); margin-top: 3px; }
  .ev .sum b { color: var(--ink); font-weight: 560; }
  .ev details { margin-top: 7px; }
  .ev summary { cursor: pointer; font: 10.5px/1.6 var(--mono); color: var(--faint); width: max-content; }
  .ev summary:hover { color: var(--c1); }
  .ev pre { margin: 7px 0 0; padding: 11px 13px; background: var(--well); border: 1px solid var(--hair);
            border-radius: 10px; overflow-x: auto; font: 11.5px/1.6 var(--mono); color: var(--dim); max-height: 320px; }

  /* command palette */
  .veil { position: fixed; inset: 0; z-index: 90; background: rgba(4,5,9,.62);
          backdrop-filter: blur(8px); display: grid; place-items: start center; padding-top: 12vh;
          animation: fade .18s var(--ease) both; }
  @keyframes fade { from { opacity: 0; } }
  .palette {
    width: min(680px, 92vw); background: color-mix(in oklab, var(--bg) 84%, transparent);
    border: 1px solid var(--hair-2); border-radius: 16px; overflow: hidden;
    backdrop-filter: blur(28px) saturate(1.5);
    box-shadow: 0 40px 90px -30px rgba(0,0,0,.9), inset 0 1px 0 rgba(255,255,255,.07);
    animation: pop .26s var(--spring) both;
  }
  @keyframes pop { from { opacity: 0; transform: translateY(-10px) scale(.97); } }
  .palette input {
    width: 100%; font: inherit; font-size: 16px; color: var(--ink); background: none;
    border: 0; border-bottom: 1px solid var(--hair); padding: 17px 20px; outline: none;
  }
  .palette input::placeholder { color: var(--faint); }
  .palette ul { list-style: none; margin: 0; padding: 6px; max-height: 46vh; overflow-y: auto; }
  .palette li { display: flex; align-items: center; gap: 11px; padding: 10px 13px; border-radius: 10px;
                cursor: pointer; font-size: 13.5px; }
  .palette li[aria-selected="true"] { background: var(--glass-2); }
  .palette li .g { width: 17px; text-align: center; color: var(--dim); }
  .palette li .hint { margin-left: auto; font: 10.5px/1 var(--mono); color: var(--faint); }
  .palette .none { padding: 26px; text-align: center; color: var(--faint); font-size: 13px; }

  /* misc */
  .note { color: var(--dim); font-size: 12.5px; margin: 10px 2px 0; }
  .muted { color: var(--faint); font-style: italic; }
  .void { text-align: center; padding: 72px 20px; }
  .void .g { font-size: 34px; opacity: .28; }
  .void h3 { margin: 14px 0 5px; font-size: 16px; font-weight: 580; }
  .skel { grid-column: span 3; height: 132px; border-radius: var(--r); border: 1px solid var(--hair);
          background: linear-gradient(100deg, var(--glass) 30%, var(--glass-2) 50%, var(--glass) 70%);
          background-size: 220% 100%; animation: shim 1.2s linear infinite; }
  @keyframes shim { to { background-position: -220% 0; } }
  .back { font: inherit; font-size: 12.5px; background: none; border: 0; color: var(--c1);
          cursor: pointer; padding: 0 0 12px; }
  .back:hover { text-decoration: underline; }
  h2.rule { grid-column: 1 / -1; margin: 16px 2px 0; display: flex; align-items: center; gap: 12px;
            font: 500 10.5px/1 var(--mono); letter-spacing: .13em; text-transform: uppercase; color: var(--faint); }
  h2.rule::after { content: ""; flex: 1; height: 1px; background: var(--hair); }
  @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: .001ms !important; transition-duration: .001ms !important; } }
</style>
</head>
<body>
<div id="shell">
  <header class="top">
    <div class="logo"><span class="glyph"></span>
      <span class="wordmark">software<span>&#8202;factory</span></span></div>
    <nav class="tabs" id="tabs"></nav>
    <span class="spacer"></span>
    <button class="kbtn" id="palette-btn"><span>Search</span><kbd>&#8984;K</kbd></button>
    <div class="seg" id="window"></div>
    <button class="kbtn" id="auto" aria-pressed="false" title="refresh every 5s">auto</button>
    <div class="live"><span class="beacon" id="beacon"></span><span id="stamp">reading</span></div>
  </header>
  <main>
    <div class="head">
      <h1 id="title">Overview</h1>
      <p id="subtitle">Metrics, their trend, and when the work actually happened.</p>
    </div>
    <div id="content"></div>
  </main>
</div>
<script>
const content = document.getElementById('content');
const titleEl = document.getElementById('title');
const subEl = document.getElementById('subtitle');
const beacon = document.getElementById('beacon');
const stamp = document.getElementById('stamp');

// Everything this page renders came from the ledger, and the ledger is full of text from
// outside the trust boundary: model output, work-item titles written by whoever opened the
// issue, command stderr. Nothing reaches innerHTML without passing through here.
function esc(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
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

/* ------------------------------------------------------------------- chrome */

const tabs = document.getElementById('tabs');
VIEWS.forEach(([id, label]) => {
  const b = document.createElement('button');
  b.dataset.view = id;
  b.textContent = label;
  b.addEventListener('click', () => go(id));
  tabs.appendChild(b);
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

const autoBtn = document.getElementById('auto');
autoBtn.addEventListener('click', () => {
  const on = autoBtn.getAttribute('aria-pressed') !== 'true';
  autoBtn.setAttribute('aria-pressed', String(on));
  if (timer) { clearInterval(timer); timer = null; }
  if (on) timer = setInterval(() => load({ quiet: true }), 5000);
});

function go(next, id) {
  view = next; runId = id || null;
  const meta = VIEWS.find(v => v[0] === next);
  tabs.querySelectorAll('button').forEach(b =>
    b.setAttribute('aria-current', String(b.dataset.view === next)));
  titleEl.textContent = next === 'run' ? 'Run' : (meta ? meta[1] : next);
  subEl.textContent = next === 'run'
    ? 'Everything the ledger recorded about one run, in the order it happened.'
    : (meta ? meta[2] : '');
  load();
}

function status(kind, text) {
  beacon.className = 'beacon' + (kind === 'ok' ? '' : ' ' + kind);
  stamp.textContent = text;
}

/* ------------------------------------------------------- command palette */

const paletteBtn = document.getElementById('palette-btn');
let veil = null, choices = [], cursor = 0;

function commands() {
  const list = VIEWS.map(([id, label, desc]) => ({
    glyph: '◇', label, hint: 'view', run: () => go(id), text: label + ' ' + desc,
  }));
  [1, 7, 30, 90, 365].forEach(d => list.push({
    glyph: '◷', label: `Window: last ${d} day${d > 1 ? 's' : ''}`, hint: d + 'd',
    run: () => { days = d; syncWindow(); load(); }, text: 'window days ' + d,
  }));
  // Whatever is on screen becomes addressable. A palette that only knows the six views is
  // a menu; one that knows the runs and work items in front of you is a way to move.
  if (lastData && Array.isArray(lastData.runs)) {
    lastData.runs.forEach(r => list.push({
      glyph: '▸', label: r.id, hint: [r.stage || '', r.status || ''].join(' ').trim(),
      run: () => go('run', r.id), text: [r.id, r.agent, r.stage, r.status].join(' '),
    }));
  }
  if (lastData && Array.isArray(lastData.workItems)) {
    lastData.workItems.forEach(w => list.push({
      glyph: '▤', label: w.title, hint: w.stage,
      run: () => {}, text: [w.id, w.title, w.stage].join(' '),
    }));
  }
  return list;
}

function openPalette() {
  if (veil) return;
  const all = commands();
  veil = document.createElement('div');
  veil.className = 'veil';
  veil.innerHTML = `<div class="palette" role="dialog" aria-label="Command palette">
    <input type="text" placeholder="Jump to a view, a run, a window…" autocomplete="off" spellcheck="false">
    <ul role="listbox"></ul></div>`;
  document.body.appendChild(veil);
  const input = veil.querySelector('input');
  const list = veil.querySelector('ul');

  const paint = () => {
    const q = input.value.trim().toLowerCase();
    choices = q ? all.filter(c => c.text.toLowerCase().includes(q)) : all;
    cursor = 0;
    list.innerHTML = choices.length
      ? choices.map((c, i) =>
          `<li role="option" data-i="${i}" aria-selected="${i === 0}">`
          + `<span class="g">${c.glyph}</span><span>${esc(c.label)}</span>`
          + `<span class="hint">${esc(c.hint)}</span></li>`).join('')
      : `<div class="none">Nothing matches that.</div>`;
  };
  const move = step => {
    if (!choices.length) return;
    cursor = (cursor + step + choices.length) % choices.length;
    list.querySelectorAll('li').forEach((li, i) => li.setAttribute('aria-selected', String(i === cursor)));
    const active = list.querySelector('li[aria-selected="true"]');
    if (active) active.scrollIntoView({ block: 'nearest' });
  };
  const pick = () => { const c = choices[cursor]; closePalette(); if (c) c.run(); };

  input.addEventListener('input', paint);
  veil.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closePalette(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
    if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
    if (e.key === 'Enter') { e.preventDefault(); pick(); }
  });
  list.addEventListener('click', e => {
    const li = e.target.closest('li');
    if (li) { cursor = Number(li.dataset.i); pick(); }
  });
  veil.addEventListener('click', e => { if (e.target === veil) closePalette(); });
  paint();
  input.focus();
}
function closePalette() { if (veil) { veil.remove(); veil = null; } }

paletteBtn.addEventListener('click', openPalette);
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); openPalette(); return; }
  if (veil) return;
  if (e.key === '/') { e.preventDefault(); openPalette(); return; }
  if (e.key === 'r') { load(); return; }
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= VIEWS.length) go(VIEWS[n - 1][0]);
});

/* -------------------------------------------------------------- drawing */

function fmt(v) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'number') {
    return Number.isInteger(v) ? String(v)
      : String(Number(v.toFixed(3))).replace(/\.?0+$/, m => m.includes('.') ? '' : m);
  }
  return String(v);
}
function pct(v) { return v === null || v === undefined ? '—' : Math.round(v * 100) + '%'; }

function delta(v, unit) {
  if (v === null || v === undefined) return `<div class="delta flat">no comparison available</div>`;
  const cls = v > 0 ? 'up' : (v < 0 ? 'down' : 'flat');
  const arrow = v > 0 ? '↑' : (v < 0 ? '↓' : '→');
  return `<div class="delta ${cls}">${arrow} ${esc(fmt(Math.abs(v)))}${unit ? ' ' + esc(unit) : ''}</div>`;
}

function tile(inner, opts) {
  const o = opts || {};
  return `<div class="tile ${o.cls || ''}" style="--i:${o.i || 0}">${inner}</div>`;
}

function stat(label, value, unit, d, i, cls) {
  const long = String(value === null || value === undefined ? '' : value).length > 16;
  return tile(
    `<div class="label">${esc(label)}</div>`
    + `<div class="figure${long ? ' id' : ''}">${esc(value)}`
    + `${unit ? `<span class="u">${esc(unit)}</span>` : ''}</div>`
    + (d || ''), { i, cls });
}

/** An area chart from one series. Drawn as SVG path data rather than by a library: this
 *  page loads nothing from anywhere, and a chart is arithmetic. */
function area(values, opts) {
  const o = opts || {};
  const w = 1000, h = 132, pad = 4;
  const n = values.length;
  if (!n) return '<div class="muted">no activity in this window</div>';
  const top = Math.max(1, ...values);
  const x = i => n === 1 ? w / 2 : (i / (n - 1)) * w;
  const y = v => pad + (1 - v / top) * (h - pad * 2);
  const line = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  const id = 'g' + (o.key || 'a');
  const rules = [0.25, 0.5, 0.75].map(f =>
    `<line class="grid" x1="0" x2="${w}" y1="${(pad + f * (h - pad * 2)).toFixed(1)}" y2="${(pad + f * (h - pad * 2)).toFixed(1)}"/>`).join('');
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img"
     aria-label="${esc(o.aria || 'activity over the window')}">
    <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="var(--c1)" stop-opacity=".42"/>
      <stop offset="100%" stop-color="var(--c2)" stop-opacity="0"/>
    </linearGradient></defs>
    ${rules}
    <path d="${line}L${w},${h}L0,${h}Z" fill="url(#${id})"/>
    <path d="${line}" fill="none" stroke="var(--c1)" stroke-width="2"
          stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
  </svg>`;
}

function sparkline(values, colour) {
  const w = 300, h = 34;
  const n = values.length;
  if (!n) return '';
  const top = Math.max(1, ...values);
  const x = i => n === 1 ? w / 2 : (i / (n - 1)) * w;
  const y = v => 2 + (1 - v / top) * (h - 4);
  const d = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <path d="${d}" fill="none" stroke="${colour}" stroke-width="1.6"
          stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
}

function gauge(share, caption, key) {
  const size = 78, r = 32, c = 2 * Math.PI * r;
  const filled = share === null || share === undefined ? 0 : Math.max(0, Math.min(1, share));
  const id = 'gg' + key;
  return `<div class="gauge">
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
      <defs><linearGradient id="${id}" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stop-color="var(--c1)"/><stop offset="100%" stop-color="var(--c3)"/>
      </linearGradient></defs>
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--well)" stroke-width="7"/>
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="url(#${id})" stroke-width="7"
        stroke-linecap="round" stroke-dasharray="${(c * filled).toFixed(1)} ${c.toFixed(1)}"
        transform="rotate(-90 ${size / 2} ${size / 2})"/>
    </svg>
    <div><div class="num">${share === null || share === undefined ? '—' : pct(share)}</div>
    <div class="cap">${esc(caption)}</div></div></div>`;
}

function bucketLabel(seconds) {
  if (seconds >= 604800) return `${Math.round(seconds / 604800)}-week buckets`;
  if (seconds >= 86400) return `${Math.round(seconds / 86400)}-day buckets`;
  return `${Math.max(1, Math.round(seconds / 3600))}-hour buckets`;
}

/* ------------------------------------------------------------- renderers */

function renderOverview(d) {
  const r = d.current.runs;
  const t = d.trend || {};
  const s = d.series || { runs: [], handoffs: [], gateFailures: [], costUnits: [], bucketSeconds: 3600 };
  const measures = d.current.measures || [];
  const available = measures.filter(m => m.availability === 'available');
  const missing = measures.filter(m => m.availability !== 'available');
  const named = n => available.find(m => m.name === n);

  const spent = (s.costUnits || []).reduce((a, b) => a + b, 0);

  const hero = tile(
    `<div class="label">activity — last ${esc(d.days || days)} days</div>`
    + `<div class="figure">${esc(r.total)}<span class="u">runs</span></div>`
    + delta(t.runs, 'vs previous')
    + area(s.runs, { key: 'runs', aria: 'runs per bucket over the window' })
    + `<div class="axis"><span>${esc(String(s.start || '').slice(0, 10))}</span>`
    + `<span>${esc(bucketLabel(s.bucketSeconds || 3600))}</span>`
    + `<span>${esc(String(s.end || '').slice(0, 10))}</span></div>`,
    { cls: 'w6', i: 0 });

  const gates = named('gate_pass_rate');
  const gateTile = tile(
    `<h3>Gate pass rate</h3><div class="label">first attempts only</div>`
    + gauge(gates ? gates.value : null, gates ? `${gates.sample} evaluated` : 'not measured', 'gate')
    + sparkline(s.gateFailures, 'var(--bad)')
    + `<p class="sub">the line is gate <em>failures</em> per bucket</p>`,
    { cls: 'w3', i: 1 });

  const autonomy = measures.find(m => m.name === 'autonomy');
  const autoTile = tile(
    `<h3>Autonomy</h3><div class="label">merged with no human commits</div>`
    + (autonomy && autonomy.availability === 'available'
        ? gauge(autonomy.value, `${autonomy.sample} merged changes`, 'auto')
        : `<div class="gauge">${gauge(null, 'not observable', 'auto')}</div>`
          + `<p class="sub muted">${esc(autonomy ? autonomy.reason : 'no measurement')}</p>`),
    { cls: 'w3', i: 2 });

  const handoffTile = tile(
    `<div class="label">reached handoff</div>`
    + `<div class="figure sm">${esc((s.handoffs || []).reduce((a, b) => a + b, 0))}`
    + `<span class="u">changes</span></div>`
    + sparkline(s.handoffs, 'var(--good)'),
    { cls: 'w3', i: 3 });

  const costTile = tile(
    `<div class="label">spend, estimated</div>`
    + `<div class="figure sm">${esc(spent.toFixed(2))}<span class="u">units</span></div>`
    + sparkline(s.costUnits, 'var(--c3)')
    + `<p class="sub muted">from recorded usage and declared prices, not billing</p>`,
    { cls: 'w3', i: 4 });

  const mixTile = tile(
    `<h3>Run mix</h3>`
    + [['work', r.work], ['evaluation', r.evaluation], ['benchmark', r.benchmark],
       ['improvement', r.improvement]].map(([name, n]) =>
      `<div style="margin-top:10px"><div style="display:flex;justify-content:space-between;font-size:12.5px">`
      + `<span>${esc(name)}</span><span class="mono">${esc(n)}</span></div>`
      + `<div class="meter"><i style="width:${r.total ? Math.round((n / r.total) * 100) : 0}%"></i></div></div>`).join('')
    + `<p class="sub">${esc(r.note)}</p>`,
    { cls: 'w6', i: 5 });

  const metricTiles = available
    .filter(m => m.name !== 'gate_pass_rate')
    .map((m, i) => tile(
      `<h3>${esc(m.name)}</h3>`
      + `<div class="figure sm">${esc(fmt(m.value))}<span class="u">${esc(m.unit || '')}</span></div>`
      + delta(t[m.name], m.unit)
      + (m.excludes && m.excludes.length
          ? `<p class="sub muted">excludes ${esc(m.excludes.join(', '))}</p>` : ''),
      { cls: 'w3', i: 6 + i })).join('');

  const missingRows = missing.map(m =>
    `<tr><td><code>${esc(m.name)}</code></td>`
    + `<td><span class="chip mute">${esc(String(m.availability).replace(/_/g, ' '))}</span></td>`
    + `<td class="note" style="margin:0">${esc(m.reason)}</td></tr>`).join('');

  return `<div class="bento">${hero}${gateTile}${autoTile}${handoffTile}${costTile}${mixTile}`
    + (metricTiles ? `<h2 class="rule">measured</h2>${metricTiles}` : '')
    + (missing.length
        ? `<h2 class="rule">not measured, and why</h2>`
          + tile(`<div class="scroll"><table><thead><tr><th>metric</th><th>state</th><th>reason</th>`
            + `</tr></thead><tbody>${missingRows}</tbody></table></div>`
            + `<p class="note">A metric with no data reports its absence. "No change" and "we could `
            + `not look" are different things, and the second must never render as the first.</p>`,
            { cls: 'w12 ', i: 0 })
        : '')
    + `</div>`;
}

const STAGES = ['intake', 'triage', 'design', 'build', 'review', 'verify', 'handoff'];

function renderActivity(d) {
  if (!d.workItems || !d.workItems.length) {
    return void_('▤', 'No work items yet', d.note || 'Run `sf work` and this board fills from the ledger.');
  }
  const byStage = new Map(STAGES.map(s => [s, []]));
  d.workItems.forEach(w => {
    const k = String(w.stage || '').toLowerCase();
    if (!byStage.has(k)) byStage.set(k, []);
    byStage.get(k).push(w);
  });
  const most = Math.max(1, ...[...byStage.values()].map(v => v.length));

  const lanes = [...byStage.entries()].map(([stage, items]) =>
    `<div class="lane ${items.length ? '' : 'vacant'}">`
    + `<header><h4>${esc(stage)}</h4><span class="n">${items.length}</span></header>`
    + `<div class="rail"><i style="width:${Math.round((items.length / most) * 100)}%"></i></div>`
    + (items.length ? items.map(w =>
        `<div class="wi ${w.needsAttention ? 'flag' : ''}">`
        + `<div class="t">${esc(w.title)}</div>`
        + `<div class="m">${esc(w.id)} &middot; ${esc(w.workClass || '')}`
        + `${w.rework ? ' &middot; rework &times;' + esc(w.rework) : ''}</div>`
        + (w.why ? `<div class="w">${esc(w.why)}</div>` : '') + `</div>`).join('')
      : `<div class="empty-lane">—</div>`)
    + `</div>`).join('');

  const flagged = d.needingAttention || 0;
  return `<div class="bento">`
    + stat('work items', d.workItems.length, '', '', 0)
    + stat('needing a person', flagged, '',
        flagged ? `<div class="delta down">↑ sorted first below</div>`
                : `<div class="delta up">→ nothing is waiting on you</div>`, 1)
    + tile(`<h3>Pipeline</h3><div class="flow" style="margin-top:12px">${lanes}</div>`
        + `<p class="note">${esc(d.note || '')}</p>`, { cls: 'w12', i: 2 })
    + `</div>`;
}

function statusChip(s) {
  const v = String(s || '').toLowerCase();
  const cls = /^(ok|pass|passed|succeeded|complete|completed|handoff|merged)$/.test(v) ? 'ok'
    : /^(blocked|failed|fail|refused|violation|closed)$/.test(v) ? 'bad'
    : /^(running|open|opened)$/.test(v) ? 'info' : 'warn';
  return `<span class="chip ${cls}">${esc(v || 'unknown')}</span>`;
}

function renderRuns(d) {
  if (!d.runs || !d.runs.length) {
    return void_('▸', 'No runs recorded', 'A run appears the moment `sf work` writes its first ledger entry.');
  }
  const spent = d.runs.reduce((a, r) => a + (r.costUnits || 0), 0);
  const rows = d.runs.map(r =>
    `<tr class="clickable" data-run="${esc(r.id)}">`
    + `<td><code>${esc(r.id)}</code></td><td>${esc(r.agent || '—')}</td>`
    + `<td><span class="chip mute plain">${esc(r.stage || '?')}</span></td>`
    + `<td>${statusChip(r.status)}</td>`
    + `<td class="num">${esc(r.modelCalls)}</td><td class="num">${esc(r.toolCalls)}</td>`
    + `<td class="num">${r.gatesFailed ? `<span class="chip bad">${esc(r.gatesFailed)}</span>` : '0'}</td>`
    + `<td class="num">${esc(r.costUnits)}</td></tr>`).join('');

  return `<div class="bento">`
    + stat('runs recorded', d.total, '', '', 0)
    + stat('spend', spent.toFixed(3), 'units', `<div class="delta flat">estimated</div>`, 1)
    + stat('shown', d.shown, d.truncated ? 'of ' + d.total : '', '', 2)
    + stat('gates failed', d.runs.reduce((a, r) => a + (r.gatesFailed || 0), 0), '', '', 3)
    + tile(`<div class="scroll"><table><thead><tr><th>run</th><th>agent</th><th>stage</th>`
      + `<th>status</th><th class="num">model</th><th class="num">tools</th>`
      + `<th class="num">gates failed</th><th class="num">cost</th></tr></thead>`
      + `<tbody>${rows}</tbody></table></div>`, { cls: 'w12 flush', i: 4 })
    + `<p class="note" style="grid-column:1/-1">${esc(d.costNote || '')} `
    + `Click a row, or press ⌘K and type a run id.</p></div>`;
}

const SUMMARY_KEYS = ['stage', 'agent', 'tier', 'gate', 'outcome', 'status', 'tool',
                      'verdict', 'costUnits', 'reason', 'blocker', 'action'];

function summarise(payload) {
  if (!payload || typeof payload !== 'object') return '';
  const parts = SUMMARY_KEYS
    .filter(k => payload[k] !== undefined && payload[k] !== null && payload[k] !== '')
    .map(k => `${esc(k)} <b>${esc(fmt(payload[k]))}</b>`);
  return parts.length ? `<div class="sum">${parts.join(' &middot; ')}</div>` : '';
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
    return `<span class="chip ${ok ? 'ok' : 'bad'}">${esc(g.gate)}</span>`;
  }).join(' ');

  const events = (d.entries || []).map(e => {
    // A text node, not a string: the inspector returns whole ledger payloads by design,
    // and JSON.stringify escapes JSON metacharacters rather than HTML ones -- so
    // `</pre><img src=x onerror=...>` in model output closed the element and ran.
    const pre = document.createElement('pre');
    pre.textContent = JSON.stringify(e.payload, null, 2);
    return `<div class="ev ${evClass(e.type)}">`
      + `<div class="h"><span class="ty">${esc(e.type)}</span>`
      + `<span class="at">${esc(e.at)}</span><span class="at">#${esc(e.seq)}</span></div>`
      + summarise(e.payload)
      + `<details><summary>payload</summary>${pre.outerHTML}</details></div>`;
  }).join('');

  const trouble = [...(d.escalations || []), ...(d.violations || [])];
  return `<button class="back" data-back="runs">← all runs</button><div class="bento">`
    + stat('run', d.run, '', '', 0)
    + stat('tool calls', d.toolCalls, '', '', 1)
    + stat('spend', d.costUnits, 'units', `<div class="delta flat">estimate, not billing</div>`, 2)
    + stat('ledger entries', (d.entries || []).length, '', '', 3)
    + (gates ? tile(`<h3>Gates</h3><div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px">${gates}</div>`, { cls: 'w6', i: 4 }) : '')
    + (trouble.length
        ? tile(`<h3>Escalations and violations</h3>`
            + trouble.map(t => `<p class="sub"><b>${esc(t.kind || t.gate || 'event')}</b> — `
              + `${esc(t.reason || t.message || JSON.stringify(t))}</p>`).join(''), { cls: 'w6', i: 5 })
        : '')
    + tile(`<h3>Trace</h3><div class="trace" style="margin-top:12px">${events}</div>`
        + `<p class="note">${esc(d.costNote || '')}</p>`, { cls: 'w12', i: 6 })
    + `</div>`;
}

function chipTile(label, values, i) {
  if (!values || !values.length) {
    return tile(`<h3>${esc(label)}</h3><p class="sub muted">none declared</p>`, { cls: 'w4', i });
  }
  return tile(`<h3>${esc(label)} <span class="chip mute plain">${values.length}</span></h3>`
    + `<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px">`
    + values.map(v => `<span class="chip info">${esc(v)}</span>`).join('') + `</div>`,
    { cls: 'w4', i });
}

function renderDefinition(d) {
  if (d.available === false) return unavailableTile('Definition', d.reason);
  return `<div class="bento">`
    + stat('factory', d.factory, '', '', 0)
    + stat('agents', (d.agents || []).length, '', '', 1)
    + stat('skills', (d.skills || []).length, '', '', 2)
    + stat('repositories', (d.repositories || []).length, '', '', 3)
    + `<h2 class="rule">components</h2>`
    + chipTile('agents', d.agents, 0) + chipTile('automations', d.automations, 1)
    + chipTile('runners', d.runners, 2) + chipTile('scorers', d.scorers, 3)
    + chipTile('skills', d.skills, 4) + chipTile('principals', d.principals, 5)
    + ((d.unloaded || []).length ? chipTile('failed to load', d.unloaded, 6) : '')
    + chipTile('repositories', d.repositories, 7)
    + `<p class="note" style="grid-column:1/-1">${esc(d.note || '')}</p></div>`;
}

function renderEvaluation(d) {
  const names = Object.keys(d.scorers || {});
  if (!names.length && !(d.proposals || []).length) {
    return void_('◎', 'Nothing evaluated yet',
      'Scorers record here once runs are sampled, and proposals once the improvement loop opens one.');
  }
  const scorerTiles = names.map((name, i) => {
    const s = d.scorers[name];
    const outcomes = Object.entries(s.outcomes || {});
    const passed = outcomes.filter(([k]) => /pass|ok/i.test(k)).reduce((a, [, v]) => a + v, 0);
    const rate = s.sampled ? passed / s.sampled : null;
    return tile(`<h3>${esc(name)}</h3>` + gauge(rate, `${s.sampled} sampled`, 'sc' + i)
      + `<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px">`
      + outcomes.map(([k, v]) => `<span class="chip ${/pass|ok/i.test(k) ? 'ok' : 'bad'}">`
        + `${esc(k)} ${esc(v)}</span>`).join('') + `</div>`, { cls: 'w4', i });
  }).join('');

  const props = (d.proposals || []).map(p =>
    `<tr><td><code>${esc(p.id)}</code></td><td>${esc(p.target)}</td>`
    + `<td>${statusChip(p.status)}</td>`
    + `<td>${esc((p.evidence || []).join('; ') || 'no evidence recorded')}</td></tr>`).join('');

  return `<div class="bento">`
    + (scorerTiles ? `<h2 class="rule">scorers</h2>${scorerTiles}` : '')
    + `<h2 class="rule">improvement proposals</h2>`
    + tile(props
        ? `<div class="scroll"><table><thead><tr><th>proposal</th><th>target</th><th>status</th>`
          + `<th>evidence</th></tr></thead><tbody>${props}</tbody></table></div>`
        : `<p class="sub muted">No proposal has been opened. The loop proposes; it never applies.</p>`,
        { cls: props ? 'w12 flush' : 'w12', i: 0 })
    + `</div>`;
}

function renderRegistry(d) {
  const m = d.memory || {};
  const lanes = Object.entries(m).filter(([k]) =>
    !['available', 'reason', 'total', 'quarantined', 'bytes'].includes(k));

  const memTiles = m.available === false
    ? tile(`<h3>Memory</h3><p class="sub muted">${esc(m.reason)}</p>`, { cls: 'w6', i: 0 })
    : stat('memories', fmt(m.total), '', '', 0)
      + stat('quarantined', fmt(m.quarantined), '',
          m.quarantined ? `<div class="delta down">↑ held out of retrieval</div>`
                        : `<div class="delta up">→ clean</div>`, 1)
      + stat('size', fmt(Math.round((m.bytes || 0) / 1024)), 'KiB', '', 2)
      + (lanes.length ? tile(`<h3>Lanes</h3>`
          + lanes.map(([lane, n]) =>
            `<div style="margin-top:10px"><div style="display:flex;justify-content:space-between;font-size:12.5px">`
            + `<span>${esc(lane)}</span><span class="mono">${esc(n)}</span></div>`
            + `<div class="meter"><i style="width:${m.total ? Math.round((n / m.total) * 100) : 0}%"></i></div></div>`
          ).join(''), { cls: 'w3', i: 3 }) : '');

  const skills = (d.skills || []).map(s =>
    `<tr><td><code>${esc(s.name)}</code></td><td>${statusChip(s.status)}</td>`
    + `<td class="num">${esc(pct(s.precision))}</td><td class="num">${esc(pct(s.recall))}</td>`
    + `<td class="num">${esc(s.offered)}</td><td class="num">${esc(s.helped)}</td></tr>`).join('');

  return `<div class="bento"><h2 class="rule">memory fabric</h2>${memTiles}`
    + `<h2 class="rule">skills</h2>`
    + tile(skills
        ? `<div class="scroll"><table><thead><tr><th>skill</th><th>status</th>`
          + `<th class="num">precision</th><th class="num">recall</th><th class="num">offered</th>`
          + `<th class="num">helped</th></tr></thead><tbody>${skills}</tbody></table></div>`
        : `<p class="sub muted">No skills are declared in this factory.</p>`,
        { cls: skills ? 'w12 flush' : 'w12', i: 0 })
    + `<p class="note" style="grid-column:1/-1">Precision is, of the times a skill was loaded, `
    + `how often it helped. Recall counts retrospectively-detected misses and is an estimate `
    + `with a stated derivation, not a measurement.</p></div>`;
}

function unavailableTile(what, reason) {
  return `<div class="bento">` + tile(`<h3>${esc(what)} is not available</h3>`
    + `<p class="sub">${esc(reason)}</p>`
    + `<p class="sub muted">The factory is fine and the page is fine — this one panel has `
    + `nothing behind it, and an operator needs to know which and why.</p>`, { cls: 'w6', i: 0 })
    + `</div>`;
}

function void_(glyph, head, body) {
  return `<div class="void"><div class="g">${glyph}</div><h3>${esc(head)}</h3>`
    + `<p class="note">${esc(body)}</p></div>`;
}

function renderError(d) {
  return unavailableTile(String(d.error), String(d.message));
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
    content.innerHTML = `<div class="bento"><div class="skel"></div><div class="skel"></div>`
      + `<div class="skel"></div><div class="skel"></div></div>`;
  }
  const q = new URLSearchParams({ days: String(days) });
  if (view === 'run' && runId) q.set('run', runId);
  let data;
  try {
    data = await (await fetch(`/api/${view}?${q}`)).json();
  } catch (err) {
    status('down', 'unreachable');
    content.innerHTML = unavailableTile('Could not reach the factory', String(err));
    return;
  }
  lastData = data;
  if (data && data.error) { content.innerHTML = renderError(data); status('stale', String(data.error)); return; }
  const render = RENDER[view];
  content.innerHTML = render ? render(data) : renderError({ error: 'view.unknown', message: view });
  status('ok', new Date().toLocaleTimeString());
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
