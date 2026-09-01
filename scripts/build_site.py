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
The same rule binds the output: no webfonts, no CDN, no external requests anywhere -- the
design system is the product's own (traced from `observability/dash.py`), set in system
font stacks, and the interactive pieces are vanilla JS shipped from `docs/site/`.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REPO_URL = "https://github.com/ammar-hasan/software-factory"


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

#: The sidebar's grouping, by page slug. Pages with no `nav` label (contributing,
#: security) are listed under Repository rather than in a group of their own.
NAV_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Start", ("index",)),
    ("Design", ("prd", "harness", "spec", "memory", "skills", "evals")),
    ("Reference", ("cli", "tools", "gates")),
    ("Evidence", ("trials", "product-trial", "stress")),
)

STYLE = """
:root {
  color-scheme: dark;
  --accent: #7c8cff; --accent-2: #58e6d0;
  --bg: #0b0c10; --raise: #101218; --card: #14161e;
  --line: #1e212b; --line-2: #2a2e3b;
  --ink: #eceef5; --dim: #9096a8; --faint: #61667a;
  --good: #4ec9a0; --warn: #e8b458; --bad: #f2748c;
  --on-accent: #0b0c10;
  --r: 14px; --r-sm: 9px;
  --ease: cubic-bezier(.2, .8, .2, 1);
  --sans: "SF Pro Text", -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text",
          "Segoe UI", Inter, Roboto, "Helvetica Neue", system-ui, sans-serif;
  --display: "SF Pro Display", -apple-system, BlinkMacSystemFont,
             "Segoe UI Variable Display", "Segoe UI", Inter, system-ui, sans-serif;
  --mono: "SF Mono", ui-monospace, "JetBrains Mono", "Cascadia Mono", "Roboto Mono",
          Menlo, Consolas, monospace;
}
[data-theme="light"] {
  color-scheme: light;
  --accent: #5566e8; --accent-2: #14796b;
  --bg: #f6f7fa; --raise: #eef0f5; --card: #ffffff;
  --line: #e3e5ec; --line-2: #d0d3df;
  --ink: #16181f; --dim: #565c6e; --faint: #8a8fa2;
  --good: #0e8f6a; --warn: #9a6a0d; --bad: #c73a54;
  --on-accent: #ffffff;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; scroll-padding-top: 28px; }
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}
body {
  margin: 0; background: var(--bg); color: var(--ink); font-family: var(--sans);
  font-size: 16px; line-height: 1.65; -webkit-font-smoothing: antialiased;
  transition: background-color .25s var(--ease), color .25s var(--ease);
}
::selection { background: color-mix(in srgb, var(--accent) 32%, transparent); }
a { color: var(--accent); }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
.skip {
  position: absolute; left: -999px; top: 0; background: var(--card); color: var(--ink);
  padding: 8px 14px; border-radius: 0 0 var(--r-sm) 0; z-index: 90;
}
.skip:focus { left: 0; }

/* ------------------------------------------------------------------ shell */
.shell { display: grid; grid-template-columns: 252px minmax(0, 1fr); min-height: 100vh; }
@media (min-width: 1240px) {
  .shell { grid-template-columns: 252px minmax(0, 1fr) 224px; max-width: 1460px; margin: 0 auto; }
}
.sidebar {
  border-right: 1px solid var(--line); padding: 30px 18px 24px; position: sticky; top: 0;
  height: 100vh; height: 100dvh; overflow-y: auto; background: var(--bg); z-index: 60;
  display: flex; flex-direction: column;
}
.brand {
  display: flex; align-items: center; gap: 10px; font-family: var(--display);
  font-weight: 650; font-size: 16.5px; letter-spacing: -0.015em; color: var(--ink);
  text-decoration: none; padding: 0 10px;
}
.brand svg { flex: none; }
.tag { color: var(--dim); font-size: 12.5px; margin: 6px 0 8px; padding: 0 10px; }
.nav-group { margin-top: 20px; }
.nav-group-title {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.09em;
  color: var(--faint); padding: 0 10px; margin-bottom: 4px;
}
.sidebar a {
  display: block; padding: 5px 10px; color: var(--dim); text-decoration: none;
  font-size: 14px; border-radius: 8px; transition: color .15s, background-color .15s;
}
.sidebar a:hover { color: var(--ink); background: var(--raise); }
.sidebar a.current {
  color: var(--accent); font-weight: 560;
  background: color-mix(in srgb, var(--accent) 11%, transparent);
}
.sidebar a.external::after { content: "↗"; margin-left: 5px; font-size: 11px; color: var(--faint); }
.sidebar .sidebar-foot { margin-top: auto; padding-top: 22px; }

.topbar {
  display: none; align-items: center; gap: 12px; padding: 10px 16px;
  border-bottom: 1px solid var(--line); background: color-mix(in srgb, var(--bg) 88%, transparent);
  backdrop-filter: blur(12px); position: sticky; top: 0; z-index: 70;
}
.topbar .brand { padding: 0; font-size: 15.5px; }
.topbar .spacer { flex: 1; }
.icon-btn {
  display: inline-flex; align-items: center; justify-content: center; width: 36px; height: 36px;
  border: 1px solid var(--line-2); border-radius: var(--r-sm); background: var(--raise);
  color: var(--dim); cursor: pointer; transition: color .15s, border-color .15s;
}
.icon-btn:hover { color: var(--ink); border-color: var(--faint); }
.icon-btn svg { width: 18px; height: 18px; }
[data-theme="dark"] .icon-sun { display: none; }
[data-theme="light"] .icon-moon { display: none; }

.scrim { display: none; }
@media (max-width: 980px) {
  .topbar { display: flex; }
  .shell { grid-template-columns: 1fr; }
  .sidebar {
    position: fixed; inset: 0 auto 0 0; width: min(300px, 85vw); z-index: 80;
    transform: translateX(-105%); transition: transform .3s var(--ease);
    border-right: 1px solid var(--line-2); box-shadow: 0 0 60px rgba(0, 0, 0, .4);
  }
  body.nav-open .sidebar { transform: none; }
  body.nav-open { overflow: hidden; }
  .scrim {
    display: block; position: fixed; inset: 0; z-index: 55; background: rgba(3, 4, 8, .55);
    opacity: 0; pointer-events: none; transition: opacity .3s var(--ease);
  }
  body.nav-open .scrim { opacity: 1; pointer-events: auto; }
}

/* ------------------------------------------------------------------ content */
.content { padding: 46px 48px 110px; max-width: 80ch; min-width: 0; }
@media (max-width: 980px) { .content { padding: 26px 20px 90px; } }
.content h1 {
  font-family: var(--display); font-size: clamp(27px, 4vw, 33px); letter-spacing: -0.025em;
  line-height: 1.18; margin: 0 0 10px;
}
.content h2 {
  font-family: var(--display); font-size: clamp(20px, 2.6vw, 22.5px); letter-spacing: -0.015em;
  margin: 46px 0 12px; padding-top: 24px; border-top: 1px solid var(--line);
}
.content h3 { font-size: 16.5px; letter-spacing: -0.01em; margin: 28px 0 8px; }
.content h4 { font-size: 14.5px; margin: 22px 0 6px; }
.content p, .content li { color: var(--ink); }
.content > p:first-of-type { font-size: 17px; }
.content a { text-decoration: none; border-bottom: 1px solid color-mix(in srgb, var(--accent) 35%, transparent); }
.content a:hover { border-bottom-color: var(--accent); }
.content a:has(img), .content a.diagram-open { border-bottom: none; }
code {
  font-family: var(--mono); font-size: 0.86em; background: var(--raise);
  border: 1px solid var(--line); padding: 1.5px 5px; border-radius: 6px;
  overflow-wrap: break-word;
}
pre {
  background: var(--raise); border: 1px solid var(--line); border-radius: var(--r-sm);
  padding: 16px 18px; overflow-x: auto; margin: 18px 0;
}
pre code { background: none; border: none; padding: 0; font-size: 13.5px; line-height: 1.62; }
blockquote {
  margin: 18px 0; padding: 4px 0 4px 16px; border-left: 3px solid var(--accent);
  color: var(--dim);
}
blockquote p { color: var(--dim); }
table {
  border-collapse: collapse; width: 100%; margin: 18px 0; font-size: 14.5px;
  display: block; overflow-x: auto; border: 1px solid var(--line); border-radius: var(--r-sm);
}
th, td { text-align: left; padding: 9px 14px; border-bottom: 1px solid var(--line); vertical-align: top; }
thead th {
  font-weight: 600; color: var(--dim); font-size: 12px; text-transform: uppercase;
  letter-spacing: 0.06em; background: var(--raise); white-space: nowrap;
}
tbody tr:last-child td { border-bottom: none; }
img { max-width: 100%; height: auto; border-radius: var(--r-sm); border: 1px solid var(--line); }
hr { border: none; border-top: 1px solid var(--line); margin: 34px 0; }
sub {
  display: block; vertical-align: baseline; font-size: 13px; line-height: 1.6;
  color: var(--dim); margin: 10px 0 6px;
}
.footer {
  margin-top: 64px; padding-top: 20px; border-top: 1px solid var(--line);
  color: var(--faint); font-size: 13px;
}

/* ------------------------------------------------------------- disclosure */
details {
  border: 1px solid var(--line); border-radius: var(--r); background: var(--card);
  margin: 20px 0; overflow: hidden;
}
details > summary {
  list-style: none; cursor: pointer; padding: 16px 20px; display: flex; align-items: center;
  gap: 12px; color: var(--ink); font-size: 15.5px; transition: background-color .15s;
}
details > summary::-webkit-details-marker { display: none; }
details > summary::before {
  content: ""; flex: none; width: 8px; height: 8px; margin-left: 2px;
  border-right: 2px solid var(--faint); border-bottom: 2px solid var(--faint);
  transform: rotate(-45deg); transition: transform .25s var(--ease);
}
details[open] > summary::before { transform: rotate(45deg); }
details > summary:hover { background: var(--raise); }
details[open] > summary { border-bottom: 1px solid var(--line); }
details > *:not(summary) { margin-left: 20px; margin-right: 20px; }
details > table, details > .diagram-embed { width: auto; }
details > :last-child { margin-bottom: 20px; }
details[open] > *:not(summary) { animation: reveal .4s var(--ease); }
@keyframes reveal { from { opacity: 0; transform: translateY(-6px); } }

/* ------------------------------------------------------------------- hero */
.hero { padding: 12px 0 6px; margin-bottom: 8px; }
.hero-eyebrow {
  display: flex; align-items: center; gap: 12px; font-family: var(--mono);
  font-size: 12px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--accent-2);
  margin: 0 0 14px;
}
.hero-eyebrow::before { content: ""; width: 26px; height: 1px; background: var(--accent-2); }
.hero-pitch {
  font-family: var(--display); font-size: clamp(25px, 3.8vw, 37px); font-weight: 640;
  letter-spacing: -0.028em; line-height: 1.22; margin: 0 0 14px; max-width: 24em;
}
.hero-pitch .refusal { color: var(--accent-2); }
.hero-ctas { display: flex; gap: 12px; flex-wrap: wrap; margin: 22px 0 6px; }
.cta {
  display: inline-flex; align-items: center; gap: 8px; padding: 10px 20px;
  border-radius: 999px; font-weight: 600; font-size: 14.5px; text-decoration: none;
  border: 1px solid var(--line-2); color: var(--ink); background: var(--raise);
  transition: transform .2s var(--ease), border-color .2s, background-color .2s;
}
.cta:hover { transform: translateY(-1px); border-color: var(--faint); }
.cta.primary { background: var(--accent); border-color: transparent; color: var(--on-accent); }
.cta.primary:hover { background: color-mix(in srgb, var(--accent) 88%, white); }
.content .cta { border-bottom: none; }
.no-terminal {
  margin: 18px 0 0; padding: 12px 16px; border: 1px solid var(--line);
  border-left: 3px solid var(--accent-2); border-radius: var(--r-sm); background: var(--card);
  color: var(--dim); font-size: 14px;
}
.film { margin: 26px 0 10px; }
.film video { width: 100%; border-radius: var(--r); border: 1px solid var(--line); display: block; }

/* --------------------------------------------------------------- pipeline */
.pipeline {
  position: relative; margin: 26px 0 8px; padding: 20px 16px 16px; border: 1px solid var(--line);
  border-radius: var(--r); background:
    radial-gradient(120% 140% at 8% 0%, color-mix(in srgb, var(--accent) 7%, transparent), transparent 55%),
    var(--card);
  overflow: hidden;
}
.pipeline svg { display: block; width: 100%; height: auto; }
.pipeline .pipe-track { stroke: var(--line-2); stroke-width: 2; }
.pipeline .pipe-track-live { stroke: var(--accent-2); stroke-width: 2; opacity: .55; }
.pipeline .stage rect {
  fill: var(--raise); stroke: var(--line-2); stroke-width: 1.5;
  transition: stroke .3s var(--ease), fill .3s var(--ease);
}
.pipeline .stage text {
  fill: var(--dim); font-family: var(--mono); font-size: 11px; letter-spacing: .12em;
  text-anchor: middle;
}
.pipeline .stage.active rect { stroke: var(--accent); }
.pipeline .stage.active text { fill: var(--ink); }
.pipeline .stage.done rect { stroke: color-mix(in srgb, var(--good) 55%, var(--line-2)); }
.pipeline .gate circle {
  fill: var(--card); stroke: var(--line-2); stroke-width: 1.5;
  transition: stroke .25s var(--ease), fill .25s var(--ease);
}
.pipeline .gate .gate-tick {
  stroke: var(--faint); stroke-width: 1.6; fill: none; opacity: 0;
  transition: opacity .2s;
}
.pipeline .gate.pass circle {
  stroke: var(--good); fill: color-mix(in srgb, var(--good) 18%, var(--card));
  filter: drop-shadow(0 0 6px color-mix(in srgb, var(--good) 55%, transparent));
}
.pipeline .gate.pass .gate-tick { stroke: var(--good); opacity: 1; }
.pipeline .gate.block circle {
  stroke: var(--bad); fill: color-mix(in srgb, var(--bad) 20%, var(--card));
  filter: drop-shadow(0 0 8px color-mix(in srgb, var(--bad) 60%, transparent));
}
.pipeline .gate.block .gate-cross { stroke: var(--bad); opacity: 1; }
.pipeline .gate .gate-cross { stroke: var(--faint); stroke-width: 1.6; fill: none; opacity: 0; transition: opacity .2s; }
.pipeline .token-glow { fill: var(--accent); opacity: .22; }
.pipeline .token-core {
  fill: var(--accent);
  filter: drop-shadow(0 0 6px color-mix(in srgb, var(--accent) 70%, transparent));
}
.pipeline .token.stopped .token-core, .pipeline .token.stopped .token-glow { fill: var(--bad); }
.pipe-chip {
  position: absolute; top: 10px; right: 14px; font-family: var(--mono); font-size: 12px;
  color: var(--good); background: color-mix(in srgb, var(--good) 12%, var(--card));
  border: 1px solid color-mix(in srgb, var(--good) 45%, var(--line));
  padding: 4px 12px; border-radius: 999px; animation: chip-in .45s var(--ease);
}
@keyframes chip-in { from { opacity: 0; transform: translateY(-8px) scale(.92); } }
.pipe-refusal {
  margin-top: 14px; padding: 12px 16px; border-radius: var(--r-sm);
  border: 1px solid color-mix(in srgb, var(--bad) 45%, var(--line));
  background: color-mix(in srgb, var(--bad) 9%, var(--card));
  font-size: 13.5px; line-height: 1.55; color: var(--dim);
  animation: reveal .45s var(--ease);
}
.pipe-refusal strong { color: var(--bad); font-family: var(--mono); font-size: 12.5px; display: block; margin-bottom: 2px; }

/* -------------------------------------------------------------- terminal */
.term {
  margin: 22px 0; border: 1px solid var(--line); border-radius: var(--r);
  background: var(--raise); overflow: hidden;
}
.term-chrome {
  display: flex; align-items: center; gap: 8px; padding: 10px 14px;
  border-bottom: 1px solid var(--line); background: var(--card);
}
.term-dot { width: 11px; height: 11px; border-radius: 50%; background: var(--line-2); }
.term-dot:nth-child(1) { background: color-mix(in srgb, var(--bad) 70%, var(--line-2)); }
.term-dot:nth-child(2) { background: color-mix(in srgb, var(--warn) 70%, var(--line-2)); }
.term-dot:nth-child(3) { background: color-mix(in srgb, var(--good) 70%, var(--line-2)); }
.term-title { font-family: var(--mono); font-size: 12px; color: var(--faint); margin-left: 6px; }
.term-replay {
  margin-left: auto; font-family: var(--mono); font-size: 12px; color: var(--dim);
  background: none; border: 1px solid var(--line-2); border-radius: 6px; padding: 3px 10px;
  cursor: pointer; transition: color .15s, border-color .15s;
}
.term-replay:hover { color: var(--ink); border-color: var(--faint); }
.term-body {
  margin: 0; padding: 16px 18px; background: none; border: none; border-radius: 0;
  font-family: var(--mono); font-size: 13px; line-height: 1.66; white-space: pre-wrap;
  overflow-wrap: anywhere; min-height: 220px;
}
.term-body .t-cmd { color: var(--ink); }
.term-body .t-ok { color: var(--good); }
.term-body .t-stop { color: var(--warn); }
.term-body .t-dim { color: var(--dim); }
.term-body .t-block { color: var(--bad); }
.term.done .t-block {
  background: color-mix(in srgb, var(--bad) 10%, transparent);
}
.term-cursor {
  display: inline-block; width: 8px; height: 15px; vertical-align: -2px;
  background: var(--accent-2); animation: blink 1s steps(1) infinite;
}
@keyframes blink { 50% { opacity: 0; } }

/* ----------------------------------------------------------- diagram embed */
.diagram-embed { margin: 18px 0; }
.diagram-embed img { display: block; width: 100%; }
.diagram-embed iframe {
  display: block; width: 100%; aspect-ratio: 16 / 10; border: 1px solid var(--line);
  border-radius: var(--r-sm); background: var(--card);
}
.diagram-actions { display: flex; align-items: center; gap: 14px; margin-top: 10px; }
.diagram-load {
  font-family: var(--mono); font-size: 12.5px; color: var(--accent-2); cursor: pointer;
  background: color-mix(in srgb, var(--accent-2) 8%, var(--card));
  border: 1px solid color-mix(in srgb, var(--accent-2) 40%, var(--line));
  padding: 6px 14px; border-radius: 999px; transition: background-color .2s, transform .2s var(--ease);
}
.diagram-load:hover { background: color-mix(in srgb, var(--accent-2) 16%, var(--card)); transform: translateY(-1px); }

/* -------------------------------------------------------------------- toc */
.toc { padding: 46px 24px 40px 8px; position: sticky; top: 0; height: 100vh; height: 100dvh; overflow-y: auto; }
.toc-title {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.09em; color: var(--faint);
  margin-bottom: 10px;
}
.toc nav a {
  display: block; padding: 3px 0 3px 14px; border-left: 2px solid var(--line);
  color: var(--dim); font-size: 13px; text-decoration: none; line-height: 1.45;
  transition: color .15s, border-color .15s;
}
.toc nav a:hover { color: var(--ink); }
.toc nav a.current { color: var(--accent); border-left-color: var(--accent); }
@media (max-width: 1239px) { .toc { display: none; } }
.toc-inline { display: none; }
@media (max-width: 1239px) {
  .toc-inline { display: block; margin: 18px 0 8px; }
  .toc-inline nav { padding: 12px 20px 16px; }
  .toc-inline nav a {
    display: block; padding: 3px 0; color: var(--dim); font-size: 14px; text-decoration: none;
  }
  .toc-inline nav a:hover { color: var(--ink); }
}

/* ------------------------------------------------------------ back to top */
.to-top {
  position: fixed; right: 22px; bottom: 22px; z-index: 40; width: 40px; height: 40px;
  border-radius: 50%; border: 1px solid var(--line-2); background: var(--card); color: var(--dim);
  cursor: pointer; opacity: 0; pointer-events: none; transform: translateY(8px);
  transition: opacity .25s var(--ease), transform .25s var(--ease), color .15s;
  display: inline-flex; align-items: center; justify-content: center;
  box-shadow: 0 6px 24px rgba(0, 0, 0, .25);
}
.to-top.show { opacity: 1; pointer-events: auto; transform: none; }
.to-top:hover { color: var(--accent); border-color: var(--accent); }
.to-top svg { width: 16px; height: 16px; }
"""


