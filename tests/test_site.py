"""The public site, built from the repository's own documents.

Generated rather than hand-written, for the same reason `docs/reference/` is: a page
describing a renamed command reads perfectly and sends every reader to something that does
not exist.

The tests are about the renderer's failures, because a documentation build fails silently
by definition — it produces a page, the page loads, and the only person who notices the
paragraph structure is wrong is a reader who has already decided the project is careless.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_site import PAGES, inline, markdown


def test_a_wrapped_paragraph_is_one_paragraph() -> None:
    """These documents wrap at ninety columns.

    Treating each line as its own paragraph put a gap between every wrapped line and made
    the whole site read as a list of sentences — which is what the first build did.
    """
    rendered = markdown("A sentence that continues\non the following line.")

    assert rendered.count("<p>") == 1
    assert "continues on the following" in rendered


def test_emphasis_may_span_a_wrap_boundary() -> None:
    """Inlining each line as it arrives silently drops every span the wrap splits in
    two — and these documents wrap at ninety columns, so half of them split."""
    rendered = markdown(
        'You describe work — *"the CSV importer\nmangles BOM headers"*. Yes.\n\nWhat landed includes **a test file the\nfactory wrote itself** — really.'
    )

    assert "<em>" in rendered and "</em>" in rendered
    assert "<strong>a test file the factory wrote itself</strong>" in rendered


def test_a_blank_line_starts_a_new_paragraph() -> None:
    """The other half: joining everything would run the whole document together."""
    assert markdown("First.\n\nSecond.").count("<p>") == 2


def test_a_multi_line_quotation_is_one_quotation() -> None:
    """Rendering each line as its own blockquote put a rule between every wrapped line of
    the README's opening, which read as three quotations from three different places."""
    rendered = markdown("> One line\n> and its continuation.")

    assert rendered.count("<blockquote>") == 1


def test_html_comments_do_not_reach_the_page() -> None:
    """`docs/reference/` opens with a "generated, do not edit by hand" comment aimed at
    somebody reading the file. It rendered as the first paragraph of the published page —
    an instruction to the wrong audience, at the top of what they came to read."""
    rendered = markdown("<!-- do not edit by hand -->\n\n# Title\n")

    assert "do not edit" not in rendered
    assert '<h1 id="title">Title</h1>' in rendered


def test_a_table_renders_as_a_table() -> None:
    rendered = markdown("| a | b |\n| --- | --- |\n| 1 | 2 |\n")

    assert "<table>" in rendered and "<th>a</th>" in rendered and "<td>1</td>" in rendered


def test_a_code_fence_is_escaped_not_interpreted() -> None:
    """A page that renders `<script>` from a code block is a page that executes whatever a
    document happens to contain."""
    rendered = markdown("```\n<script>alert(1)</script>\n```")

    assert "&lt;script&gt;" in rendered
    assert "<script>" not in rendered


def test_emphasis_inside_code_is_left_alone() -> None:
    """`**kwargs` is an argument, not bold text — and Python documents are full of it."""
    assert "<strong>" not in inline("`**kwargs`")


def test_a_repository_link_lands_on_a_page() -> None:
    assert 'href="prd.html"' in inline("[the PRD](docs/PRD.md)")


def test_an_external_link_is_left_alone() -> None:
    assert 'href="https://example.test/x"' in inline("[x](https://example.test/x)")


def test_an_unknown_repository_link_is_not_rewritten() -> None:
    """A dead link a reader can see beats a silent rewrite to a page that does not exist."""
    assert 'href="docs/nothing.md"' in inline("[x](docs/nothing.md)")


def test_an_image_path_points_at_the_copied_directory() -> None:
    assert 'src="images/a.png"' in inline("![a](docs/images/a.png)")


