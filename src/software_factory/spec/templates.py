"""Spec templates derived from the repository they will be used in (V37, V34, V38).

Induction already reads a repository and proposes spec units. What it could not do was
change *shape*: every unit came out with the same sections, the same idea of what an
acceptance criterion looks like, and the same expectation of what could be anchored. One
shape for every repository means the shape fits none of them, and the cost lands on whoever
writes the unit -- a template asking for a status code in a data pipeline, or for a
`verifies` anchor in a repository with no tests, is a form somebody has to work around
before they can say anything true.

So a template is *derived*, and every part of it names the evidence in the repository that
produced it. Four decisions:

**Nothing is asserted that the repository cannot support.** A repository with no test
directory gets a template whose test-anchor section is marked `unenforceable` rather than
absent and rather than required. Absent hides a real gap; required makes every unit fail a
check nobody can satisfy; `unenforceable` says the repository cannot answer this yet, which
is the true thing and the one an operator can act on.

**Ambiguity is reported, not resolved.** A repository that is 60% TypeScript and 40% Python
is not a TypeScript repository. It gets both, and the template says so, because a template
confidently tailored to the wrong half is worse than a generic one -- the generic one at
least does not mislead.

**Vocabulary is mined against a stop list of its own domain.** The most common identifiers
in any codebase are `self`, `data`, `test` and `result`. A vocabulary section built from raw
frequency teaches an agent the language of programming rather than the language of this
repository, and the terms that matter are the ones frequent *here* and rare everywhere.

**An unrecognisable repository gets the generic template and is told so.** Guessing produces
a template that reads as authoritative and is wrong; the fallback reads as generic and is
merely unhelpful, which is recoverable.
"""

from __future__ import annotations

import enum
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Directories never read. Vendored code is somebody else's conventions, and mining it
#: teaches this repository's agents a vocabulary nobody here uses.
SKIP = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    "target",
    "vendor",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "site-packages",
}

#: Identifiers common to all code in a language, which say nothing about a repository. The
#: point of a vocabulary is the words that are frequent *here* and rare everywhere else.
NOISE = {
    "self",
    "cls",
    "data",
    "test",
    "tests",
    "result",
    "results",
    "value",
    "values",
    "item",
    "items",
    "args",
    "kwargs",
    "path",
    "paths",
    "name",
    "names",
    "file",
    "files",
    "type",
    "types",
    "list",
    "dict",
    "set",
    "str",
    "int",
    "bool",
    "none",
    "true",
    "false",
    "get",
    "new",
    "old",
    "index",
    "count",
    "key",
    "keys",
    "main",
    "run",
    "init",
    "config",
    "error",
    "errors",
    "return",
    "class",
    "def",
    "function",
    "const",
    "var",
    "let",
    "async",
    "await",
    "import",
    "from",
    "export",
    "default",
    "public",
    "private",
    "static",
    "void",
    "string",
    "number",
    "object",
    "array",
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
}

#: A repository needs at least this many files of a language before it counts as one of its
#: languages. Below it, a single vendored config file would make every repository polyglot.
MIN_FILES = 3

#: Share of files below which a language is present but not characteristic. A repository is
#: not "a Rust repository" because of one build script.
CHARACTERISTIC = 0.2


class Support(enum.StrEnum):
    """Whether the repository can satisfy a section of the template.

    `UNENFORCEABLE` is the value that makes this module worth having. A repository with no
    tests cannot supply a test anchor: saying `REQUIRED` makes every unit fail a check
    nobody can satisfy, and omitting the section hides a real gap. Naming it is the only
    option that leaves an operator something to do.
    """

    REQUIRED = "required"
    OPTIONAL = "optional"
    UNENFORCEABLE = "unenforceable"


