"""The Memory Fabric: self-organising, self-regulating, self-policing, self-filtering.

See ``docs/harness/memory.md`` for the design and ``docs/PRD.md`` §7.6 for requirements.
"""

from software_factory.memory.admission import ScopeBudget, admit, is_secret_shaped
from software_factory.memory.policing import (
    PolicyReport,
    blast_radius,
    consolidate,
    detect_contradictions,
    enforce_budget,
    expire_and_decay,
    invalidate,
    revalidate_anchors,
    run_pass,
)
from software_factory.memory.promotion import (
    Corroboration,
    PromotionRefused,
    demote,
    promote,
)
from software_factory.memory.records import (
    Candidate,
    Kind,
    Lane,
    Memory,
    PromotionCriterion,
    Rejected,
    RejectionReason,
    Scope,
    Source,
    SourceKind,
)
from software_factory.memory.retrieval import (
    CitedMemory,
    RetrievalRequest,
    RetrievalResult,
    record_use,
    retrieve,
)
from software_factory.memory.store import MemoryStore, MemoryStoreError

__all__ = [
    "Candidate",
    "CitedMemory",
    "Corroboration",
    "Kind",
    "Lane",
    "Memory",
    "MemoryStore",
    "MemoryStoreError",
    "PolicyReport",
    "PromotionCriterion",
    "PromotionRefused",
    "Rejected",
    "RejectionReason",
    "RetrievalRequest",
    "RetrievalResult",
    "Scope",
    "ScopeBudget",
    "Source",
    "SourceKind",
    "admit",
    "blast_radius",
    "consolidate",
    "demote",
    "detect_contradictions",
    "enforce_budget",
    "expire_and_decay",
    "invalidate",
    "is_secret_shaped",
    "promote",
    "record_use",
    "retrieve",
    "revalidate_anchors",
    "run_pass",
]
