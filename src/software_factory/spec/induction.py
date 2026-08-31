"""Bootstrapping a Living Spec from an existing codebase (PRD FR-5.12).

Most repositories have no spec. Without an on-ramp the whole subsystem is unusable, so
induction is a first-class feature rather than a nicety.

The ordering principle: **a test is an executable criterion**. Units derived from tests
arrive already anchored and already verified, which makes induction immediately valuable
rather than producing a pile of unverified prose. Units derived from signatures and
documentation arrive at lower confidence, and all of them arrive as `draft` — an inducted
unit gates nothing until a person promotes it (living-spec.md S-21).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from software_factory.spec.units import (
    CodeAnchor,
    Criterion,
    SpecUnit,
    TestAnchor,
    UnitStatus,
    digest_text,
)

SKIP_PARTS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".factory"}
TEST_FILE = re.compile(r"(?:^|/)(?:tests?/|test_|.*_test\.py$)")


class Source(str):
    """Where an inducted unit came from. Used for the confidence it starts at."""


TEST = Source("test")
SIGNATURE = Source("signature")
DOCSTRING = Source("docstring")

#: How much to trust a unit by where it came from. A test is an executable claim; a
#: docstring is someone's intention at the time of writing.
CONFIDENCE: dict[Source, float] = {TEST: 0.8, SIGNATURE: 0.5, DOCSTRING: 0.3}


@dataclass(slots=True)
class InductionReport:
    """What induction proposed, and what it could not read."""

    units: list[SpecUnit] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    scanned: int = 0

    def by_source(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for unit in self.units:
            origin = unit.provenance[0].split(":")[0] if unit.provenance else "unknown"
            counts[origin] = counts.get(origin, 0) + 1
        return counts

    def as_dict(self) -> dict[str, object]:
        return {
            "proposed": len(self.units),
            "scanned": self.scanned,
            "bySource": self.by_source(),
            "skipped": [{"path": path, "reason": reason} for path, reason in self.skipped],
            "units": [
                {
                    "id": unit.id,
                    "title": unit.title,
                    "status": unit.status.value,
                    "confidence": unit.confidence,
                    "intent": unit.intent,
                    "anchors": [anchor.locator() for anchor in unit.implements],
                    "verifies": [anchor.locator() for anchor in unit.verifies],
                    "criteria": len(unit.acceptance),
                }
                for unit in self.units
            ],
        }


def induct(
    root: Path,
    *,
    prefix: str = "",
    id_prefix: str = "SPEC",
    start: int = 1,
    limit: int = 200,
) -> InductionReport:
    """Propose draft spec units from a repository.

    Never writes: the caller decides what to do with the proposal, which is the same
    rule every other proposing subsystem here follows.
    """
    report = InductionReport()
    counter = start

    for path in _sources(root, prefix):
        report.scanned += 1
        if len(report.units) >= limit:
            break
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            report.skipped.append((str(path.relative_to(root)), f"unreadable: {exc}"))
            continue

        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            report.skipped.append((str(path.relative_to(root)), f"does not parse: {exc.msg}"))
            continue

        relative = str(path.relative_to(root))
        produced = (
            _from_tests(tree, relative, id_prefix, counter)
            if _is_test_file(relative)
            else _from_module(tree, text, relative, id_prefix, counter)
        )
        report.units.extend(produced)
        counter += len(produced)

    # The per-file check above stops scanning; this makes `limit` exact. A single file
    # can yield many units, so without it a limit of 1 can return several.
    if len(report.units) > limit:
        report.units = report.units[:limit]
    return report


def _sources(root: Path, prefix: str) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if not any(part in SKIP_PARTS for part in path.relative_to(root).parts)
        and (not prefix or str(path.relative_to(root)).startswith(prefix))
    )


def _is_test_file(relative: str) -> bool:
    return bool(TEST_FILE.search(relative))


def _from_tests(tree: ast.Module, relative: str, id_prefix: str, start: int) -> list[SpecUnit]:
    """One unit per test module, with each test as a criterion.

    Grouping by module rather than by test is deliberate: a unit per test would produce
    hundreds of units nobody will read, and the module is usually the behaviour boundary
    a person actually thinks in.
    """
    tests = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test")
    ]
    if not tests:
        return []

    criteria = tuple(
        Criterion(
            id=node.name,
            statement=_statement_from_test(node),
            verified_by=(TestAnchor(path=relative, test_id=node.name),),
            # A test in the repository has been observed passing; whether it has ever
            # been observed *failing* is a different question this cannot answer.
            observed_failing=False,
        )
        for node in tests
    )

    module_doc = ast.get_docstring(tree) or ""
    intent = (
        module_doc.strip().splitlines()[0]
        if module_doc.strip()
        else f"The behaviour covered by {relative}."
    )

    return [
        SpecUnit(
            id=f"{id_prefix}-{start}",
            title=f"Behaviour covered by {Path(relative).stem}",
            status=UnitStatus.DRAFT,
            intent=intent,
            acceptance=criteria,
            implements=(),
            verifies=tuple(TestAnchor(path=relative, test_id=node.name) for node in tests),
            provenance=(f"test:{relative}",),
            confidence=CONFIDENCE[TEST],
        )
    ]


def _from_module(
    tree: ast.Module, text: str, relative: str, id_prefix: str, start: int
) -> list[SpecUnit]:
    """Units from public functions and classes, anchored and content-addressed."""
    units: list[SpecUnit] = []
    lines = text.splitlines()

    for index, node in enumerate(_public_definitions(tree)):
        anchor_text = "\n".join(lines[node.lineno - 1 : (node.end_lineno or node.lineno)])
        doc = ast.get_docstring(node) if isinstance(node, ast.AST) else None
        source = DOCSTRING if doc else SIGNATURE
        intent = (
            doc.strip().splitlines()[0]
            if doc and doc.strip()
            else f"{relative}:{node.name} exists and behaves as its signature implies."
        )

        units.append(
            SpecUnit(
                id=f"{id_prefix}-{start + index}",
                title=f"{Path(relative).stem}.{node.name}",
                status=UnitStatus.DRAFT,
                intent=intent,
                implements=(
                    CodeAnchor(
                        path=relative,
                        symbol=node.name,
                        start_line=node.lineno,
                        end_line=node.end_lineno,
                        digest=digest_text(anchor_text),
                    ),
                ),
                provenance=(f"{source}:{relative}:{node.name}",),
                confidence=CONFIDENCE[source],
            )
        )
    return units


def _public_definitions(
    tree: ast.Module,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]:
    """Top-level public functions and classes. Private names are implementation detail."""
    return [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and not node.name.startswith("_")
    ]


_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def _statement_from_test(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Turn a test's name or docstring into a criterion statement.

    A docstring wins when there is one: the author already said what the test means, and
    a name-derived sentence is a worse version of it.
    """
    doc = ast.get_docstring(node)
    if doc and doc.strip():
        return doc.strip().splitlines()[0]
    words = node.name.removeprefix("test_").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else node.name
