"""File-backed memory store (PRD FR-6.14, FR-6.15).

An append-only JSONL log plus an in-memory index. The log is the truth; the index is a
cache that can always be rebuilt from it. This is the same choice the ledger makes, for
the same reason: it needs no service, so a laptop-only factory is the reference
implementation rather than a degraded one.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from software_factory.errors import FactoryError
from software_factory.memory.records import Lane, Memory, Scope, utc_now

try:  # pragma: no cover - platform dependent
    import fcntl

    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    _HAVE_FCNTL = False


class MemoryStoreError(FactoryError):
    """The memory log is unreadable or inconsistent."""


@dataclass(frozen=True, slots=True)
class Mutation:
    """One recorded change to a memory. Every mutation is auditable (FR-6.11)."""

    memory_id: str
    op: str
    actor: str
    reason: str
    at: datetime
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class MemoryStore:
    """Memories for one factory, with an append-only mutation log."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._memories: dict[str, Memory] = {}
        self._loaded = False

    # ------------------------------------------------------------------- lifecycle

    def load(self) -> None:
        """Rebuild the index by replaying the log. Idempotent."""
        self._memories = {}
        if self.path.exists():
            for number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise MemoryStoreError(
                        f"{self.path}:{number}: malformed memory record: {exc}",
                        remediation=(
                            "The memory log is append-only. Restore it from backup, or "
                            "remove the corrupt line and rebuild the index."
                        ),
                    ) from exc
                self._apply(record)
        self._loaded = True

    def _apply(self, record: dict[str, Any]) -> None:
        op = record.get("op")
        if op == "delete":
            self._memories.pop(str(record["memory"]["id"]), None)
            return
        memory = Memory.from_dict(record["memory"])
        self._memories[memory.id] = memory

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # --------------------------------------------------------------------- writing

    def put(self, memory: Memory, *, op: str, actor: str, reason: str) -> Memory:
        """Write a memory and record the mutation that produced it."""
        self._ensure_loaded()
        memory.updated_at = utc_now()
        record = {
            "op": op,
            "actor": actor,
            "reason": reason,
            "at": memory.updated_at.isoformat(),
            "memory": memory.as_dict(),
        }
        with self._locked(), self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._memories[memory.id] = memory
        return memory

    def erase(self, memory_id: str, *, actor: str, reason: str) -> None:
        """Destroy a memory's content, leaving the mutation record (FR-15.10b).

        Erasure is by reference: the log keeps the fact that something was erased, by
        whom and why, and destroys what it pointed at. This is what makes deletion
        possible at all in an append-only design.
        """
        self._ensure_loaded()
        existing = self._memories.get(memory_id)
        if existing is None:
            return
        tombstone = {
            "op": "delete",
            "actor": actor,
            "reason": reason,
            "at": utc_now().isoformat(),
            "memory": {"id": memory_id, "digest": existing.digest()},
        }
        with self._locked(), self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(tombstone, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._memories.pop(memory_id, None)

    # --------------------------------------------------------------------- reading

    def get(self, memory_id: str) -> Memory | None:
        self._ensure_loaded()
        return self._memories.get(memory_id)

    def all(self) -> list[Memory]:
        self._ensure_loaded()
        return list(self._memories.values())

    def in_scope(self, scope: Scope, scope_ref: str) -> list[Memory]:
        self._ensure_loaded()
        return [m for m in self._memories.values() if m.scope is scope and m.scope_ref == scope_ref]

    def in_lane(self, lane: Lane) -> list[Memory]:
        self._ensure_loaded()
        return [m for m in self._memories.values() if m.lane is lane]

    def children_of(self, memory_id: str) -> list[Memory]:
        self._ensure_loaded()
        return [m for m in self._memories.values() if memory_id in m.parents]

    def descendants_of(self, memory_id: str) -> list[Memory]:
        """Every memory transitively derived from ``memory_id``.

        Breadth-first with a visited set, because provenance graphs are not guaranteed
        acyclic once merges enter the picture.
        """
        self._ensure_loaded()
        seen: set[str] = set()
        frontier = [memory_id]
        found: list[Memory] = []
        while frontier:
            current = frontier.pop()
            for child in self.children_of(current):
                if child.id in seen:
                    continue
                seen.add(child.id)
                found.append(child)
                frontier.append(child.id)
        return found

    def provenance_tree(self, memory_id: str) -> dict[str, Any]:
        """The complete "why does this exist" answer (FR-6.11, memory.md M-34).

        A memory a human cannot trace is a memory a human should not accept, so this is
        the subsystem's primary trust instrument.
        """
        self._ensure_loaded()
        memory = self._memories.get(memory_id)
        if memory is None:
            return {"id": memory_id, "found": False}
        return {
            "id": memory.id,
            "found": True,
            "lane": memory.lane.value,
            "kind": memory.kind.value,
            "trust": memory.trust.value,
            "content": memory.content,
            "confidence": memory.effective_confidence(),
            "sources": [s.as_dict() for s in memory.provenance],
            "promotion": (
                {
                    "criterion": memory.promotion.criterion.value,
                    "evidence": list(memory.promotion.evidence),
                    "actor": memory.promotion.actor,
                }
                if memory.promotion
                else None
            ),
            "parents": [self.provenance_tree(pid) for pid in memory.parents],
        }

    def mutations(self, memory_id: str | None = None) -> list[Mutation]:
        """Replay the log as a mutation history, optionally for one memory."""
        history: list[Mutation] = []
        if not self.path.exists():
            return history
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            target = str(record["memory"]["id"])
            if memory_id is not None and target != memory_id:
                continue
            history.append(
                Mutation(
                    memory_id=target,
                    op=str(record["op"]),
                    actor=str(record.get("actor", "")),
                    reason=str(record.get("reason", "")),
                    at=datetime.fromisoformat(record["at"]),
                    after=record.get("memory"),
                )
            )
        return history

    # -------------------------------------------------------------------- utilities

    @staticmethod
    def new_id() -> str:
        return f"mem_{uuid.uuid4().hex[:16]}"

    def stats(self) -> dict[str, int]:
        self._ensure_loaded()
        counts = dict.fromkeys((lane.value for lane in Lane), 0)
        for memory in self._memories.values():
            counts[memory.lane.value] += 1
        counts["total"] = len(self._memories)
        counts["quarantined"] = sum(1 for m in self._memories.values() if m.quarantined)
        counts["bytes"] = sum(len(m.content) for m in self._memories.values())
        return counts

    @contextmanager
    def _locked(self) -> Iterator[None]:
        if not _HAVE_FCNTL:  # pragma: no cover - Windows
            yield
            return
        with self._lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                with suppress(OSError):
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
