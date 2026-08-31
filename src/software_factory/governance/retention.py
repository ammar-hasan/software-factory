"""Retention, legal hold, and erasure by reference (PRD FR-27.3, FR-27.4, FR-15.10).

An append-only design plus a permanent archive makes compliance architecturally impossible,
and the PRD says so. Three mechanisms make it possible again, and they have to compose:

* **Retention** removes bodies, never records (FR-15.10a). The record becomes a tombstone,
  so a claim resting on expired evidence renders as "evidence expired" -- never as
  unsupported, and never as satisfied.
* **Legal hold** suspends retention for named subjects. It is checked *before* expiry, not
  after, because a sweep that deletes and then notices the hold has already destroyed the
  thing the hold existed to preserve.
* **Erasure by reference** destroys content anywhere it appears for a subject, and produces
  a report saying what was destroyed and what could not be. A subject-erasure request whose
  answer is "probably everything" is not an answer.

The reason these are one module: a retention sweep that does not know about holds is a
compliance bug, and an erasure that does not know about retention reports as complete a job
that retention was about to redo.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from software_factory.governance.classification import (
    DEFAULT_CLASSIFICATION,
    Classification,
    DataClass,
)
from software_factory.memory.records import utc_now


class HoldReason(enum.StrEnum):
    LITIGATION = "litigation"
    INVESTIGATION = "investigation"
    AUDIT = "audit"
    REGULATORY = "regulatory"


@dataclass(frozen=True, slots=True)
class LegalHold:
    """Retention is suspended for these subjects until the hold is lifted.

    A hold names *subjects*, not artifacts. Naming artifacts would require enumerating them
    at hold time, and the whole point of a hold is that it catches things nobody has thought
    to enumerate yet -- including artifacts created after the hold was placed.
    """

    id: str
    subjects: frozenset[str]
    reason: HoldReason
    placed_by: str
    placed_at: datetime = field(default_factory=utc_now)
    note: str = ""
    lifted_at: datetime | None = None
    lifted_by: str = ""

    @property
    def active(self) -> bool:
        return self.lifted_at is None

    def covers(self, subject: str) -> bool:
        return self.active and subject in self.subjects


@dataclass(frozen=True, slots=True)
class Artifact:
    """One retained thing, as retention sees it.

    Deliberately thin. Retention needs to know what class it is, how old, and who it is
    about -- not what it says. A retention sweep that reads bodies is a retention sweep that
    can leak them.
    """

    id: str
    data_class: DataClass
    created_at: datetime
    subjects: frozenset[str] = frozenset()
    tombstoned: bool = False

    def age(self, now: datetime | None = None) -> timedelta:
        return (now or utc_now()) - self.created_at


@dataclass(slots=True)
class SweepReport:
    """What one retention pass did, and what it deliberately did not do."""

    expired: list[str] = field(default_factory=list)
    held: list[tuple[str, str]] = field(default_factory=list)
    """``(artifact id, hold id)`` for artifacts a hold kept. Reported, never silent: an
    operator watching storage grow needs to see *why* it is growing."""

    already_tombstoned: list[str] = field(default_factory=list)

    @property
    def acted(self) -> bool:
        return bool(self.expired)

    def as_dict(self) -> dict[str, object]:
        return {
            "expired": sorted(self.expired),
            "held": [{"artifact": a, "hold": h} for a, h in sorted(self.held)],
            "alreadyTombstoned": sorted(self.already_tombstoned),
        }


@dataclass(slots=True)
class ErasureReport:
    """The answer to a subject-erasure request (FR-27.3).

    ``unerasable`` is the field that makes this a report rather than a receipt. A subject
    whose data lives in a class that cannot be erased -- the ledger, deliberately -- needs
    to be told that, and told what remains: references and decisions, never bodies.
    """

    subject: str
    requested_by: str
    at: datetime = field(default_factory=utc_now)
    erased: list[str] = field(default_factory=list)
    unerasable: list[tuple[str, str]] = field(default_factory=list)
    """``(artifact id, why)``."""

    blocked_by_hold: list[tuple[str, str]] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """True only when nothing was blocked. Unerasable classes do not block completion --
        they are a stated property of the design, not a failure of this request."""
        return not self.blocked_by_hold

    def as_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "requestedBy": self.requested_by,
            "at": self.at.isoformat(),
            "complete": self.complete,
            "erased": sorted(self.erased),
            "unerasable": [{"artifact": a, "why": w} for a, w in sorted(self.unerasable)],
            "blockedByHold": [{"artifact": a, "hold": h} for a, h in sorted(self.blocked_by_hold)],
        }


class Retention:
    """Retention sweeps and erasure requests over a set of artifacts.

    ``classification`` is injected so an operator's `policy/` overrides are used rather than
    the defaults, and so a test can state an unusual retention in one line instead of
    manipulating time.
    """

    def __init__(
        self,
        *,
        classification: dict[DataClass, Classification] | None = None,
        holds: list[LegalHold] | None = None,
    ) -> None:
        self.classification = classification or dict(DEFAULT_CLASSIFICATION)
        self.holds: list[LegalHold] = list(holds or [])

    def place_hold(self, hold: LegalHold) -> LegalHold:
        if any(existing.id == hold.id for existing in self.holds):
            raise ValueError(f"duplicate hold id {hold.id!r}")
        self.holds.append(hold)
        return hold

    def lift_hold(self, hold_id: str, *, by: str, at: datetime | None = None) -> LegalHold | None:
        for index, hold in enumerate(self.holds):
            if hold.id == hold_id and hold.active:
                lifted = LegalHold(
                    id=hold.id,
                    subjects=hold.subjects,
                    reason=hold.reason,
                    placed_by=hold.placed_by,
                    placed_at=hold.placed_at,
                    note=hold.note,
                    lifted_at=at or utc_now(),
                    lifted_by=by,
                )
                self.holds[index] = lifted
                return lifted
        return None

    def holding(self, artifact: Artifact) -> LegalHold | None:
        """The first active hold covering any of this artifact's subjects."""
        for hold in self.holds:
            if any(hold.covers(subject) for subject in artifact.subjects):
                return hold
        return None

    def sweep(
        self,
        artifacts: list[Artifact],
        *,
        tombstone: Callable[[Artifact], None] | None = None,
        now: datetime | None = None,
    ) -> SweepReport:
        """Expire what is due, keeping what a hold covers.

        The hold check runs *before* the expiry check on purpose: a sweep that deletes and
        then notices the hold has already destroyed the thing the hold existed to preserve.
        """
        now = now or utc_now()
        report = SweepReport()
        for artifact in sorted(artifacts, key=lambda a: a.id):
            if artifact.tombstoned:
                report.already_tombstoned.append(artifact.id)
                continue
            hold = self.holding(artifact)
            if hold is not None:
                report.held.append((artifact.id, hold.id))
                continue
            rule = self.classification.get(artifact.data_class)
            if rule is None or not rule.expires_at_age(artifact.age(now)):
                continue
            if tombstone is not None:
                tombstone(artifact)
            report.expired.append(artifact.id)
        return report

    def erase(
        self,
        subject: str,
        artifacts: list[Artifact],
        *,
        requested_by: str,
        destroy: Callable[[Artifact], None] | None = None,
        now: datetime | None = None,
    ) -> ErasureReport:
        """Destroy everything erasable for one subject, and report what remains.

        A legal hold *blocks* an erasure rather than overriding it, and the report says so.
        The two obligations genuinely conflict, and resolving that conflict silently -- in
        either direction -- is worse than naming it for the person who has to.
        """
        report = ErasureReport(subject=subject, requested_by=requested_by, at=now or utc_now())
        for artifact in sorted(artifacts, key=lambda a: a.id):
            if subject not in artifact.subjects:
                continue
            hold = self.holding(artifact)
            if hold is not None:
                report.blocked_by_hold.append((artifact.id, hold.id))
                continue
            rule = self.classification.get(artifact.data_class)
            if rule is None or not rule.erasable_by_subject:
                report.unerasable.append(
                    (
                        artifact.id,
                        (
                            f"{artifact.data_class.value} holds references and decisions, "
                            "never bodies; the record that a thing existed and was erased "
                            "survives by design"
                        ),
                    )
                )
                continue
            if destroy is not None:
                destroy(artifact)
            report.erased.append(artifact.id)
        return report
