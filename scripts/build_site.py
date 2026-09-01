#!/usr/bin/env python3
"""Build the public site from the repository's own documents.

Generated rather than hand-written, for the same reason `docs/reference/` is: a page
describing a renamed command reads perfectly and sends every reader to something that does
not exist. Everything here comes from a file that something else already checks -- the
README, the generated CLI reference, the trials report, the design documents -- so a page
cannot describe a factory this repository does not contain.

    python scripts/build_site.py --out _site

Deliberately dependency-free: no static-site generator, no theme, no build step beyond
Python. A documentation site that needs a toolchain is a documentation site that stops
building, and it stops building at exactly the moment somebody is trying to publish a fix.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Page:
    """One page, and the document it is generated from."""

    slug: str
    title: str
    source: Path
    nav: str = ""
    """Short label for the navigation. Empty means the page is reachable but not listed."""


PAGES: tuple[Page, ...] = (
    Page("index", "Software Factory", ROOT / "README.md", nav="Overview"),
    Page("prd", "Product requirements", ROOT / "docs" / "PRD.md", nav="PRD"),
    Page("harness", "The Loom harness", ROOT / "docs" / "harness" / "HARNESS.md", nav="Harness"),
    Page("spec", "Living Spec + Delta", ROOT / "docs" / "harness" / "living-spec.md", nav="Spec"),
    Page("memory", "Memory Fabric", ROOT / "docs" / "harness" / "memory.md", nav="Memory"),
    Page("skills", "Skill lifecycle", ROOT / "docs" / "harness" / "skills.md", nav="Skills"),
    Page("evals", "Evals and gates", ROOT / "docs" / "harness" / "evals.md", nav="Evals"),
    Page("cli", "Command reference", ROOT / "docs" / "reference" / "cli.md", nav="CLI"),
    Page("tools", "Tool reference", ROOT / "docs" / "reference" / "tools.md", nav="Tools"),
    Page("gates", "Gate reference", ROOT / "docs" / "reference" / "gates.md", nav="Gates"),
    Page("trials", "Trials", ROOT / "docs" / "trials.md", nav="Trials"),
    Page("product-trial", "Product trial", ROOT / "docs" / "product-trial.md", nav="Product trial"),
    Page("stress", "Stress report", ROOT / "docs" / "stress.md", nav="Stress"),
    Page("contributing", "Contributing", ROOT / "CONTRIBUTING.md"),
    Page("security", "Security", ROOT / "SECURITY.md"),
)

STYLE = """
:root {
  color-scheme: light dark;
  --bg: #fbfbfa; --fg: #1a1a19; --muted: #6b6b68; --rule: #e4e4e0;
  --accent: #2f6f5e; --code-bg: #f2f2ef; --card: #ffffff;
  --font: ui-sans-serif, -apple-system, "Segoe UI", Inter, system-ui, sans-serif;
  --mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a; --fg: #e7e7e4; --muted: #9a9a95; --rule: #2a2d33;
    --accent: #6ec5a8; --code-bg: #1c1f24; --card: #191c21;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg); font-family: var(--font);
  font-size: 16px; line-height: 1.65; -webkit-font-smoothing: antialiased;
}
.shell { display: grid; grid-template-columns: 232px minmax(0, 1fr); min-height: 100vh; }
nav {
  border-right: 1px solid var(--rule); padding: 28px 20px; position: sticky; top: 0;
  height: 100vh; overflow-y: auto;
}
nav .brand { font-weight: 620; letter-spacing: -0.015em; margin-bottom: 4px; display: block;
  color: var(--fg); text-decoration: none; }
nav .tag { color: var(--muted); font-size: 12.5px; margin-bottom: 22px; }
nav a { display: block; padding: 5px 0; color: var(--muted); text-decoration: none;
  font-size: 14.5px; border-radius: 5px; }
nav a:hover { color: var(--fg); }
nav a.current { color: var(--accent); font-weight: 560; }
nav .group { margin-top: 18px; font-size: 11.5px; text-transform: uppercase;
  letter-spacing: 0.07em; color: var(--muted); opacity: .75; }
main { padding: 40px 44px 96px; max-width: 860px; }
h1 { font-size: 30px; letter-spacing: -0.02em; margin: 0 0 6px; }
h2 { font-size: 21px; letter-spacing: -0.01em; margin: 34px 0 10px;
  padding-top: 14px; border-top: 1px solid var(--rule); }
h3 { font-size: 16.5px; margin: 22px 0 6px; }
p, li { color: var(--fg); }
a { color: var(--accent); }
code { font-family: var(--mono); font-size: 0.88em; background: var(--code-bg);
  padding: 1.5px 5px; border-radius: 4px; }
pre { background: var(--code-bg); padding: 14px 16px; border-radius: 9px;
  overflow-x: auto; border: 1px solid var(--rule); }
