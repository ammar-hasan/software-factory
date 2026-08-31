"""Work items and the stage machine (PRD FR-4, FR-3.3a).

A work item is one request the factory acts on, holding a stable identity from intake to
handoff however many runs, agents, stages and humans touch it.

Two properties are load-bearing:

* **Legal transitions are an explicit table.** A transition not in it is a defect, not a
  surprise. The table is data, so a factory can declare its own stage graph.
* **Skip authority is bounded by policy, not by judgement.** The conductor reads
  attacker-controllable text, so unbounded routing authority is an injection primitive:
  text that persuades the conductor to skip review removes review.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from software_factory.definition.models import Stage
from software_factory.memory.records import utc_now
from software_factory.spec.units import TrustClass


class Blocker(enum.StrEnum):
    """Why a work item stopped. Every blocker names the action that clears it."""

    AWAITING_HUMAN = "awaiting_human"
    AWAITING_CI = "awaiting_ci"
    MISSING_CREDENTIAL = "missing_credential"
    BUDGET_EXCEEDED = "budget_exceeded"
    CONFLICTING_SPEC = "conflicting_spec"
    GATE_FAILED_TERMINAL = "gate_failed_terminal"
    EXTERNAL_DEPENDENCY = "external_dependency"
    CONFLICT = "conflict"


class WorkClass(enum.StrEnum):
    """What kind of work this is. Decides which gates apply."""

    DEFECT = "defect"
    FEATURE = "feature"
    REFACTOR = "refactor"
    REVIEW = "review"
    CHORE = "chore"
    INVESTIGATION = "investigation"


TERMINAL: frozenset[Stage] = frozenset({Stage.COMPLETE, Stage.CANCELLED})

#: Where each stage may go next, before BLOCKED and CANCELLED are added.
_FORWARD: dict[Stage, frozenset[Stage]] = {
    Stage.INTAKE: frozenset({Stage.TRIAGE, Stage.DESIGN, Stage.BUILD, Stage.REVIEW}),
    Stage.TRIAGE: frozenset({Stage.DESIGN, Stage.BUILD, Stage.HANDOFF}),
    Stage.DESIGN: frozenset({Stage.BUILD, Stage.TRIAGE}),
    Stage.BUILD: frozenset({Stage.REVIEW, Stage.DESIGN}),
    Stage.REVIEW: frozenset({Stage.VERIFY, Stage.BUILD, Stage.HANDOFF}),
    Stage.VERIFY: frozenset({Stage.HANDOFF, Stage.BUILD}),
    Stage.HANDOFF: frozenset({Stage.COMPLETE, Stage.BUILD}),
    Stage.COMPLETE: frozenset(),
    Stage.BLOCKED: frozenset(
        {Stage.TRIAGE, Stage.DESIGN, Stage.BUILD, Stage.REVIEW, Stage.VERIFY, Stage.HANDOFF}
    ),
    Stage.CANCELLED: frozenset(),
}


def _with_escape_hatches(forward: dict[Stage, frozenset[Stage]]) -> dict[Stage, frozenset[Stage]]:
    """Every non-terminal stage can reach BLOCKED and CANCELLED.

    Work can stall or be called off at any point, and a graph that has to enumerate
    those edges by hand will eventually miss one -- leaving a stage from which nothing
    can park an item.
    """
    escapes = frozenset({Stage.BLOCKED, Stage.CANCELLED})
    return {
        stage: (targets if stage in TERMINAL or stage is Stage.BLOCKED else targets | escapes)
        for stage, targets in forward.items()
    }


#: The default stage graph. Data, not architecture: a factory may declare its own,
#: subject to the two invariants in :func:`validate_graph` (PRD FR-4.2a).
DEFAULT_TRANSITIONS: dict[Stage, frozenset[Stage]] = _with_escape_hatches(_FORWARD)

#: Stages the conductor may not skip on its own authority (PRD FR-3.3a).
DEFAULT_NON_SKIPPABLE: frozenset[Stage] = frozenset({Stage.REVIEW})


@dataclass(frozen=True, slots=True)
class Transition:
    """One recorded move. Every field is required because "who and why" is the point."""

    from_stage: Stage
    to_stage: Stage
    actor: str
    reason: str
    at: datetime
    evidence: tuple[str, ...] = ()
    skipped: tuple[Stage, ...] = ()
    basis_trust: TrustClass = TrustClass.INTERNAL

    def render(self) -> str:
        skipped = f" (skipped {', '.join(s.value for s in self.skipped)})" if self.skipped else ""
        return f"{self.from_stage.value} -> {self.to_stage.value}{skipped}: {self.reason}"


@dataclass(frozen=True, slots=True)
class SourceContext:
    """Where the request came from. Carried through every stage; replies go back here."""

    provider: str
    kind: str
    ref: str
    permalink: str = ""

    def identity(self) -> str:
        return f"{self.provider}:{self.kind}:{self.ref}"


@dataclass(frozen=True, slots=True)
class TransitionRefused:
    code: str
    message: str
    remediation: str


@dataclass(slots=True)
class WorkItem:
    """One request, from intake to handoff."""

    id: str
    factory: str
    title: str
    request: str
    source: SourceContext
    work_class: WorkClass = WorkClass.FEATURE
    stage: Stage = Stage.INTAKE
    blocker: Blocker | None = None
    blocker_action: str = ""
    parked_at: Stage | None = None
    """The stage this item was at when it was blocked, so resuming measures skips from it."""
    depends_on: tuple[str, ...] = ()
    history: list[Transition] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    definition_revision: str = ""
    """Pinned for the item's duration, so its stages stay comparable (PRD FR-24.2)."""

    @property
    def terminal(self) -> bool:
        return self.stage in TERMINAL

    @property
    def stages_visited(self) -> tuple[Stage, ...]:
        return (*(t.from_stage for t in self.history), self.stage)

    def returned_to_earlier_stage(self) -> int:
        """How many times this item went backwards. The rework signal (metric O-8)."""
        order: list[Stage] = list(DEFAULT_TRANSITIONS)
        count = 0
        for transition in self.history:
            if transition.to_stage in TERMINAL or transition.to_stage is Stage.BLOCKED:
                continue
            try:
                if order.index(transition.to_stage) < order.index(transition.from_stage):
                    count += 1
            except ValueError:  # pragma: no cover - custom graphs
                continue
        return count

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "factory": self.factory,
            "title": self.title,
            "stage": self.stage.value,
            "workClass": self.work_class.value,
            "blocker": self.blocker.value if self.blocker else None,
            "blockerAction": self.blocker_action,
            "source": self.source.identity(),
            "definitionRevision": self.definition_revision,
            "rework": self.returned_to_earlier_stage(),
            "history": [t.render() for t in self.history],
        }


