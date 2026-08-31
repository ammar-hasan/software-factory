"""Computing the spec/code/test agreement state (docs/harness/living-spec.md §3).

Everything here is deterministic. The only inputs are: does an anchor resolve, has its
digest changed, and did its tests pass. That is what lets the spec block a build without
anyone having to trust a model's reading of the code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from software_factory.spec.units import (
    Agreement,
    AgreementResult,
    SpecUnit,
    UnitStatus,
    digest_text,
)

#: Resolve an anchor to its current source text, or ``None`` when it no longer resolves.
AnchorResolver = Callable[[str, str | None], str | None]

#: Look up the outcome of a test anchor: ``True`` passed, ``False`` failed, ``None`` unknown.
TestOutcome = Callable[[str], bool | None]


@dataclass(frozen=True, slots=True)
class AnchorState:
    locator: str
    resolved: bool
    drifted: bool


def evaluate(
    unit: SpecUnit,
    *,
    resolve: AnchorResolver,
    outcome: TestOutcome,
    conflicts: Mapping[str, tuple[str, ...]] | None = None,
) -> AgreementResult:
    """Compute one unit's agreement state.

    Order matters and encodes the policy: an unresolvable anchor is *orphaned* (the code
    moved, which is a different problem from the code being wrong); a failing test is
    *contradicted* whether or not anything drifted; drift with passing tests is a
    re-anchor proposal rather than a block.
    """
    conflicting = tuple((conflicts or {}).get(unit.id, ()))
    if conflicting:
        return AgreementResult(
            unit_id=unit.id,
            state=Agreement.CONTRADICTED,
            reason=(
                f"{unit.id} and {', '.join(conflicting)} mandate incompatible behaviour on a "
                "shared anchor; both are marked, because recency does not imply correctness"
            ),
            conflicting_units=conflicting,
        )

    states: list[AnchorState] = []
    for anchor in unit.implements:
        text = resolve(anchor.path, anchor.symbol)
        if text is None:
            states.append(AnchorState(anchor.locator(), resolved=False, drifted=False))
            continue
        is_drifted = anchor.digest is not None and digest_text(text) != anchor.digest
        states.append(AnchorState(anchor.locator(), resolved=True, drifted=is_drifted))

    unresolved = [s.locator for s in states if not s.resolved]
    if unresolved:
        return AgreementResult(
            unit_id=unit.id,
            state=Agreement.ORPHANED,
            reason=(
                f"{len(unresolved)} anchor(s) no longer resolve ({', '.join(unresolved[:3])}); "
                "the code moved or was removed"
            ),
            drifted_anchors=tuple(unresolved),
        )

    failing = [a.locator() for a in unit.verifies if outcome(a.locator()) is False]
    drifted = tuple(s.locator for s in states if s.drifted)

    if failing:
        return AgreementResult(
            unit_id=unit.id,
            state=Agreement.CONTRADICTED,
            reason=(
                f"{len(failing)} verifying test(s) failing ({', '.join(failing[:3])}); "
                "either the code or the intent is wrong"
            ),
            drifted_anchors=drifted,
            failing_tests=tuple(failing),
        )

    if not unit.verifies:
        return AgreementResult(
            unit_id=unit.id,
            state=Agreement.UNVERIFIED,
            reason="no test anchors; nothing checks this intent",
            drifted_anchors=drifted,
        )

    if drifted:
        return AgreementResult(
            unit_id=unit.id,
            state=Agreement.DRIFTED,
            reason=(
                f"{len(drifted)} anchor(s) changed with tests still passing "
                f"({', '.join(drifted[:3])}); behaviour appears preserved, so re-anchor"
            ),
            drifted_anchors=drifted,
        )

    return AgreementResult(
        unit_id=unit.id, state=Agreement.AGREED, reason="anchors and tests agree"
    )


def find_conflicts(units: list[SpecUnit]) -> dict[str, tuple[str, ...]]:
    """Find active units that mandate incompatible behaviour on a shared anchor.

    Two units sharing an anchor is normal -- one file implements many behaviours. The
    signal is a shared anchor *plus* directly negating intent, so this checks for one
    unit asserting what another forbids on the same locator.

    Both sides are marked, never just the newer one: when code is reverted the older
    unit is frequently the correct one (living-spec.md S-8).
    """
    by_anchor: dict[str, list[SpecUnit]] = {}
    for unit in units:
        if unit.status is not UnitStatus.ACTIVE:
            continue
        for anchor in unit.implements:
            by_anchor.setdefault(anchor.locator(), []).append(unit)

    conflicts: dict[str, set[str]] = {}
    for sharing in by_anchor.values():
        for index, left in enumerate(sharing):
            for right in sharing[index + 1 :]:
                if _negates(left, right):
                    conflicts.setdefault(left.id, set()).add(right.id)
                    conflicts.setdefault(right.id, set()).add(left.id)
    return {unit_id: tuple(sorted(others)) for unit_id, others in conflicts.items()}


_NEGATIONS = (
    ("must ", "must not "),
    ("should ", "should not "),
    ("always ", "never "),
    ("is ", "is not "),
    ("allow", "forbid"),
    ("enable", "disable"),
    ("include", "exclude"),
)


def _negates(left: SpecUnit, right: SpecUnit) -> bool:
    """Cheap, deterministic negation screen over two units' intent.

    Syntactic on purpose. A semantic check would need a model, and a spec gate that
    depends on a model call is a gate that fails differently every time it runs. This
    catches the direct contradictions and leaves subtler ones to review, which is stated
    plainly rather than hidden.
    """
    a, b = left.intent.lower(), right.intent.lower()
    for positive, negative in _NEGATIONS:
        if positive in a and negative in b and _shares_object(a, b, positive, negative):
            return True
        if negative in a and positive in b and _shares_object(b, a, positive, negative):
            return True
    return False


def _shares_object(positive_text: str, negative_text: str, positive: str, negative: str) -> bool:
    """True when the two clauses talk about the same thing after the modal verb."""
    left_tail = positive_text.split(positive, 1)[1][:60]
    right_tail = negative_text.split(negative, 1)[1][:60]
    left_words = {w for w in left_tail.split() if len(w) > 3}
    right_words = {w for w in right_tail.split() if len(w) > 3}
    if not left_words or not right_words:
        return False
    overlap = len(left_words & right_words) / min(len(left_words), len(right_words))
    return overlap >= 0.5