def test_details_and_summary_pass_through_the_renderer() -> None:
    """The README's progressive disclosure is raw HTML that renders natively on GitHub;
    the site must let exactly those tags through, or the sections render as text."""
    rendered = markdown(
        "<details>\n<summary><strong>More</strong></summary>\n\nBody.\n\n</details>"
    )

    assert "<details>" in rendered
    assert "</details>" in rendered
    assert "<summary><strong>More</strong></summary>" in rendered


def test_the_raw_html_allowlist_is_strict() -> None:
    """The passthrough is an allowlist, not a policy of looking the other way: a script
    or a div in prose is text on the page, not markup."""
    rendered = markdown("<script>alert(1)</script>")

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<div>" not in markdown("<div>not on the list</div>")


def test_every_heading_gets_a_unique_anchor() -> None:
    """The table of contents and every in-page link hang off heading ids; two sections
    with the same title must not collide."""
    rendered = markdown("## The refusal, explained\n\n## The refusal, explained\n")

    assert '<h2 id="the-refusal-explained">' in rendered
    assert '<h2 id="the-refusal-explained-2">' in rendered


def test_a_diagram_link_becomes_a_lazy_embed() -> None:
    """An image whose link targets a shipped interactive diagram renders as a poster with
    the iframe one click away -- not four interactive documents loading up front."""
    rendered = inline(
        "[![the machine](docs/diagrams/stage-machine.workflow.png)]"
        "(docs/diagrams/stage-machine.workflow.html)"
    )

    assert 'data-diagram="diagrams/stage-machine.workflow.html"' in rendered
    assert 'src="diagrams/stage-machine.workflow.png"' in rendered
    assert "diagram-load" in rendered
    assert "<iframe" not in rendered


def test_every_page_has_a_source_or_is_a_generated_report(tmp_path: Path) -> None:
    """A page whose source moved 404s for every reader while the build reports success.

    Reports written by a trial run are allowed to be absent — they are generated by a
    command that needs a live model — but everything else must exist in the repository.
    """
    generated = {"trials", "product-trial", "stress"}
    missing = [p.slug for p in PAGES if not p.source.is_file() and p.slug not in generated]

    assert missing == [], f"these pages have no source: {missing}"


def test_a_diagram_claiming_a_complete_set_names_the_whole_set() -> None:
    """ "The five triggers are the complete set" and "six behaviours" are checkable claims.

    I have twice doubted a diagram's claim about this codebase and been wrong both times,
    and once asserted two things a diagram could not support -- a scaffold nothing applied
    and a delta nothing measured. A picture reads as authoritative because it was generated,
    so the enumerations in it are pinned to the enums rather than to my memory of them.
    """
    import json

    from software_factory.harness.routing import Scaffold, Trigger

    source = (
        Path(__file__).resolve().parent.parent
        / "docs"
        / "diagrams"
        / "spec"
        / "routing-ladder.workflow.json"
    )
    if not source.is_file():
        pytest.skip("the routing-ladder diagram is not in this checkout")
    text = json.dumps(json.loads(source.read_text(encoding="utf-8")))

    for trigger in Trigger:
        assert trigger.value in text, f"the diagram omits the trigger {trigger.value}"
    for scaffold in Scaffold:
        assert scaffold.value in text, f"the diagram omits the scaffold {scaffold.value}"
    assert f"one of {len(list(Trigger))}" in text or "five" in text
    assert "six behaviours" in text