#: The order stages are considered to occur in, for deciding what a transition skipped.
#: Explicit rather than derived from the transition table's key order: a security control
#: must not depend on dict literal ordering, and a factory declaring its own graph
#: (FR-4.2a) would otherwise silently change which stages are skippable.
DEFAULT_ORDER: tuple[Stage, ...] = (
    Stage.INTAKE,
    Stage.TRIAGE,
    Stage.DESIGN,
    Stage.BUILD,
    Stage.REVIEW,
    Stage.VERIFY,
    Stage.HANDOFF,
    Stage.COMPLETE,
)


@dataclass(slots=True)
class StageMachine:
    """Enforces the stage graph and the skip policy."""

    transitions: dict[Stage, frozenset[Stage]] = field(
        default_factory=lambda: dict(DEFAULT_TRANSITIONS)
    )
    non_skippable: frozenset[Stage] = DEFAULT_NON_SKIPPABLE
    order: tuple[Stage, ...] = DEFAULT_ORDER

    def legal(self, from_stage: Stage, to_stage: Stage) -> bool:
        return to_stage in self.transitions.get(from_stage, frozenset())

    def skipped_between(self, from_stage: Stage, to_stage: Stage) -> tuple[Stage, ...]:
        """Stages passed over by going straight from one stage to another.

        ``from_stage`` must be a positioned stage. Leaving ``BLOCKED`` is measured from
        the stage the item was parked at, not from ``BLOCKED`` itself -- otherwise
        "park it, then hand it off" walks straight past review with an empty skip list,
        which is exactly the primitive the non-skippable rule exists to prevent.
        """
        order = list(self.order)
        try:
            start, end = order.index(from_stage), order.index(to_stage)
        except ValueError:
            return ()
        if end <= start:
            return ()
        return tuple(order[start + 1 : end])

    def advance(
        self,
        item: WorkItem,
        to_stage: Stage,
        *,
        actor: str,
        reason: str,
        evidence: tuple[str, ...] = (),
        basis_trust: TrustClass = TrustClass.INTERNAL,
        human_approved_skip: bool = False,
    ) -> Transition | TransitionRefused:
        """Move a work item, or refuse with the reason.

        Three refusals matter: an illegal transition, a skip of a non-skippable stage,
        and a routing decision justified only by untrusted input.
        """
        if item.terminal:
            return TransitionRefused(
                "stage.terminal",
                f"{item.id} is {item.stage.value} and cannot move",
                "Open a new work item.",
            )

        if not self.legal(item.stage, to_stage):
            allowed = ", ".join(sorted(s.value for s in self.transitions.get(item.stage, ())))
            return TransitionRefused(
                "stage.illegal_transition",
                f"{item.stage.value} -> {to_stage.value} is not in the stage graph",
                f"Legal next stages: {allowed or 'none'}.",
            )

        if basis_trust is TrustClass.UNTRUSTED:
            return TransitionRefused(
                "stage.untrusted_basis",
                (
                    f"the only justification for moving {item.id} to {to_stage.value} traces to "
                    "untrusted input"
                ),
                (
                    "Routing decided by text an attacker can write is not a routing decision. "
                    "Establish the basis from the code, the tests, or a person."
                ),
            )

        # Resuming from BLOCKED is measured from where the item was parked. Without this
        # the skip list is empty and the non-skippable check never runs.
        measured_from = (
            item.parked_at if item.stage is Stage.BLOCKED and item.parked_at else item.stage
        )
        skipped = self.skipped_between(measured_from, to_stage)
        blocked_skips = tuple(s for s in skipped if s in self.non_skippable)
        if blocked_skips and not human_approved_skip:
            names = ", ".join(s.value for s in blocked_skips)
            return TransitionRefused(
                "stage.non_skippable",
                f"moving to {to_stage.value} would skip {names}",
                (
                    f"{names} cannot be skipped on an agent's authority. A human must approve "
                    "it, and the approval is recorded against their identity."
                ),
            )

        transition = Transition(
            from_stage=item.stage,
            to_stage=to_stage,
            actor=actor,
            reason=reason,
            at=utc_now(),
            evidence=evidence,
            skipped=skipped,
            basis_trust=basis_trust,
        )
        item.history.append(transition)
        item.stage = to_stage
        if to_stage is not Stage.BLOCKED:
            item.blocker = None
            item.blocker_action = ""
            item.parked_at = None
        return transition

    def block(
        self, item: WorkItem, blocker: Blocker, *, actor: str, action: str
    ) -> Transition | TransitionRefused:
        """Park an item. The action needed to clear it is required, not optional."""
        if not action.strip():
            return TransitionRefused(
                "stage.blocker_without_action",
                f"blocking {item.id} without saying what would unblock it",
                "State the exact action needed. A blocker nobody can act on is a dead end.",
            )
        parked_at = item.stage
        transition = self.advance(
            item, Stage.BLOCKED, actor=actor, reason=f"{blocker.value}: {action}"
        )
        if isinstance(transition, Transition):
            item.blocker = blocker
            item.blocker_action = action
            item.parked_at = parked_at
        return transition

    def cancel(self, item: WorkItem, *, actor: str, reason: str) -> Transition | TransitionRefused:
        """Cancel from any stage. Always available to a human (PRD FR-4.8)."""
        if item.terminal:
            return TransitionRefused(
                "stage.terminal", f"{item.id} is already {item.stage.value}", "Nothing to do."
            )
        transition = Transition(
            from_stage=item.stage,
            to_stage=Stage.CANCELLED,
            actor=actor,
            reason=reason,
            at=utc_now(),
        )
        item.history.append(transition)
        item.stage = Stage.CANCELLED
        return transition