@dataclass(frozen=True, slots=True)
class Heading:
    """A rendered heading: its level, its anchor slug, and its plain text for the TOC."""

    level: int
    slug: str
    text: str


def slugify(title: str) -> str:
    """`The refusal, explained` -> `the-refusal-explained`."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", title)
    text = re.sub(r"[`*_~]", "", text)
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def plain(title: str) -> str:
    """A heading's text with the inline Markdown removed, for the table of contents."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", title)
    text = re.sub(r"[*`_~]", "", text)
    return html.escape(text.strip())


def markdown(text: str) -> str:
    """Render the document; drop the heading record. See `render`."""
    return render(text)[0]


def render(text: str) -> tuple[str, list[Heading]]:
    """A small Markdown subset: enough for these documents, and nothing else.

    Hand-rolled rather than a dependency, and the trade is deliberate. The documents this
    renders are in the repository and are checked by other things; a renderer that handles
    them is worth more than one that handles every document nobody here writes. Anything it
    cannot render comes out as literal text rather than as mangled HTML, so a formatting
    gap is visible instead of silently changing what a sentence says.

    Two exceptions to "everything is escaped", both allowlisted and both required by the
    README's progressive disclosure: raw `<details>`/`</details>`/`<summary>…</summary>`
    lines pass through (they render natively on GitHub too), and inline `<sub>`/`</sub>`
    tags survive (the diagram captions are written with them). A `<script>` in prose is
    still escaped, and a test keeps it that way.
    """
    # HTML comments are stripped before anything else. `docs/reference/` opens with a
    # "generated, do not edit by hand" comment aimed at somebody reading the file, and it
    # rendered as the first paragraph of the published page -- an instruction to the wrong
    # audience, at the top of the page they came to read.
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    headings: list[Heading] = []
    used_slugs: dict[str, int] = {}
    in_code = False
    in_table = False
    in_list = False
    # The current paragraph, buffered raw and inlined only when it ends. Inlining each
    # line as it arrives breaks every span that crosses a wrap boundary -- and these
    # documents wrap at ninety columns, so `*"a phrase\nsplit in two"*` lost its emphasis.
    para: list[str] = []
    para_quote = False

    def flush_para() -> None:
        nonlocal para, para_quote
        if not para:
            return
        body = inline(" ".join(para))
        if para_quote:
            out.append(f"<blockquote>{body}</blockquote>")
        elif body.startswith('<div class="diagram-embed"'):
            # A <div> inside a <p> is invalid and the browser breaks the paragraph
            # around it; the embed is a block in its own right.
            out.append(body)
        else:
            out.append(f"<p>{body}</p>")
        para = []
        para_quote = False

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

    def heading_slug(title: str) -> str:
        base = slugify(title)
        seen = used_slugs.get(base, 0)
        used_slugs[base] = seen + 1
        return base if seen == 0 else f"{base}-{seen + 1}"

    for index, raw in enumerate(lines):
        line = raw.rstrip()

        if line.startswith("```"):
            flush_para()
            close_list()
            close_table()
            out.append("</code></pre>" if in_code else "<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line))
            continue

        if not line.strip():
            flush_para()
            close_list()
            close_table()
            continue

        stripped = line.strip()

        # Raw-HTML passthrough, allowlisted. Anything not on this list -- a <script>, a
        # <div>, an <iframe> -- is escaped by the paragraph path below.
        if stripped in ("<details>", "</details>") or (
            stripped.startswith("<summary>") and stripped.endswith("</summary>")
        ):
            flush_para()
            close_list()
            close_table()
            out.append(stripped)
            continue

        # A table: a header row, then a separator of dashes.
        if line.startswith("|") and not in_table:
            following = lines[index + 1].strip() if index + 1 < len(lines) else ""
            if re.fullmatch(r"\|[\s:|-]+\|", following):
                flush_para()
                close_list()
                cells = [inline(c.strip()) for c in line.strip("|").split("|")]
                out.append("<table><thead><tr>")
                out.extend(f"<th>{c}</th>" for c in cells)
                out.append("</tr></thead><tbody>")
                in_table = True
                continue
        if in_table:
            if re.fullmatch(r"\|[\s:|-]+\|", stripped):
                continue
            if not line.startswith("|"):
                close_table()
            else:
                cells = [inline(c.strip()) for c in line.strip("|").split("|")]
                out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
                continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            flush_para()
            close_list()
            close_table()
            level = len(heading.group(1))
            title = heading.group(2)
            slug = heading_slug(title)
            headings.append(Heading(level, slug, plain(title)))
            out.append(f'<h{level} id="{slug}">{inline(title)}</h{level}>')
            continue

        if line.startswith(("- ", "* ")):
            flush_para()
            close_table()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(line[2:])}</li>")
            continue
        close_list()

        if line.startswith("> "):
            # Consecutive quote lines are one quotation. Rendering each as its own
            # blockquote put a rule and a gap between every wrapped line of the README's
            # opening paragraph, which read as three separate quotations from three
            # different places.
            close_table()
            if para and not para_quote:
                flush_para()
            para_quote = True
            para.append(line[2:])
            continue
        if re.fullmatch(r"-{3,}", stripped):
            flush_para()
            close_table()
            out.append("<hr>")
            continue

        # A paragraph runs until a blank line, which is what Markdown means and what these
        # documents are written as: they wrap at ninety columns, so treating each line as
        # its own paragraph put a gap between every wrapped line and made the whole site
        # look like a list of sentences.
        if para_quote:
            # A plain line after quotation lines ends the quotation.
            flush_para()
        para.append(line)

    flush_para()
    close_list()
    close_table()
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out), headings