@pytest.mark.integration
def test_the_site_builds(tmp_path: Path) -> None:
    """End to end, through the same entry point CI uses."""
    out = tmp_path / "_site"
    result = subprocess.run(
        [sys.executable, "scripts/build_site.py", "--out", str(out)],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (out / "index.html").is_file()
    assert (out / ".nojekyll").is_file(), "Jekyll would drop anything under an underscore"
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "<title>Software Factory</title>" in index
    assert 'href="cli.html"' in index


@pytest.mark.integration
def test_the_landing_does_not_repeat_the_hero(tmp_path: Path) -> None:
    """The hero carries the README's title and pitch; rendering the body's own copy under
    the film reads the same paragraph twice on the project's front page."""
    out = tmp_path / "_site"
    subprocess.run(
        [sys.executable, "scripts/build_site.py", "--out", str(out)],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        check=True,
    )
    index = (out / "index.html").read_text(encoding="utf-8")

    assert index.count("Hand it a bug report") == 1
    assert "The refusal is the point." in index, "the body keeps its lead-in"


def test_the_film_appears_on_the_overview_and_nowhere_else(tmp_path: Path) -> None:
    """A video embedded on every page is a six-megabyte element on thirteen pages nobody
    came to watch it on. The site is the one place the film is actually playable, and the
    overview is the one page somebody arrives at wanting to see what this is."""
    out = tmp_path / "_site"
    subprocess.run(
        [sys.executable, "scripts/build_site.py", "--out", str(out)],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        check=True,
    )

    film = Path(__file__).resolve().parent.parent / "docs" / "video" / "software-factory.mp4"
    if not film.is_file():
        pytest.skip("the film has not been rendered in this checkout")

    assert "<video" in (out / "index.html").read_text(encoding="utf-8")
    assert "<video" not in (out / "cli.html").read_text(encoding="utf-8")
    assert (out / "video" / "software-factory.mp4").is_file()
    assert (out / "video" / "software-factory.vtt").is_file(), "captions must ship with it"


def test_the_film_does_not_autoplay(tmp_path: Path) -> None:
    """A six-megabyte download nobody asked for is a worse first impression than no video,
    and a page that starts making noise is worse still."""
    out = tmp_path / "_site"
    subprocess.run(
        [sys.executable, "scripts/build_site.py", "--out", str(out)],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        check=True,
    )
    index = (out / "index.html").read_text(encoding="utf-8")

    if "<video" not in index:
        pytest.skip("the film has not been rendered in this checkout")
    assert "autoplay" not in index
    assert 'preload="none"' in index


def test_the_diagrams_reach_the_site(tmp_path: Path) -> None:
    """The site is the one place the interactive artefact works.

    GitHub renders the PNG; a browser renders the real thing, with search, route probes and
    lenses. Shipping only the raster to both would throw away the half that cannot be
    printed.
    """
    out = tmp_path / "_site"
    subprocess.run(
        [sys.executable, "scripts/build_site.py", "--out", str(out)],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        check=True,
    )

    source = Path(__file__).resolve().parent.parent / "docs" / "diagrams"
    if not source.is_dir():
        pytest.skip("no diagrams in this checkout")

    copied = {p.name for p in (out / "diagrams").glob("*")}
    assert copied, "the diagrams directory exists and nothing was copied"
    assert not any(".visual-check." in name for name in copied), (
        "QA renders were published: two resolutions in two themes, meant to be looked at once"
    )
    index = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="diagrams/' in index, "the README's diagram links were not rewritten"


def test_no_readme_link_points_at_a_missing_file() -> None:
    """A README that references an image it does not have renders a broken icon on the
    project's front page, which is the first thing anybody sees."""
    import re

    root = Path(__file__).resolve().parent.parent
    text = (root / "README.md").read_text(encoding="utf-8")

    # Generated reports are produced by commands that need a live model; everything else
    # must exist in the checkout.
    generated = {"docs/product-trial.md"}
    referenced = set(re.findall(r"\((docs/[^)#]+)\)", text))
    missing = sorted(r for r in referenced - generated if not (root / r).exists())

    assert missing == [], f"README references files that do not exist: {missing}"


def test_every_diagram_can_be_regenerated_from_a_committed_source() -> None:
    """A generated artefact whose input is not in the repository cannot be regenerated.

    The first `.gitignore` here excluded `spec/` alongside the QA renders, which would have
    committed four diagrams nobody could rebuild from a fresh clone — the same shape as a
    screenshot taken by hand: correct today, unreproducible tomorrow, and stale silently.
    """
    root = Path(__file__).resolve().parent.parent / "docs" / "diagrams"
    if not root.is_dir():
        pytest.skip("no diagrams in this checkout")

    delivered = {
        p.name.rsplit(".", 1)[0] for p in root.glob("*.html") if ".visual-check." not in p.name
    }
    sources = {p.name.rsplit(".", 1)[0] for p in (root / "spec").glob("*.json")}

    assert delivered, "no delivered diagrams found"
    assert delivered <= sources, f"delivered with no source: {sorted(delivered - sources)}"


def test_no_diagram_claims_a_gate_that_does_not_exist() -> None:
    """A diagram naming a gate the code does not have is documentation of a product we do
    not ship, and it reads as authoritative precisely because it was generated."""
    import json
    import re

    from software_factory.evals.gates import BASELINE_GATES

    root = Path(__file__).resolve().parent.parent / "docs" / "diagrams" / "spec"
    if not root.is_dir():
        pytest.skip("no diagram sources in this checkout")

    known = {gate.name if hasattr(gate, "name") else str(gate) for gate in BASELINE_GATES}
    known |= {str(g) for g in BASELINE_GATES}
    text = " ".join(
        json.dumps(json.loads(p.read_text(encoding="utf-8"))) for p in root.glob("*.json")
    )

    # Gate-shaped names only: lowercase hyphenated words that look like the real ones.
    named = set(re.findall(r"\b(?:coverage-of-criteria|criterion-observed-failing)\b", text))

    assert named == set(), f"diagrams name gates that are not in BASELINE_GATES: {sorted(named)}"


@pytest.mark.integration
def test_no_link_on_the_built_site_points_at_nothing(tmp_path: Path) -> None:
    """Five 404s shipped on the published front page before this existed.

    `LICENSE`, `NOTICE`, `docs/adr` and `docs/reviews` were rewritten site-relative and are
    not on the site; `product-trial.html` was linked from a build that had skipped it, with
    the reason printed to a stderr nobody reads. The old check walked the README against the
    *repository*, where all five resolve. Only the built output can answer this.
    """
    out = tmp_path / "_site"
    result = subprocess.run(
        [sys.executable, "scripts/build_site.py", "--out", str(out)],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    broken, checked = [], 0
    for page in sorted(out.glob("*.html")):
        for match in re.finditer(r'(?:href|src)="([^"]+)"', page.read_text(encoding="utf-8")):
            href = match.group(1)
            if href.startswith(("http://", "https://", "#", "mailto:", "data:")):
                continue
            target = href.split("#")[0]
            if not target:
                continue
            checked += 1
            if not (out / target).exists():
                broken.append(f"{page.name} -> {href}")

    assert checked > 100, f"only {checked} local links checked; the sweep found nothing"
    assert broken == [], f"{len(broken)} dead link(s) on the built site: {broken[:10]}"


@pytest.mark.integration
def test_a_cross_reference_between_two_site_pages_stays_on_the_site(tmp_path: Path) -> None:
    """`docs/harness/HARNESS.md` writes `[memory](memory.md)`, meaning its own directory.

    Matched against the repository root that is not a page, so every cross-reference between
    the harness documents left for GitHub -- a reader following the spec through six
    documents was ejected from the site five times.
    """
    out = tmp_path / "_site"
    subprocess.run(
        [sys.executable, "scripts/build_site.py", "--out", str(out)],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        check=True,
    )
    harness = (out / "harness.html").read_text(encoding="utf-8")

    assert 'href="memory.html"' in harness
    assert "blob/main/docs/harness/memory.md" not in harness


@pytest.mark.integration
def test_a_dead_link_stops_the_build_and_writes_nothing(tmp_path: Path) -> None:
    """A generated site reads as authoritative *because* it was generated.

    Both halves matter. Reporting every dead link in one run rather than the first, and
    writing no page at all -- a half-written site whose front page links to twelve missing
    ones is worse than the link it stopped for.
    """
    root = Path(__file__).resolve().parent.parent
    scratch = tmp_path / "repo"
    shutil.copytree(root, scratch, ignore=shutil.ignore_patterns(".git", "_site", "*.pyc"))
    readme = scratch / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8")
        + "\n[gone](docs/no-such-document.md) and [also gone](docs/nor-this-one.md)\n",
        encoding="utf-8",
    )

    out = tmp_path / "_site"
    result = subprocess.run(
        [sys.executable, "scripts/build_site.py", "--out", str(out)],
        cwd=scratch,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, result.stdout
    assert "no-such-document.md" in result.stderr
    assert "nor-this-one.md" in result.stderr, "only the first dead link was reported"
    assert not list(out.glob("*.html")), "a partial site was written"


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def built_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One build for the whole-page invariants below."""
    out = tmp_path_factory.mktemp("site") / "_site"
    subprocess.run(
        [sys.executable, "scripts/build_site.py", "--out", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return out


def test_every_in_page_link_resolves_to_an_anchor_on_the_page(built_site: Path) -> None:
    """A table of contents that links to ids nobody rendered is a list of dead ends."""
    import re

    for page in built_site.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        ids = set(re.findall(r'id="([^"]+)"', text))
        headings = re.findall(r"<h[1-4][ >]", text)
        anchored = re.findall(r'<h[1-4] id="[^"]+"', text)

        assert len(headings) == len(anchored), f"{page.name}: a heading has no id"
        for fragment in re.findall(r'href="#([^"]+)"', text):
            assert fragment in ids, f"{page.name}: #{fragment} has no target on the page"


def test_diagram_embeds_point_at_diagrams_actually_shipped(built_site: Path) -> None:
    """A lazy embed whose target was never copied is a button that loads a 404."""
    import re

    diagrams = built_site / "diagrams"
    if not diagrams.is_dir():
        pytest.skip("no diagrams in this checkout")

    index = (built_site / "index.html").read_text(encoding="utf-8")
    targets = re.findall(r'data-diagram="(diagrams/[^"]+\.html)"', index)

    assert targets, "the landing page has no interactive diagram embeds"
    for target in targets:
        assert (built_site / target).is_file(), f"embed target was not shipped: {target}"


def test_no_built_page_or_asset_fetches_anything_external(built_site: Path) -> None:
    """Local-first is the claim; a page that phones a CDN is the claim disproven.

    The one allowed external URL is the repository's own link in the navigation. The
    copied diagram artefacts under `diagrams/` are generated by a separate tool with its
    own checks and are out of scope here.
    """
    import re

    allowed = (
        "https://github.com/ammar-hasan/software-factory",  # the repository link in the nav
        "https://your-host/",  # a placeholder endpoint inside a README code block, not fetched
    )
    files = list(built_site.glob("*.html")) + list((built_site / "site").glob("*"))

    offenders: dict[str, list[str]] = {}
    for page in files:
        for url in re.findall(r"https://[^\s\"'<>)\]]+", page.read_text(encoding="utf-8")):
            if not any(url.startswith(ok) for ok in allowed):
                offenders.setdefault(page.name, []).append(url)

    assert not offenders, f"external URLs found: {offenders}"


def test_the_landing_page_carries_the_interactive_pieces(built_site: Path) -> None:
    """The hero pipeline and the terminal replay are the landing page's argument; both
    must be present, motion-guarded, and pausable off-screen."""
    index = (built_site / "index.html").read_text(encoding="utf-8")

    assert "data-pipeline" in index, "the hero stage machine is missing"
    assert "data-terminal" in index, "the terminal replay is missing"
    assert "details" in index, "the README's progressive disclosure did not render"

    pipeline = (built_site / "site" / "pipeline.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in pipeline
    assert "IntersectionObserver" in pipeline, "the animation must pause off-screen"

    terminal = (built_site / "site" / "terminal.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in terminal
