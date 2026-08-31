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
        days = _days_from(params)
        if days is None:
            return {
                "error": "days.invalid",
                "message": (
                    f"`days` must be a whole number between 1 and {MAX_WINDOW_DAYS}; "
                    f"got {params.get('days', [''])[0]!r}"
                ),
            }
        ledger = Ledger(self.ledger_path)
        entries = list(ledger.read())
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

// Everything this page renders came from the ledger, and the ledger is full of text from
// outside the trust boundary: model output, work-item titles written by whoever opened the
// issue, command stderr. Nothing reaches innerHTML without passing through here.
function esc(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function measureRow(m) {
  if (m.availability !== 'available') {
    return `<tr><td>${esc(m.name)}</td><td class="unavailable" colspan="2">`
         + `${esc(String(m.availability).replace('_', ' '))} — ${esc(m.reason)}</td></tr>`;
  }
  const cls = m.estimate ? 'estimate' : '';
  const excludes = m.excludes.length ? `excludes ${esc(m.excludes.join(', '))}` : '';
  return `<tr><td>${esc(m.name)}</td><td class="${cls}">${esc(m.value)} ${esc(m.unit)}</td>`
       + `<td class="note">${excludes}</td></tr>`;
}

function renderOverview(d) {
  const r = d.current.runs;
  const rows = d.current.measures.map(measureRow).join('');
  return `<p class="note">${esc(r.note)}</p>`
    + `<table><tr><th>runs</th><th>count</th><th></th></tr>`
    + `<tr><td>total</td><td>${esc(r.total)}</td><td class="note">`
    + `${esc(Math.round(r.measurementShare * 100))}% measurement</td></tr>`
    + `<tr><td>work</td><td>${esc(r.work)}</td><td></td></tr>`
    + `<tr><td>evaluation</td><td>${esc(r.evaluation)}</td><td></td></tr>`
    + `<tr><td>benchmark</td><td>${esc(r.benchmark)}</td><td></td></tr>`
    + `<tr><td>improvement</td><td>${esc(r.improvement)}</td><td></td></tr>`
    + `</table><table><tr><th>metric</th><th>value</th><th></th></tr>${rows}</table>`;
}

function renderActivity(d) {
  if (!d.workItems.length) return `<p class="note">${esc(d.note || 'No work items.')}</p>`;
  const rows = d.workItems.map(w =>
    `<tr class="${w.needsAttention ? 'attention' : ''}"><td>${esc(w.id)}</td>`
    + `<td>${esc(w.title)}</td>`
    + `<td>${esc(w.stage)}</td><td class="note">${esc(w.why || '')}</td></tr>`).join('');
  return `<table><tr><th>id</th><th>title</th><th>stage</th><th>needs attention</th></tr>`
       + `${rows}</table>`;
}

function renderError(d) {
  return `<p class="unavailable">${esc(d.error)} — ${esc(d.message)}</p>`;
}

async function show(view) {
  document.querySelectorAll('nav button').forEach(b =>
    b.setAttribute('aria-current', String(b.dataset.view === view)));
  const q = view === 'run' ? '?run=' + encodeURIComponent(prompt('Run id?') || '') : '';
  let data;
  try {
    data = await (await fetch(`/api/${view}${q}`)).json();
  } catch (err) {
    content.innerHTML = `<p class="unavailable">could not reach the factory: ${esc(err)}</p>`;
    return;
  }
  if (data && data.error) { content.innerHTML = renderError(data); return; }
  if (view === 'overview') content.innerHTML = renderOverview(data);
  else if (view === 'activity') content.innerHTML = renderActivity(data);
  else {
    // A text node, not a string: the run inspector returns whole ledger payloads by
    // design, and JSON.stringify escapes JSON metacharacters rather than HTML ones -- so
    // `</pre><img src=x onerror=...>` in model output closed the element and ran.
    const pre = document.createElement('pre');
    pre.textContent = JSON.stringify(data, null, 2);
    content.replaceChildren(pre);
  }
}

document.querySelectorAll('nav button').forEach(b =>
  b.addEventListener('click', () => show(b.dataset.view)));
show('overview');
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