@dataclass(frozen=True, slots=True)
class Section:
    """One part of a spec unit, and what this repository can say in it."""

    name: str
    prompt: str
    support: Support
    evidence: str
    """What in the repository decided this. A section with no evidence is a fixed template
    with extra steps, and the whole claim of this module is that the shape was derived."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "prompt": self.prompt,
            "support": self.support.value,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class Template:
    """The shape a spec unit takes in one repository."""

    languages: tuple[str, ...]
    sections: tuple[Section, ...]
    vocabulary: tuple[str, ...]
    criterion_examples: tuple[str, ...]
    generic: bool = False
    notes: tuple[str, ...] = ()

    @property
    def unenforceable(self) -> tuple[Section, ...]:
        return tuple(s for s in self.sections if s.support is Support.UNENFORCEABLE)

    def render(self) -> str:
        """The template as an agent sees it.

        Unenforceable sections are shown, marked, rather than dropped. An agent that is not
        told the repository has no tests will keep proposing test anchors and keep being
        refused, and neither it nor the reader will ever learn why.
        """
        lines: list[str] = []
        if self.generic:
            lines.append(
                "<!-- Generic template: this repository's shape could not be determined. -->"
            )
        elif self.languages:
            lines.append(f"<!-- Tailored to: {', '.join(self.languages)} -->")
        for section in self.sections:
            mark = "" if section.support is Support.REQUIRED else f" [{section.support.value}]"
            lines.append(f"## {section.name}{mark}")
            lines.append(section.prompt)
            if section.support is Support.UNENFORCEABLE:
                lines.append(f"  (not available here: {section.evidence})")
            lines.append("")
        if self.criterion_examples:
            lines.append("## Acceptance criteria, in this repository's terms")
            lines.extend(f"  - {example}" for example in self.criterion_examples)
            lines.append("")
        if self.vocabulary:
            lines.append("## Words this repository uses")
            lines.append("  " + ", ".join(self.vocabulary))
        return "\n".join(lines).rstrip() + "\n"

    def as_dict(self) -> dict[str, Any]:
        return {
            "languages": list(self.languages),
            "generic": self.generic,
            "sections": [s.as_dict() for s in self.sections],
            "vocabulary": list(self.vocabulary),
            "criterionExamples": list(self.criterion_examples),
            "notes": list(self.notes),
        }


#: Extension to language. Only extensions whose language is unambiguous: `.h` is C or C++
#: depending on what is next to it, and guessing produces a confidently wrong template.
LANGUAGES = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".sql": "sql",
}

#: Criterion phrasings that fit a repository's shape. A status-code example in a data
#: pipeline teaches the wrong thing more effectively than no example at all.
CRITERION_EXAMPLES: dict[str, tuple[str, ...]] = {
    "http": (
        "A request with no auth header returns 401 and a body naming the missing header.",
        "A malformed payload returns 400 without reaching the handler.",
    ),
    "cli": (
        "`sf <command>` exits 2 and names the option that was missing.",
        "`--json` output parses and carries `ok: false` on the failure path.",
    ),
    "data": (
        "A row with a null join key is written to the reject table, not dropped.",
        "Re-running the job over the same input produces byte-identical output.",
    ),
    "ui": (
        "The empty state names what to do next, not just that there is nothing.",
        "The control is reachable and operable from the keyboard alone.",
    ),
    "library": (
        "Passing an empty sequence raises `ValueError` naming the argument.",
        "The public signature is unchanged for callers on the previous minor version.",
    ),
}


def derive(root: Path) -> Template:
    """Read a repository and produce the spec template that fits it."""
    files = _files(root)
    if not files:
        return _generic("the directory holds no readable source files")

    counts = Counter(LANGUAGES[path.suffix] for path in files if path.suffix in LANGUAGES)
    total = sum(counts.values())
    if not total:
        return _generic("no file extension mapped to a language this template knows")

    languages = tuple(
        sorted(
            language
            for language, count in counts.items()
            if count >= MIN_FILES and count / total >= CHARACTERISTIC
        )
    )
    if not languages:
        # Present but not characteristic: several languages, none of them dominant enough to
        # tailor to. Reported rather than resolved by picking the largest.
        return _generic(
            "no language accounts for enough of the repository to tailor to "
            f"({', '.join(f'{k} {v}' for k, v in counts.most_common(3))})"
        )

    shape, shape_evidence = _shape(root, files)
    tests, test_evidence = _tests(files)
    typed, typed_evidence = _typed(files)

    sections = (
        Section(
            name="Intent",
            prompt="What this behaviour is for, in one paragraph a reviewer can disagree with.",
            support=Support.REQUIRED,
            evidence="every unit needs one",
        ),
        Section(
            name="Acceptance",
            prompt=(
                "Individually checkable criteria. Each one names an observable outcome, "
                "not a quality."
            ),
            support=Support.REQUIRED,
            evidence="every unit needs one",
        ),
        Section(
            name="Implements",
            prompt=f"Paths or symbols this governs ({', '.join(languages)}).",
            support=Support.REQUIRED,
            evidence=f"{total} source file(s) in {', '.join(languages)}",
        ),
        Section(
            name="Verifies",
            prompt="The test that fails without this change and passes with it.",
            support=Support.REQUIRED if tests else Support.UNENFORCEABLE,
            evidence=test_evidence,
        ),
        Section(
            name="Types",
            prompt="Signatures a caller depends on, where the change alters them.",
            support=Support.REQUIRED if typed else Support.OPTIONAL,
            evidence=typed_evidence,
        ),
    )

    return Template(
        languages=languages,
        sections=sections,
        vocabulary=_vocabulary(files),
        criterion_examples=CRITERION_EXAMPLES.get(shape, CRITERION_EXAMPLES["library"]),
        notes=(f"shape: {shape} ({shape_evidence})",),
    )


def _generic(reason: str) -> Template:
    """The fallback, which says it is one.

    Guessing produces a template that reads as authoritative and is wrong. This reads as
    generic and is merely unhelpful, which is recoverable by whoever reads it.
    """
    return Template(
        languages=(),
        sections=(
            Section(
                name="Intent",
                prompt="What this behaviour is for.",
                support=Support.REQUIRED,
                evidence=reason,
            ),
            Section(
                name="Acceptance",
                prompt="Individually checkable criteria.",
                support=Support.REQUIRED,
                evidence=reason,
            ),
            Section(
                name="Implements",
                prompt="Paths or symbols this governs.",
                support=Support.REQUIRED,
                evidence=reason,
            ),
            Section(
                name="Verifies",
                prompt="The test that fails without this change.",
                support=Support.OPTIONAL,
                evidence=reason,
            ),
        ),
        vocabulary=(),
        criterion_examples=CRITERION_EXAMPLES["library"],
        generic=True,
        notes=(reason,),
    )


def _files(root: Path) -> list[Path]:
    """Source files this repository actually contains.

    Dot-directories are skipped wholesale, not just the ones named in `SKIP`. Two reasons,
    and the second is the one that was found the hard way: caches and virtualenvs are
    somebody else's code, and *anything under a dot-directory is generally ignored by git* --
    scratch notes, research material, downloaded references. A template derived from those
    describes a working directory rather than a repository, and the vocabulary it mines is
    the vocabulary of whatever was last read rather than of the code.
    """
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix
        and not any(
            part in SKIP or part.startswith(".") for part in path.relative_to(root).parts
        )
    ]


#: Markers that identify what a repository *is*, checked in order. First match wins, and the
#: order matters: a web service with a CLI entry point is a web service.
SHAPE_MARKERS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("http", ("fastapi", "flask", "django", "express", "axum", "gin-gonic"), "a web framework"),
    ("ui", ("react", "vue", "svelte", "@angular"), "a front-end framework"),
    ("data", ("pandas", "polars", "dbt", "airflow", "pyspark"), "a data-processing library"),
    ("cli", ("typer", "click", "argparse", "cobra", "clap"), "a command-line framework"),
)

MANIFESTS = (
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "requirements.txt",
    "pom.xml",
    "Gemfile",
)


def _shape(root: Path, files: list[Path]) -> tuple[str, str]:
    """What kind of thing this repository is, from its declared dependencies.

    Dependencies rather than file names: a repository with `api.py` may be anything, and a
    repository that depends on a web framework is serving something. Manifests are read
    because they are what the authors declared, which is a stronger signal than what a
    directory happens to be called.
    """
    declared = ""
    for name in MANIFESTS:
        manifest = root / name
        if manifest.is_file():
            try:
                declared += manifest.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:  # pragma: no cover - unreadable manifest
                continue
    for shape, markers, description in SHAPE_MARKERS:
        for marker in markers:
            if marker in declared:
                return shape, f"the manifest declares {description} ({marker})"
    if any("cmd" in path.parts or path.name in {"cli.py", "main.go"} for path in files):
        return "cli", "a command entry point"
    return "library", "no framework declared in any manifest"


def _tests(files: list[Path]) -> tuple[bool, str]:
    """Whether this repository has tests a unit could anchor to."""
    found = [
        path
        for path in files
        if "test" in path.name.lower() or "test" in {p.lower() for p in path.parts}
    ]
    if not found:
        return False, "no test files were found, so a unit here cannot cite one"
    return True, f"{len(found)} test file(s)"


def _typed(files: list[Path]) -> tuple[bool, str]:
    """Whether signatures are something a unit can meaningfully anchor to.

    Sampled rather than parsed. A full parse of every file to decide the shape of a
    *template* costs more than the template is worth, and the question here is only whether
    type annotations are the repository's habit.
    """
    sample = [p for p in files if p.suffix in {".py", ".ts", ".tsx", ".go", ".rs"}][:200]
    if not sample:
        return False, "no files in a language this checks for annotations"
    annotated = 0
    for path in sample:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover
            continue
        if re.search(r"->\s*[\w\[\]|.]+\s*[:{]|:\s*[A-Z]\w+\s*[,)=]", text):
            annotated += 1
    if annotated / len(sample) >= 0.5:
        return True, f"{annotated} of {len(sample)} sampled files carry annotations"
    return False, f"only {annotated} of {len(sample)} sampled files carry annotations"


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,}")


def _vocabulary(files: list[Path], *, limit: int = 15) -> tuple[str, ...]:
    """Terms this repository uses that are not the language's own furniture.

    Frequency alone produces `self`, `data`, `result` -- the language of programming rather
    than the language of this repository. Only identifiers that survive the noise list are
    worth putting in front of an agent, because the vocabulary's purpose is to let it name
    things the way the codebase already does.

    Source files only, not every file with a suffix. Mining this repository's own docs
    taught it `clip`, `path` and `terminal`, from the `clipPath` elements of checked-in SVG
    screenshots: the vocabulary of the image format, offered to agents as the language of
    the codebase.
    """
    source = [path for path in files if path.suffix in LANGUAGES]
    counts: Counter[str] = Counter()
    for path in source[:400]:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover
            continue
        for match in _WORD.findall(text):
            for part in re.split(r"_|(?<=[a-z])(?=[A-Z])", match):
                word = part.lower()
                if len(word) > 3 and word not in NOISE:
                    counts[word] += 1
    return tuple(word for word, _ in counts.most_common(limit))
