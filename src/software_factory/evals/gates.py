"""Gates: the blocking checks a stage must pass (PRD FR-13, docs/harness/evals.md §3).

Three properties hold for every gate here:

* **Deterministic where possible.** A gate that needs a model to decide is a weaker gate,
  and all but one of the baseline set are pure functions over structured input.
* **No pass by timeout.** A gate that cannot run returns ``ERROR``, which is not ``PASS``.
* **Findings, not verdicts.** A failure names the criterion, what was observed, and the
  next action, because the finding goes straight back to the agent verbatim.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field

from software_factory.evals.evidence import EvidenceBundle
from software_factory.evals.results import TestRun
from software_factory.memory.admission import is_secret_shaped
from software_factory.spec.units import AgreementResult


class GateOutcome(enum.StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"
    UNENFORCEABLE = "unenforceable"
    """The repository cannot support this gate (no tests, no build).

    Distinct from ``SKIP`` (not applicable) and from ``PASS`` (checked and fine). A
    factory working a repository with no validation must report degradation rather than
    silently satisfying the gates that depend on it (PRD FR-23.2).
    """


class Severity(enum.StrEnum):
    BLOCK = "block"
    WARN = "warn"


class ViolationClass(enum.StrEnum):
    """Not every out-of-workspace write is an attack (PRD FR-12.10).

    Ordinary toolchains write caches and temporary files constantly. A single
    zero-tolerance counter is a gate that gets switched off in the first week, so
    violations are classified and only escalating ones block.
    """

    BENIGN = "benign"
    BLOCKED = "blocked"
    ESCALATING = "escalating"


@dataclass(frozen=True, slots=True)
class Finding:
    """Why a gate failed, in the form the agent will read."""

    criterion: str
    observed: str
    expected: str
    remediation: str
    locator: str = ""

    def render(self) -> str:
        where = f" at {self.locator}" if self.locator else ""
        return f"{self.criterion}{where}: observed {self.observed}; expected {self.expected}"


@dataclass(frozen=True, slots=True)
class GateResult:
    gate: str
    outcome: GateOutcome
    severity: Severity = Severity.BLOCK
    findings: tuple[Finding, ...] = ()
    detail: str = ""

    @property
    def blocks(self) -> bool:
        """Only a blocking gate that failed or errored stops a stage.

        ``UNENFORCEABLE`` does not block: a repository without tests can still be worked,
        as long as every change says so.
        """
        return self.severity is Severity.BLOCK and self.outcome in (
            GateOutcome.FAIL,
            GateOutcome.ERROR,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "gate": self.gate,
            "outcome": self.outcome.value,
            "severity": self.severity.value,
            "blocks": self.blocks,
            "detail": self.detail,
            "findings": [
                {
                    "criterion": f.criterion,
                    "observed": f.observed,
                    "expected": f.expected,
                    "remediation": f.remediation,
                    "locator": f.locator,
                }
                for f in self.findings
            ],
        }


@dataclass(slots=True)
class GateContext:
    """Everything the baseline gates need. Absent fields mean "could not check"."""

    stage: str
    work_class: str = "feature"
    calibration: object | None = None
    violations: dict[ViolationClass, int] = field(default_factory=dict)
    diff_text: str | None = None
    log_text: str | None = None
    build_ok: bool | None = None
    tests_at_tip: TestRun | None = None
    tests_at_parent: TestRun | None = None
    new_test_ids: tuple[str, ...] = ()
    agreements: tuple[AgreementResult, ...] = ()
    behaviour_changed: bool = False
    delta_approved: bool = False
    bundle: EvidenceBundle | None = None
    external_actions: tuple[str, ...] = ()
    permitted_external: frozenset[str] = frozenset()
    builder_engine: tuple[str, str] | None = None
    critic_engine: tuple[str, str] | None = None
    allow_shared_blind_spot: bool = False
    has_test_command: bool = True


Gate = Callable[[GateContext], GateResult]


def calibration_present(ctx: GateContext) -> GateResult:
    """An output with no structured self-assessment is not a finished output."""
    if ctx.calibration is None:
        return GateResult(
            "calibration-present",
            GateOutcome.FAIL,
            findings=(
                Finding(
                    criterion="calibration statement",
                    observed="absent",
                    expected="a schema-valid statement with per-criterion confidence and evidence",
                    remediation="Emit the calibration block required by the stage's output schema.",
                ),
            ),
        )
    return GateResult("calibration-present", GateOutcome.PASS)


def blast_radius_clean(ctx: GateContext) -> GateResult:
    """Only escalating violations block; blocked ones are reported."""
    escalating = ctx.violations.get(ViolationClass.ESCALATING, 0)
    blocked = ctx.violations.get(ViolationClass.BLOCKED, 0)
    if escalating:
        return GateResult(
            "blast-radius-clean",
            GateOutcome.FAIL,
            findings=(
                Finding(
                    criterion="blast-radius contract",
                    observed=f"{escalating} attempt(s) at a grant boundary",
                    expected="no attempt to reach outside the declared grants",
                    remediation="Review the violation records; this is a security event, not a bug.",
                ),
            ),
        )
    detail = f"{blocked} denied write(s) outside the workspace" if blocked else ""
    return GateResult("blast-radius-clean", GateOutcome.PASS, detail=detail)


def secret_clean(ctx: GateContext) -> GateResult:
    """Screen the diff and the logs. Redaction is a backstop, not this gate's excuse."""
    if ctx.diff_text is None and ctx.log_text is None:
        return GateResult(
            "secret-clean",
            GateOutcome.ERROR,
            detail="no diff or log available to screen",
        )
    findings = [
        Finding(
            criterion="no secret material",
            observed=f"credential-shaped content in the {label}",
            expected="no credential material in changes, logs, or evidence",
            remediation="Remove it, then rotate the credential. Assume it is already exposed.",
        )
        for label, text in (("diff", ctx.diff_text), ("logs", ctx.log_text))
        if text and is_secret_shaped(text)
    ]
    if findings:
        return GateResult("secret-clean", GateOutcome.FAIL, findings=tuple(findings))
    return GateResult("secret-clean", GateOutcome.PASS)


