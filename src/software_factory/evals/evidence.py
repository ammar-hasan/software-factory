"""Evidence bundles: claims that resolve to artifacts (PRD FR-22, docs/harness/evals.md §2).

The unit of assurance here is ``claim -> artifact``. A summary saying "tests pass" with
no structured result behind it is a gate failure, not a style issue -- that single rule
is what separates evidence from decoration.

Bundles seal. After sealing, a change produces a *new* bundle referencing the old one,
so what a reviewer saw at approval time stays recoverable.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from software_factory.memory.records import utc_now


class EvidenceClass(enum.StrEnum):
    TEST_RESULTS = "test_results"
    COMMAND_TRANSCRIPT = "command_transcript"
    DIFF = "diff"
    TERMINAL_RECORDING = "terminal_recording"
    SCREEN_RECORDING = "screen_recording"
    MEASUREMENT = "measurement"
    CI_RESULT = "ci_result"
    SCORER_RESULT = "scorer_result"
    ARTIFACT = "artifact"


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One artifact. ``truncated`` is a field, never a silent property."""

    id: str
    evidence_class: EvidenceClass
    digest: str
    location: str
    captured_at: datetime = field(default_factory=utc_now)
    redacted: bool = False
    truncated: bool = False
    tombstoned: bool = False
    """True when retention removed the body. The record survives; the content does not.

    A claim pointing at a tombstone renders as "evidence expired" -- never as
    unsupported, and never as satisfied (PRD FR-15.10a).
    """

    def summary(self) -> str:
        marks = []
        if self.truncated:
            marks.append("truncated")
        if self.redacted:
            marks.append("redacted")
        if self.tombstoned:
            marks.append("expired")
        suffix = f" [{', '.join(marks)}]" if marks else ""
        return f"{self.evidence_class.value}:{self.id}{suffix}"


@dataclass(frozen=True, slots=True)
class Claim:
    """Something a run asserted, and the evidence it rests on."""

    text: str
    supported_by: tuple[str, ...] = ()


@dataclass(slots=True)
class EvidenceBundle:
    """Everything produced by one stage of one work item."""

    id: str
    run_id: str
    work_item_id: str
    stage: str
    items: dict[str, EvidenceItem] = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)
    sealed_at: datetime | None = None
    supersedes: str | None = None

    @property
    def sealed(self) -> bool:
        return self.sealed_at is not None

    def add(self, item: EvidenceItem) -> EvidenceItem:
        if self.sealed:
            raise ValueError(
                f"bundle {self.id} is sealed; create a superseding bundle instead of editing it"
            )
        self.items[item.id] = item
        return item

    def claim(self, text: str, *supported_by: str) -> Claim:
        if self.sealed:
            raise ValueError(f"bundle {self.id} is sealed")
        entry = Claim(text=text, supported_by=tuple(supported_by))
        self.claims.append(entry)
        return entry

    def unsupported_claims(self) -> list[Claim]:
        """Claims with no evidence, or whose evidence does not exist in this bundle."""
        return [
            claim
            for claim in self.claims
            if not claim.supported_by or any(ref not in self.items for ref in claim.supported_by)
        ]

    def expired_claims(self) -> list[Claim]:
        """Claims whose every supporting item has been tombstoned by retention."""
        expired = []
        for claim in self.claims:
            if not claim.supported_by:
                continue
            refs = [self.items[r] for r in claim.supported_by if r in self.items]
            if refs and all(item.tombstoned for item in refs):
                expired.append(claim)
        return expired

    def seal(self) -> str:
        """Freeze the bundle and return its digest."""
        self.sealed_at = utc_now()
        material = (
            "|".join(sorted(f"{item.id}:{item.digest}" for item in self.items.values()))
            + "||"
            + "|".join(sorted(c.text for c in self.claims))
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "run": self.run_id,
            "workItem": self.work_item_id,
            "stage": self.stage,
            "sealed": self.sealed,
            "items": [item.summary() for item in self.items.values()],
            "claims": [{"text": c.text, "supportedBy": list(c.supported_by)} for c in self.claims],
        }
