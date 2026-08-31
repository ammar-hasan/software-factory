"""Memory records: lanes, kinds, provenance (PRD FR-6, docs/harness/memory.md).

The whole subsystem is shaped by one asymmetry:

    the cost of a wrong memory is unbounded and compounding;
    the cost of a missing memory is one retrieval.

So the bar to enter Canon is high and the bar to leave is low, provenance is mandatory
rather than nice-to-have, and a claim nobody can trace is a claim nobody should act on.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Self

from software_factory.spec.units import TrustClass


class Lane(enum.StrEnum):
    """Where a memory sits in its lifecycle. Promotion between lanes is earned."""

    WORKING = "working"
    CANDIDATE = "candidate"
    CANON = "canon"
    ARCHIVE = "archive"


class Kind(enum.StrEnum):
    """What sort of claim a memory makes. Each kind has its own admission rules and decay."""

    FACT = "fact"
    CONVENTION = "convention"
    DECISION = "decision"
    FAILURE = "failure"
    PREFERENCE = "preference"
    PROCEDURE = "procedure"
    ANCHOR = "anchor"
    METRIC = "metric"


class Scope(enum.StrEnum):
    """Visibility. A memory is never seen outside its scope without an audited widening."""

    RUN = "run"
    WORK_ITEM = "work-item"
    REPOSITORY = "repository"
    FACTORY = "factory"
    TEAM = "team"
    PERSONAL = "personal"


class SourceKind(enum.StrEnum):
    RUN = "run"
    TOOL = "tool"
    FILE = "file"
    HUMAN = "human"
    TEST = "test"
    CI = "ci"
    EXTERNAL = "external"


class PromotionCriterion(enum.StrEnum):
    CORROBORATION = "corroboration"
    VERIFICATION = "verification"
    HUMAN = "human"


class RejectionReason(enum.StrEnum):
    """Why an admission was refused. Monitored: a spike in one of these is a signal.

    A rise in ``UNSOURCED`` means an agent's extraction prompt is wrong, not that memory
    is broken -- which is exactly the distinction an operator needs to act.
    """

    INCOMPLETE = "incomplete"
    COMPOUND_CLAIM = "compound_claim"
    UNSOURCED = "unsourced"
    DUPLICATE = "duplicate"
    CONTRADICTION = "contradiction"
    OUT_OF_SCOPE = "out_of_scope"
    BUDGET = "budget"
    SECRET_SUSPECTED = "secret_suspected"
    UNTRUSTED = "untrusted"


#: Default lifetime per kind, in days. ``None`` means "until superseded".
DEFAULT_TTL_DAYS: dict[Kind, int | None] = {
    Kind.FACT: 90,
    Kind.CONVENTION: 180,
    Kind.DECISION: None,
    # Failure memories are the highest-value kind and the most neglected elsewhere:
    # "we tried X and it broke because Y" prevents more waste than "X is the answer"
    # creates. Long-lived on purpose.
    Kind.FAILURE: 180,
    Kind.PREFERENCE: None,
    Kind.PROCEDURE: 60,
    Kind.ANCHOR: 30,
    Kind.METRIC: 30,
}

#: How much a kind is worth when evicting under budget pressure.
KIND_WEIGHT: dict[Kind, float] = {
    Kind.FACT: 1.0,
    Kind.CONVENTION: 1.4,
    Kind.DECISION: 1.6,
    Kind.FAILURE: 1.8,
    Kind.PREFERENCE: 1.5,
    Kind.PROCEDURE: 1.2,
    Kind.ANCHOR: 0.8,
    Kind.METRIC: 0.5,
}

SINGLE_SOURCE_CONFIDENCE_CAP = 0.6
"""A Canon memory tracing to one unverified source cannot outweigh corroborated ones.

