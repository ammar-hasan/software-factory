"""Grouping failures that share a cause (PRD FR-14.2, step one).

The loop's first step is *cluster*, and the reason is economic: diagnosing one failure
costs a run, and diagnosing forty instances of one failure costs forty runs and produces
one answer. What makes clustering possible is that a failure has a **signature** -- a small
set of facts that are stable across instances of the same cause and different across
instances of different ones.

The signature is deliberately built from structure rather than text. Two runs failing the
same gate at the same stage on the same failure class are the same problem; two runs whose
error *messages* look similar may not be, because a message carries the input that produced
it and inputs vary. Text similarity is used only to split a signature that is too coarse,
never to merge two that are distinct -- merging on text is how a clusterer produces a
"cluster" whose members share nothing but vocabulary.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime

from software_factory.digests import digest_parts
from software_factory.memory.records import utc_now
from software_factory.memory.similarity import analyse, jaccard_of

#: How similar two failures' detail text must be to stay in one cluster.
#:
#: Applied only to *split*, never to merge. A low threshold would fragment a real cluster;
#: a high one leaves a coarse cluster whose diagnosis has to cover several causes. This sits
#: deliberately low, because a diagnosis covering two related causes is more useful than two
#: diagnoses covering half a cause each.
SPLIT_THRESHOLD = 0.25

#: Failures below this count are not worth a diagnosis run.
#:
#: Not a claim that a single failure does not matter -- it does, and the gate that caught it
#: already blocked the work. It is a claim about where an *improvement* run's budget goes: a
#: one-off has no pattern to generalise from, and a proposal drawn from one instance is a
#: proposal fitted to one instance.
MIN_CLUSTER_SIZE = 3


class Source(enum.StrEnum):
    """Where an observed failure came from (FR-33.1).

    A gate or scorer failure is a failure mode somebody already encoded as a rule. A
    reviewer's complaint is one nobody has encoded *yet*, which makes it the most valuable
    input this loop could have and the one it ignored -- FR-14.2 clustered only the first
    kind.

    Kept as a field rather than folded into `failure_class` because it changes what a
    proposal may be measured against; see `ImprovementProposal` and FR-33.2.
    """

    ASSURANCE = "assurance"
    REVIEW_COMMENT = "review_comment"


@dataclass(frozen=True, slots=True)
class Failure:
    """One observed failure, as the loop sees it."""

    run_id: str
    work_item_id: str
    stage: str
    agent: str
    scorer: str = ""
    gate: str = ""
    failure_class: str = ""
    detail: str = ""
    source: Source = Source.ASSURANCE
    at: datetime = field(default_factory=utc_now)

    def signature(self) -> str:
        """The structural facts that make two failures the same problem.

        The run id, the work item, and the timestamp are all deliberately absent: including
        any of them would give every failure its own signature, which is a clusterer that
        never clusters.
        """
        return digest_parts(
            self.source.value,
            self.stage.lower(),
            self.agent.lower(),
            self.scorer.lower(),
            self.gate.lower(),
            self.failure_class.lower(),
            length=16,
        )

    def describe(self) -> str:
        parts = [p for p in (self.gate, self.scorer, self.failure_class) if p]
        prefix = "review" if self.source is Source.REVIEW_COMMENT else self.stage
        return f"{prefix}/{self.agent}: {' · '.join(parts) or 'unclassified'}"


@dataclass(frozen=True, slots=True)
class Cluster:
    """Failures that share a cause, and the evidence a diagnosis would start from."""

    signature: str
    failures: tuple[Failure, ...]

    @property
    def source(self) -> Source:
        """Where this cluster's failures came from.

        Every member shares it, because `source` is part of the signature: an assurance
        failure and a reviewer's complaint about the same stage are not the same problem
        even when they look alike, and merging them would hide which one a proposal is
        answering.
        """
        return self.failures[0].source

    @property
    def size(self) -> int:
        return len(self.failures)

    @property
    def stage(self) -> str:
        return self.failures[0].stage

    @property
    def agent(self) -> str:
        return self.failures[0].agent

    @property
    def scorer(self) -> str:
        return self.failures[0].scorer

    @property
    def first_seen(self) -> datetime:
        return min(f.at for f in self.failures)

    @property
    def last_seen(self) -> datetime:
        return max(f.at for f in self.failures)

    @property
    def work_items(self) -> tuple[str, ...]:
        """Distinct work items affected.

        The number that matters more than the failure count: forty failures across two work
        items is a flaky pair, and six failures across six work items is a pattern.
        """
        return tuple(sorted({f.work_item_id for f in self.failures}))

    @property
    def run_ids(self) -> tuple[str, ...]:
        return tuple(sorted({f.run_id for f in self.failures}))

    def describe(self) -> str:
        return (
            f"{self.size} failure(s) across {len(self.work_items)} work item(s): "
            f"{self.failures[0].describe()}"
        )


def cluster_failures(
    failures: list[Failure],
    *,
    min_size: int = MIN_CLUSTER_SIZE,
    split_threshold: float = SPLIT_THRESHOLD,
) -> list[Cluster]:
    """Group failures by signature, splitting a signature whose details diverge.

    Returned largest-first, because a loop with a budget should spend it on the pattern that
    is costing the most.
    """
    by_signature: dict[str, list[Failure]] = {}
    for failure in failures:
        by_signature.setdefault(failure.signature(), []).append(failure)

    clusters: list[Cluster] = []
    for signature, members in by_signature.items():
        groups = _split(members, split_threshold)
        for group in groups:
            if len(group) < min_size:
                continue
            # A split group is still the same structural signature, suffixed so a diagnosis
            # and a cooling period can address one half without silencing the other.
            #
            # The suffix is derived from the group's own members, not from its position in
            # the split. As an enumeration index it was a property of how the ledger
            # happened to be read: the same three failures carried `sig` on one read and
            # `sig.1` on another, so FR-14.6's "a rejected proposal must not return without
            # new evidence" was defeated by reading the log the other way round -- or by one
            # extra detail-free failure arriving and re-sorting the groups.
            key = signature if len(groups) == 1 else f"{signature}.{_group_key(group)}"
            clusters.append(Cluster(signature=key, failures=tuple(group)))

    clusters.sort(key=lambda c: (-c.size, -len(c.work_items), c.signature))
    return clusters


def _split(members: list[Failure], threshold: float) -> list[list[Failure]]:
    """Split one signature's failures where their detail text has nothing in common.

    Single-link over detail similarity. Failures with no detail are never split apart:
    absence of text is not evidence of a different cause, and treating it as such would
    scatter every failure whose gate reported a bare verdict.
    """
    detailed = [f for f in members if f.detail.strip()]
    if len(detailed) < 2:
        return [members]

    # Sorted before grouping, so the result is a property of the *set* of failures rather
    # than of the order they arrived in. Single-link grouping is order-sensitive by nature,
    # and the anti-thrash rule downstream keys on the outcome.
    detailed = sorted(detailed, key=lambda f: (f.detail, f.run_id, f.work_item_id))

    # Keyed by identity, not by `run_id`: one run can report several failures, and keying
    # by run made the second overwrite the first, so a cluster could hold two members whose
    # details had nothing in common.
    analysed = {id(f): analyse(f.detail) for f in detailed}
    groups: list[list[Failure]] = []
    for failure in detailed:
        for group in groups:
            if any(
                jaccard_of(analysed[id(failure)].tokens, analysed[id(other)].tokens) >= threshold
                for other in group
            ):
                group.append(failure)
                break
        else:
            groups.append([failure])

    if len(groups) < 2:
        return [members]

    # Detail-free failures join the largest group rather than forming one of their own.
    # Sorted by a content key rather than by size alone: ties in size were broken by
    # arrival order, and that order then reached the cluster signature.
    bare = [f for f in members if not f.detail.strip()]
    if bare:
        groups.sort(key=lambda g: (-len(g), _group_key(g)))
        groups[0].extend(bare)
    return groups


def _group_key(group: list[Failure]) -> str:
    """A short, content-addressed identifier for a split group.

    Derived from the members themselves so the same members always carry the same key,
    whatever order they were read in. `run_id` alone is not enough -- one run can produce
    several failures -- so the work item and gate go in too.
    """
    return digest_parts(
        *sorted(f"{f.run_id}\x00{f.work_item_id}\x00{f.gate}" for f in group), length=8
    )
