"""Living Spec: mechanical drift detection, agreement states, and delta gating.

Every assertion here holds without a model call. That is the point of the design: a
spec that needs an LLM to decide whether it is satisfied cannot block a build.
"""

from __future__ import annotations

import pytest

from software_factory.spec import (
    Agreement,
    BehaviourChange,
    Change,
    ChangeKind,
    CodeAnchor,
    Criterion,
    SpecDelta,
    SpecStore,
    SpecUnit,
    TestAnchor,
    TrustClass,
    UnitStatus,
    apply_delta,
    criterion_is_checkable,
    derived_trust,
    digest_text,
    evaluate,
    find_conflicts,
    impact_of,
    validate_delta,
)

SOURCE = "def strip_bom(text):\n    return text.lstrip('\\ufeff')\n"


def unit(
    unit_id: str = "PAY-1",
    *,
    intent: str = "The importer must strip a byte-order mark from CSV headers.",
    status: UnitStatus = UnitStatus.ACTIVE,
    digest: str | None = None,
    verifies: tuple[TestAnchor, ...] = (
        TestAnchor(path="tests/test_import.py", test_id="test_bom"),
    ),
    acceptance: tuple[Criterion, ...] = (),
) -> SpecUnit:
    return SpecUnit(
        id=unit_id,
        title="BOM handling",
        status=status,
        intent=intent,
        implements=(CodeAnchor(path="src/importers/csv.py", symbol="strip_bom", digest=digest),),
        verifies=verifies,
        acceptance=acceptance,
    )


def resolver(text: str | None):
    return lambda _path, _symbol: text


def outcomes(**results: bool):
    return lambda locator: results.get(locator)


# --------------------------------------------------------------------------- digests


def test_digest_ignores_reformatting() -> None:
    """A reformat is not a behaviour change; treating it as drift makes drift useless."""
    reformatted = "def strip_bom(text):\n\n        return   text.lstrip('\\ufeff')\n"

    assert digest_text(SOURCE) == digest_text(reformatted)


def test_digest_changes_when_code_changes() -> None:
    changed = SOURCE.replace("lstrip", "rstrip")

    assert digest_text(SOURCE) != digest_text(changed)


# ----------------------------------------------------------------------- agreement


def test_unchanged_anchor_with_passing_test_agrees() -> None:
    result = evaluate(
        unit(digest=digest_text(SOURCE)),
        resolve=resolver(SOURCE),
        outcome=outcomes(**{"tests/test_import.py::test_bom": True}),
    )

    assert result.state is Agreement.AGREED
    assert not result.blocks_build


def test_failing_test_contradicts_and_blocks_build() -> None:
    result = evaluate(
        unit(digest=digest_text(SOURCE)),
        resolve=resolver(SOURCE),
        outcome=outcomes(**{"tests/test_import.py::test_bom": False}),
    )

    assert result.state is Agreement.CONTRADICTED
    assert result.blocks_build
    assert "test_bom" in result.reason


def test_drift_with_passing_tests_is_a_reanchor_not_a_block() -> None:
    """Refactors must not generate false alarms, or the gate gets switched off."""
    result = evaluate(
        unit(digest=digest_text(SOURCE)),
        resolve=resolver(SOURCE.replace("text", "value")),
        outcome=outcomes(**{"tests/test_import.py::test_bom": True}),
    )

    assert result.state is Agreement.DRIFTED
    assert not result.blocks_build
    assert result.drifted_anchors


def test_drift_with_failing_tests_contradicts() -> None:
    result = evaluate(
        unit(digest=digest_text(SOURCE)),
        resolve=resolver(SOURCE.replace("lstrip", "rstrip")),
        outcome=outcomes(**{"tests/test_import.py::test_bom": False}),
    )

    assert result.state is Agreement.CONTRADICTED


def test_unresolvable_anchor_is_orphaned() -> None:
    result = evaluate(unit(), resolve=resolver(None), outcome=outcomes())

    assert result.state is Agreement.ORPHANED
    assert not result.blocks_build


def test_unit_without_test_anchors_is_unverified() -> None:
    result = evaluate(
        unit(digest=digest_text(SOURCE), verifies=()),
        resolve=resolver(SOURCE),
        outcome=outcomes(),
    )

    assert result.state is Agreement.UNVERIFIED
    assert not result.blocks_build


def test_unknown_test_outcome_is_unverified_not_agreed() -> None:
    """A test we have not run is neither a test that failed nor a test that passed.

    This test previously asserted AGREED, which is the bug M9 names: the outcome callable
    answers pass / fail / unknown, and folding unknown into "not failing" let a unit whose
    tests have never executed report the same state as one whose tests passed.
    """
    result = evaluate(
        unit(digest=digest_text(SOURCE)),
        resolve=resolver(SOURCE),
        outcome=outcomes(),
    )

    assert result.state is Agreement.UNVERIFIED
    assert not result.blocks_build
    assert "no recorded outcome" in result.reason


# ------------------------------------------------------------------ cross-unit conflict


