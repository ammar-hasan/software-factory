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
from software_factory.observability.views import activity_board, overview, run_inspector

#: Loopback only. A dashboard reachable from the network is one that has published a
#: factory's whole history to whoever can reach the port, and FR-15.8 asks for a local
#: application rather than an unauthenticated service.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


@dataclass(frozen=True, slots=True)
class DashboardData:
    """Everything the dashboard can show, from one ledger.

    A snapshot rather than a live connection: each request re-reads, so a page is always a
    fold over the ledger as it stands and never a cache that could disagree with it.
    """

    ledger_path: Path
    integrations: frozenset[str] = frozenset()

    def payload(self, view: str, params: dict[str, list[str]]) -> dict[str, Any]:
        ledger = Ledger(self.ledger_path)
        entries = list(ledger.read())
        days = int(params.get("days", ["7"])[0])
        window = Window.last(timedelta(days=days))

        match view:
            case "overview":
                return overview(entries, window=window, integrations=self.integrations)
            case "activity":
                # Work items are reconstructed by the orchestrator, not the ledger reader, so
                # the board is empty until a caller supplies them. Saying so beats an empty
                # table that reads as "no work".
                return {
                    **activity_board([]),
                    "note": (
                        "This view lists work items from the orchestrator's state. Served "
                        "from a ledger alone it is empty by construction, not because the "
                        "factory has no work."
                    ),
                }
            case "run":
                run_id = params.get("run", [""])[0]
                if not run_id:
                    return {"view": "run", "error": "run.missing", "message": "pass ?run=<id>"}
                return run_inspector(entries, run_id)
            case _:
                return {
                    "error": "view.unknown",
                    "message": f"no view {view!r}",
                    "views": ["overview", "activity", "run"],
                }


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
                body = json.dumps(data.payload(view, params), indent=2, default=str)
                self._respond(200, "application/json; charset=utf-8", body.encode("utf-8"))
                return
            self._respond(404, "text/plain; charset=utf-8", b"not found")

        def log_message(self, format: str, *args: Any) -> None:
            """Silent by default. A dashboard that prints a line per request buries whatever
            the operator was actually watching in the terminal they started it from."""

        def _respond(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # No external anything: the page loads no scripts, fonts, or styles from
            # elsewhere, which is what makes it work on a machine with no network.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
            )
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(
    ledger_path: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    integrations: frozenset[str] = frozenset(),
    ready: Callable[[str], None] | None = None,
) -> ThreadingHTTPServer:
    """Start the dashboard. Returns the server so a caller can shut it down.

    Returning rather than blocking: `sf dash` blocks on `serve_forever`, and a test needs the
    handle. A function that can only be used one way is a function only one caller can have.
    """
    server = ThreadingHTTPServer(
        (host, port), make_handler(DashboardData(ledger_path, integrations))
    )
    if ready is not None:
        ready(f"http://{host}:{server.server_address[1]}/")
    return server


#: One document, no build step, no external resources.
#:
#: Written plainly rather than prettily. The dashboard's job is to make a factory's state
#: legible to somebody who is trying to decide something, and a page that needs a toolchain
#: to change is a page nobody changes.
INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>software factory</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 14px/1.5 system-ui, sans-serif; margin: 0; padding: 2rem; max-width: 62rem; }
  h1 { font-size: 1.1rem; letter-spacing: .02em; text-transform: uppercase; opacity: .6; }
  nav button { font: inherit; padding: .4rem .8rem; margin-right: .4rem; cursor: pointer; }
  nav button[aria-current="true"] { font-weight: 600; }
  table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
  th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid rgba(128,128,128,.3); }
  th { font-weight: 600; opacity: .7; }
  .unavailable { opacity: .55; font-style: italic; }
  .estimate::after { content: " (estimate)"; opacity: .6; font-style: italic; }
  .attention { font-weight: 600; }
  pre { overflow-x: auto; padding: 1rem; background: rgba(128,128,128,.08); }
  .note { opacity: .7; font-size: .9em; margin: .5rem 0 1.5rem; }
</style>
</head>
<body>
<h1>software factory</h1>
<nav>
  <button data-view="overview" aria-current="true">Overview</button>
  <button data-view="activity">Activity</button>
  <button data-view="run">Run</button>
</nav>
<div id="content">loading…</div>
<script>
const content = document.getElementById('content');

function measureRow(m) {
  if (m.availability !== 'available') {
    return `<tr><td>${m.name}</td><td class="unavailable" colspan="2">`
         + `${m.availability.replace('_', ' ')} — ${m.reason}</td></tr>`;
  }
  const cls = m.estimate ? 'estimate' : '';
  const excludes = m.excludes.length ? `excludes ${m.excludes.join(', ')}` : '';
  return `<tr><td>${m.name}</td><td class="${cls}">${m.value} ${m.unit}</td>`
       + `<td class="note">${excludes}</td></tr>`;
}

function renderOverview(d) {
  const r = d.current.runs;
  const rows = d.current.measures.map(measureRow).join('');
  return `<p class="note">${r.note}</p>`
    + `<table><tr><th>runs</th><th>count</th><th></th></tr>`
    + `<tr><td>total</td><td>${r.total}</td><td class="note">`
    + `${Math.round(r.measurementShare * 100)}% measurement</td></tr>`
    + `<tr><td>work</td><td>${r.work}</td><td></td></tr>`
    + `<tr><td>evaluation</td><td>${r.evaluation}</td><td></td></tr>`
    + `<tr><td>benchmark</td><td>${r.benchmark}</td><td></td></tr>`
    + `<tr><td>improvement</td><td>${r.improvement}</td><td></td></tr>`
    + `</table><table><tr><th>metric</th><th>value</th><th></th></tr>${rows}</table>`;
}

function renderActivity(d) {
  if (!d.workItems.length) return `<p class="note">${d.note || 'No work items.'}</p>`;
  const rows = d.workItems.map(w =>
    `<tr class="${w.needsAttention ? 'attention' : ''}"><td>${w.id}</td><td>${w.title}</td>`
    + `<td>${w.stage}</td><td class="note">${w.why || ''}</td></tr>`).join('');
  return `<table><tr><th>id</th><th>title</th><th>stage</th><th>needs attention</th></tr>`
       + `${rows}</table>`;
}

async function show(view) {
  document.querySelectorAll('nav button').forEach(b =>
    b.setAttribute('aria-current', String(b.dataset.view === view)));
  const q = view === 'run' ? '?run=' + (prompt('Run id?') || '') : '';
  const data = await (await fetch(`/api/${view}${q}`)).json();
  if (view === 'overview') content.innerHTML = renderOverview(data);
  else if (view === 'activity') content.innerHTML = renderActivity(data);
  else content.innerHTML = '<pre>' + JSON.stringify(data, null, 2) + '</pre>';
}

document.querySelectorAll('nav button').forEach(b =>
  b.addEventListener('click', () => show(b.dataset.view)));
show('overview');
</script>
</body>
</html>
"""
