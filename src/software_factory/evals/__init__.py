"""Assurance: gates that block, scorers that sample, benchmarks that compare.

Three mechanisms answering three different questions, deliberately kept apart. See
``docs/harness/evals.md`` and ``docs/PRD.md`` §7.13.
"""

from software_factory.evals.evidence import (
    Claim,
    EvidenceBundle,
    EvidenceClass,
    EvidenceItem,
)
from software_factory.evals.gates import (
    BASELINE_GATES,
    STAGE_GATES,
    Finding,
    GateContext,
    GateOutcome,
    GateReport,
    GateResult,
    Severity,
    ViolationClass,
    run_gates,
)
from software_factory.evals.results import (
    FailureClass,
    Outcome,
    TestResult,
    TestRun,
    classify_failure,
)
from software_factory.evals.scorers import (
    BenchmarkReport,
    ImprovementProposal,
    Label,
    ProposalVerdict,
    ScoreOutcome,
    Scorer,
    ScoreResult,
    Trial,
    cohens_kappa,
    evaluate_proposal,
    summarise,
)

__all__ = [
    "BASELINE_GATES",
    "STAGE_GATES",
    "BenchmarkReport",
    "Claim",
    "EvidenceBundle",
    "EvidenceClass",
    "EvidenceItem",
    "FailureClass",
    "Finding",
    "GateContext",
    "GateOutcome",
    "GateReport",
    "GateResult",
    "ImprovementProposal",
    "Label",
    "Outcome",
    "ProposalVerdict",
    "ScoreOutcome",
    "ScoreResult",
    "Scorer",
    "Severity",
    "TestResult",
    "TestRun",
    "Trial",
    "ViolationClass",
    "classify_failure",
    "cohens_kappa",
    "evaluate_proposal",
    "run_gates",
    "summarise",
]