def build_green(ctx: GateContext) -> GateResult:
    if ctx.build_ok is None:
        return GateResult("build-green", GateOutcome.ERROR, detail="build was not run")
    if not ctx.build_ok:
        return GateResult(
            "build-green",
            GateOutcome.FAIL,
            findings=(
                Finding(
                    criterion="project builds",
                    observed="build failed",
                    expected="the project's own build command succeeds",
                    remediation="Fix the build before continuing.",
                ),
            ),
        )
    return GateResult("build-green", GateOutcome.PASS)


def tests_pass(ctx: GateContext) -> GateResult:
    """Structured results required; a repository with no test command is unenforceable."""
    if not ctx.has_test_command:
        return GateResult(
            "tests-pass",
            GateOutcome.UNENFORCEABLE,
            detail=(
                "this repository has no runnable validation, so this gate cannot be enforced; "
                "the change carries that label"
            ),
        )
    if ctx.tests_at_tip is None:
        return GateResult(
            "tests-pass",
            GateOutcome.ERROR,
            detail="no structured test results were attached",
        )
    run = ctx.tests_at_tip
    if not run.passed:
        return GateResult(
            "tests-pass",
            GateOutcome.FAIL,
            findings=tuple(
                Finding(
                    criterion="repository validation passes",
                    observed=f"{r.outcome.value} ({(c.value if (c := r.classified()) else 'n/a')})",
                    expected="passed",
                    remediation="Fix the failure, or explain why the test is wrong.",
                    locator=r.test_id,
                )
                for r in run.failures[:10]
            )
            or (
                Finding(
                    criterion="repository validation passes",
                    observed=f"exit code {run.exit_code} with {len(run.results)} results",
                    expected="a non-empty suite exiting zero",
                    remediation="A suite that collects nothing is not a passing suite.",
                ),
            ),
        )
    return GateResult("tests-pass", GateOutcome.PASS, detail=f"{len(run.results)} tests")