def test_two_units_disagreeing_on_one_anchor_mark_both() -> None:
    """Recency does not imply correctness, so the newer unit is not assumed right."""
    left = unit("PAY-1", intent="The importer must strip a byte-order mark from CSV headers.")
    right = unit("PAY-2", intent="The importer must not strip a byte-order mark from CSV headers.")

    conflicts = find_conflicts([left, right])

    assert conflicts["PAY-1"] == ("PAY-2",)
    assert conflicts["PAY-2"] == ("PAY-1",)


def test_conflicting_unit_is_contradicted_even_when_tests_pass() -> None:
    left = unit("PAY-1", intent="The importer must strip a byte-order mark from CSV headers.")
    right = unit("PAY-2", intent="The importer must not strip a byte-order mark from CSV headers.")
    conflicts = find_conflicts([left, right])

    result = evaluate(
        left,
        resolve=resolver(SOURCE),
        outcome=outcomes(**{"tests/test_import.py::test_bom": True}),
        conflicts=conflicts,
    )

    assert result.state is Agreement.CONTRADICTED
    assert result.conflicting_units == ("PAY-2",)


def test_units_sharing_an_anchor_without_disagreeing_do_not_conflict() -> None:
    """One file implements many behaviours; a shared anchor alone is not a conflict."""
    left = unit("PAY-1", intent="The importer must strip a byte-order mark from CSV headers.")
    right = unit("PAY-2", intent="The importer must report the row count after loading.")

    assert find_conflicts([left, right]) == {}


def test_inactive_units_are_not_considered_for_conflicts() -> None:
    left = unit("PAY-1", intent="The importer must strip a byte-order mark from CSV headers.")
    right = unit(
        "PAY-2",
        intent="The importer must not strip a byte-order mark from CSV headers.",
        status=UnitStatus.RETIRED,
    )

    assert find_conflicts([left, right]) == {}


# ------------------------------------------------------------------------- criteria


@pytest.mark.parametrize("statement", ["should be fast", "must be robust", "user-friendly"])
def test_vague_criteria_are_rejected(statement: str) -> None:
    assert not criterion_is_checkable(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "p99 latency stays under 200ms at 100 requests per second",
        "a header beginning with U+FEFF is parsed as the first column name",
    ],
)
def test_checkable_criteria_are_accepted(statement: str) -> None:
    assert criterion_is_checkable(statement)


def test_an_active_unit_must_be_anchored() -> None:
    """An active unit with no code anchor can never be checked, so it cannot gate."""
    with pytest.raises(ValueError, match="at least one `implements` anchor"):
        SpecUnit(
            id="PAY-9",
            title="Unanchored",
            status=UnitStatus.ACTIVE,
            intent="Something true about the system.",
        )


def test_a_draft_unit_need_not_be_anchored() -> None:
    """Induction produces drafts from prose; requiring anchors would block the on-ramp."""
    assert SpecUnit(id="PAY-9", title="Draft", intent="Something to pin down later.")


# ---------------------------------------------------------------------------- deltas


def test_delta_adding_an_existing_id_is_refused() -> None:
    """Ids are never reused, including after retirement."""
    units = {"PAY-1": unit()}
    delta = SpecDelta(
        id="D1",
        work_item="W1",
        changes=[Change(kind=ChangeKind.ADD, unit_id="PAY-1", unit=unit())],
    )

    codes = {p.code for p in validate_delta(delta, units)}

    assert "delta.duplicate_id" in codes


def test_delta_touching_an_unknown_unit_is_refused() -> None:
    delta = SpecDelta(
        id="D1",
        work_item="W1",
        changes=[Change(kind=ChangeKind.RETIRE, unit_id="PAY-404", reason="gone")],
    )

    codes = {p.code for p in validate_delta(delta, {})}

    assert "delta.unknown_unit" in codes


def test_retiring_without_a_reason_is_refused() -> None:
    delta = SpecDelta(
        id="D1", work_item="W1", changes=[Change(kind=ChangeKind.RETIRE, unit_id="PAY-1")]
    )

    codes = {p.code for p in validate_delta(delta, {"PAY-1": unit()})}

    assert "delta.retire_without_reason" in codes


def test_silently_dropping_a_criterion_is_refused() -> None:
    """Deleting a failing criterion is not the same as meeting it."""
    before = unit(acceptance=(Criterion(id="C1", statement="BOM headers parse"),))
    after = unit(acceptance=())
    delta = SpecDelta(
        id="D1",
        work_item="W1",
        changes=[Change(kind=ChangeKind.MODIFY, unit_id="PAY-1", unit=after)],
    )

    codes = {p.code for p in validate_delta(delta, {"PAY-1": before})}

    assert "delta.criterion_removed_silently" in codes


def test_dropping_a_criterion_with_a_stated_reason_is_allowed() -> None:
    before = unit(acceptance=(Criterion(id="C1", statement="BOM headers parse"),))
    after = unit(acceptance=())
    delta = SpecDelta(
        id="D1",
        work_item="W1",
        changes=[
            Change(
                kind=ChangeKind.MODIFY,
                unit_id="PAY-1",
                unit=after,
                reason="The importer no longer accepts CSV; superseded by PAY-7.",
            )
        ],
    )

    assert validate_delta(delta, {"PAY-1": before}) == []


