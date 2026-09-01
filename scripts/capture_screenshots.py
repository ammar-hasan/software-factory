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


def build_demo(root: Path) -> tuple[Path, Path, str]:
    """A factory with three work items carried to handoff.

    Returns (factory, ledger, one work item id) -- the id because `sf explain` needs one,
    and an example that hardcodes an id is an example that stops working.
    """
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

    first_item = ""
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
        first_item = first_item or item.id
        local_coordinator(
            definition, repo=repo, state_dir=state, provider=provider, allow_unsandboxed=True
        ).run(item)

    return factory, state / "ledger.jsonl", first_item


def capture_cli(factory: Path, ledger: Path, work_item: str, out: Path) -> None:
    """Every `sf` screen an operator actually reads.

    Every one, deliberately. A gallery that shows the four commands whose output happened to
    look good is a gallery that hides the rest, and the ones a reader most wants to see
    before adopting anything are the ones that report refusals.
    """
    from rich.console import Console
    from typer.testing import CliRunner

    from software_factory.cli import app

    shots: list[tuple[str, list[str], set[int]]] = [
        ("cli-doctor", ["doctor"], {0, 1}),
        ("cli-validate", ["validate", str(factory)], {0}),
        ("cli-lint", ["lint", str(factory)], {0, 1}),
        ("cli-plan", ["plan", str(factory)], {0}),
        ("cli-audit", ["audit", str(factory), "--egress"], {0}),
        ("cli-providers", ["providers", str(factory)], {0}),
        ("cli-principals", ["principals", str(factory)], {0}),
        ("cli-stages", ["stages"], {0}),
        ("cli-gates", ["gates"], {0}),
        ("cli-schema", ["schema", "agent"], {0}),
        ("cli-serve", ["serve", str(factory)], {0}),
        (
            "cli-intake",
            ["intake", str(factory), "--event", "issue.labelled", "-a", "label=bug"],
            {0},
        ),
        ("cli-metrics", ["metrics", str(ledger), "--days", "7"], {0}),
        ("cli-spend", ["spend", str(ledger)], {0}),
        ("cli-delegation", ["delegation", str(ledger)], {0}),
        ("cli-improve", ["improve", str(ledger)], {0}),
        ("cli-govern-classes", ["govern", "classes"], {0}),
        ("cli-schedule", ["schedule", "list", str(factory)], {0}),
        ("cli-skill", ["skill", "list", str(factory)], {0}),
        ("cli-stop", ["stop", "list", "--state", str(ledger.parent)], {0}),
        (
            "cli-explain",
            ["explain", str(ledger), work_item, "what did you decide about the public signature?"],
            {0},
        ),
        # And the refusal, which is the more important of the two: `sf explain` answers from
        # what the run wrote down and never by re-running, so a question the record does not
        # cover gets a stated absence rather than a plausible sentence.
        (
            "cli-explain-silent",
            ["explain", str(ledger), work_item, "was the customer told about this?"],
            {1},
        ),
    ]
    runner = CliRunner()
    for name, argv, allowed in shots:
        result = runner.invoke(app, argv, color=True)
        if result.exit_code not in allowed:
            raise SystemExit(
                f"{name}: sf exited {result.exit_code}, expected one of "
                f"{sorted(allowed)}\n{result.output}"
            )
        with Path(os.devnull).open("w") as sink:
            console = Console(record=True, width=100, force_terminal=True, file=sink)
            shown = " ".join(_shorten(a, factory, ledger) for a in argv)
            console.print(f"[bold green]$[/] sf {shown}")
            console.print(result.output.rstrip())
            console.save_svg(str(out / f"{name}.svg"), title=f"sf {argv[0]}")
        _delocalise(out / f"{name}.svg")


def _shorten(arg: str, factory: Path, ledger: Path) -> str:
    """Show `<factory>` and `<ledger>` rather than a tmpdir nobody has."""
    if arg == str(factory):
        return "<factory>"
    if arg == str(ledger):
        return "<ledger>"
    return f'"{arg}"' if " " in arg else arg


def _delocalise(svg: Path) -> None:
    """Remove the CDN font `rich` embeds, and use the reader's own monospace stack."""
    text = svg.read_text(encoding="utf-8")
    text = re.sub(r"@font-face\s*\{[^}]*\}", "", text)
    text = text.replace("'Fira Code'", "").replace('"Fira Code"', "")
    text = re.sub(r"font-family:\s*[^;}]*", f"font-family: {MONOSPACE}", text)
    svg.write_text(text, encoding="utf-8")


def capture_dashboard(factory: Path, ledger: Path, out: Path) -> None:
    """Every view the server serves, plus one run opened from the index.

    `root=factory` matters: without it the definition and registry views report -- correctly
    -- that there is no factory tree beside the ledger, and a gallery of correct refusals is
    not what a reader is trying to see.
    """
    from playwright.sync_api import sync_playwright

    from software_factory.observability.dash import VIEWS, DashboardData, make_handler

    data = DashboardData(ledger_path=ledger, root=factory)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(data))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    problems: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=CHROMIUM)
            for scheme in ("dark", "light"):
                page = browser.new_page(
                    viewport={"width": 1600, "height": 900},
                    device_scale_factor=2,
                    color_scheme=scheme,
                )
                page.on("console", lambda m: problems.append(f"{m.type}: {m.text}"))
                page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
                # The entry animations start at `opacity: 0`, and a headless tab does not
                # always advance them before the screenshot lands -- which produced a
                # gallery of correctly-rendered, entirely invisible pages. Asking for
                # reduced motion takes the CSS path the page already has for it, so the
                # screenshots show the state a reader ends up looking at.
                page.emulate_media(reduced_motion="reduce")
                page.goto(base, wait_until="networkidle")
                suffix = "" if scheme == "dark" else "-light"

                for view in VIEWS:
                    page.click(f'.side nav button[data-view="{view}"]')
                    page.wait_for_selector("#content .grid, #content .nothing", timeout=10_000)
                    page.wait_for_timeout(320)  # let the entry animation settle
                    page.screenshot(
                        path=out / f"dashboard-{view}{suffix}.png",
                        full_page=True,
                        animations="disabled",
                    )

                if scheme == "dark":
                    # The run inspector, reached the way an operator reaches it: from the
                    # index, by clicking. If this selector stops matching, the index stopped
                    # being clickable and the inspector went back to being unreachable.
                    page.click('.side nav button[data-view="runs"]')
                    page.wait_for_selector("tr[data-run]", timeout=10_000)
                    page.click("tr[data-run]")
                    page.wait_for_selector(".thread .beat details", timeout=10_000)
                    page.wait_for_timeout(320)
                    page.screenshot(
                        path=out / "dashboard-run.png",
                        full_page=True,
                        animations="disabled",
                    )
                page.close()
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
    for stale in out.glob("*.png"):
        stale.unlink()
    for stale in out.glob("*.svg"):
        stale.unlink()
    with tempfile.TemporaryDirectory() as directory:
        factory, ledger, work_item = build_demo(Path(directory))
        capture_cli(factory, ledger, work_item, out)
        capture_dashboard(factory, ledger, out)
    print(f"wrote {len(list(out.iterdir()))} image(s) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