def validate_graph(
    transitions: dict[Stage, frozenset[Stage]],
    non_skippable: frozenset[Stage],
    order: tuple[Stage, ...] | None = None,
) -> list[str]:
    """Check a custom stage graph against the two invariants that are actually derived.

    Everything else about the graph is a factory's choice; these two are not.
    """
    problems: list[str] = []

    if Stage.HANDOFF not in transitions:
        problems.append("the graph has no HANDOFF stage; work must end by reaching a human")
    if not non_skippable:
        problems.append(
            "no stage is marked non-skippable; at least one verification stage must precede "
            "handoff, or routing can be talked out of every check"
        )

    # Non-skippability is enforced by the skip check, which measures a transition against
    # the declared order. A stage missing from that order is invisible to the check, so
    # its declaration would be a promise nothing keeps.
    positioned = set(order or DEFAULT_ORDER)
    for stage in sorted(non_skippable, key=lambda s: s.value):
        if stage not in positioned:
            problems.append(
                f"{stage.value} is declared non-skippable but is absent from the stage order, "
                "so no transition can be measured against it"
            )

    for stage in sorted(transitions, key=lambda s: s.value):
        if stage in TERMINAL or stage is Stage.BLOCKED:
            continue
        if stage not in positioned:
            problems.append(
                f"{stage.value} appears in the transition table but not in the stage order, "
                "so transitions across it are unmeasured"
            )

    reachable = {Stage.INTAKE}
    frontier = [Stage.INTAKE]
    while frontier:
        current = frontier.pop()
        for target in transitions.get(current, frozenset()):
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)
    unreachable = sorted(s.value for s in transitions if s not in reachable and s != Stage.INTAKE)
    if unreachable:
        problems.append(f"unreachable stage(s) from INTAKE: {', '.join(unreachable)}")

    return problems


def new_id() -> str:
    return f"wi_{uuid.uuid4().hex[:12]}"


def classify_request(text: str) -> WorkClass:
    """A cheap first guess at work class, refined by triage.

    Deliberately shallow: getting this wrong costs one gate configuration, and triage
    corrects it with actual evidence. Guessing harder here would be false precision.
    """
    lowered = text.lower()
    if any(
        word in lowered
        for word in ("bug", "broken", "regression", "crash", "error", "fails", "defect")
    ):
        return WorkClass.DEFECT
    if any(word in lowered for word in ("refactor", "clean up", "tidy", "restructure")):
        return WorkClass.REFACTOR
    if any(word in lowered for word in ("review", "look at this pr", "check my change")):
        return WorkClass.REVIEW
    if any(word in lowered for word in ("why", "investigate", "how does", "understand")):
        return WorkClass.INVESTIGATION
    return WorkClass.FEATURE
