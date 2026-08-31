"""The policy pass: contradiction, staleness, consolidation, and poisoning containment.

Implements PRD FR-6.5, FR-6.6, FR-6.8, FR-6.12 and docs/harness/memory.md §5, §8.

The pass is deterministic and idempotent: running it twice on an unchanged store must
produce the same actions the second time as none at all. Every action it takes is
recorded with a reason, so an operator can always answer "why did this memory move?".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from software_factory.memory.promotion import demote
from software_factory.memory.records import (
    Lane,
    Memory,
    Source,
    SourceKind,
    utc_now,
)
from software_factory.memory.similarity import (
    analyse,
    containment_of,
    jaccard_of,
    negates_of,
)
from software_factory.memory.store import MemoryStore
from software_factory.spec.units import derived_trust

DUPLICATE_MERGE_THRESHOLD = 0.8
CONSOLIDATION_CONTAINMENT = 0.9
COLLAPSE_PENALTY = 0.5
CANON_FLOOR = 0.35
STALE_PENALTY = 0.6


@dataclass(slots=True)
class PolicyReport:
    """What one pass did. Empty is the healthy steady state."""

    quarantined: list[str] = field(default_factory=list)
    contradictions: list[tuple[str, str]] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    merged: list[tuple[tuple[str, ...], str]] = field(default_factory=list)
    invalidated: list[str] = field(default_factory=list)
    weakened: list[str] = field(default_factory=list)
    evicted: list[str] = field(default_factory=list)

    @property
    def acted(self) -> bool:
        return any(
            (
                self.quarantined,
                self.expired,
                self.stale,
                self.merged,
                self.invalidated,
                self.weakened,
                self.evicted,
            )
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "quarantined": self.quarantined,
            "contradictions": [list(pair) for pair in self.contradictions],
            "expired": self.expired,
            "stale": self.stale,
            "merged": [{"from": list(sources), "into": target} for sources, target in self.merged],
            "invalidated": self.invalidated,
            "weakened": self.weakened,
            "evicted": self.evicted,
        }


def detect_contradictions(store: MemoryStore) -> PolicyReport:
    """Quarantine *both* sides of a contradiction (memory.md M-9).

    Not the newer one. When code is reverted, the older memory is frequently the correct
    one, and a system that assumes recency implies correctness will confidently keep the
    wrong claim.
    """
    report = PolicyReport()
    live = [m for m in store.all() if m.lane in (Lane.CANDIDATE, Lane.CANON)]

    # Tokenize once, then compare only the pairs that could possibly match. `negates`
    # requires three shared content words, so an inverted index over those words is not a
    # heuristic here -- it enumerates exactly the candidate set. Without both, one scope at
    # its 5000-item budget meant ~12.5M comparisons and ~25M tokenizations in a `sf memory
    # policy --apply` the operator is waiting on.
    analysed = {memory.id: analyse(memory.content) for memory in live}
    by_id = {memory.id: memory for memory in live}
    # An explicit position map: `live.index(right)` inside the loop would be a linear scan
    # per candidate, putting back the quadratic factor this is removing.
    position = {memory.id: rank for rank, memory in enumerate(live)}
    index_by_token: dict[str, set[str]] = {}
    for memory in live:
        for token in analysed[memory.id].tokens:
            index_by_token.setdefault(token, set()).add(memory.id)

    for index, left in enumerate(live):
        neighbours: set[str] = set()
        for token in analysed[left.id].tokens:
            neighbours |= index_by_token.get(token, set())
        for right_id in sorted(neighbours):
            right = by_id[right_id]
            # Preserve the original pair ordering so each pair is considered once, in the
            # same order as before: quarantine reasons name "left contradicts right".
            if position[right_id] <= index:
                continue
            if left.scope is not right.scope or left.scope_ref != right.scope_ref:
                continue
            if left.quarantined and right.quarantined:
                continue
            if not negates_of(analysed[left.id], analysed[right.id]):
                continue

            report.contradictions.append((left.id, right.id))
            for memory, other in ((left, right), (right, left)):
                if memory.quarantined and other.id in memory.contradicts:
                    continue
                memory.quarantined = True
                memory.contradicts = tuple(sorted({*memory.contradicts, other.id}))
                store.put(
                    memory,
                    op="quarantine",
                    actor="policy",
                    reason=f"contradicts {other.id}; both sides held pending resolution",
                )
                report.quarantined.append(memory.id)
    return report


def expire_and_decay(store: MemoryStore, *, now: datetime | None = None) -> PolicyReport:
    """Archive what has expired; weaken what no longer resolves."""
    now = now or utc_now()
    report = PolicyReport()

    for memory in store.all():
        if memory.lane is Lane.ARCHIVE:
            continue
        if memory.is_expired(now):
            demote(memory, store, reason="ttl expired without revalidation")
            report.expired.append(memory.id)

    return report


def revalidate_anchors(store: MemoryStore, *, resolve: object) -> PolicyReport:
    """Re-resolve anchor memories against current code (memory.md §5.2).

    Staleness is checked by digest, never by asking a model whether something is still
    true -- a model asked that question will usually say yes.
    """
    from software_factory.spec.units import digest_text

    report = PolicyReport()
    resolver = resolve if callable(resolve) else (lambda _locator: None)

    for memory in store.all():
        if memory.lane is Lane.ARCHIVE or memory.kind.value != "anchor":
            continue
        for source in memory.provenance:
            if not source.locator:
                continue
            current = resolver(source.locator)
            if current is None:
                demote(memory, store, reason=f"anchor {source.locator} no longer resolves")
                report.stale.append(memory.id)
                break
            if not source.excerpt_digest:
                continue
            observed = digest_text(current)
            if observed == source.excerpt_digest:
                if memory.stale_for is not None:
                    # The anchor came back. Clear the mark so a *future* change weakens again.
                    memory.stale_for = None
                    store.put(
                        memory,
                        op="revalidate",
                        actor="policy",
                        reason=f"anchor {source.locator} matches the recorded excerpt again",
                    )
                continue

            # Weakening is a transition, not a repeated multiplication. `excerpt_digest` is
            # never rewritten, so the mismatch is permanent: re-applying the penalty each
            # pass drove confidence to zero in a fortnight of nightly runs and kept
            # `report.weakened` non-empty forever, so an operator never saw the empty report
            # that this module's docstring calls the healthy steady state.
            mark = f"{source.locator}@{observed}"
            if memory.stale_for == mark:
                break
            memory.confidence *= STALE_PENALTY
            memory.stale_for = mark
            store.put(
                memory,
                op="weaken",
                actor="policy",
                reason=f"anchor {source.locator} changed under this memory",
            )
            report.weakened.append(memory.id)
            break
    return report


def consolidate(store: MemoryStore) -> PolicyReport:
    """Merge near-duplicates, preserving the union of provenance (memory.md M-13).

    A merged memory is better-sourced than any of its inputs, never worse. That property
    is what makes consolidation safe to run automatically.
    """
    report = PolicyReport()
    # Cluster within a lane *and* a trust class, never across either. Clustering across
    # them let an untrusted candidate absorb and archive a canon memory, or let a canon
    # memory swallow untrusted provenance and an attacker-set confidence -- promotion
    # into canon is supposed to be earned, and consolidation must not be a side door.
    by_group: dict[tuple[str, str, str, str], list[Memory]] = {}
    for memory in store.all():
        if memory.lane not in (Lane.CANDIDATE, Lane.CANON) or memory.quarantined:
            continue
        by_group.setdefault(
            (memory.scope.value, memory.scope_ref, memory.lane.value, memory.trust.value), []
        ).append(memory)

    for group in by_group.values():
        clusters = _cluster(group)
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            merged = _merge(cluster, store)
            report.merged.append((tuple(sorted(m.id for m in cluster)), merged.id))
    return report


def _cluster(memories: list[Memory]) -> list[list[Memory]]:
    """Single-link clustering on lexical similarity. Deterministic given a stable order.

    Tokenized once and pruned by an inverted index. Both thresholds need a non-empty token
    intersection to be met at all, so a memory sharing no content word with any cluster
    member cannot join it -- which makes the index exact rather than approximate. The
    previous form tested each unassigned memory against every member of a growing cluster,
    re-tokenizing both sides each time: O(n^3) tokenizations in the worst case.
    """
    ordered = sorted(memories, key=lambda m: m.id)
    analysed = {memory.id: analyse(memory.content) for memory in ordered}
    index_by_token: dict[str, set[str]] = {}
    for memory in ordered:
        for token in analysed[memory.id].tokens:
            index_by_token.setdefault(token, set()).add(memory.id)

    clusters: list[list[Memory]] = []
    assigned: set[str] = set()
    position = {memory.id: rank for rank, memory in enumerate(ordered)}

    for memory in ordered:
        if memory.id in assigned:
            continue
        cluster = [memory]
        assigned.add(memory.id)
        # Single-link: a new member brings its own neighbours into consideration, so the
        # frontier grows as the cluster does. Iterating in the declared order keeps the
        # result identical to the exhaustive scan it replaces.
        frontier = [memory]
        while frontier:
            member = frontier.pop(0)
            member_tokens = analysed[member.id]
            candidates: set[str] = set()
            for token in member_tokens.tokens:
                candidates |= index_by_token.get(token, set())
            for other_id in sorted(candidates - assigned, key=lambda i: position[i]):
                other = ordered[position[other_id]]
                other_tokens = analysed[other_id]
                if (
                    jaccard_of(member_tokens.tokens, other_tokens.tokens)
                    >= DUPLICATE_MERGE_THRESHOLD
                    or containment_of(member_tokens.tokens, other_tokens.tokens)
                    >= CONSOLIDATION_CONTAINMENT
                ):
                    cluster.append(other)
                    assigned.add(other_id)
                    frontier.append(other)
        clusters.append(sorted(cluster, key=lambda m: position[m.id]))
    return clusters


def _merge(cluster: list[Memory], store: MemoryStore) -> Memory:
    """Fold a cluster into its best-sourced member.

    The survivor is the one with the most provenance -- it is already the most defensible
    claim -- and it inherits the union of every input's sources, evidence, and parents.
    """
    survivor = max(cluster, key=lambda m: (len(m.provenance_ids()), m.confidence, m.id))
    others = [m for m in cluster if m.id != survivor.id]

    sources: dict[str, Source] = {s.identity(): s for s in survivor.provenance}
    evidence = set(survivor.evidence)
    parents = set(survivor.parents)
    supersedes = set(survivor.supersedes)

    for memory in others:
        for source in memory.provenance:
            sources.setdefault(source.identity(), source)
        evidence.update(memory.evidence)
        parents.update(memory.parents)
        supersedes.add(memory.id)

    survivor.provenance = tuple(sorted(sources.values(), key=lambda s: s.identity()))
    survivor.evidence = tuple(sorted(evidence))
    # A merged member that listed the survivor -- or another member -- as a parent would
    # otherwise make the survivor its own ancestor. That is where the cycles in the
    # provenance graph come from in ordinary operation, not from hand-built data.
    survivor.parents = tuple(sorted(parents - {m.id for m in cluster}))
    survivor.supersedes = tuple(sorted(supersedes))
    # Trust is monotone downward even within a group: a merge can only ever lower it.
    survivor.trust = derived_trust(*(m.trust for m in cluster))
    survivor.confidence = max(m.confidence for m in cluster)
    survivor.created_at = min(m.created_at for m in cluster)
    survivor.helped_count = sum(m.helped_count for m in cluster)
    store.put(
        survivor,
        op="merge",
        actor="policy",
        reason=f"absorbed {', '.join(sorted(m.id for m in others))}",
    )

    for memory in others:
        memory.superseded_by = survivor.id
        memory.lane = Lane.ARCHIVE
        store.put(memory, op="supersede", actor="policy", reason=f"merged into {survivor.id}")
    return survivor


def invalidate(
    store: MemoryStore, memory_id: str, *, reason: str, actor: str = "policy"
) -> PolicyReport:
    """Withdraw a memory and everything that rests on it (PRD FR-6.6, memory.md M-15).

    This is the containment mechanism for poisoning. A descendant whose *entire*
    provenance collapses is archived; one that keeps an independent source is penalised
    and may fall out of Canon, but is not silently kept at full confidence.
    """
    report = PolicyReport()
    root = store.get(memory_id)
    if root is None:
        return report

    demote(root, store, reason=reason, actor=actor)
    report.invalidated.append(root.id)

    descendants = store.descendants_of(memory_id)

    # An already-archived descendant was skipped *and* left out of `invalidated`, so its own
    # children still saw it as a surviving parent. A -> B -> C with B archived earlier left C
    # merely weakened, though its entire provenance ran through two withdrawn memories. A
    # withdrawn memory supports nothing; seed it into the set rather than stepping over it.
    invalidated = {root.id} | {d.id for d in descendants if d.lane is Lane.ARCHIVE}

    # `descendants_of` is breadth-first over a graph the store itself documents as possibly
    # cyclic, so arrival order is not topological -- a child can be examined before the
    # parent whose collapse decides it. Iterate to a fixed point instead of trusting order.
    pending = [d for d in descendants if d.lane is not Lane.ARCHIVE]
    collapsed: list[Memory] = []
    changed = True
    while changed:
        changed = False
        surviving: list[Memory] = []
        for candidate in pending:
            if any(parent not in invalidated for parent in candidate.parents):
                surviving.append(candidate)
                continue
            collapsed.append(candidate)
            invalidated.add(candidate.id)
            changed = True
        pending = surviving

    for descendant in collapsed:
        demote(
            descendant,
            store,
            reason=f"provenance collapsed: every parent traces to invalidated {root.id}",
            actor=actor,
        )
        report.invalidated.append(descendant.id)

    for descendant in pending:
        descendant.confidence *= COLLAPSE_PENALTY
        if descendant.lane is Lane.CANON and descendant.confidence < CANON_FLOOR:
            descendant.lane = Lane.ARCHIVE
            store.put(
                descendant,
                op="demote",
                actor=actor,
                reason=f"weakened below the canon floor by invalidation of {root.id}",
            )
            report.invalidated.append(descendant.id)
        else:
            store.put(
                descendant,
                op="weaken",
                actor=actor,
                reason=f"partial provenance collapse from {root.id}",
            )
            report.weakened.append(descendant.id)
    return report


def blast_radius(store: MemoryStore, memory_id: str) -> dict[str, object]:
    """What invalidating this memory would affect (memory.md M-17).

    Run before accepting a high-fan-out claim: a memory that hundreds of others rest on
    deserves more scrutiny than one nothing depends on.
    """
    descendants = store.descendants_of(memory_id)
    return {
        "memory": memory_id,
        "descendants": [m.id for m in descendants],
        "canon_affected": [m.id for m in descendants if m.lane is Lane.CANON],
        "total": len(descendants),
    }


def enforce_budget(
    store: MemoryStore,
    scope: str,
    scope_ref: str,
    *,
    max_items: int,
    max_bytes: int,
    now: datetime | None = None,
) -> PolicyReport:
    """Archive the lowest-value memories until the scope is under budget (FR-6.12).

    Memory must be able to shrink, and what was dropped is always recorded -- an
    operator who cannot see what a pass removed cannot trust the pass.
    """
    from software_factory.memory.records import Scope

    now = now or utc_now()
    report = PolicyReport()
    live = [
        m for m in store.in_scope(Scope(scope), scope_ref) if m.lane in (Lane.CANDIDATE, Lane.CANON)
    ]

    def over() -> bool:
        # `>=`, matching admission. With `>` the pass considered a scope holding exactly
        # `max_items` to be fine while admission refused it -- and the rejection told the
        # operator to run this pass, the one action that could not help. The scope stayed
        # closed until someone archived by hand. Eviction now leaves room for one more,
        # which is the state admission accepts.
        # UTF-8 bytes, matching `ScopeBudget.max_bytes`. Characters under-counted a
        # non-Latin store by two to four times against a bound named in bytes.
        used = sum(len(m.content.encode("utf-8")) for m in live)
        return len(live) >= max_items or used >= max_bytes

    if not over():
        return report

    for memory in sorted(live, key=lambda m: (m.value_density(now), m.id)):
        if not over():
            break
        demote(
            memory,
            store,
            reason=(f"scope budget: archived at value density {memory.value_density(now):.6f}"),
        )
        live.remove(memory)
        report.evicted.append(memory.id)
    return report


def run_pass(
    store: MemoryStore,
    *,
    resolve: object | None = None,
    now: datetime | None = None,
) -> PolicyReport:
    """Run the whole policy pass in dependency order.

    Contradictions first (a quarantined memory must not be merged into a clean one),
    then expiry, then anchor revalidation, then consolidation.
    """
    combined = PolicyReport()
    for report in (
        detect_contradictions(store),
        expire_and_decay(store, now=now),
        revalidate_anchors(store, resolve=resolve) if resolve else PolicyReport(),
        consolidate(store),
    ):
        combined.quarantined += report.quarantined
        combined.contradictions += report.contradictions
        combined.expired += report.expired
        combined.stale += report.stale
        combined.merged += report.merged
        combined.invalidated += report.invalidated
        combined.weakened += report.weakened
        combined.evicted += report.evicted
    return combined


def source_of(kind: SourceKind, ref: str, locator: str = "", digest: str = "") -> Source:
    """Convenience constructor used by callers building provenance."""
    return Source(kind=kind, ref=ref, locator=locator, excerpt_digest=digest)
