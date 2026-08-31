#!/usr/bin/env python3
"""Regenerate the images in `docs/images/` from a real run of the factory.

Generated rather than taken by hand, for the same reason `docs/reference/` is generated: a
screenshot somebody captured once goes stale silently, and a stale screenshot of a CLI is
worse than none because it shows output the tool no longer produces.

Two kinds of image, and they need different machinery:

* **CLI output** is rendered by `rich`, which can export its own console to SVG. That keeps
  the text selectable and the file small, and it needs no browser.
* **The dashboard** is a web page, so it needs one. Chromium is used headless and the run
  fails if the page logs anything to the console -- a screenshot of a page that errored is a
  screenshot of a bug presented as a feature.

Both are stripped of external references. `rich` embeds a CDN font by default, and a
local-first project whose own README reaches a CDN to render is making the claim it exists
to disprove.

    python scripts/capture_screenshots.py [output-dir]
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

MONOSPACE = (
    "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
    "'DejaVu Sans Mono', 'Liberation Mono', monospace"
)

CHROMIUM = os.environ.get("SF_CHROMIUM", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")

CALIBRATION = {"confidence": 0.82, "evidence": ["importer.py:3"], "unknowns": []}
CARRIED = {
    "decisions": ["kept the public signature"],
    "attempted": ["tried a decorator; it broke pickling"],
    "constraints": ["the importer runs under a 30s timeout"],
    "artifacts": ["branch factory/bom-headers"],
}

WORK = [
    (
        "CSV importer mangles BOM headers",
        "Uploading a UTF-8 CSV with a BOM names the first column oddly.",
    ),
    ("Add a --verbose flag to the importer", "Operators want progress output on large files."),
    (
        "Retry the registry call on timeout",
        "The registry times out under load and the run fails outright.",
    ),
]


def build_demo(root: Path) -> tuple[Path, Path]:
    """A factory with three work items carried to handoff. Returns (factory, ledger)."""
    from software_factory.definition import load_strict
    from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
    from software_factory.orchestrator.coordinator import local_coordinator
    from software_factory.providers import StubProvider, says
    from software_factory.scaffold import init_factory

    repo = root / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "amaya",
        "GIT_AUTHOR_EMAIL": "amaya@example.test",
        "GIT_COMMITTER_NAME": "amaya",
        "GIT_COMMITTER_EMAIL": "amaya@example.test",
    }
    (repo / "importer.py").write_text("def strip_bom(text):\n    return text\n", encoding="utf-8")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    for args in (("init", "--quiet", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "x")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    factory = root / "factory"
    init_factory(factory, name="payments", owner="acme", repo="payments-service")
    definition = load_strict(factory)
    state = root / "state"

    def output(**fields: object) -> str:
        return json.dumps({"calibration": CALIBRATION, **CARRIED, **fields})

    for index, (title, request) in enumerate(WORK):
        item = WorkItem(
            id=new_id(),
            factory="payments",
            title=title,
            request=request,
            source=SourceContext(
                provider="git-host", kind="issue", ref=f"acme/payments#{40 + index}"
            ),
            work_class=WorkClass.CHORE,
        )
        provider = StubProvider(
            [
                says(
                    output(findings="strip_bom returns its input unchanged", scope="one function")
                ),
                says(
                    output(
                        summary="Stripped the BOM before parsing headers.",
                        claims=["The importer now strips the BOM."],
                    )
                ),
                says(output(verdict="accept", findings=[])),
                says(output(summary="Handed off.", branch="factory/bom-headers")),
            ]
        )
        local_coordinator(
            definition, repo=repo, state_dir=state, provider=provider, allow_unsandboxed=True
        ).run(item)

    return factory, state / "ledger.jsonl"


def capture_cli(factory: Path, ledger: Path, out: Path) -> None:
    from rich.console import Console
    from typer.testing import CliRunner

    from software_factory.cli import app

    shots = [
        ("cli-providers", ["providers", str(factory)]),
        ("cli-audit", ["audit", str(factory), "--egress"]),
        ("cli-metrics", ["metrics", str(ledger), "--days", "7"]),
        ("cli-principals", ["principals", str(factory)]),
    ]
    runner = CliRunner()
    for name, argv in shots:
        result = runner.invoke(app, argv, color=True)
        if result.exit_code != 0:
            raise SystemExit(f"{name}: sf exited {result.exit_code}\n{result.output}")
        with Path(os.devnull).open("w") as sink:
            console = Console(record=True, width=100, force_terminal=True, file=sink)
            shown = " ".join([argv[0], "<factory>", *argv[2:]])
            console.print(f"[bold green]$[/] sf {shown}")
            console.print(result.output.rstrip())
            console.save_svg(str(out / f"{name}.svg"), title=f"sf {argv[0]}")
        _delocalise(out / f"{name}.svg")


def _delocalise(svg: Path) -> None:
    """Remove the CDN font `rich` embeds, and use the reader's own monospace stack."""
    text = svg.read_text(encoding="utf-8")
    text = re.sub(r"@font-face\s*\{[^}]*\}", "", text)
    text = text.replace("'Fira Code'", "").replace('"Fira Code"', "")
    text = re.sub(r"font-family:\s*[^;}]*", f"font-family: {MONOSPACE}", text)
    svg.write_text(text, encoding="utf-8")


def capture_dashboard(ledger: Path, out: Path) -> None:
    from playwright.sync_api import sync_playwright

    from software_factory.observability.dash import DashboardData, make_handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardData(ledger_path=ledger)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    problems: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=CHROMIUM)
            page = browser.new_page(viewport={"width": 1100, "height": 760}, device_scale_factor=2)
            page.on("console", lambda m: problems.append(f"{m.type}: {m.text}"))
            page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))

            page.goto(base, wait_until="networkidle")
            page.wait_for_selector("table", timeout=10_000)
            page.screenshot(path=out / "dashboard-overview.png", full_page=True)

            page.click('nav button[data-view="activity"]')
            page.wait_for_selector("table", timeout=10_000)
            page.screenshot(path=out / "dashboard-activity.png", full_page=True)
            browser.close()
    finally:
        server.shutdown()

    if problems:
        # A page that errored is not a page worth showing. The CSP is the likely cause and
        # the likely regression: it names the hash of the inline script, so an edit to the
        # script without a re-render breaks the page silently in a browser and not in the
        # test suite.
        raise SystemExit("the dashboard logged problems:\n  " + "\n  ".join(problems))


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/images")
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        factory, ledger = build_demo(Path(directory))
        capture_cli(factory, ledger, out)
        capture_dashboard(ledger, out)
    print(f"wrote {len(list(out.iterdir()))} image(s) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