def regression_proven(ctx: GateContext) -> GateResult:
    """The keystone gate (PRD FR-13.3, FR-13.3a).

    A fix must come with a test that fails at the parent commit **for the right reason**
    and passes at the tip. The failure-class check is what makes it real: without it,
    ``from mymodule import the_new_function`` satisfies the gate, which is exactly the
    shape a small model produces by default.
    """
    if ctx.work_class != "defect":
        return GateResult("regression-proven", GateOutcome.SKIP, detail="not defect-class work")
    if not ctx.new_test_ids:
        return GateResult(
            "regression-proven",
            GateOutcome.FAIL,
            findings=(
                Finding(
                    criterion="a new test proves the regression",
                    observed="no new test",
                    expected="a test that fails at the parent commit and passes at the tip",
                    remediation="Write the test first, and watch it fail before you fix anything.",
                ),
            ),
        )
    if ctx.tests_at_parent is None or ctx.tests_at_tip is None:
        return GateResult(
            "regression-proven",
            GateOutcome.ERROR,
            detail="the suite was not run at both the parent commit and the tip",
        )

    findings: list[Finding] = []
    for test_id in ctx.new_test_ids:
        parent = ctx.tests_at_parent.by_id(test_id)
        tip = ctx.tests_at_tip.by_id(test_id)

        if tip is None or tip.outcome.value != "passed":
            findings.append(
                Finding(
                    criterion="the new test passes at the tip",
                    observed="absent" if tip is None else tip.outcome.value,
                    expected="passed",
                    remediation="The fix does not make the test pass.",
                    locator=test_id,
                )
            )
            continue

        if parent is None:
            findings.append(
                Finding(
                    criterion="the new test was run at the parent commit",
                    observed="not present in the parent run",
                    expected="present and failing",
                    remediation="Run the new test against the parent commit.",
                    locator=test_id,
                )
            )
            continue

        if parent.outcome.value == "passed":
            findings.append(
                Finding(
                    criterion="the new test fails without the change",
                    observed="passed at the parent commit",
                    expected="failed at the parent commit",
                    remediation=(
                        "This test passes with and without the change, so it proves nothing. "
                        "Write one that exercises the behaviour you fixed."
                    ),
                    locator=test_id,
                )
            )
            continue

        if not parent.is_behavioural_failure:
            failure_class = parent.classified()
            findings.append(
                Finding(
                    criterion="the parent-commit failure is about behaviour",
                    observed=f"{failure_class.value if failure_class else 'unknown'} failure",
                    expected="an assertion failure",
                    remediation=(
                        "The test failed before its body ran, so it proves the code did not "
                        "exist, not that the behaviour was wrong. Assert on behaviour that "
                        "the parent commit gets wrong."
                    ),
                    locator=test_id,
                )
            )

    if findings:
        return GateResult("regression-proven", GateOutcome.FAIL, findings=tuple(findings))
    return GateResult(
        "regression-proven",
        GateOutcome.PASS,
        detail=f"{len(ctx.new_test_ids)} test(s) fail at parent on an assertion and pass at tip",
    )


def spec_agreement(ctx: GateContext) -> GateResult:
    contradicted = [a for a in ctx.agreements if a.blocks_build]
    if contradicted:
        return GateResult(
            "spec-agreement",
            GateOutcome.FAIL,
            findings=tuple(
                Finding(
                    criterion="no contradicted spec unit on the change surface",
                    observed=a.state.value,
                    expected="agreed, drifted, or unverified",
                    remediation=a.reason,
                    locator=a.unit_id,
                )
                for a in contradicted
            ),
        )
    return GateResult("spec-agreement", GateOutcome.PASS)


def delta_present(ctx: GateContext) -> GateResult:
    if ctx.behaviour_changed and not ctx.delta_approved:
        return GateResult(
            "delta-present",
            GateOutcome.FAIL,
            findings=(
                Finding(
                    criterion="behaviour change carries an approved spec delta",
                    observed="behaviour changed with no approved delta",
                    expected="an approved delta describing the new intent",
                    remediation=(
                        "Propose the delta and have it reviewed. Changing what the system does "
                        "without changing what it is supposed to do hides the decision."
                    ),
                ),
            ),
        )
    return GateResult("delta-present", GateOutcome.PASS)


def evidence_complete(ctx: GateContext) -> GateResult:
    """Every claim resolves to an artifact, or it is not a claim."""
    if ctx.bundle is None:
        return GateResult("evidence-complete", GateOutcome.ERROR, detail="no evidence bundle")

    findings = [
        Finding(
            criterion="every claim resolves to evidence",
            observed="claim with no supporting artifact",
            expected="an attached artifact for each claim",
            remediation=(
                'Attach the artifact, or remove the claim. "Tests pass" without structured '
                "results is not a claim."
            ),
            locator=claim.text[:80],
        )
        for claim in ctx.bundle.unsupported_claims()
    ]
    if findings:
        return GateResult("evidence-complete", GateOutcome.FAIL, findings=tuple(findings))

    expired = ctx.bundle.expired_claims()
    detail = f"{len(expired)} claim(s) rest on expired evidence" if expired else ""
    return GateResult("evidence-complete", GateOutcome.PASS, detail=detail)


