"""Ledger entries: the unit of record (PRD FR-15.1).

Every consequential thing the factory does becomes one of these. Entries are
hash-chained, so a reader can verify that nothing was inserted, removed, or edited
after the fact -- which is what makes "derived state is rebuildable from the ledger"
(FR-15.2) a guarantee rather than a hope.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

GENESIS = "0" * 64
"""``prev_hash`` of the first entry in a chain."""


class EntryType(enum.StrEnum):
    """Recorded event types. Values are persisted; never rename, only add."""

    DEFINITION_APPLIED = "definition.applied"
    WORK_ITEM_CREATED = "work_item.created"
    WORK_ITEM_TRANSITION = "work_item.transition"
    WORK_ITEM_BLOCKED = "work_item.blocked"
    RUN_STARTED = "run.started"
    RUN_FINISHED = "run.finished"
    PACK_ASSEMBLED = "pack.assembled"
    TOOL_CALLED = "tool.called"
    MODEL_CALLED = "model.called"
    ESCALATION = "run.escalation"
    VIOLATION = "run.violation"
    CHECKPOINT = "run.checkpoint"
    COMPACTION = "run.compaction"
    REPAIR_ATTEMPT = "run.repair"
    GATE_EVALUATED = "gate.evaluated"
    SCORE_RECORDED = "score.recorded"
    MEMORY_MUTATED = "memory.mutated"
    SKILL_LIFECYCLE = "skill.lifecycle"
    SPEC_DELTA = "spec.delta"
    HUMAN_DECISION = "human.decision"
    CHECKPOINT_OPENED = "checkpoint.opened"
    CHECKPOINT_ESCALATED = "checkpoint.escalated"
    CHECKPOINT_RESOLVED = "checkpoint.resolved"
    BUDGET_EVENT = "budget.event"
    SEGMENT_SEALED = "segment.sealed"


def utc_now() -> str:
    """Timestamp in RFC 3339 with a ``Z`` suffix, at microsecond resolution."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One append-only record.

    ``hash`` covers every other field including ``prev_hash``, so any edit anywhere in
    the chain invalidates every entry after it.
    """

    seq: int
    ts: str
    type: EntryType
    actor: str
    subject: str
    payload: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = GENESIS
    hash: str = ""

    def digest(self) -> str:
        """Compute this entry's hash from its content.

        No `default=str` fallback. A payload value JSON could not serialise was hashed as
        `str(value)`, and `str()` of a set depends on `PYTHONHASHSEED` -- grants and effect
        sets are frozensets and plausible payload values, so an entry sealed in one process
        and verified in another could report a "content hash mismatch" that was nothing but
        two different iteration orders. A tamper-evidence mechanism that cries wolf is one
        that gets ignored.

        A non-serialisable payload is refused at `Ledger.append` instead, where the caller
        can still fix it.
        """
        material = json.dumps(
            {
                "seq": self.seq,
                "ts": self.ts,
                "type": self.type.value,
                "actor": self.actor,
                "subject": self.subject,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def sealed(self) -> LedgerEntry:
        """Return a copy with ``hash`` computed."""
        from dataclasses import replace

        return replace(self, hash=self.digest())

    def verify(self) -> bool:
        return bool(self.hash) and self.hash == self.digest()

    def to_json(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "ts": self.ts,
                "type": self.type.value,
                "actor": self.actor,
                "subject": self.subject,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
                "hash": self.hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )

    @classmethod
    def from_json(cls, line: str) -> LedgerEntry:
        raw = json.loads(line)
        return cls(
            seq=int(raw["seq"]),
            ts=str(raw["ts"]),
            type=EntryType(raw["type"]),
            actor=str(raw["actor"]),
            subject=str(raw["subject"]),
            payload=dict(raw.get("payload") or {}),
            prev_hash=str(raw.get("prev_hash", GENESIS)),
            hash=str(raw.get("hash", "")),
        )
