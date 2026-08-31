"""Tier routing and evidence-gated escalation (PRD FR-11, docs/harness/HARNESS.md §8).

This is where "lighter models do wonders" becomes a mechanism rather than a hope.

Runs start at the lowest tier whose capabilities cover the stage, and climb only when a
*recorded, machine-evaluable trigger* fires. "The model seems to be struggling" is not a
trigger. Every escalation records its outcome delta, because without that the factory
can never learn where escalation actually pays -- and a ladder nobody learns from is
just a more expensive default.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from software_factory.definition.models import Ladder, Tier


class Trigger(enum.StrEnum):
    """The complete set of reasons a run may climb. Nothing else escalates."""

    GATE_REPEAT = "gate_repeat"
    LOW_CONFIDENCE = "low_confidence"
    SCHEMA_REPEAT = "schema_repeat"
    COMPLEXITY = "complexity"
    EXPLICIT = "explicit"


@dataclass(frozen=True, slots=True)
class Escalation:
    trigger: Trigger
    from_tier: str
    to_tier: str
    detail: str
    outcome_delta: float | None = None
    """Filled in after the run. Without it, escalation cost cannot be justified."""


@dataclass(frozen=True, slots=True)
class EscalationRefused:
    code: str
    message: str


@dataclass(slots=True)
class RoutingState:
    """One run's position on the ladder, and how it got there."""

    ladder: Ladder
    current: str
    escalations: list[Escalation] = field(default_factory=list)
    gate_failures: dict[str, int] = field(default_factory=dict)
    schema_failures: int = 0
    retrieval_attempted: bool = False

    @property
    def tier(self) -> Tier:
        return self.ladder.tiers[self.ladder.index_of(self.current)]

    @property
    def escalations_used(self) -> int:
        return len(self.escalations)

    def record_gate_failure(self, gate: str, signature: str) -> None:
        """Track failures by signature: the *same* failure twice is the trigger.

        Two different gate failures mean the run is making progress on a hard task. The
        same failure twice means it is stuck, which is a different thing.
        """
        key = f"{gate}:{signature}"
        self.gate_failures[key] = self.gate_failures.get(key, 0) + 1

    def repeated_gate_failure(self) -> str | None:
        for key, count in sorted(self.gate_failures.items()):
            if count >= 2:
                return key
        return None


def starting_tier(ladder: Ladder, *, required: frozenset[str] = frozenset()) -> str:
    """The lowest tier whose capabilities cover what the stage needs (HARNESS.md R-1).

    Starting high is an explicit, justified choice recorded in the definition; the
    default is to start low and make the run earn its way up.

    `defaultTier` is where that choice is recorded, so the search starts there rather than
    at the bottom. It used to be a fallback after the loop -- and `required` defaults to the
    empty set, which is a subset of everything, so the loop always returned on its first
    iteration and the fallback was unreachable. A factory that configured `defaultTier: mid`
    silently started every run on the cheapest rung, which is the opposite of what it asked
    for and invisible from the outside.
    """
    start = ladder.index_of(ladder.default_tier) if ladder.default_tier else 0
    for tier in ladder.tiers[start:]:
        if required <= set(tier.capabilities):
            return tier.name
    # Nothing at or above the default covers the requirement. The top rung is the closest
    # the ladder can come; the caller sees a run that will escalate or fail on capability
    # rather than one silently started somewhere it cannot succeed.
    return ladder.tiers[-1].name


def may_escalate(
    state: RoutingState,
    trigger: Trigger,
    *,
    confidence: float | None = None,
    complexity: float | None = None,
    confidence_threshold: float = 0.5,
    complexity_threshold: float = 0.8,
    detail: str = "",
) -> Escalation | EscalationRefused:
    """Decide whether a run may climb, and record why if it does."""
    if state.escalations_used >= state.ladder.max_escalations:
        return EscalationRefused(
            "escalation.budget",
            f"already escalated {state.escalations_used} time(s); the ladder allows "
            f"{state.ladder.max_escalations}",
        )

    index = state.ladder.index_of(state.current)
    ceiling = state.ladder.ceiling_tier or state.ladder.tiers[-1].name
    ceiling_index = state.ladder.index_of(ceiling)
    if index >= ceiling_index:
        return EscalationRefused(
            "escalation.ceiling",
            f"{state.current} is at the factory's ceiling tier ({ceiling})",
        )

    justification = _justify(
        state,
        trigger,
        confidence=confidence,
        complexity=complexity,
        confidence_threshold=confidence_threshold,
        complexity_threshold=complexity_threshold,
        detail=detail,
    )
    if isinstance(justification, EscalationRefused):
        return justification

    to_tier = state.ladder.tiers[index + 1].name
    escalation = Escalation(
        trigger=trigger, from_tier=state.current, to_tier=to_tier, detail=justification
    )
    state.escalations.append(escalation)
    state.current = to_tier
    return escalation


