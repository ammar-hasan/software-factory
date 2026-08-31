"""The eight-stage retrieval filter (PRD FR-6.7, docs/harness/memory.md §6).

A fixed, ordered pipeline, each stage individually testable:

    1. SCOPE      hard filter to scopes the agent is granted
    2. LANE       canon only, unless the agent opted in
    3. DISPUTE    drop quarantined and open-contradiction memories entirely
    4. FRESHNESS  drop expired
    5. RELEVANCE  rank against the task and the change surface
    6. DIVERSITY  cap per source and per parent cluster
    7. BUDGET     truncate at the section budget, whole items only
    8. CITE       emit with id, lane, confidence, and a provenance summary

Stage 3 is a *drop*, not a demotion. A disputed memory never reaches an agent in any
lane: operating on a claim that is under dispute is worse than operating without it.

Stage 6 is what stops one confident extraction from colouring every subsequent run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from software_factory.memory.records import Lane, Memory, Scope, utc_now
from software_factory.memory.similarity import relevance
from software_factory.memory.store import MemoryStore

DEFAULT_DIVERSITY_CAP = 0.3
DEFAULT_PARENT_CAP = 0.5
MIN_RELEVANCE = 0.05


@dataclass(frozen=True, slots=True)
class CitedMemory:
    """A memory as an agent sees it: always with its lane, confidence, and sources."""

    id: str
    content: str
    kind: str
    lane: str
    confidence: float
    sources: tuple[str, ...]
    unverified: bool

    def render(self) -> str:
        """One line for a pack. The `[unverified]` marker is never omitted."""
        mark = " [unverified]" if self.unverified else ""
        return f"{self.content}{mark}  ({self.id}, {self.kind}, confidence {self.confidence:.2f})"


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    query: str
    scopes: tuple[tuple[Scope, str], ...]
    surfaces: frozenset[str] = frozenset()
    include_candidate: bool = False
    limit: int = 12
    diversity_cap: float = DEFAULT_DIVERSITY_CAP
    parent_cap: float = DEFAULT_PARENT_CAP


@dataclass(slots=True)
class RetrievalResult:
    memories: list[CitedMemory]
    considered: int = 0
    dropped_disputed: int = 0
    dropped_expired: int = 0
    dropped_diversity: int = 0
    truncated: int = 0
    partial: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "returned": len(self.memories),
            "considered": self.considered,
            "droppedDisputed": self.dropped_disputed,
            "droppedExpired": self.dropped_expired,
            "droppedDiversity": self.dropped_diversity,
            "truncated": self.truncated,
            "partial": self.partial,
        }


def retrieve(
    store: MemoryStore, request: RetrievalRequest, *, now: datetime | None = None
) -> RetrievalResult:
    """Run the pipeline. Never writes: usage statistics are recorded out of band."""
    now = now or utc_now()
    result = RetrievalResult(memories=[])

    # 1. SCOPE
    pool: list[Memory] = []
    for scope, scope_ref in request.scopes:
        pool.extend(store.in_scope(scope, scope_ref))
    result.considered = len(pool)

    # 2. LANE
    allowed = {Lane.CANON} | ({Lane.CANDIDATE} if request.include_candidate else set())
    pool = [m for m in pool if m.lane in allowed]

    # 3. DISPUTE -- a drop, in every lane
    before = len(pool)
    pool = [m for m in pool if not m.quarantined and not m.contradicts]
    result.dropped_disputed = before - len(pool)

    # 4. FRESHNESS
    before = len(pool)
    pool = [m for m in pool if not m.is_expired(now)]
    result.dropped_expired = before - len(pool)

    # 5. RELEVANCE
    scored: list[tuple[float, Memory]] = []
    for memory in pool:
        score = relevance(memory.content, request.query, set(request.surfaces))
        if score < MIN_RELEVANCE:
            continue
        scored.append((score * memory.effective_confidence(), memory))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))

    # 6. DIVERSITY
    kept, dropped = _apply_diversity(
        scored, limit=request.limit, source_cap=request.diversity_cap, parent_cap=request.parent_cap
    )
    result.dropped_diversity = dropped

    # 7. BUDGET -- whole items only
    if len(kept) > request.limit:
        result.truncated = len(kept) - request.limit
        kept = kept[: request.limit]

    # 8. CITE
    result.memories = [
        CitedMemory(
            id=memory.id,
            content=memory.content,
            kind=memory.kind.value,
            lane=memory.lane.value,
            confidence=memory.effective_confidence(),
            sources=tuple(source.identity() for source in memory.provenance),
            unverified=memory.lane is Lane.CANDIDATE,
        )
        for memory in kept
    ]
    return result


def _apply_diversity(
    scored: list[tuple[float, Memory]],
    *,
    limit: int,
    source_cap: float,
    parent_cap: float,
) -> tuple[list[Memory], int]:
    """Cap how much of a result any one source or parent cluster may supply.

    The cap is computed against the requested limit rather than against what happens to
    be available, so a small result set cannot be dominated by one source just because
    little else matched.
    """
    max_per_source = max(1, int(limit * source_cap))
    max_per_parent = max(1, int(limit * parent_cap))

    per_source: dict[str, int] = {}
    per_parent: dict[str, int] = {}
    kept: list[Memory] = []
    dropped = 0

    for _score, memory in scored:
        source_ids = memory.provenance_ids() or {"<none>"}
        # Only memories that actually share a parent form a cluster. Grouping every
        # root memory under one synthetic key would cap unrelated memories against
        # each other, which is not what a parent cap is for.
        parent_ids = set(memory.parents)

        if any(per_source.get(sid, 0) >= max_per_source for sid in source_ids):
            dropped += 1
            continue
        if parent_ids and any(per_parent.get(pid, 0) >= max_per_parent for pid in parent_ids):
            dropped += 1
            continue

        for sid in source_ids:
            per_source[sid] = per_source.get(sid, 0) + 1
        for pid in parent_ids:
            per_parent[pid] = per_parent.get(pid, 0) + 1
        kept.append(memory)

    return kept, dropped


def record_use(
    store: MemoryStore, memory_ids: list[str], *, helped: bool, actor: str = "harness"
) -> None:
    """Record that memories were used, and whether the run they served passed.

    ``helped_count`` moves only when ``helped`` is true, which happens only for a run
    that then passed its gates. Being retrieved is not being useful (memory.md M-30),
    and conflating the two makes the eviction ranking reward noise.
    """
    now = utc_now()
    for memory_id in memory_ids:
        # A compact usage event, not a full `put`. Writing the entire serialised memory --
        # content, provenance, promotion record -- to increment two integers meant one run
        # appended up to `RetrievalRequest.limit` (12) such records, so a factory doing 200
        # runs a day wrote ~2400 bookkeeping records daily and `load()` read all of them.
        store.note_use(memory_id, helped=helped, actor=actor, at=now)