def no_unreviewed_external(ctx: GateContext) -> GateResult:
    unpermitted = [a for a in ctx.external_actions if a not in ctx.permitted_external]
    if unpermitted:
        return GateResult(
            "no-unreviewed-external",
            GateOutcome.FAIL,
            findings=tuple(
                Finding(
                    criterion="external actions stay inside the permitted set",
                    observed=action,
                    expected=f"one of: {', '.join(sorted(ctx.permitted_external)) or 'none'}",
                    remediation=(
                        "This action already happened and is not reversible. Grant it "
                        "explicitly, or remove the capability."
                    ),
                )
                for action in unpermitted
            ),
        )
    return GateResult("no-unreviewed-external", GateOutcome.PASS)


def independent_review(ctx: GateContext) -> GateResult:
    """Independence is reported, not assumed (PRD FR-3.5a).

    A factory that can only reach the weakest rung is valid; it just has to say so, on
    every verdict, so nobody reads a same-engine review as an independent one.
    """
    if ctx.builder_engine is None or ctx.critic_engine is None:
        return GateResult("independent-review", GateOutcome.ERROR, detail="engines not recorded")
    if ctx.builder_engine == ctx.critic_engine and not ctx.allow_shared_blind_spot:
        return GateResult(
            "independent-review",
            GateOutcome.FAIL,
            findings=(
                Finding(
                    criterion="review has failure modes the builder does not",
                    observed=f"both on {ctx.critic_engine[0]}/{ctx.critic_engine[1]}",
                    expected="a different model or harness",
                    remediation=(
                        "Move the critic to another engine, or set allowSharedBlindSpot to "
                        "accept the risk explicitly."
                    ),
                ),
            ),
        )
    rung = (
        "same engine (accepted)" if ctx.builder_engine == ctx.critic_engine else "distinct engine"
    )
    return GateResult("independent-review", GateOutcome.PASS, detail=rung)


BASELINE_GATES: dict[str, Gate] = {
    "calibration-present": calibration_present,
    "blast-radius-clean": blast_radius_clean,
    "secret-clean": secret_clean,
    "build-green": build_green,
    "tests-pass": tests_pass,
    "regression-proven": regression_proven,
    "spec-agreement": spec_agreement,
    "delta-present": delta_present,
    "evidence-complete": evidence_complete,
    "no-unreviewed-external": no_unreviewed_external,
    "independent-review": independent_review,
}

STAGE_GATES: dict[str, tuple[str, ...]] = {
    "TRIAGE": ("calibration-present", "blast-radius-clean"),
    "DESIGN": ("calibration-present", "blast-radius-clean", "spec-agreement"),
    "BUILD": (
        "calibration-present",
        "blast-radius-clean",
        "secret-clean",
        "build-green",
        "tests-pass",
        "regression-proven",
    ),
    "REVIEW": (
        "calibration-present",
        "blast-radius-clean",
        "secret-clean",
        "tests-pass",
        "spec-agreement",
        "delta-present",
        "evidence-complete",
        "no-unreviewed-external",
        "independent-review",
    ),
    "VERIFY": ("calibration-present", "blast-radius-clean", "evidence-complete"),
}


@dataclass(slots=True)
class GateReport:
    stage: str
    results: list[GateResult] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(result.blocks for result in self.results)

    @property
    def findings(self) -> list[Finding]:
        """Every finding, flattened, for feeding back to the agent verbatim."""
        return [finding for result in self.results for finding in result.findings]

    @property
    def unenforceable(self) -> list[str]:
        return [r.gate for r in self.results if r.outcome is GateOutcome.UNENFORCEABLE]

    def as_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "blocked": self.blocked,
            "unenforceable": self.unenforceable,
            "results": [r.as_dict() for r in self.results],
        }


def run_gates(
    ctx: GateContext, *, gates: dict[str, Gate] | None = None, stage: str | None = None
) -> GateReport:
    """Run every gate declared for a stage.

    A gate that raises is recorded as ``ERROR`` rather than propagating: one broken gate
    must not hide the results of the others, and ``ERROR`` blocks anyway.
    """
    gates = gates or BASELINE_GATES
    stage = stage or ctx.stage
    report = GateReport(stage=stage)
    for name in STAGE_GATES.get(stage, ()):
        gate = gates.get(name)
        if gate is None:
            report.results.append(GateResult(name, GateOutcome.ERROR, detail="gate not registered"))
            continue
        try:
            report.results.append(gate(ctx))
        except Exception as exc:
            report.results.append(
                GateResult(name, GateOutcome.ERROR, detail=f"gate raised: {exc!r}")
            )
    return report