def image_size(src: str) -> str:
    """`width`/`height` attributes for a shipped image, read from the file itself.

    Without them a `loading="lazy"` image has no box until it loads, and the page shifts
    under the reader as each poster arrives. Only paths the build copies -- `images/` and
    `diagrams/` -- resolve to a file; anything else gets no attributes.
    """
    candidate = ROOT / "docs" / src
    try:
        if src.endswith(".png"):
            header = candidate.read_bytes()[:24]
            if header[:8] == b"\x89PNG\r\n\x1a\n":
                width, height = struct.unpack(">II", header[16:24])
                return f' width="{width}" height="{height}"'
        elif src.endswith(".svg"):
            head = candidate.read_text(encoding="utf-8")[:2000]
            viewbox = re.search(r'viewBox="[\d.-]+ [\d.-]+ ([\d.]+) ([\d.]+)"', head)
            if viewbox:
                return f' width="{viewbox.group(1)}" height="{viewbox.group(2)}"'
    except OSError:
        pass
    return ""


def inline(text: str) -> str:
    """Inline spans. Code first, so `**` inside a code span is not read as emphasis."""
    spans: list[str] = []

    def stash(match: re.Match[str]) -> str:
        spans.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"\x00{len(spans) - 1}\x00"

    def image(match: re.Match[str]) -> str:
        src = link(match.group(2))
        return f'<img alt="{match.group(1)}" src="{src}"{image_size(src)}>'

    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text)
    # The diagram captions are written as <sub>…</sub>; keep those two tags and no others.
    text = text.replace("&lt;sub&gt;", "<sub>").replace("&lt;/sub&gt;", "</sub>")
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", image, text)
    # An image whose wrapping link targets a shipped interactive diagram becomes a lazy
    # embed: the PNG as a poster, and a button that swaps in the iframe on request.
    text = re.sub(
        r'\[<img alt="([^"]*)" src="(diagrams/[^"]+\.png)"([^>]*)>\]\((docs/diagrams/[^)]+\.html)\)',
        lambda m: diagram_embed(m.group(1), m.group(2), m.group(4), m.group(3)),
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f'<a href="{link(m.group(2))}">{m.group(1)}</a>', text
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\w)\*([^*]+)\*(?!\w)", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)


