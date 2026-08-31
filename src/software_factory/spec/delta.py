"""Spec deltas: the only way intent changes (PRD FR-5.3, living-spec.md §4).

No agent writes ``specs/`` directly. Every change arrives as a delta that can be
reviewed on its own terms, *before* the code implementing it exists -- intent is far
cheaper to correct than code.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from software_factory.spec.units import CodeAnchor, SpecUnit, TrustClass, UnitStatus


class BehaviourChange(enum.StrEnum):
    NONE = "none"
    ADDITIVE = "additive"
    BREAKING = "breaking"


class ChangeKind(enum.StrEnum):
    ADD = "add"
    MODIFY = "modify"
    SUPERSEDE = "supersede"
    RETIRE = "retire"
    REANCHOR = "reanchor"


@dataclass(frozen=True, slots=True)
class Change:
    """One proposed change to the spec."""

    kind: ChangeKind
    unit_id: str
    unit: SpecUnit | None = None
    anchors: tuple[CodeAnchor, ...] = ()
    reason: str = ""
    replaces_criterion: str | None = None
    """When a criterion is removed, what replaces it. Absent removal is gated (S-13)."""


@dataclass(frozen=True, slots=True)
class ImpactReport:
    units_affected: tuple[str, ...]
    criteria_added: tuple[str, ...]
    criteria_removed: tuple[str, ...]
    behaviour_change: BehaviourChange
    tests_required: tuple[str, ...]


@dataclass(slots=True)
class SpecDelta:
    """A reviewable proposal to change the spec."""

    id: str
    work_item: str
    changes: list[Change] = field(default_factory=list)
    rationale: str = ""
    provenance: tuple[str, ...] = ()
    trust: TrustClass = TrustClass.INTERNAL

    def touched(self) -> set[str]:
        return {change.unit_id for change in self.changes}


@dataclass(frozen=True, slots=True)
class DeltaProblem:
    """Why a delta cannot be applied. ``remediation`` states the next action."""

    code: str
    message: str
    remediation: str


def validate_delta(delta: SpecDelta, units: dict[str, SpecUnit]) -> list[DeltaProblem]:
    """Check a delta against the current spec.

    Returns every problem rather than the first: a reviewer should see the whole shape
    of what is wrong in one pass.
    """
    problems: list[DeltaProblem] = []

    for change in delta.changes:
        existing = units.get(change.unit_id)

        if change.kind is ChangeKind.ADD:
            if existing is not None:
                problems.append(
                    DeltaProblem(
                        code="delta.duplicate_id",
                        message=f"{change.unit_id} already exists; ids are never reused",
                        remediation="Use `modify` or `supersede`, or choose an unused id.",
                    )
                )
            if change.unit is None:
                problems.append(
                    DeltaProblem(
                        code="delta.missing_unit",
                        message=f"add of {change.unit_id} carries no unit body",
                        remediation="Include the unit being added.",
                    )
                )
            continue

        if existing is None:
            problems.append(
                DeltaProblem(
                    code="delta.unknown_unit",
                    message=f"{change.kind.value} of unknown unit {change.unit_id}",
                    remediation="Reference a unit that exists, or use `add`.",
                )
            )
            continue

        if change.kind is ChangeKind.RETIRE and not change.reason:
            problems.append(
                DeltaProblem(
                    code="delta.retire_without_reason",
                    message=f"retiring {change.unit_id} without a reason",
                    remediation="State why this intent no longer applies.",
                )
            )

        if change.kind in (ChangeKind.MODIFY, ChangeKind.SUPERSEDE) and change.unit is not None:
            problems.extend(_check_criterion_removal(change, existing))

    if delta.trust is TrustClass.UNTRUSTED:
        problems.append(
            DeltaProblem(
                code="delta.untrusted",
                message="this delta's provenance is untrusted",
                remediation=(
                    "Intent sourced only from untrusted input (issue text, comments, model "
                    "output derived from them) needs a human decision before it becomes spec."
                ),
            )
        )

    return problems


def _check_criterion_removal(change: Change, existing: SpecUnit) -> list[DeltaProblem]:
    """Silent criterion removal is the easiest way to make a failing system look healthy.

    So removal is allowed, but never silently: a delta must say what replaces the
    criterion or why it no longer applies (living-spec.md S-13).
    """
    assert change.unit is not None
    before = {c.id for c in existing.acceptance}
    after = {c.id for c in change.unit.acceptance}
    removed = sorted(before - after)
    if not removed:
        return []
    if change.replaces_criterion or change.reason:
        return []
    return [
        DeltaProblem(
            code="delta.criterion_removed_silently",
            message=(
                f"{change.unit_id} drops acceptance criteria ({', '.join(removed)}) with no "
                "replacement and no reason"
            ),
            remediation=(
                "Name the criterion that replaces it, or state why the requirement no longer "
                "applies. Removing a failing criterion is not the same as meeting it."
            ),
        )
    ]


def impact_of(delta: SpecDelta, units: dict[str, SpecUnit]) -> ImpactReport:
    """Summarise what a delta does, for a reviewer deciding how carefully to read it."""
    added: list[str] = []
    removed: list[str] = []
    tests_required: list[str] = []
    breaking = False
    additive = False

    for change in delta.changes:
        existing = units.get(change.unit_id)
        proposed = change.unit

        if change.kind is ChangeKind.RETIRE:
            breaking = True
            continue
        if change.kind is ChangeKind.REANCHOR:
            continue

        before = {c.id for c in existing.acceptance} if existing else set()
        after = {c.id for c in proposed.acceptance} if proposed else before

        new_ids = sorted(after - before)
        gone_ids = sorted(before - after)
        added.extend(f"{change.unit_id}:{c}" for c in new_ids)
        removed.extend(f"{change.unit_id}:{c}" for c in gone_ids)

        if gone_ids:
            breaking = True
        if new_ids:
            additive = True

        if proposed:
            for criterion in proposed.acceptance:
                if criterion.id in new_ids and not criterion.verified_by:
                    tests_required.append(f"{change.unit_id}:{criterion.id}")

        # An existing active unit whose intent text changes is a behaviour change, not a
        # wording change, unless the delta says otherwise.
        if (
            existing is not None
            and proposed is not None
            and existing.status is UnitStatus.ACTIVE
            and existing.intent.strip() != proposed.intent.strip()
        ):
            breaking = True

    if breaking:
        behaviour = BehaviourChange.BREAKING
    elif additive:
        behaviour = BehaviourChange.ADDITIVE
    else:
        behaviour = BehaviourChange.NONE

    return ImpactReport(
        units_affected=tuple(sorted(delta.touched())),
        criteria_added=tuple(added),
        criteria_removed=tuple(removed),
        behaviour_change=behaviour,
        tests_required=tuple(tests_required),
    )


def apply_delta(delta: SpecDelta, units: dict[str, SpecUnit]) -> dict[str, SpecUnit]:
    """Apply a delta, returning a new unit map. Atomic: validate before calling.

    Every write goes back through ``SpecUnit`` validation rather than ``model_copy``.
    Bypassing the validators let a REANCHOR to an empty tuple leave an ACTIVE unit with
    no anchors, which ``evaluate()`` then reports as ``AGREED`` -- permanently satisfying
    the spec gate for that unit. Units are keyed by their own id, so a SUPERSEDE cannot
    write a successor under its predecessor's key.

    The input map is never mutated, so a caller that discovers a problem mid-apply still
    has the original (living-spec.md S-14).
    """
    result = dict(units)
    for change in delta.changes:
        match change.kind:
            case ChangeKind.ADD | ChangeKind.MODIFY:
                if change.unit is not None:
                    result[change.unit.id] = _revalidated(change.unit)
            case ChangeKind.SUPERSEDE:
                if change.unit is not None:
                    old = result.get(change.unit_id)
                    if old is not None:
                        result[change.unit_id] = _revalidated(old, status=UnitStatus.DEPRECATED)
                    result[change.unit.id] = _revalidated(change.unit)
            case ChangeKind.RETIRE:
                old = result.get(change.unit_id)
                if old is not None:
                    result[change.unit_id] = _revalidated(old, status=UnitStatus.RETIRED)
            case ChangeKind.REANCHOR:
                old = result.get(change.unit_id)
                if old is not None:
                    result[change.unit_id] = _revalidated(old, implements=change.anchors)
    return result


def _revalidated(unit: SpecUnit, **updates: object) -> SpecUnit:
    """Apply updates through full validation.

    ``model_copy`` skips validators, which is how an active unit ended up anchorless.
    """
    data = unit.model_dump()
    data.update(updates)
    return SpecUnit.model_validate(data)
