"""Admission control: what is allowed to become a memory at all (PRD FR-6.2, FR-6.4b).

Eight checks, each with its own rejection reason. Rejections are recorded rather than
swallowed, because the *distribution* of reasons is a diagnostic: a spike in
``UNSOURCED`` means an agent's extraction prompt is wrong, not that memory is broken.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from software_factory.memory.records import (
    Candidate,
    Kind,
    Lane,
    Memory,
    Rejected,
    RejectionReason,
    default_expiry,
)
from software_factory.memory.similarity import jaccard, negates
from software_factory.memory.store import MemoryStore
from software_factory.spec.units import TrustClass

DUPLICATE_THRESHOLD = 0.85
CONTRADICTION_TOPIC_THRESHOLD = 0.45
MAX_CLAIM_CHARS = 600

#: A claim joining two independent assertions cannot be selectively invalidated, so it
#: is refused at the door rather than split later (memory.md M-3).
_COMPOUND = re.compile(
    r"(?:\.\s+[A-Z])"  # two sentences
    r"|(?:\band\s+also\b)"
    r"|(?:;\s*(?:additionally|moreover|furthermore)\b)"
    r"|(?:\bfirst\b.{0,80}\bsecond\b)",
    re.IGNORECASE | re.DOTALL,
)

#: Shapes that look like credentials. Deliberately broad: a false positive costs one
#: rejected memory, a false negative writes a secret into a store that feeds prompts.
_SECRET_SHAPED = re.compile(
    r"(?:sk-[A-Za-z0-9]{16,})"
    r"|(?:gh[pousr]_[A-Za-z0-9]{20,})"
    r"|(?:github_pat_[A-Za-z0-9_]{20,})"
    r"|(?:xox[baprs]-[A-Za-z0-9-]{10,})"
    r"|(?:AKIA[0-9A-Z]{16})"
    r"|(?:-----BEGIN [A-Z ]*PRIVATE KEY-----)"
    r"|(?:eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)


@dataclass(frozen=True, slots=True)
class ScopeBudget:
    max_items: int = 5000
    max_bytes: int = 8_000_000


def admit(
    candidate: Candidate,
    store: MemoryStore,
    *,
    granted_scopes: set[str] | None = None,
    budget: ScopeBudget | None = None,
) -> Memory | Rejected:
    """Run every admission check, returning the new Candidate-lane memory or a rejection.

    Checks run cheapest-first so a malformed candidate is refused without touching the
    store.
    """
    budget = budget or ScopeBudget()

    if not candidate.content.strip():
        return Rejected(
            RejectionReason.INCOMPLETE,
            "a memory needs content",
            "Write the claim, in one sentence.",
        )
    if not candidate.provenance:
        return Rejected(
            RejectionReason.UNSOURCED,
            "a memory needs at least one source",
            "Record where this claim came from: a run, a tool result, a file, or a person.",
        )
    if candidate.kind is Kind.ANCHOR and not any(s.locator for s in candidate.provenance):
        return Rejected(
            RejectionReason.INCOMPLETE,
            "an anchor memory needs a locator to re-resolve against",
            "Record the file and symbol this anchor points at.",
        )

    if len(candidate.content) > MAX_CLAIM_CHARS or _COMPOUND.search(candidate.content):
        return Rejected(
            RejectionReason.COMPOUND_CLAIM,
            "this looks like more than one claim",
            (
                "Split it. A memory holding two claims cannot be selectively invalidated "
                "when one of them turns out to be wrong."
            ),
        )

    if _SECRET_SHAPED.search(candidate.content):
        return Rejected(
            RejectionReason.SECRET_SUSPECTED,
            "the content looks like it contains a credential",
            "Remove it. If this is a real credential, rotate it now.",
        )

    if granted_scopes is not None:
        key = f"{candidate.scope.value}:{candidate.scope_ref}"
        if key not in granted_scopes:
            return Rejected(
                RejectionReason.OUT_OF_SCOPE,
                f"this agent may not write to {key}",
                "Grant the scope in the agent's configuration, or write to a scope it holds.",
            )

    existing = store.in_scope(candidate.scope, candidate.scope_ref)

    # Contradiction is checked before duplication, and both are checked before anything
    # is written. A claim that negates canon is lexically very close to it, so testing
    # for duplication first would report the more serious problem as the lesser one.
    for memory in existing:
        if memory.lane is not Lane.CANON or memory.quarantined:
            continue
        if negates(
            memory.content, candidate.content, topic_threshold=CONTRADICTION_TOPIC_THRESHOLD
        ):
            return Rejected(
                RejectionReason.CONTRADICTION,
                f"contradicts canon memory {memory.id}",
                (
                    "Resolve the contradiction with evidence before writing. Both claims are "
                    "now in question, not just the older one."
                ),
                conflicting=(memory.id,),
            )

    for memory in existing:
        if memory.lane is Lane.ARCHIVE:
            continue
        if negates(memory.content, candidate.content):
            continue  # a negation is not a duplicate, whatever the token overlap says
        if jaccard(memory.content, candidate.content) >= DUPLICATE_THRESHOLD:
            return Rejected(
                RejectionReason.DUPLICATE,
                f"near-duplicate of {memory.id}",
                "Merge into the existing memory instead, so provenance accumulates.",
                conflicting=(memory.id,),
            )

    live = [m for m in existing if m.lane is not Lane.ARCHIVE]
    if len(live) >= budget.max_items or sum(len(m.content) for m in live) >= budget.max_bytes:
        return Rejected(
            RejectionReason.BUDGET,
            f"scope {candidate.scope.value}:{candidate.scope_ref} is at its budget",
            "Run the policy pass to consolidate and archive, then retry.",
        )

    memory = Memory(
        id=store.new_id(),
        lane=Lane.CANDIDATE,
        kind=candidate.kind,
        scope=candidate.scope,
        scope_ref=candidate.scope_ref,
        content=candidate.content.strip(),
        provenance=candidate.provenance,
        confidence=candidate.confidence,
        trust=candidate.trust,
        evidence=candidate.evidence,
        parents=candidate.parents,
        expires_on=default_expiry(candidate.kind),
    )
    return store.put(memory, op="admit", actor="admission", reason="passed admission control")


def is_secret_shaped(text: str) -> bool:
    """Exposed for gate reuse: the same screen guards diffs and evidence."""
    return bool(_SECRET_SHAPED.search(text))


def untrusted_barred_from_canon(memory: Memory) -> bool:
    """Untrusted content may never reach Canon (PRD FR-6.4b).

    This is the structural half of the injection defence: text an attacker can write
    cannot become a cited convention, no matter how many runs repeat it.
    """
    return memory.trust is TrustClass.UNTRUSTED
