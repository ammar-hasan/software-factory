"""Human checkpoints: what waits, who can clear it, and what happens if nobody does.

Implements PRD FR-16. A checkpoint is a *question put to a named capability*, not a pause.
The difference matters twice:

* It can be routed. FR-16.3 requires a checkpoint be resolvable from wherever the work
  arrived, and you cannot route a question to "a human" -- you route it to the principals
  holding the capability that answers it.
* It can expire. FR-16.4: an unanswered checkpoint escalates and then parks the work item
  rather than holding a run open and burning budget. A checkpoint with no deadline is a run
  that quietly costs money until someone notices, which is the failure this whole design is
  meant to make impossible.

Checkpoints are workflow policy, not enforcement (FR-16.2, PR-4). Nothing here stops a
merge; repository permissions do. What this does is refuse to *record* an unanswered
checkpoint as answered, which is the only guarantee a policy layer can honestly make.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from software_factory.identity.principals import (
    Capability,
    Decision,
    Directory,
    Refused,
)
from software_factory.memory.records import utc_now


class CheckpointKind(enum.StrEnum):
    """The default checkpoints of FR-16.1. A factory may add its own in `policy/`."""

    SPEC_APPROVAL = "spec_approval"
    QUESTION = "question"
    BLAST_RADIUS_WIDENING = "blast_radius_widening"
    IMPROVEMENT_ADOPTION = "improvement_adoption"
    SELF_REFERENTIAL_CHANGE = "self_referential_change"
    GATE_OVERRIDE = "gate_override"


#: Which capability answers which checkpoint. One map, so a checkpoint cannot be routed to
#: principals who could not have cleared it.
ANSWERED_BY: dict[CheckpointKind, Capability] = {
    CheckpointKind.SPEC_APPROVAL: Capability.APPROVE_SPEC,
    CheckpointKind.QUESTION: Capability.ANSWER_QUESTION,
    CheckpointKind.BLAST_RADIUS_WIDENING: Capability.WIDEN_BLAST_RADIUS,
    CheckpointKind.IMPROVEMENT_ADOPTION: Capability.ADOPT_DEFINITION_CHANGE,
    CheckpointKind.SELF_REFERENTIAL_CHANGE: Capability.APPROVE_SELF_REFERENTIAL_CHANGE,
    CheckpointKind.GATE_OVERRIDE: Capability.OVERRIDE_GATE,
}

#: How long a checkpoint waits before its notification escalates, and before it parks the
#: work item. Both are defaults an operator overrides in `policy/`; neither may be absent.
DEFAULT_REMINDER = timedelta(hours=4)
DEFAULT_DEADLINE = timedelta(hours=48)


class CheckpointStatus(enum.StrEnum):
    OPEN = "open"
    REMINDED = "reminded"
    RESOLVED = "resolved"
    PARKED = "parked"


@dataclass(slots=True)
class Checkpoint:
    """One question, waiting.

    ``origin`` is where the work arrived from, carried so the checkpoint can be answered
    there (FR-16.3). A question that can only be answered in a tool the asker does not use
    is a question that does not get answered.
    """

    id: str
    kind: CheckpointKind
    work_item_id: str
    question: str
    asked_by: str
    origin: str = "cli"
    evidence: tuple[str, ...] = ()
    opened_at: datetime = field(default_factory=utc_now)
    reminder_after: timedelta = DEFAULT_REMINDER
    deadline_after: timedelta = DEFAULT_DEADLINE
    status: CheckpointStatus = CheckpointStatus.OPEN
    resolution: Decision | None = None
    answer: str = ""

    @property
    def capability(self) -> Capability:
        return ANSWERED_BY[self.kind]

    def due_state(self, now: datetime | None = None) -> CheckpointStatus:
        """What this checkpoint's status *should* be, given the clock.

        Computed rather than stored so a process that was not running when a deadline
        passed does not miss it. The scheduler applies the transition; this decides it.
        """
        if self.status in (CheckpointStatus.RESOLVED, CheckpointStatus.PARKED):
            return self.status
        elapsed = (now or utc_now()) - self.opened_at
        if elapsed >= self.deadline_after:
            return CheckpointStatus.PARKED
        if elapsed >= self.reminder_after:
            return CheckpointStatus.REMINDED
        return CheckpointStatus.OPEN

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "workItem": self.work_item_id,
            "question": self.question,
            "askedBy": self.asked_by,
            "origin": self.origin,
            "status": self.status.value,
            "openedAt": self.opened_at.isoformat(),
            "answeredBy": self.resolution.principal_id if self.resolution else None,
            "answer": self.answer,
        }


@dataclass(slots=True)
class CheckpointBook:
    """Every open checkpoint in a factory, and the transitions over them."""

    directory: Directory
    checkpoints: dict[str, Checkpoint] = field(default_factory=dict)

    def open(self, checkpoint: Checkpoint) -> Checkpoint:
        if checkpoint.id in self.checkpoints:
            raise ValueError(f"duplicate checkpoint id {checkpoint.id!r}")
        if not self.directory.holders(checkpoint.capability):
            raise ValueError(
                f"no principal holds {checkpoint.capability.value}, so a "
                f"{checkpoint.kind.value} checkpoint could never be cleared; grant it "
                "in the definition before asking the question"
            )
        self.checkpoints[checkpoint.id] = checkpoint
        return checkpoint

    def routable_to(self, checkpoint_id: str) -> list[str]:
        """Who can clear this checkpoint. The notification's recipient list."""
        checkpoint = self.checkpoints[checkpoint_id]
        return [p.id for p in self.directory.holders(checkpoint.capability)]

    def resolve(
        self,
        checkpoint_id: str,
        *,
        principal_id: str,
        answer: str,
        channel: str = "cli",
    ) -> Decision | Refused:
        """Answer a checkpoint, if this principal may.

        A parked checkpoint still accepts an answer: parking is about not holding a run
        open, and the question stays worth answering after the work item is put down.
        """
        checkpoint = self.checkpoints.get(checkpoint_id)
        if checkpoint is None:
            return Refused(
                "checkpoint.unknown",
                f"no checkpoint {checkpoint_id!r}",
                "Check the id; `sf checkpoints` lists what is open.",
            )
        if checkpoint.status is CheckpointStatus.RESOLVED:
            return Refused(
                "checkpoint.already_resolved",
                f"{checkpoint_id!r} was answered by "
                f"{checkpoint.resolution.principal_id if checkpoint.resolution else 'someone'}",
                "Open a new checkpoint if the answer needs revisiting.",
            )

        decision = self.directory.authorise(
            principal_id,
            checkpoint.capability,
            subject=f"{checkpoint.kind.value}:{checkpoint.work_item_id}",
            rationale=answer,
            evidence_shown=checkpoint.evidence,
            channel=channel,
        )
        if isinstance(decision, Refused):
            return decision

        checkpoint.status = CheckpointStatus.RESOLVED
        checkpoint.resolution = decision
        checkpoint.answer = answer
        return decision

    def sweep(self, now: datetime | None = None) -> list[tuple[str, CheckpointStatus]]:
        """Advance every checkpoint's status to what the clock says it should be.

        Returns what changed, so the caller can send the reminders and park the work items.
        Escalation is *not* silent: a checkpoint that parks a work item without telling
        anyone converts a question into a stall.
        """
        now = now or utc_now()
        changes: list[tuple[str, CheckpointStatus]] = []
        for checkpoint in sorted(self.checkpoints.values(), key=lambda c: c.id):
            due = checkpoint.due_state(now)
            if due is not checkpoint.status:
                checkpoint.status = due
                changes.append((checkpoint.id, due))
        return changes

    def open_for(self, work_item_id: str) -> list[Checkpoint]:
        return sorted(
            (c for c in self.checkpoints.values() if c.work_item_id == work_item_id),
            key=lambda c: c.opened_at,
        )