def diagram_embed(alt: str, poster: str, page: str, attrs: str = "") -> str:
    """The poster frame for an interactive diagram, with the iframe one click away.

    The iframe is not in the markup at load: four interactive documents on one page is
    four documents nobody asked for yet. `site.js` swaps it in when the button is pressed.
    """
    target = link(page)
    return (
        f'<div class="diagram-embed" data-diagram="{target}">'
        f'<img alt="{alt}" src="{poster}"{attrs} loading="lazy">'
        '<span class="diagram-actions">'
        '<button type="button" class="diagram-load">Load interactive diagram</button>'
        "</span></div>"
    )


#: Repository paths that have a page, so an in-document link lands on the site rather than
#: on a 404. A link this does not know is left alone: an external URL must keep working,
#: and a repository path with no page is better as a visible dead link than as a silent
#: rewrite to a page that does not exist.
LINKS = {
    "docs/PRD.md": "prd.html",
    "docs/harness/HARNESS.md": "harness.html",
    "docs/harness/living-spec.md": "spec.html",
    "docs/harness/memory.md": "memory.md",
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
    if clean.startswith("docs/diagrams/"):
        return html.escape("diagrams/" + clean.split("/", 2)[2])
    return html.escape(href)


#: The film, embedded at the top of the overview page only.
#:
#: `preload="none"` and no autoplay: a six-megabyte download nobody asked for is a worse
#: first impression than no video, and a page that starts making noise is worse still.
#: Captions are a track rather than burned-in-only, so the film is readable with sound off
#: and searchable by anything that reads the page.
FILM = """<video controls preload="none" poster="images/dashboard-overview.png">
  <source src="video/software-factory.mp4" type="video/mp4">
  <track kind="captions" src="video/software-factory.vtt" srclang="en" label="English" default>
  Your browser cannot play this video.
  <a href="video/software-factory.mp4">Download it instead.</a>
</video>"""

MARK = (
    '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true">'
    '<rect x="2.5" y="9" width="5.5" height="11" rx="1.6" fill="var(--accent)" opacity=".45"/>'
    '<rect x="9.5" y="4.5" width="5.5" height="15.5" rx="1.6" fill="var(--accent)"/>'
    '<path d="M17.5 12.5h4" stroke="var(--accent-2)" stroke-width="2" '
    'stroke-linecap="round"/></svg>'
)

THEME_TOGGLE = (
    '<button class="icon-btn theme-toggle" type="button" aria-label="Toggle colour theme">'
    '<svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.2"/>'
    '<path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4M5 5l1.7 1.7M17.3 17.3L19 19'
    'M19 5l-1.7 1.7M6.7 17.3L5 19"/></svg>'
    '<svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M20.4 14.2A8.5 8.5 0 0 1 9.8 3.6a8.5 8.5 0 1 0 10.6 10.6z"/></svg>'
    "</button>"
)

#: Set the theme before first paint. Anything later flashes dark-then-light (or the
#: reverse) for every reader whose system disagrees with the default.
THEME_SCRIPT = """<script>(function(){var t=null;try{t=localStorage.getItem("sf-theme")}catch(e){}
if(t!=="light"&&t!=="dark")
t=window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";
document.documentElement.setAttribute("data-theme",t)})()</script>"""

STAGES = ("TRIAGE", "DESIGN", "BUILD", "REVIEW", "HANDOFF")

#: The landing hero: the pitch, the calls to action, and the stage machine, alive. The
#: SVG is inline so the pipeline reads as a static diagram with scripting off or with
#: reduced motion on; `pipeline.js` only sets it in motion.
HERO = f"""<section class="hero">
<p class="hero-eyebrow">local-first software factory</p>
<p class="hero-pitch">Hand it a bug report. Get back a branch with the fix, a test that
proves it, and a record of every decision &mdash; or <span class="refusal">a
refusal</span> that tells you exactly what is missing.</p>
<div class="hero-ctas">
<a class="cta primary" href="#watch-one-run">Watch a run</a>
<a class="cta" href="#the-refusal-explained">Read the story</a>
<a class="cta" href="{REPO_URL}">GitHub</a>
</div>
<div class="pipeline" data-pipeline>
<svg viewBox="0 0 780 118" role="img"
  aria-label="A work item moving through the stages triage, design, build, review and handoff, with gates between them">
<line class="pipe-track" x1="74" y1="60" x2="706" y2="60"/>
<line class="pipe-track-live" data-pipe-live x1="74" y1="60" x2="74" y2="60"/>
{
    "".join(
        f'<g class="gate" data-gate-x="{x}" transform="translate({x} 60)">'
        f'<circle r="9"/><path class="gate-tick" d="M-3.4 .2l2.4 2.6 4.4-5"/>'
        f'<path class="gate-cross" d="M-3 -3l6 6M3 -3l-6 6"/></g>'
        for x in (153, 311, 469, 627)
    )
}
{
    "".join(
        f'<g class="stage" data-stage="{name.lower()}" data-stage-x="{x}">'
        f'<rect x="{x - 64}" y="42" width="128" height="36" rx="18"/>'
        f'<text x="{x}" y="64">{name}</text></g>'
        for x, name in zip((74, 232, 390, 548, 706), STAGES, strict=True)
    )
}
<g class="token" data-token transform="translate(74 60)">
<circle class="token-glow" r="13"/><circle class="token-core" r="6.5"/>
</g>
</svg>
<div class="pipe-chip" data-pipe-chip hidden>HANDOFF &mdash; 4 file(s) changed</div>
<div class="pipe-refusal" data-pipe-refusal hidden>
<strong>blocked (gate_failed_terminal)</strong>
The test failed before its body ran, so it proves the code did not exist, not that the
behaviour was wrong.
</div>
</div>
</section>
"""

#: The non-developer path. Only rendered when the film is actually shipped -- a link to
#: #film with no film on the page is a dead anchor on the project's front porch.
NO_TERMINAL = """<p class="no-terminal">No terminal required to look around:
<a href="#film">watch the film</a>, or press <em>Load interactive diagram</em> under any
figure below.</p>
"""

#: The refusal transcript, typed out. Injected ahead of "The refusal, explained" so the
#: reader meets the machine's own words before the prose that explains them. The lines
#: are the README's own, and `terminal.js` types them.
TERMINAL = """<div class="term" data-terminal>
<div class="term-chrome">
<span class="term-dot"></span><span class="term-dot"></span><span class="term-dot"></span>
<span class="term-title">sf work &mdash; a refusal</span>
<button class="term-replay" type="button" hidden>Replay</button>
</div>
<pre class="term-body"><code data-term-out></code><span class="term-cursor"></span></pre>
</div>
"""

REFUSAL_HEADING = '<h2 id="the-refusal-explained">The refusal, explained</h2>'


def toc(headings: list[Heading]) -> tuple[str, str]:
    """The "On this page" column: a sticky aside on wide screens, a disclosure elsewhere."""
    items = [h for h in headings if h.level == 2]
    if not items:
        return "", ""
    links = "".join(f'<a href="#{h.slug}">{h.text}</a>' for h in items)
    aside = (
        f'<aside class="toc"><div class="toc-title">On this page</div><nav>{links}</nav></aside>'
    )
    inline = (
        f'<details class="toc-inline"><summary>On this page</summary><nav>{links}</nav></details>'
    )
    return aside, inline


def shell(page: Page, body: str, headings: list[Heading], pages: tuple[Page, ...]) -> str:
    by_slug = {p.slug: p for p in pages}
    nav = [f'<a class="brand" href="index.html">{MARK}Software Factory</a>']
    nav.append('<div class="tag">local-first agent factory</div>')
    for group, slugs in NAV_GROUPS:
        entries = [by_slug[s] for s in slugs if s in by_slug]
        if not entries:
            continue
        nav.append(f'<div class="nav-group"><div class="nav-group-title">{group}</div>')
        for other in entries:
            current = ' class="current"' if other.slug == page.slug else ""
            nav.append(f'<a href="{other.slug}.html"{current}>{html.escape(other.nav)}</a>')
        nav.append("</div>")
    nav.append('<div class="nav-group"><div class="nav-group-title">Repository</div>')
    nav.append(f'<a class="external" href="{REPO_URL}">Source</a>')
    for slug in ("contributing", "security"):
        if slug in by_slug:
            current = ' class="current"' if slug == page.slug else ""
            nav.append(f'<a href="{slug}.html"{current}>{by_slug[slug].title}</a>')
    nav.append("</div>")

    film = ""
    if page.slug == "index" and (ROOT / "docs" / "video" / "software-factory.mp4").is_file():
        film = f'<div id="film" class="film">{FILM}</div>'

    lead = ""
    scripts = '<script src="site/site.js" defer></script>'
    if page.slug == "index":
        body = body.replace(REFUSAL_HEADING, TERMINAL + REFUSAL_HEADING, 1)
        lead = HERO + (NO_TERMINAL + film if film else "")
        scripts += (
            '<script src="site/pipeline.js" defer></script>'
            '<script src="site/terminal.js" defer></script>'
        )

    aside, inline_toc = toc(headings)

    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{THEME_SCRIPT}
<title>{html.escape(page.title)}</title>
<meta name="description" content="A local-first software factory: specialist agents carrying requests into reviewable changes.">
<style>{STYLE}</style>
</head>
<body>
<a class="skip" href="#content">Skip to content</a>
<header class="topbar">
<button class="icon-btn nav-toggle" type="button" aria-label="Menu" aria-expanded="false">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
</button>
<a class="brand" href="index.html">{MARK}Software Factory</a>
<span class="spacer"></span>
{THEME_TOGGLE}
</header>
<div class="scrim"></div>
<div class="shell">
<nav class="sidebar">{"".join(nav)}<div class="sidebar-foot">{THEME_TOGGLE}</div></nav>
<main class="content" id="content">
{lead}
{inline_toc}
{body}
<div class="footer">
Generated from the repository by <code>scripts/build_site.py</code>.
Apache-2.0.
</div>
</main>
{aside}
</div>
<button class="to-top" type="button" aria-label="Back to top">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
</button>
{scripts}
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
        if page.slug == "index":
            # The hero already carries the README's title and pitch; rendering them
            # again under the film reads the same paragraph twice on the project's own
            # front page. The body starts at "The refusal is the point."
            marker = "The refusal is the point."
            if marker in text:
                text = text[text.index(marker) :]
        body, headings = render(text)
        (out / f"{page.slug}.html").write_text(
            shell(page, body, headings, present), encoding="utf-8"
        )

    images = ROOT / "docs" / "images"
    if images.is_dir():
        shutil.copytree(images, out / "images")

    # The film, if it has been rendered. Copied rather than linked to the repository,
    # because a page that plays a video from a raw GitHub URL stops playing the moment the
    # branch is renamed -- and the site is the one place the film is actually watchable.
    # The interactive diagrams, which are the one thing the site can show and the README
    # cannot: GitHub renders a PNG, a browser renders the real artefact.
    diagrams = ROOT / "docs" / "diagrams"
    if diagrams.is_dir():
        (out / "diagrams").mkdir(exist_ok=True)
        for artefact in sorted(diagrams.glob("*.html")):
            if ".visual-check." in artefact.name:
                continue
            shutil.copy2(artefact, out / "diagrams" / artefact.name)
        for image in sorted(diagrams.glob("*.png")):
            if ".visual-check." in image.name:
                continue
            shutil.copy2(image, out / "diagrams" / image.name)

    # The site's own assets: the shell behaviour and the two landing widgets, as vanilla
    # JS. No framework, no bundler, nothing fetched from anywhere.
    site_assets = ROOT / "docs" / "site"
    if site_assets.is_dir():
        shutil.copytree(site_assets, out / "site")

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
