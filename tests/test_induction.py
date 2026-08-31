"""Spec induction: the on-ramp for a repository that has no spec.

The ordering principle under test is that a test is an executable criterion, so
test-derived units arrive already verified and at the highest confidence, while
docstring-derived ones arrive lowest. Everything arrives as `draft` and gates nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from software_factory.spec import UnitStatus
from software_factory.spec.induction import induct

MODULE = '''\
"""The CSV importer."""


def strip_bom(text: str) -> str:
    """Remove a byte-order mark from the start of a header line."""
    return text.lstrip("\\ufeff")


def _internal(value: str) -> str:
    return value


class Importer:
    """Reads delimited files."""

    def read(self) -> None:
        ...
'''

TESTS = '''\
"""Behaviour of the CSV importer."""


def test_bom_is_stripped_from_headers():
    """A header beginning with U+FEFF parses as the first column name."""
    assert True


def test_plain_headers_are_unchanged():
    assert True


def helper():
    return 1
'''


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "importer.py").write_text(MODULE, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_importer.py").write_text(TESTS, encoding="utf-8")
    return tmp_path


def units_by_id(report) -> dict[str, object]:
    return {unit.id: unit for unit in report.units}


# ------------------------------------------------------------------------ from tests


def test_a_test_module_becomes_one_unit_with_a_criterion_per_test(repo: Path) -> None:
    """One unit per test would produce hundreds nobody reads; the module is the boundary."""
    report = induct(repo)

    test_units = [u for u in report.units if u.provenance and u.provenance[0].startswith("test:")]
    assert len(test_units) == 1
    assert len(test_units[0].acceptance) == 2


def test_only_test_functions_become_criteria(repo: Path) -> None:
    report = induct(repo)

    unit = next(u for u in report.units if u.provenance[0].startswith("test:"))
    assert {c.id for c in unit.acceptance} == {
        "test_bom_is_stripped_from_headers",
        "test_plain_headers_are_unchanged",
    }


def test_a_docstring_becomes_the_criterion_statement(repo: Path) -> None:
    """The author already said what the test means; a name-derived sentence is worse."""
    report = induct(repo)

    unit = next(u for u in report.units if u.provenance[0].startswith("test:"))
    documented = next(c for c in unit.acceptance if c.id == "test_bom_is_stripped_from_headers")
    assert documented.statement.startswith("A header beginning with")


def test_a_test_name_becomes_a_readable_statement_when_undocumented(repo: Path) -> None:
    report = induct(repo)

    unit = next(u for u in report.units if u.provenance[0].startswith("test:"))
    derived = next(c for c in unit.acceptance if c.id == "test_plain_headers_are_unchanged")
    assert derived.statement == "Plain headers are unchanged"


def test_test_derived_units_carry_their_test_anchors(repo: Path) -> None:
    report = induct(repo)

    unit = next(u for u in report.units if u.provenance[0].startswith("test:"))
    assert {a.test_id for a in unit.verifies} == {
        "test_bom_is_stripped_from_headers",
        "test_plain_headers_are_unchanged",
    }


def test_test_derived_units_have_the_highest_confidence(repo: Path) -> None:
    report = induct(repo)

    by_source = {u.provenance[0].split(":")[0]: u.confidence for u in report.units if u.provenance}
    assert by_source["test"] > by_source.get("docstring", 0)
    assert by_source["test"] > by_source.get("signature", 0)


# ----------------------------------------------------------------------- from modules


def test_public_definitions_become_anchored_units(repo: Path) -> None:
    report = induct(repo)

    strip = next(u for u in report.units if u.title.endswith("strip_bom"))
    anchor = strip.implements[0]
    assert anchor.path == "importer.py"
    assert anchor.symbol == "strip_bom"
    assert anchor.digest


def test_private_definitions_are_skipped(repo: Path) -> None:
    """Private names are implementation detail, not intent."""
    report = induct(repo)

    assert not any(u.title.endswith("_internal") for u in report.units)


def test_a_docstring_becomes_the_units_intent(repo: Path) -> None:
    report = induct(repo)

    strip = next(u for u in report.units if u.title.endswith("strip_bom"))
    assert strip.intent.startswith("Remove a byte-order mark")


def test_an_undocumented_definition_falls_back_to_its_signature(repo: Path) -> None:
    (repo / "bare.py").write_text("def compute(a, b):\n    return a + b\n", encoding="utf-8")

    report = induct(repo)

    compute = next(u for u in report.units if u.title.endswith("compute"))
    assert compute.provenance[0].startswith("signature:")
    assert "behaves as its signature implies" in compute.intent


# ---------------------------------------------------------------------------- policy


def test_everything_arrives_as_draft(repo: Path) -> None:
    """An inducted unit gates nothing until a person promotes it."""
    report = induct(repo)

    assert all(unit.status is UnitStatus.DRAFT for unit in report.units)


def test_induction_writes_nothing(repo: Path) -> None:
    before = {path: path.read_bytes() for path in repo.rglob("*.py")}

    induct(repo)

    assert {path: path.read_bytes() for path in repo.rglob("*.py")} == before


def test_ids_are_unique_and_sequential(repo: Path) -> None:
    report = induct(repo, id_prefix="PAY", start=10)

    ids = [unit.id for unit in report.units]
    assert len(ids) == len(set(ids))
    assert ids[0] == "PAY-10"


def test_a_prefix_scopes_induction_for_incremental_onboarding(repo: Path) -> None:
    """A large repository is onboarded module by module as work touches it."""
    report = induct(repo, prefix="tests/")

    assert report.scanned == 1
    assert all(u.provenance[0].startswith("test:") for u in report.units)


def test_the_limit_is_respected(repo: Path) -> None:
    report = induct(repo, limit=1)

    assert len(report.units) <= 1


# -------------------------------------------------------------------------- failures


def test_an_unparseable_file_is_skipped_with_a_reason(repo: Path) -> None:
    """One broken file must not stop the onboarding of a whole repository."""
    (repo / "broken.py").write_text("def oops(\n", encoding="utf-8")

    report = induct(repo)

    assert any("does not parse" in reason for _path, reason in report.skipped)
    assert report.units


def test_an_empty_repository_proposes_nothing(tmp_path: Path) -> None:
    report = induct(tmp_path)

    assert report.units == []
    assert report.scanned == 0


def test_the_report_summarises_by_source(repo: Path) -> None:
    report = induct(repo)

    counts = report.by_source()
    assert counts["test"] == 1
    assert counts["docstring"] >= 1