def test_an_untrusted_delta_needs_a_human() -> None:
    """Intent sourced only from issue text must not become spec on its own."""
    delta = SpecDelta(
        id="D1",
        work_item="W1",
        changes=[Change(kind=ChangeKind.ADD, unit_id="PAY-2", unit=unit("PAY-2"))],
        trust=TrustClass.UNTRUSTED,
    )

    codes = {p.code for p in validate_delta(delta, {})}

    assert "delta.untrusted" in codes


def test_impact_marks_criterion_removal_as_breaking() -> None:
    before = unit(acceptance=(Criterion(id="C1", statement="BOM headers parse"),))
    after = unit(acceptance=())
    delta = SpecDelta(
        id="D1",
        work_item="W1",
        changes=[Change(kind=ChangeKind.MODIFY, unit_id="PAY-1", unit=after, reason="superseded")],
    )

    report = impact_of(delta, {"PAY-1": before})

    assert report.behaviour_change is BehaviourChange.BREAKING
    assert report.criteria_removed == ("PAY-1:C1",)


def test_impact_reports_new_criteria_that_need_tests() -> None:
    before = unit()
    after = unit(acceptance=(Criterion(id="C1", statement="BOM headers parse"),))
    delta = SpecDelta(
        id="D1",
        work_item="W1",
        changes=[Change(kind=ChangeKind.MODIFY, unit_id="PAY-1", unit=after)],
    )

    report = impact_of(delta, {"PAY-1": before})

    assert report.behaviour_change is BehaviourChange.ADDITIVE
    assert report.tests_required == ("PAY-1:C1",)


def test_apply_does_not_mutate_the_input() -> None:
    """A caller that finds a problem mid-apply still has the original."""
    units = {"PAY-1": unit()}
    delta = SpecDelta(
        id="D1",
        work_item="W1",
        changes=[Change(kind=ChangeKind.RETIRE, unit_id="PAY-1", reason="obsolete")],
    )

    applied = apply_delta(delta, units)

    assert units["PAY-1"].status is UnitStatus.ACTIVE
    assert applied["PAY-1"].status is UnitStatus.RETIRED


def test_supersede_deprecates_the_old_unit_and_adds_the_new() -> None:
    units = {"PAY-1": unit()}
    successor = unit("PAY-2", intent="The importer must normalise all header encodings.")
    delta = SpecDelta(
        id="D1",
        work_item="W1",
        changes=[Change(kind=ChangeKind.SUPERSEDE, unit_id="PAY-1", unit=successor)],
    )

    applied = apply_delta(delta, units)

    assert applied["PAY-1"].status is UnitStatus.DEPRECATED
    assert applied["PAY-2"].id == "PAY-2"


def test_reanchor_replaces_anchors_without_touching_intent() -> None:
    units = {"PAY-1": unit()}
    moved = CodeAnchor(path="src/importers/delimited.py", symbol="strip_bom")
    delta = SpecDelta(
        id="D1",
        work_item="W1",
        changes=[Change(kind=ChangeKind.REANCHOR, unit_id="PAY-1", anchors=(moved,))],
    )

    applied = apply_delta(delta, units)

    assert applied["PAY-1"].implements == (moved,)
    assert applied["PAY-1"].intent == units["PAY-1"].intent


# ----------------------------------------------------------------------------- store


def test_slice_returns_units_governing_the_change_surface() -> None:
    store = SpecStore()
    store.add(unit("PAY-1"))
    store.add(
        SpecUnit(
            id="PAY-2",
            title="Elsewhere",
            status=UnitStatus.ACTIVE,
            intent="Reports render totals in the account currency.",
            implements=(CodeAnchor(path="src/reports/render.py"),),
        )
    )

    found = store.slice_for({"src/importers/csv.py"})

    assert [u.id for u in found] == ["PAY-1"]


def test_slice_matches_a_directory_anchor_by_prefix() -> None:
    store = SpecStore()
    store.add(
        SpecUnit(
            id="PAY-3",
            title="Importers",
            status=UnitStatus.ACTIVE,
            intent="Every importer reports the row count after loading.",
            implements=(CodeAnchor(path="src/importers"),),
        )
    )

    assert [u.id for u in store.slice_for({"src/importers/csv.py"})] == ["PAY-3"]


def test_duplicate_unit_ids_are_refused() -> None:
    store = SpecStore()
    store.add(unit("PAY-1"))

    with pytest.raises(ValueError, match="duplicate spec unit id"):
        store.add(unit("PAY-1"))


# ----------------------------------------------------------------------------- trust


def test_trust_is_monotone_downward() -> None:
    assert derived_trust(TrustClass.VERIFIED, TrustClass.UNTRUSTED) is TrustClass.UNTRUSTED
    assert derived_trust(TrustClass.VERIFIED, TrustClass.OPERATOR) is TrustClass.OPERATOR
    assert derived_trust(TrustClass.VERIFIED) is TrustClass.VERIFIED
