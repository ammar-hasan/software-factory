"""Scorers and benchmarks: measuring in aggregate, not blocking (docs/harness/evals.md §4-5).

A scorer classifies a *sample* of completed runs. It never blocks and never influences
the run it scores. The important rule is that **the grader is a subject, not an
authority**: before a scorer's verdicts may drive change, its agreement with human
labels is measured, and a scorer that disagrees with people is marked untrusted rather
than believed.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from statistics import mean, pstdev

MIN_HUMAN_LABELS = 30
MIN_AGREEMENT = 0.8
MIN_KAPPA = 0.6
"""Raw agreement alone rewards a scorer that always answers with the majority label.

Cohen's kappa discounts chance agreement, so both thresholds must be met.
"""


class ScoreOutcome(enum.StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Label:
    value: str
    score: float
    description: str = ""


@dataclass(frozen=True, slots=True)
class ScoreResult:
    run_id: str
    scorer: str
    scorer_version: int
    label: str
    score: float
    reasoning: str = ""
    outcome: ScoreOutcome = ScoreOutcome.PASS

    def as_dict(self) -> dict[str, object]:
        return {
            "run": self.run_id,
            "scorer": self.scorer,
            "scorerVersion": self.scorer_version,
            "label": self.label,
            "score": self.score,
            "outcome": self.outcome.value,
        }


@dataclass(slots=True)
class Scorer:
    """One classification question about a class of runs."""

    name: str
    labels: tuple[Label, ...]
    passing_score: float
    sampling_rate: int = 25
    version: int = 1
    self_improvement: bool = False
    judge_engine: tuple[str, str] = ("oz", "judge-model")
    agreement: float | None = None
    kappa: float | None = None
    labelled_sample: int = 0
    outcome_partner: str | None = None
    """The §11.1 outcome metric this scorer's failures should move (PRD FR-14.7a).

    A scorer with no outcome partner cannot drive self-improvement: without one, there is
    no way to tell an improvement from a scorer that learned to be easier to satisfy.
    """

    def classify(self, label_value: str) -> ScoreOutcome:
        for label in self.labels:
            if label.value == label_value:
                return ScoreOutcome.PASS if label.score >= self.passing_score else ScoreOutcome.FAIL
        return ScoreOutcome.ERROR

    def score_of(self, label_value: str) -> float:
        for label in self.labels:
            if label.value == label_value:
                return label.score
        return 0.0

    def samples(self, run_id: str) -> bool:
        """Deterministic sampling: the same run is always sampled or never is.

        Random sampling would make a scorer's coverage unreproducible, and a benchmark
        comparing two configurations would be comparing different samples.
        """
        if self.sampling_rate <= 0:
            return False
        if self.sampling_rate >= 100:
            return True
        digest = hashlib.sha256(f"{self.name}:{run_id}".encode()).hexdigest()
        return int(digest[:8], 16) % 100 < self.sampling_rate

    @property
    def trusted(self) -> bool:
        """Whether this scorer's verdicts may gate adoption or drive improvement."""
        return (
            self.labelled_sample >= MIN_HUMAN_LABELS
            and self.agreement is not None
            and self.agreement >= MIN_AGREEMENT
            and self.kappa is not None
            and self.kappa >= MIN_KAPPA
        )

    def untrusted_reason(self) -> str | None:
        if self.labelled_sample < MIN_HUMAN_LABELS:
            return (
                f"only {self.labelled_sample} human-labelled runs; needs {MIN_HUMAN_LABELS} "
                "before its verdicts can drive change"
            )
        if self.agreement is None or self.agreement < MIN_AGREEMENT:
            return f"agrees with humans {(self.agreement or 0):.0%}; needs {MIN_AGREEMENT:.0%}"
        if self.kappa is None or self.kappa < MIN_KAPPA:
            return (
                f"kappa {(self.kappa or 0):.2f} needs {MIN_KAPPA:.2f}; raw agreement alone "
                "rewards always answering with the majority label"
            )
        return None

    def may_drive_improvement(self) -> tuple[bool, str]:
        """Two conditions: the judge is trusted, and its failures map to a real outcome."""
        if not self.self_improvement:
            return False, "self-improvement is not enabled for this scorer"
        reason = self.untrusted_reason()
        if reason is not None:
            return False, reason
        if not self.outcome_partner:
            return False, (
                "no outcome partner declared; without one there is no way to tell an "
                "improvement from a scorer that learned to be easier to satisfy"
            )
        return True, "trusted, with a declared outcome partner"


def cohens_kappa(judge: list[str], human: list[str]) -> float:
    """Agreement corrected for chance. Returns 0.0 for degenerate inputs."""
    if not judge or len(judge) != len(human):
        return 0.0
    n = len(judge)
    observed = sum(1 for a, b in zip(judge, human, strict=True) if a == b) / n
    categories = set(judge) | set(human)
    expected = sum((judge.count(c) / n) * (human.count(c) / n) for c in categories)
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return (observed - expected) / (1 - expected)


@dataclass(frozen=True, slots=True)
class Trial:
    task_id: str
    configuration: str
    passed: bool
    cost: float = 0.0
    latency_s: float = 0.0