pre code { background: none; padding: 0; font-size: 13.5px; line-height: 1.55; }
blockquote { margin: 18px 0; padding: 2px 0 2px 16px; border-left: 3px solid var(--accent);
  color: var(--muted); }
table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 14.5px;
  display: block; overflow-x: auto; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--rule);
  vertical-align: top; }
th { font-weight: 600; color: var(--muted); font-size: 12.5px; text-transform: uppercase;
  letter-spacing: 0.05em; }
img { max-width: 100%; border-radius: 9px; border: 1px solid var(--rule); }
hr { border: none; border-top: 1px solid var(--rule); margin: 30px 0; }
.footer { margin-top: 56px; padding-top: 18px; border-top: 1px solid var(--rule);
  color: var(--muted); font-size: 13.5px; }
@media (max-width: 760px) {
  .shell { grid-template-columns: 1fr; }
  nav { position: static; height: auto; border-right: none;
    border-bottom: 1px solid var(--rule); }
  main { padding: 26px 20px 64px; }
}
"""


def markdown(text: str) -> str:
    """A small Markdown subset: enough for these documents, and nothing else.

    Hand-rolled rather than a dependency, and the trade is deliberate. The documents this
    renders are in the repository and are checked by other things; a renderer that handles
    them is worth more than one that handles every document nobody here writes. Anything it
    cannot render comes out as literal text rather than as mangled HTML, so a formatting
    gap is visible instead of silently changing what a sentence says.
    """
    # HTML comments are stripped before anything else. `docs/reference/` opens with a
    # "generated, do not edit by hand" comment aimed at somebody reading the file, and it
    # rendered as the first paragraph of the published page -- an instruction to the wrong
    # audience, at the top of the page they came to read.
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    in_code = False
    in_table = False
    in_list = False
    # Whether the previous line ended a block. Without it, "continue the previous
    # paragraph" was decided purely by what the last emitted element happened to be, so a
    # blank line closed nothing and every paragraph in a section ran into the next one.
    broken = True

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    def close_table() -> None:
        nonlocal in_table
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    for index, raw in enumerate(lines):
        line = raw.rstrip()

        if line.startswith("```"):
            close_list()
            close_table()
            out.append("</code></pre>" if in_code else "<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line))
            continue

        if not line.strip():
            close_list()
            close_table()
            broken = True
            continue

        # A table: a header row, then a separator of dashes.
        if line.startswith("|") and not in_table:
            following = lines[index + 1].strip() if index + 1 < len(lines) else ""
            if re.fullmatch(r"\|[\s:|-]+\|", following):
                close_list()
                cells = [inline(c.strip()) for c in line.strip("|").split("|")]
                out.append("<table><thead><tr>")
                out.extend(f"<th>{c}</th>" for c in cells)
                out.append("</tr></thead><tbody>")
                in_table = True
                continue
        if in_table:
            if re.fullmatch(r"\|[\s:|-]+\|", line.strip()):
                continue
            if not line.startswith("|"):
                close_table()
            else:
                cells = [inline(c.strip()) for c in line.strip("|").split("|")]
                out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
                continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            close_list()
            close_table()
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            broken = True
            continue

        if line.startswith(("- ", "* ")):
            close_table()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(line[2:])}</li>")
            broken = True
            continue
        close_list()

        if line.startswith("> "):
            # Consecutive quote lines are one quotation. Rendering each as its own
            # blockquote put a rule and a gap between every wrapped line of the README's
            # opening paragraph, which read as three separate quotations from three
            # different places.
            if not broken and out and out[-1].startswith("<blockquote>"):
                out[-1] = out[-1][: -len("</blockquote>")] + " " + inline(line[2:])
                out[-1] += "</blockquote>"
            else:
                out.append(f"<blockquote>{inline(line[2:])}</blockquote>")
            broken = False
            continue
        if re.fullmatch(r"-{3,}", line.strip()):
            out.append("<hr>")
            broken = True
            continue

        # A paragraph runs until a blank line, which is what Markdown means and what these
        # documents are written as: they wrap at ninety columns, so treating each line as
        # its own paragraph put a gap between every wrapped line and made the whole site
        # look like a list of sentences.
        if not broken and out and out[-1].startswith("<p>") and out[-1].endswith("</p>"):
            out[-1] = out[-1][: -len("</p>")] + " " + inline(line) + "</p>"
        else:
            out.append(f"<p>{inline(line)}</p>")
        broken = False

    close_list()
    close_table()
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


def inline(text: str) -> str:
    """Inline spans. Code first, so `**` inside a code span is not read as emphasis."""
    spans: list[str] = []

    def stash(match: re.Match[str]) -> str:
        spans.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text)
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        lambda m: f'<img alt="{m.group(1)}" src="{link(m.group(2))}">',
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f'<a href="{link(m.group(2))}">{m.group(1)}</a>', text
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\w)\*([^*]+)\*(?!\w)", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)


#: Repository paths that have a page, so an in-document link lands on the site rather than
#: on a 404. A link this does not know is left alone: an external URL must keep working,
#: and a repository path with no page is better as a visible dead link than as a silent
#: rewrite to a page that does not exist.
LINKS = {
    "docs/PRD.md": "prd.html",
    "docs/harness/HARNESS.md": "harness.html",
    "docs/harness/living-spec.md": "spec.html",
    "docs/harness/memory.md": "memory.html",
    "docs/harness/skills.md": "skills.html",
    "docs/harness/evals.md": "evals.html",
    "docs/reference/cli.md": "cli.html",
    "docs/reference/tools.md": "tools.html",
    "docs/reference/gates.md": "gates.html",
    "docs/trials.md": "trials.html",
    "docs/product-trial.md": "product-trial.html",
    "docs/stress.md": "stress.html",
    "CONTRIBUTING.md": "contributing.html",
    "SECURITY.md": "security.html",
    "README.md": "index.html",
}


def link(href: str) -> str:
    if href.startswith(("http://", "https://", "#", "mailto:")):
        return html.escape(href)
    clean = href.lstrip("./")
    if clean in LINKS:
        return LINKS[clean]
    if clean.startswith("docs/images/"):
        return html.escape("images/" + clean.split("/", 2)[2])
    return html.escape(href)


#: The film, embedded at the top of the overview page only.
#:
#: `preload="none"` and no autoplay: a six-megabyte download nobody asked for is a worse
#: first impression than no video, and a page that starts making noise is worse still.
#: Captions are a track rather than burned-in-only, so the film is readable with sound off
#: and searchable by anything that reads the page.
FILM = """<video controls preload="none" poster="images/dashboard-overview.png"
  style="width:100%;border-radius:10px;border:1px solid var(--rule);margin:0 0 26px">
  <source src="video/software-factory.mp4" type="video/mp4">
  <track kind="captions" src="video/software-factory.vtt" srclang="en" label="English" default>
  Your browser cannot play this video.
  <a href="video/software-factory.mp4">Download it instead.</a>