def _justify(
    state: RoutingState,
    trigger: Trigger,
    *,
    confidence: float | None,
    complexity: float | None,
    confidence_threshold: float,
    complexity_threshold: float,
    detail: str,
) -> str | EscalationRefused:
    # Membership is checked before the match rather than with a `case _` arm, which is
    # statically unreachable. The hole was real at runtime: a value outside the enum fell
    # off the match, `_justify` returned None, and the caller read that as a justification
    # -- so an unrecognised trigger *granted* the escalation with no recorded reason.
    if trigger not in set(Trigger):
        return EscalationRefused(
            "escalation.unknown_trigger",
            f"{trigger!r} is not a recognised escalation trigger",
        )

    match trigger:
        case Trigger.GATE_REPEAT:
            repeated = state.repeated_gate_failure()
            if repeated is None:
                return EscalationRefused(
                    "escalation.no_repeat",
                    "no gate has failed twice with the same signature",
                )
            return f"{repeated} failed twice with an identical signature"

        case Trigger.LOW_CONFIDENCE:
            if confidence is None:
                return EscalationRefused(
                    "escalation.no_confidence", "no calibrated confidence was supplied"
                )
            if confidence >= confidence_threshold:
                return EscalationRefused(
                    "escalation.confident_enough",
                    f"confidence {confidence:.2f} is at or above {confidence_threshold:.2f}",
                )
            if not state.retrieval_attempted:
                # Escalating before trying to *learn more* spends money on a context
                # problem. Retrieval is cheaper than a bigger model and fixes more.
                return EscalationRefused(
                    "escalation.retrieval_first",
                    (
                        "low confidence, but on-demand retrieval has not been attempted; "
                        "fetch more context before spending a tier"
                    ),
                )
            return f"confidence {confidence:.2f} after retrieval, below {confidence_threshold:.2f}"

        case Trigger.SCHEMA_REPEAT:
            if state.schema_failures < 2:
                return EscalationRefused(
                    "escalation.schema_budget",
                    f"only {state.schema_failures} schema failure(s); the repair budget "
                    "is not exhausted",
                )
            return f"output failed schema validation {state.schema_failures} times"

        case Trigger.COMPLEXITY:
            if complexity is None:
                return EscalationRefused(
                    "escalation.no_complexity", "no complexity signal was supplied"
                )
            if complexity < complexity_threshold:
                return EscalationRefused(
                    "escalation.below_complexity",
                    f"complexity {complexity:.2f} is below {complexity_threshold:.2f}",
                )
            return f"complexity {complexity:.2f} exceeds {complexity_threshold:.2f}"

        case Trigger.EXPLICIT:
            if not detail.strip():
                return EscalationRefused(
                    "escalation.no_reason",
                    "an explicit escalation must state why, in terms a reviewer can check",
                )
            return detail.strip()


# --------------------------------------------------------------------- scaffolding


class Scaffold(enum.StrEnum):
    """Behaviours applied automatically below the scaffolding tier (HARNESS.md §8.4)."""

    DECOMPOSE = "decompose"
    VERIFY_THEN_ADVANCE = "verify-then-advance"
    CHECKPOINT_PER_STEP = "checkpoint-per-step"
    NARROW_WORKING_SET = "narrow-working-set"
    PRE_RESOLVE = "pre-resolve"
    ONE_QUESTION_AT_A_TIME = "one-question-at-a-time"


class VerifierClass(enum.StrEnum):
    """How strongly a decomposed step can be checked (PRD FR-11.9b)."""

    DETERMINISTIC = "deterministic"
    HEURISTIC = "heuristic"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Step:
    id: str
    description: str
    verifier: VerifierClass
    verifier_ref: str = ""


@dataclass(slots=True)
class Decomposition:
    """A plan, and an honest account of how much of it can actually be checked."""

    steps: list[Step] = field(default_factory=list)
    source: str = "unknown"
    """Where the decomposition came from: a skill, a design-stage plan, or the run itself."""

    @property
    def verified_fraction(self) -> float:
        if not self.steps:
            return 0.0
        checkable = sum(1 for s in self.steps if s.verifier is VerifierClass.DETERMINISTIC)
        return checkable / len(self.steps)

    @property
    def is_scaffolded(self) -> bool:
        """A plan of mostly-unverifiable steps is not scaffolding; it is a list.

        Reporting it as scaffolded would let a run claim a safety property it does not
        have, which is worse than admitting the task resisted decomposition.
        """
        return self.verified_fraction >= 0.5

    def unverifiable_steps(self) -> list[Step]:
        return [s for s in self.steps if s.verifier is VerifierClass.NONE]


def scaffolds_for(ladder: Ladder, tier_name: str) -> frozenset[Scaffold]:
    """Which scaffolds apply at this tier.

    Tier-conditioned and recorded, never silent: a run at a high tier must not quietly
    behave differently from what its configuration says.

    The threshold is inclusive, which is why the field is `scaffoldAtOrBelow` rather than
    `scaffoldBelow`. The old name was ambiguous against this behaviour, and the ambiguity
    was not cosmetic: the lowest tier is precisely the one that needs the scaffolding, so
    reading the name as exclusive would have made `scaffoldBelow: <lowest tier>` mean "no
    scaffolding anywhere" -- the opposite of what an operator writing it wants, and the
    opposite of the premise that a modest model in a good harness is the point.
    """
    if ladder.scaffold_at_or_below is None:
        return frozenset()
    try:
        threshold = ladder.index_of(ladder.scaffold_at_or_below)
        current = ladder.index_of(tier_name)
    except KeyError:
        return frozenset()
    if current > threshold:
        return frozenset()
    return frozenset(Scaffold)


def decomposition_source_preference() -> tuple[str, ...]:
    """Where a decomposition should come from, best first (PRD FR-11.9a).

    Splitting a task into verifiable steps is the hardest reasoning in the run, so
    assigning it to the tier least able to do it is self-defeating.
    """
    return ("skill", "design-stage-plan", "higher-tier-call", "self")
