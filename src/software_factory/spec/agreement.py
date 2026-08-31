"""Computing the spec/code/test agreement state (docs/harness/living-spec.md §3).

Everything here is deterministic. The only inputs are: does an anchor resolve, has its
digest changed, and did its tests pass. That is what lets the spec block a build without
anyone having to trust a model's reading of the code.
"""

from __future__ import annotations

import re
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

    # `outcome` has three answers, not two. Folding `None` into "not failing" made a unit
    # whose tests have never run indistinguishable from one whose tests passed, and the
    # `spec-agreement` gate read it as satisfied -- the third of the three questions in this
    # module's docstring answered by assumption.
    outcomes = {anchor.locator(): outcome(anchor.locator()) for anchor in unit.verifies}
    failing = [locator for locator, passed in outcomes.items() if passed is False]
    unknown = [locator for locator, passed in outcomes.items() if passed is None]
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

    # Ahead of the drift branch on purpose: "behaviour appears preserved, so re-anchor"
    # is a claim about passing tests, and an unrun test supports no such claim.
    if unknown:
        return AgreementResult(
            unit_id=unit.id,
            state=Agreement.UNVERIFIED,
            reason=(
                f"{len(unknown)} verifying test(s) have no recorded outcome "
                f"({', '.join(unknown[:3])}); nothing has checked this intent yet"
            ),
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


@dataclass(frozen=True, slots=True)
class _NegationPair:
    """One assert/forbid pair, as word-boundary patterns.

    Patterns rather than substrings because the substring form matched inside words:
    ``"is "`` is present in ``"this "``, so any two units mentioning "this" were candidates
    for a contradiction.
    """

    positive: re.Pattern[str]
    negative: re.Pattern[str]


def _pair(positive: str, negative: str) -> _NegationPair:
    return _NegationPair(re.compile(positive), re.compile(negative))


_NEGATIONS = (
    _pair(r"\bmust\b", r"\bmust\s+not\b"),
    _pair(r"\bshall\b", r"\bshall\s+not\b"),
    _pair(r"\bshould\b", r"\bshould\s+not\b"),
    _pair(r"\balways\b", r"\bnever\b"),
    _pair(r"\bis\b", r"\bis\s+not\b"),
    _pair(r"\ballow(?:s|ed|ing)?\b", r"\bforbid(?:s|den|ding)?\b"),
    _pair(r"\benable(?:s|d|ing)?\b", r"\bdisable(?:s|d|ing)?\b"),
    _pair(r"\binclude(?:s|d|ing)?\b", r"\bexclude(?:s|d|ing)?\b"),
)


def _polarity(text: str, pair: _NegationPair) -> tuple[bool, str] | None:
    """Which side of one negation pair a text falls on, and the clause after the marker.

    The negative is tested first because three of these pairs are prefix-shaped: any text
    containing "must not" contains "must " too. Testing the positive first therefore put
    *both* sides of an agreement on opposite polarities -- two units that each said
    "the cache must not be enabled" were read as one asserting and one forbidding, both
    were marked CONTRADICTED, and the build was blocked by two units that agreed.
    """
    negative = pair.negative.search(text)
    if negative is not None:
        return True, text[negative.end() :]
    positive = pair.positive.search(text)
    if positive is not None:
        return False, text[positive.end() :]
    return None


def _negates(left: SpecUnit, right: SpecUnit) -> bool:
    """Cheap, deterministic negation screen over two units' intent.

    Syntactic on purpose. A semantic check would need a model, and a spec gate that
    depends on a model call is a gate that fails differently every time it runs. This
    catches the direct contradictions and leaves subtler ones to review, which is stated
    plainly rather than hidden.
    """
    a, b = left.intent.lower(), right.intent.lower()
    for pair in _NEGATIONS:
        left_side = _polarity(a, pair)
        right_side = _polarity(b, pair)
        if left_side is None or right_side is None:
            continue
        left_negated, left_tail = left_side
        right_negated, right_tail = right_side
        if left_negated == right_negated:
            # Both assert, or both forbid. That is agreement on this pair, not conflict.
            continue
        if _shares_object(left_tail, right_tail):
            return True
    return False


def _shares_object(left_tail: str, right_tail: str) -> bool:
    """True when the two clauses talk about the same thing after the modal verb."""
    left_words = {w for w in left_tail[:60].split() if len(w) > 3}
    right_words = {w for w in right_tail[:60].split() if len(w) > 3}
    if not left_words or not right_words:
        return False
    overlap = len(left_words & right_words) / min(len(left_words), len(right_words))
    return overlap >= 0.5