Without this cap, one confident extraction dominates every pack it appears in, which is
the poisoning failure this subsystem exists to prevent.
"""


def _as_utc(value: datetime) -> datetime:
    """Read a naive datetime as UTC. Every timestamp this package writes is UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Source:
    """Where a claim came from, addressed so staleness is mechanical."""

    kind: SourceKind
    ref: str
    locator: str = ""
    excerpt_digest: str = ""
    trust: TrustClass = TrustClass.INTERNAL

    def identity(self) -> str:
        """Stable identity for provenance-set intersection (memory.md M-23, FR-6.4a)."""
        return f"{self.kind.value}:{self.ref}:{self.locator}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "ref": self.ref,
            "locator": self.locator,
            "excerpt_digest": self.excerpt_digest,
            "trust": self.trust.value,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Source:
        return cls(
            kind=SourceKind(raw["kind"]),
            ref=str(raw["ref"]),
            locator=str(raw.get("locator", "")),
            excerpt_digest=str(raw.get("excerpt_digest", "")),
            trust=TrustClass(raw.get("trust", TrustClass.INTERNAL.value)),
        )


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    """Why a memory is in Canon. Canon membership is always explicable."""

    criterion: PromotionCriterion
    evidence: tuple[str, ...]
    actor: str
    at: datetime


