"""Leases on irreversible external actions (PRD FR-19.5a).

FR-19.5 makes a deliberate choice: picking work up does not claim, lock, or pause it. Two
people can look at one work item, which is how work actually happens. FR-19.5a is where that
choice stops -- because "two actors read the same work item" is fine and "two actors both
open a change for it" is not.

So the lease is keyed on **(work item, action class)** rather than on the work item. Reading,
planning, and building are unleased. Opening a change, commenting on the tracker, and posting
to chat are leased, because each is externally visible and none of them un-happens.

Three properties matter:

* **Short and renewable.** A lease that outlives its holder's crash blocks the work item
  until a human intervenes. A short lease that the holder renews while working expires on
  its own when the holder stops existing.
* **It says who holds it.** FR-19.5's "a second actor is told who holds it and what they are
  doing, rather than racing them" is the whole user-facing point: a refusal that does not
  name the holder just moves the race one retry later.
* **It is advisory about intent, not about permission.** A lease does not grant the right to
  act -- grants do that. It coordinates between actors who all already have the right.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from software_factory.memory.records import utc_now


class ActionClass(enum.StrEnum):
    """Externally visible actions that must not happen twice.

    The list is short on purpose. Every member is something a second actor doing it produces
    a visible artifact of -- a duplicate pull request, a doubled comment, two tracker
    transitions -- rather than merely wasted work.
    """

    OPEN_CHANGE = "open_change"
    UPDATE_CHANGE = "update_change"
    COMMENT = "comment"
    UPDATE_TRACKER = "update_tracker"
    NOTIFY = "notify"
    HANDOFF = "handoff"


#: How long a lease lasts without renewal.
#:
#: Short enough that a crashed holder does not block the work item for long, long enough that
#: an actor doing the thing does not have to renew constantly. An actor that cannot renew
#: within this window is an actor that has stopped.
DEFAULT_TTL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class Lease:
    """One actor's claim on one action class of one work item."""

    work_item_id: str
    action: ActionClass
    holder: str
    intent: str
    """What they are doing, in words. FR-19.5 requires telling a second actor what the
    holder is up to, and "held by run-7" is not that."""

    acquired_at: datetime = field(default_factory=utc_now)
    ttl: timedelta = DEFAULT_TTL

    @property
    def key(self) -> tuple[str, ActionClass]:
        return (self.work_item_id, self.action)

    def expires_at(self) -> datetime:
        return self.acquired_at + self.ttl

    def active(self, now: datetime | None = None) -> bool:
        return (now or utc_now()) < self.expires_at()

    def describe(self, now: datetime | None = None) -> str:
        remaining = self.expires_at() - (now or utc_now())
        seconds = max(0, int(remaining.total_seconds()))
        return (
            f"{self.holder} is {self.intent} on {self.work_item_id} "
            f"({self.action.value}, {seconds}s remaining)"
        )


@dataclass(frozen=True, slots=True)
class Held:
    """The lease was not available. Names who has it and what they are doing."""

    lease: Lease
    message: str
    remediation: str


@dataclass(slots=True)
class LeaseBook:
    """Every live lease. Expiry is computed on read rather than swept.

    A sweep would mean a lease staying "held" until something ran, which is exactly the
    window where a crashed holder blocks a work item nobody is working on.
    """

    leases: dict[tuple[str, ActionClass], Lease] = field(default_factory=dict)

    def held(
        self, work_item_id: str, action: ActionClass, *, now: datetime | None = None
    ) -> Lease | None:
        lease = self.leases.get((work_item_id, action))
        if lease is None or not lease.active(now):
            return None
        return lease

    def acquire(
        self,
        work_item_id: str,
        action: ActionClass,
        *,
        holder: str,
        intent: str,
        ttl: timedelta = DEFAULT_TTL,
        now: datetime | None = None,
    ) -> Lease | Held:
        """Take the lease, or say who has it.

        Re-acquiring a lease you already hold *renews* it rather than failing: an actor that
        loops -- open a change, push again, update it -- would otherwise have to remember
        whether this pass is its first.
        """
        now = now or utc_now()
        existing = self.held(work_item_id, action, now=now)
        if existing is not None and existing.holder != holder:
            return Held(
                lease=existing,
                message=existing.describe(now),
                remediation=(
                    "Wait for the lease to expire or be released, or coordinate with the "
                    "holder. Picking up a work item does not claim it, but doing something "
                    "externally visible to it twice produces two of the artifact."
                ),
            )
        lease = Lease(
            work_item_id=work_item_id,
            action=action,
            holder=holder,
            intent=intent,
            acquired_at=now,
            ttl=ttl,
        )
        self.leases[lease.key] = lease
        return lease

    def renew(
        self,
        work_item_id: str,
        action: ActionClass,
        *,
        holder: str,
        now: datetime | None = None,
    ) -> Lease | Held:
        """Extend a lease you hold. Refuses if it has already been taken by someone else."""
        return self.acquire(
            work_item_id,
            action,
            holder=holder,
            intent=(
                self.leases[(work_item_id, action)].intent
                if (work_item_id, action) in self.leases
                else "continuing"
            ),
            now=now,
        )

    def release(self, work_item_id: str, action: ActionClass, *, holder: str) -> bool:
        """Give up a lease. Returns False when it was not yours -- which is worth knowing,
        because releasing someone else's lease silently would be a way to defeat the whole
        mechanism."""
        lease = self.leases.get((work_item_id, action))
        if lease is None or lease.holder != holder:
            return False
        del self.leases[(work_item_id, action)]
        return True

    def active_for(self, work_item_id: str, *, now: datetime | None = None) -> list[Lease]:
        """Every live lease on one work item. What FR-19.5's "expose active runs" reads."""
        return sorted(
            (
                lease
                for (item, _), lease in self.leases.items()
                if item == work_item_id and lease.active(now)
            ),
            key=lambda lease: lease.action.value,
        )