@dataclass(slots=True)
class ConfigurationSummary:
    configuration: str
    trials: int
    pass_rate: float
    pass_rate_spread: float
    cost: float
    latency_s: float

    def as_dict(self) -> dict[str, object]:
        return {
            "configuration": self.configuration,
            "trials": self.trials,
            "passRate": round(self.pass_rate, 4),
            "passRateSpread": round(self.pass_rate_spread, 4),
            "cost": round(self.cost, 4),
            "latencySeconds": round(self.latency_s, 3),
        }


@dataclass(slots=True)
class BenchmarkReport:
    """A comparison, deliberately without a winner.

    Collapsing pass rate, cost, and latency into one number chooses the weighting for
    the operator. The report presents the trade-off; the operator decides (PRD FR-13.9).
    """

    name: str
    summaries: list[ConfigurationSummary] = field(default_factory=list)
    holdout_used: bool = False
    excluded_notes: tuple[str, ...] = ()

    def difference_is_meaningful(self, left: str, right: str) -> bool:
        """True only when the gap exceeds the combined spread of the two configurations.

        A difference inside the noise is not a difference, and reporting it as one is how
        a benchmark becomes a rubber stamp for whatever was tried most recently.
        """
        a = self._summary(left)
        b = self._summary(right)
        if a is None or b is None:
            return False
        return abs(a.pass_rate - b.pass_rate) > (a.pass_rate_spread + b.pass_rate_spread)

    def _summary(self, configuration: str) -> ConfigurationSummary | None:
        for summary in self.summaries:
            if summary.configuration == configuration:
                return summary
        return None

    def as_dict(self) -> dict[str, object]:
        return {
            "benchmark": self.name,
            "holdoutUsed": self.holdout_used,
            "configurations": [s.as_dict() for s in self.summaries],
            "notes": list(self.excluded_notes),
            "winner": None,  # deliberately absent
        }


def summarise(name: str, trials: list[Trial], *, holdout_used: bool = False) -> BenchmarkReport:
    """Aggregate trials per configuration, reporting spread rather than hiding it."""
    by_configuration: dict[str, list[Trial]] = {}
    for trial in trials:
        by_configuration.setdefault(trial.configuration, []).append(trial)

    report = BenchmarkReport(name=name, holdout_used=holdout_used)
    for configuration, group in sorted(by_configuration.items()):
        by_task: dict[str, list[bool]] = {}
        for trial in group:
            by_task.setdefault(trial.task_id, []).append(trial.passed)
        per_task_rates = [sum(v) / len(v) for v in by_task.values()]
        report.summaries.append(
            ConfigurationSummary(
                configuration=configuration,
                trials=len(group),
                pass_rate=sum(t.passed for t in group) / len(group),
                # Spread across tasks, not across all trials: repetitions on one task are
                # not independent samples.
                pass_rate_spread=pstdev(per_task_rates) if len(per_task_rates) > 1 else 0.0,
                cost=sum(t.cost for t in group),
                latency_s=mean(t.latency_s for t in group) if group else 0.0,
            )
        )
    return report


@dataclass(frozen=True, slots=True)
class ImprovementProposal:
    """A self-improvement proposal, with the counter-metrics that could refuse it."""

    target: str
    kind: str
    rationale: str
    regressions_addressed: tuple[str, ...]
    metric_delta: float
    counter_metrics: dict[str, float] = field(default_factory=dict)
    holdout_delta: float | None = None
    edits_assurance: bool = False


@dataclass(frozen=True, slots=True)
class ProposalVerdict:
    accepted: bool
    reason: str
    requires_second_reviewer: bool = False


COUNTER_METRIC_TOLERANCE = -0.02


def evaluate_proposal(
    proposal: ImprovementProposal, scorer: Scorer | None = None
) -> ProposalVerdict:
    """Decide whether an improvement proposal may be adopted (PRD FR-14.7, FR-14.7a).

    Three defences, all mandatory: held-out validation, self-referential flagging, and a
    counter-metric panel. Together they cover the case the self-referential flag alone
    misses -- grader capture that edits no grader, only what the agent writes.
    """
    if scorer is not None:
        allowed, reason = scorer.may_drive_improvement()
        if not allowed:
            return ProposalVerdict(False, f"scorer {scorer.name} may not drive change: {reason}")

    if proposal.holdout_delta is None:
        return ProposalVerdict(
            False,
            (
                "no held-out validation; a proposal validated only on the tasks that motivated "
                "it has not been shown to generalise"
            ),
        )
    if proposal.holdout_delta <= 0:
        return ProposalVerdict(
            False,
            (
                f"held-out performance moved {proposal.holdout_delta:+.1%}; the gain does not "
                "survive contact with tasks the loop could not see"
            ),
        )

    degraded = {
        name: value
        for name, value in proposal.counter_metrics.items()
        if value < COUNTER_METRIC_TOLERANCE
    }
    if degraded:
        listed = ", ".join(f"{k} {v:+.1%}" for k, v in sorted(degraded.items()))
        return ProposalVerdict(
            False,
            f"target improved but counter-metrics degraded: {listed}",
        )

    if proposal.edits_assurance:
        return ProposalVerdict(
            True,
            (
                "self-referential: this proposal edits a scorer, gate, eval, threshold or the "
                "held-out set, and may not be validated by the artefact it modifies"
            ),
            requires_second_reviewer=True,
        )

    return ProposalVerdict(
        True,
        (
            f"target {proposal.metric_delta:+.1%}, held-out {proposal.holdout_delta:+.1%}, "
            "no counter-metric degraded"
        ),
    )
