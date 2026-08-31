"""Promotion: how a claim earns its way into Canon (PRD FR-6.4, FR-6.4a, FR-6.4b).

The critical rule, and the one most systems get wrong: **corroboration is computed over
sources, not over runs.** Two runs that read the same issue comment are one observation
sampled twice. Without a provenance-set intersection, untrusted text launders itself
into Canon by being read twice, and from there it is rendered as a *cited convention* in
every subsequent pack.
"""

from __future__ import annotations

from dataclasses import dataclass

from software_factory.memory.admission import untrusted_barred_from_canon
from software_factory.memory.records import (
    Lane,
    Memory,
    PromotionCriterion,
    PromotionRecord,
    utc_now,
)
from software_factory.memory.store import MemoryStore

#: Kinds whose claims can, in principle, be checked by running something. For these,
#: agreement between agents is not enough (memory.md M-25).
CHECKABLE_KINDS = frozenset({"fact", "anchor", "metric", "failure"})


@dataclass(frozen=True, slots=True)
class PromotionRefused:
    code: str
    message: str
    remediation: str


@dataclass(frozen=True, slots=True)
class Corroboration:
    """An independent observation offered in support of a claim."""

    memory_id: str
    run_id: str
    model: str
    tool_path: str
    provenance_ids: frozenset[str]


def promote(
    memory: Memory,
    store: MemoryStore,
    *,
    criterion: PromotionCriterion,
    evidence: tuple[str, ...],
    actor: str,
    corroboration: Corroboration | None = None,
    origin_run: str | None = None,
    origin_model: str | None = None,
) -> Memory | PromotionRefused:
    """Move a memory from Candidate to Canon, if it has earned it."""
    if memory.lane is Lane.CANON:
        return memory
    if memory.lane is not Lane.CANDIDATE:
        return PromotionRefused(
            "promotion.wrong_lane",
            f"only candidate memories can be promoted; {memory.id} is {memory.lane.value}",
            "Archive memories do not re-enter Canon; the claim must be re-derived.",
        )

    if untrusted_barred_from_canon(memory):
        return PromotionRefused(
            "promotion.untrusted",
            f"{memory.id} carries untrusted provenance and may never enter canon",
            (
                "Content originating outside the definition cannot become a cited convention. "
                "Verify the claim with a deterministic check, or have a person confirm it, "
                "and record that as the source."
            ),
        )

    if memory.quarantined:
        return PromotionRefused(
            "promotion.quarantined",
            f"{memory.id} is quarantined pending a contradiction resolution",
            "Resolve the contradiction first; operating on a disputed claim is worse than not.",
        )

    if not evidence:
        return PromotionRefused(
            "promotion.no_evidence",
            "promotion must record the evidence that satisfied it",
            "Cite the test, run, or person that established this claim.",
        )

    if criterion is PromotionCriterion.CORROBORATION:
        refusal = _check_corroboration(memory, corroboration, origin_run, origin_model)
        if refusal is not None:
            return refusal

    memory.lane = Lane.CANON
    memory.promotion = PromotionRecord(
        criterion=criterion, evidence=evidence, actor=actor, at=utc_now()
    )
    memory.updated_at = utc_now()
    return store.put(
        memory,
        op="promote",
        actor=actor,
        reason=f"promoted to canon by {criterion.value}",
    )


def _check_corroboration(
    memory: Memory,
    corroboration: Corroboration | None,
    origin_run: str | None,
    origin_model: str | None,
) -> PromotionRefused | None:
    """Corroboration must be independent in *both* senses: engine and source."""
    if corroboration is None:
        return PromotionRefused(
            "promotion.no_corroboration",
            "corroboration was claimed but none was supplied",
            "Supply the corroborating run, its model, and its provenance set.",
        )

    if memory.kind.value in CHECKABLE_KINDS and _is_deterministically_checkable(memory):
        return PromotionRefused(
            "promotion.verification_available",
            (
                f"{memory.id} makes a claim that can be checked deterministically; "
                "agreement between agents is weaker than one passing check"
            ),
            "Run the check and promote by verification instead.",
        )

    if origin_run is not None and corroboration.run_id == origin_run:
        return PromotionRefused(
            "promotion.same_run",
            "a run cannot corroborate itself",
            "Corroboration must come from a different run.",
        )

    if origin_model is not None and corroboration.model == origin_model:
        return PromotionRefused(
            "promotion.same_engine",
            (
                f"corroborating run used the same model ({corroboration.model}); "
                "that is one observation sampled twice, not two observations"
            ),
            "Corroborate with a different model or a different tool path.",
        )

    shared = memory.provenance_ids() & set(corroboration.provenance_ids)
    if shared:
        return PromotionRefused(
            "promotion.shared_provenance",
            (
                f"both observations derive from the same source(s): {', '.join(sorted(shared))}. "
                "Reading one issue comment twice is one observation"
            ),
            (
                "Corroborate from a disjoint source, or verify the claim with a deterministic "
                "check. This rule is what stops untrusted text from laundering into canon."
            ),
        )

    return None


def _is_deterministically_checkable(memory: Memory) -> bool:
    """Whether a claim of this shape has an obvious mechanical check.

    Conservative: an anchor always resolves or does not; a metric can be re-measured.
    Facts and failures are only treated as checkable when they carry a test or CI source,
    which is a signal that a check already exists.
    """
    if memory.kind.value in {"anchor", "metric"}:
        return True
    return any(source.kind.value in {"test", "ci"} for source in memory.provenance)


def demote(memory: Memory, store: MemoryStore, *, reason: str, actor: str = "policy") -> Memory:
    """Move a memory to Archive. Cheap on purpose (memory.md M-26).

    The bar to enter Canon is high and the bar to leave is low, because the cost of a
    wrong memory is unbounded and the cost of a missing one is a single retrieval.
    """
    memory.lane = Lane.ARCHIVE
    memory.updated_at = utc_now()
    return store.put(memory, op="demote", actor=actor, reason=reason)