</video>"""


def shell(page: Page, body: str, pages: tuple[Page, ...]) -> str:
    nav = [
        '<a class="brand" href="index.html">Software Factory</a>',
        '<div class="tag">local-first agent factory</div>',
    ]
    for other in pages:
        if not other.nav:
            continue
        current = ' class="current"' if other.slug == page.slug else ""
        nav.append(f'<a href="{other.slug}.html"{current}>{html.escape(other.nav)}</a>')
    nav.append('<div class="group">Repository</div>')
    nav.append('<a href="https://github.com/ammar-hasan/software-factory">Source</a>')
    nav.append('<a href="contributing.html">Contributing</a>')
    nav.append('<a href="security.html">Security</a>')

    film = (
        FILM
        if page.slug == "index" and (ROOT / "docs" / "video" / "software-factory.mp4").is_file()
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(page.title)}</title>
<meta name="description" content="A local-first software factory: specialist agents carrying requests into reviewable changes.">
<style>{STYLE}</style>
</head>
<body>
<div class="shell">
<nav>{"".join(nav)}</nav>
<main>
{film}
{body}
<div class="footer">
Generated from the repository by <code>scripts/build_site.py</code>.
Apache-2.0.
</div>
</main>
</div>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="_site")
    args = parser.parse_args()

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    present = tuple(p for p in PAGES if p.source.is_file())
    missing = [p.slug for p in PAGES if not p.source.is_file()]
    for page in present:
        text = page.source.read_text(encoding="utf-8")
        (out / f"{page.slug}.html").write_text(
            shell(page, markdown(text), present), encoding="utf-8"
        )

    images = ROOT / "docs" / "images"
    if images.is_dir():
        shutil.copytree(images, out / "images")

    # The film, if it has been rendered. Copied rather than linked to the repository,
    # because a page that plays a video from a raw GitHub URL stops playing the moment the
    # branch is renamed -- and the site is the one place the film is actually watchable.
    film = ROOT / "docs" / "video" / "software-factory.mp4"
    if film.is_file():
        (out / "video").mkdir(exist_ok=True)
        shutil.copy2(film, out / "video" / film.name)
        for sidecar in ("software-factory.srt", "software-factory.vtt"):
            captions = film.parent / sidecar
            if captions.is_file():
                shutil.copy2(captions, out / "video" / sidecar)

    # No Jekyll: the site is already HTML, and Jekyll would try to process it and drop
    # anything in a directory beginning with an underscore.
    (out / ".nojekyll").write_text("", encoding="utf-8")

    print(f"wrote {len(present)} page(s) to {out}", file=sys.stderr)
    if missing:
        # Named rather than silently skipped. A page that vanishes because its source moved
        # is a link that 404s for every reader while the build still reports success.
        print(f"no source for: {', '.join(missing)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