@dataclass(slots=True)
class Memory:
    """One claim, with everything needed to trust it or to withdraw it."""

    id: str
    lane: Lane
    kind: Kind
    scope: Scope
    scope_ref: str
    content: str
    provenance: tuple[Source, ...]
    confidence: float = 0.5
    trust: TrustClass = TrustClass.INTERNAL
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    expires_on: datetime | None = None
    parents: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    promotion: PromotionRecord | None = None
    supersedes: tuple[str, ...] = ()
    superseded_by: str | None = None
    contradicts: tuple[str, ...] = ()
    quarantined: bool = False
    last_used_at: datetime | None = None
    stale_for: str | None = None
    """The ``locator@digest`` this memory has already been penalised for.

    Staleness is a state, not an event. Without this the drift penalty was re-applied on
    every policy pass -- the source's ``excerpt_digest`` is never rewritten, so the mismatch
    was permanent and a nightly pass drove confidence to zero in a fortnight. Recording what
    the penalty was for makes the pass idempotent, and lets a *second, different* change to
    the same anchor weaken the memory again, which is the behaviour the penalty is for.
    """
    use_count: int = 0
    helped_count: int = 0
    """Incremented only when this memory was cited in a run that then passed its gates.

    Being retrieved is not being useful, and conflating the two makes the eviction
    ranking reward noise (memory.md M-30).
    """

    def digest(self) -> str:
        return hashlib.sha256(
            f"{self.kind.value}|{self.scope.value}:{self.scope_ref}|{self.content}".encode()
        ).hexdigest()

    def provenance_ids(self) -> set[str]:
        return {source.identity() for source in self.provenance}

    def is_expired(self, now: datetime | None = None) -> bool:
        """Whether this memory's TTL has passed.

        `Memory` is a plain dataclass, so a caller can assign a naive `expires_on` and
        every comparison here would raise `TypeError: can't compare offset-naive and
        offset-aware datetimes` -- from the retrieval pipeline, on a claim that was
        otherwise fine. A naive value is read as UTC rather than refused: the store's own
        timestamps are all UTC, so that is the only reading that could have been meant.
        """
        if self.expires_on is None:
            return False
        return _as_utc(now or utc_now()) >= _as_utc(self.expires_on)

    def effective_confidence(self) -> float:
        """Confidence after the single-source cap. Decay is applied by the policy pass.

        The cap lives here rather than in retrieval so it cannot be forgotten by a second
        call site: a single-source memory is capped everywhere it is read.
        """
        capped = self.confidence
        if len(self.provenance_ids()) <= 1 and self.promotion is None:
            capped = min(capped, SINGLE_SOURCE_CONFIDENCE_CAP)
        return max(0.0, min(1.0, capped))

    def value_density(self, now: datetime | None = None) -> float:
        """Eviction ranking: worth per byte (memory.md M-28).

        Rare-but-critical memories are protected by ``KIND_WEIGHT`` rather than by
        recency alone, because a failure memory that has not been needed in months is
        exactly the one worth keeping.
        """
        now = now or utc_now()
        age_days = max(0.0, (now - (self.last_used_at or self.created_at)).total_seconds() / 86400)
        recency = 1.0 / (1.0 + age_days / 30.0)
        helpfulness = 1.0 + (self.helped_count**0.5)
        size = max(1, len(self.content))
        return float(
            self.effective_confidence()
            * recency
            * helpfulness
            * KIND_WEIGHT.get(self.kind, 1.0)
            / (size**0.5)
        )

    def with_lane(self, lane: Lane) -> Self:
        self.lane = lane
        self.updated_at = utc_now()
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "lane": self.lane.value,
            "kind": self.kind.value,
            "scope": self.scope.value,
            "scope_ref": self.scope_ref,
            "content": self.content,
            "provenance": [s.as_dict() for s in self.provenance],
            "confidence": self.confidence,
            "trust": self.trust.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_on": self.expires_on.isoformat() if self.expires_on else None,
            "parents": list(self.parents),
            "evidence": list(self.evidence),
            "promotion": (
                {
                    "criterion": self.promotion.criterion.value,
                    "evidence": list(self.promotion.evidence),
                    "actor": self.promotion.actor,
                    "at": self.promotion.at.isoformat(),
                }
                if self.promotion
                else None
            ),
            "supersedes": list(self.supersedes),
            "superseded_by": self.superseded_by,
            "contradicts": list(self.contradicts),
            "quarantined": self.quarantined,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "stale_for": self.stale_for,
            "use_count": self.use_count,
            "helped_count": self.helped_count,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Memory:
        promotion_raw = raw.get("promotion")
        return cls(
            id=str(raw["id"]),
            lane=Lane(raw["lane"]),
            kind=Kind(raw["kind"]),
            scope=Scope(raw["scope"]),
            scope_ref=str(raw.get("scope_ref", "")),
            content=str(raw["content"]),
            provenance=tuple(Source.from_dict(s) for s in raw.get("provenance", ())),
            confidence=float(raw.get("confidence", 0.5)),
            trust=TrustClass(raw.get("trust", TrustClass.INTERNAL.value)),
            created_at=datetime.fromisoformat(raw["created_at"]),
            updated_at=datetime.fromisoformat(raw["updated_at"]),
            expires_on=(
                datetime.fromisoformat(raw["expires_on"]) if raw.get("expires_on") else None
            ),
            parents=tuple(raw.get("parents", ())),
            evidence=tuple(raw.get("evidence", ())),
            promotion=(
                PromotionRecord(
                    criterion=PromotionCriterion(promotion_raw["criterion"]),
                    evidence=tuple(promotion_raw.get("evidence", ())),
                    actor=str(promotion_raw.get("actor", "")),
                    at=datetime.fromisoformat(promotion_raw["at"]),
                )
                if promotion_raw
                else None
            ),
            supersedes=tuple(raw.get("supersedes", ())),
            superseded_by=raw.get("superseded_by"),
            contradicts=tuple(raw.get("contradicts", ())),
            quarantined=bool(raw.get("quarantined", False)),
            last_used_at=(
                datetime.fromisoformat(raw["last_used_at"]) if raw.get("last_used_at") else None
            ),
            stale_for=raw.get("stale_for"),
            use_count=int(raw.get("use_count", 0)),
            helped_count=int(raw.get("helped_count", 0)),
        )


def default_expiry(kind: Kind, created: datetime | None = None) -> datetime | None:
    days = DEFAULT_TTL_DAYS.get(kind)
    if days is None:
        return None
    return (created or utc_now()) + timedelta(days=days)


@dataclass(frozen=True, slots=True)
class Candidate:
    """A proposed memory, before admission control has looked at it."""

    kind: Kind
    scope: Scope
    scope_ref: str
    content: str
    provenance: tuple[Source, ...]
    confidence: float = 0.5
    trust: TrustClass = TrustClass.INTERNAL
    evidence: tuple[str, ...] = ()
    parents: tuple[str, ...] = ()

    def provenance_ids(self) -> set[str]:
        return {source.identity() for source in self.provenance}


@dataclass(frozen=True, slots=True)
class Rejected:
    reason: RejectionReason
    message: str
    remediation: str
    conflicting: tuple[str, ...] = ()
