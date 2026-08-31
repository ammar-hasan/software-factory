"""The Living Spec: versioned intent that can block a change.

See ``docs/harness/living-spec.md`` for the design and ``docs/PRD.md`` §7.5 for the
requirements this implements.
"""

from software_factory.spec.agreement import evaluate, find_conflicts
from software_factory.spec.delta import (
    BehaviourChange,
    Change,
    ChangeKind,
    DeltaProblem,
    ImpactReport,
    SpecDelta,
    apply_delta,
    impact_of,
    validate_delta,
)
from software_factory.spec.units import (
    Agreement,
    AgreementResult,
    CodeAnchor,
    Criterion,
    SpecStore,
    SpecUnit,
    TestAnchor,
    TrustClass,
    UnitStatus,
    criterion_is_checkable,
    derived_trust,
    digest_text,
)

__all__ = [
    "Agreement",
    "AgreementResult",
    "BehaviourChange",
    "Change",
    "ChangeKind",
    "CodeAnchor",
    "Criterion",
    "DeltaProblem",
    "ImpactReport",
    "SpecDelta",
    "SpecStore",
    "SpecUnit",
    "TestAnchor",
    "TrustClass",
    "UnitStatus",
    "apply_delta",
    "criterion_is_checkable",
    "derived_trust",
    "digest_text",
    "evaluate",
    "find_conflicts",
    "impact_of",
    "validate_delta",
]
