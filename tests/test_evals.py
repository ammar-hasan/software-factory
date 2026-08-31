"""Gates, evidence, scorers, and the improvement loop's defences.

The centrepiece is `regression-proven`: the adversarial review of the PRD found that a
naive implementation is satisfied by an import error at the parent commit, which is
exactly the shape a small model produces by default. Several tests below exist only to
keep that bypass closed.
"""

from __future__ import annotations

import pytest

from software_factory.evals import (
    EvidenceBundle,
    EvidenceClass,
    EvidenceItem,
    FailureClass,
    GateContext,
    GateOutcome,
    ImprovementProposal,
    Label,
    Outcome,
    Scorer,
    TestResult,
    TestRun,
    Trial,
    ViolationClass,
    classify_failure,
    cohens_kappa,
    evaluate_proposal,
    run_gates,
    summarise,
)
from software_factory.evals import gates as gate_impl  # `tests_pass` matches pytest's test*
from software_factory.evals.gates import (
    BASELINE_GATES,
    blast_radius_clean,
    calibration_present,
    evidence_complete,
    independent_review,
    regression_proven,
    secret_clean,
)


def result(
    test_id: str = "tests/test_import.py::test_bom",
    outcome: Outcome = Outcome.PASSED,
    message: str = "",
) -> TestResult:
    return TestResult(test_id=test_id, outcome=outcome, message=message)


def run(commit: str, *results: TestResult, exit_code: int | None = None) -> TestRun:
    failed = any(r.outcome in (Outcome.FAILED, Outcome.ERROR) for r in results)
    return TestRun(
        command="pytest",
        commit=commit,
        exit_code=exit_code if exit_code is not None else (1 if failed else 0),
        results=list(results),
    )


def context(**kwargs) -> GateContext:
    base: dict[str, object] = {"stage": "BUILD", "calibration": object()}
    base.update(kwargs)
    return GateContext(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------ failure classes


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("ImportError: cannot import name 'strip_bom'", FailureClass.IMPORT),
        ("ModuleNotFoundError: No module named 'importers'", FailureClass.IMPORT),
        ("E   AssertionError: assert 'a' == 'b'", FailureClass.ASSERTION),
        ("ERRORS during collection", FailureClass.COLLECTION),
        ("error at setup of test_x", FailureClass.FIXTURE),
        ("Timeout: test exceeded 60s", FailureClass.TIMEOUT),
    ],
)
def test_failures_are_classified(message: str, expected: FailureClass) -> None:
    assert classify_failure(message) is expected


def test_an_import_error_mentioning_assert_is_still_an_import_error() -> None:
    """Structural classes are checked first, so a traceback's wording cannot launder one."""
    message = "ImportError: cannot import name 'assert_valid' from 'checks'"

    assert classify_failure(message) is FailureClass.IMPORT


# --------------------------------------------------------------- regression-proven


def test_an_import_error_at_the_parent_does_not_prove_a_regression() -> None:
    """The one-line bypass: `from mod import new_function` fails at parent and passes at tip."""
    ctx = context(
        work_class="defect",
        new_test_ids=("tests/test_import.py::test_bom",),
        tests_at_parent=run(
            "parent",
            result(outcome=Outcome.ERROR, message="ImportError: cannot import name 'strip_bom'"),
        ),
        tests_at_tip=run("tip", result()),
    )

    outcome = regression_proven(ctx)

    assert outcome.outcome is GateOutcome.FAIL
    assert "assertion failure" in outcome.findings[0].expected


def test_a_collection_error_at_the_parent_does_not_prove_a_regression() -> None:
    ctx = context(
        work_class="defect",
        new_test_ids=("tests/test_import.py::test_bom",),
        tests_at_parent=run(
            "parent", result(outcome=Outcome.ERROR, message="ERRORS during collection")
        ),
        tests_at_tip=run("tip", result()),
    )

    assert regression_proven(ctx).outcome is GateOutcome.FAIL


def test_an_assertion_failure_at_the_parent_proves_a_regression() -> None:
    ctx = context(
        work_class="defect",
        new_test_ids=("tests/test_import.py::test_bom",),
        tests_at_parent=run(
            "parent",
            result(
                outcome=Outcome.FAILED, message="E   AssertionError: assert '\\ufeffid' == 'id'"
            ),
        ),
        tests_at_tip=run("tip", result()),
    )

    assert regression_proven(ctx).outcome is GateOutcome.PASS


def test_a_test_passing_at_both_commits_proves_nothing() -> None:
    ctx = context(
        work_class="defect",
        new_test_ids=("tests/test_import.py::test_bom",),
        tests_at_parent=run("parent", result()),
        tests_at_tip=run("tip", result()),
    )

    outcome = regression_proven(ctx)

    assert outcome.outcome is GateOutcome.FAIL
    assert "proves nothing" in outcome.findings[0].remediation


def test_a_defect_fix_with_no_new_test_fails_the_gate() -> None:
    ctx = context(work_class="defect", tests_at_tip=run("tip", result()))

    outcome = regression_proven(ctx)

    assert outcome.outcome is GateOutcome.FAIL
    assert "watch it fail" in outcome.findings[0].remediation


def test_a_missing_parent_run_is_an_error_not_a_pass() -> None:
    """A gate that cannot run is never satisfied."""
    ctx = context(
        work_class="defect",
        new_test_ids=("tests/test_import.py::test_bom",),
        tests_at_tip=run("tip", result()),
    )

    outcome = regression_proven(ctx)

    assert outcome.outcome is GateOutcome.ERROR
    assert outcome.blocks


def test_regression_proven_is_skipped_for_feature_work() -> None:
    assert regression_proven(context(work_class="feature")).outcome is GateOutcome.SKIP


def test_a_new_test_failing_at_the_tip_fails_the_gate() -> None:
    ctx = context(
        work_class="defect",
        new_test_ids=("tests/test_import.py::test_bom",),
        tests_at_parent=run(
            "parent", result(outcome=Outcome.FAILED, message="AssertionError: nope")
        ),
        tests_at_tip=run(
            "tip", result(outcome=Outcome.FAILED, message="AssertionError: still nope")
        ),
    )

    assert regression_proven(ctx).outcome is GateOutcome.FAIL


# ------------------------------------------------------------------------ tests-pass


def test_a_suite_that_collected_nothing_is_not_a_passing_suite() -> None:
    """Exit code alone is not enough: a suite that collects nothing exits zero."""
    ctx = context(tests_at_tip=TestRun(command="pytest", commit="tip", exit_code=0, results=[]))

    outcome = gate_impl.tests_pass(ctx)

    assert outcome.outcome is GateOutcome.FAIL


def test_a_repository_with_no_test_command_is_unenforceable_not_passing() -> None:
    """Degradation is named, never silently satisfied."""
    ctx = context(has_test_command=False)

    outcome = gate_impl.tests_pass(ctx)

    assert outcome.outcome is GateOutcome.UNENFORCEABLE
    assert not outcome.blocks


def test_missing_results_are_an_error() -> None:
    assert gate_impl.tests_pass(context()).outcome is GateOutcome.ERROR


def test_a_passing_suite_passes() -> None:
    assert (
        gate_impl.tests_pass(context(tests_at_tip=run("tip", result()))).outcome is GateOutcome.PASS
    )


# ------------------------------------------------------------------------ other gates


def test_missing_calibration_fails() -> None:
    assert calibration_present(GateContext(stage="BUILD")).outcome is GateOutcome.FAIL


def test_benign_violations_do_not_block() -> None:
    """Ordinary toolchains write caches constantly; a zero-tolerance gate gets disabled."""
    ctx = context(violations={ViolationClass.BENIGN: 12, ViolationClass.BLOCKED: 3})

    outcome = blast_radius_clean(ctx)

    assert outcome.outcome is GateOutcome.PASS
    assert "3 denied" in outcome.detail


def test_an_escalating_violation_blocks() -> None:
    ctx = context(violations={ViolationClass.ESCALATING: 1})

    outcome = blast_radius_clean(ctx)

    assert outcome.outcome is GateOutcome.FAIL
    assert "security event" in outcome.findings[0].remediation


def test_a_credential_in_the_diff_fails_and_says_to_rotate() -> None:
    ctx = context(diff_text="+ TOKEN = 'ghp_abcdefghijklmnopqrstuvwxyz0123'")

    outcome = secret_clean(ctx)

    assert outcome.outcome is GateOutcome.FAIL
    assert "rotate" in outcome.findings[0].remediation


def test_nothing_to_screen_is_an_error_not_a_pass() -> None:
    assert secret_clean(context()).outcome is GateOutcome.ERROR


def test_a_critic_sharing_the_builders_engine_fails_review() -> None:
    ctx = context(stage="REVIEW", builder_engine=("oz", "model-a"), critic_engine=("oz", "model-a"))

    assert independent_review(ctx).outcome is GateOutcome.FAIL


def test_shared_engine_can_be_accepted_explicitly_and_is_reported() -> None:
    """A factory that can only reach the weakest rung is valid; it has to say so."""
    ctx = context(
        stage="REVIEW",
        builder_engine=("oz", "model-a"),
        critic_engine=("oz", "model-a"),
        allow_shared_blind_spot=True,
    )

    outcome = independent_review(ctx)

    assert outcome.outcome is GateOutcome.PASS
    assert "accepted" in outcome.detail


# -------------------------------------------------------------------------- evidence


def test_a_bare_claim_fails_evidence_complete() -> None:
    bundle = EvidenceBundle(id="b1", run_id="r1", work_item_id="w1", stage="REVIEW")
    bundle.claim("Tests pass.")

    outcome = evidence_complete(context(stage="REVIEW", bundle=bundle))

    assert outcome.outcome is GateOutcome.FAIL
    assert "not a claim" in outcome.findings[0].remediation


def test_a_claim_pointing_at_a_missing_item_fails() -> None:
    bundle = EvidenceBundle(id="b1", run_id="r1", work_item_id="w1", stage="REVIEW")
    bundle.claim("Tests pass.", "nonexistent")

    assert evidence_complete(context(stage="REVIEW", bundle=bundle)).outcome is GateOutcome.FAIL


def test_a_supported_claim_passes() -> None:
    bundle = EvidenceBundle(id="b1", run_id="r1", work_item_id="w1", stage="REVIEW")
    bundle.add(
        EvidenceItem(
            id="e1",
            evidence_class=EvidenceClass.TEST_RESULTS,
            digest="abc",
            location="results.json",
        )
    )
    bundle.claim("Tests pass.", "e1")

    assert evidence_complete(context(stage="REVIEW", bundle=bundle)).outcome is GateOutcome.PASS


def test_an_expired_evidence_body_is_reported_not_treated_as_satisfied() -> None:
    """Retention removes content, never the record. The claim renders as expired."""
    bundle = EvidenceBundle(id="b1", run_id="r1", work_item_id="w1", stage="REVIEW")
    bundle.add(
        EvidenceItem(
            id="e1",
            evidence_class=EvidenceClass.TEST_RESULTS,
            digest="abc",
            location="results.json",
            tombstoned=True,
        )
    )
    bundle.claim("Tests pass.", "e1")

    outcome = evidence_complete(context(stage="REVIEW", bundle=bundle))

    assert outcome.outcome is GateOutcome.PASS
    assert "expired" in outcome.detail


def test_a_sealed_bundle_cannot_be_edited() -> None:
    bundle = EvidenceBundle(id="b1", run_id="r1", work_item_id="w1", stage="BUILD")
    bundle.seal()

    with pytest.raises(ValueError, match="sealed"):
        bundle.claim("something new")


def test_sealing_is_deterministic_for_the_same_content() -> None:
    def build() -> EvidenceBundle:
        bundle = EvidenceBundle(id="b", run_id="r", work_item_id="w", stage="BUILD")
        bundle.add(
            EvidenceItem(
                id="e1", evidence_class=EvidenceClass.DIFF, digest="d", location="diff.patch"
            )
        )
        bundle.claim("The change is small.", "e1")
        return bundle

    assert build().seal() == build().seal()


# ------------------------------------------------------------------------- gate runs


def test_running_a_stage_reports_every_gate() -> None:
    report = run_gates(
        context(
            stage="BUILD",
            work_class="feature",
            build_ok=True,
            tests_at_tip=run("tip", result()),
            diff_text="+ safe change",
        )
    )

    assert not report.blocked
    assert {r.gate for r in report.results} == {
        "calibration-present",
        "blast-radius-clean",
        "secret-clean",
        "build-green",
        "tests-pass",
        "regression-proven",
    }


def test_a_gate_that_raises_is_recorded_as_an_error_and_blocks() -> None:
    """One broken gate must not hide the results of the others."""

    def exploding(_ctx: GateContext):
        raise RuntimeError("boom")

    gates = dict(BASELINE_GATES)
    gates["tests-pass"] = exploding

    report = run_gates(context(stage="BUILD", build_ok=True), gates=gates)

    failing = next(r for r in report.results if r.gate == "tests-pass")
    assert failing.outcome is GateOutcome.ERROR
    assert report.blocked
    assert len(report.results) == 6


def test_findings_are_flattened_for_feeding_back_verbatim() -> None:
    report = run_gates(context(stage="BUILD", work_class="defect", build_ok=False))

    assert report.findings
    assert all(f.remediation for f in report.findings)


# --------------------------------------------------------------------------- scorers


def test_sampling_is_deterministic() -> None:
    """Random sampling would make a benchmark compare different samples."""
    scorer = Scorer(
        name="tests-run",
        labels=(Label("ran", 1.0), Label("not_run", 0.0)),
        passing_score=1.0,
        sampling_rate=50,
    )

    assert scorer.samples("run-123") == scorer.samples("run-123")


def test_a_zero_sample_rate_scores_nothing() -> None:
    scorer = Scorer(
        name="s", labels=(Label("a", 1.0), Label("b", 0.0)), passing_score=1.0, sampling_rate=0
    )

    assert not scorer.samples("run-1")


def test_an_unknown_label_is_an_error_not_a_failure() -> None:
    scorer = Scorer(name="s", labels=(Label("a", 1.0), Label("b", 0.0)), passing_score=1.0)

    assert scorer.classify("nonsense").value == "error"


def test_a_scorer_below_the_human_agreement_threshold_is_untrusted() -> None:
    scorer = Scorer(
        name="s",
        labels=(Label("a", 1.0), Label("b", 0.0)),
        passing_score=1.0,
        labelled_sample=40,
        agreement=0.6,
        kappa=0.3,
    )

    assert not scorer.trusted
    assert "agrees with humans" in (scorer.untrusted_reason() or "")


def test_high_raw_agreement_with_low_kappa_is_still_untrusted() -> None:
    """Raw agreement alone rewards a judge that always answers with the majority label."""
    scorer = Scorer(
        name="s",
        labels=(Label("a", 1.0), Label("b", 0.0)),
        passing_score=1.0,
        labelled_sample=40,
        agreement=0.9,
        kappa=0.1,
    )

    assert not scorer.trusted
    assert "kappa" in (scorer.untrusted_reason() or "")


def test_too_few_human_labels_is_untrusted() -> None:
    scorer = Scorer(
        name="s",
        labels=(Label("a", 1.0), Label("b", 0.0)),
        passing_score=1.0,
        labelled_sample=5,
        agreement=1.0,
        kappa=1.0,
    )

    assert not scorer.trusted


def test_a_scorer_without_an_outcome_partner_cannot_drive_improvement() -> None:
    """Without one, an improvement is indistinguishable from an easier scorer."""
    scorer = Scorer(
        name="s",
        labels=(Label("a", 1.0), Label("b", 0.0)),
        passing_score=1.0,
        self_improvement=True,
        labelled_sample=40,
        agreement=0.95,
        kappa=0.9,
    )

    allowed, reason = scorer.may_drive_improvement()

    assert not allowed
    assert "outcome partner" in reason


def test_a_trusted_anchored_scorer_may_drive_improvement() -> None:
    scorer = Scorer(
        name="s",
        labels=(Label("a", 1.0), Label("b", 0.0)),
        passing_score=1.0,
        self_improvement=True,
        labelled_sample=40,
        agreement=0.95,
        kappa=0.9,
        outcome_partner="O-2 revert rate",
    )

    allowed, _ = scorer.may_drive_improvement()

    assert allowed


def test_kappa_discounts_chance_agreement() -> None:
    judge = ["a"] * 90 + ["b"] * 10
    human = ["a"] * 90 + ["b"] * 10
    assert cohens_kappa(judge, human) == pytest.approx(1.0)

    lazy = ["a"] * 100
    assert cohens_kappa(lazy, human) < 0.1


# ------------------------------------------------------------------------ benchmarks


def test_a_benchmark_report_declares_no_winner() -> None:
    trials = [
        Trial("t1", "config-a", passed=True, cost=1.0),
        Trial("t1", "config-b", passed=False, cost=3.0),
    ]

    report = summarise("compare", trials)

    assert report.as_dict()["winner"] is None
    assert len(report.summaries) == 2


def test_a_difference_inside_the_spread_is_not_a_difference() -> None:
    trials = []
    for task in ("t1", "t2", "t3", "t4"):
        trials.append(Trial(task, "a", passed=task in {"t1", "t2"}))
        trials.append(Trial(task, "b", passed=task in {"t1", "t2", "t3"}))

    report = summarise("compare", trials)

    assert not report.difference_is_meaningful("a", "b")


def test_a_large_consistent_difference_is_meaningful() -> None:
    trials = []
    for task in ("t1", "t2", "t3", "t4"):
        trials.append(Trial(task, "a", passed=False))
        trials.append(Trial(task, "b", passed=True))

    report = summarise("compare", trials)

    assert report.difference_is_meaningful("a", "b")


# ------------------------------------------------------------- improvement proposals


def proposal(**kwargs) -> ImprovementProposal:
    base: dict[str, object] = {
        "target": "agents/builder/agent.md",
        "kind": "prompt",
        "rationale": "builder skips tests on 18% of sampled runs",
        "regressions_addressed": ("run-1", "run-2"),
        "metric_delta": 0.15,
        "holdout_delta": 0.10,
        "counter_metrics": {"cost_per_change": 0.0, "rework_rate": -0.01},
    }
    base.update(kwargs)
    return ImprovementProposal(**base)  # type: ignore[arg-type]


def test_a_proposal_without_holdout_validation_is_refused() -> None:
    verdict = evaluate_proposal(proposal(holdout_delta=None))

    assert not verdict.accepted
    assert "held-out" in verdict.reason


def test_a_proposal_that_does_not_generalise_is_refused() -> None:
    verdict = evaluate_proposal(proposal(holdout_delta=-0.05))

    assert not verdict.accepted
    assert "does not survive" in verdict.reason


def test_a_proposal_degrading_a_counter_metric_is_refused() -> None:
    """This is the concrete defence against moving a metric while reality gets worse."""
    verdict = evaluate_proposal(proposal(counter_metrics={"human_review_cost": -0.20}))

    assert not verdict.accepted
    assert "counter-metrics degraded" in verdict.reason


def test_a_self_referential_proposal_needs_a_second_reviewer() -> None:
    verdict = evaluate_proposal(proposal(edits_assurance=True))

    assert verdict.accepted
    assert verdict.requires_second_reviewer


def test_a_clean_proposal_is_accepted() -> None:
    verdict = evaluate_proposal(proposal())

    assert verdict.accepted
    assert not verdict.requires_second_reviewer


def test_an_untrusted_scorer_cannot_carry_a_proposal() -> None:
    scorer = Scorer(
        name="s",
        labels=(Label("a", 1.0), Label("b", 0.0)),
        passing_score=1.0,
        self_improvement=True,
        labelled_sample=5,
    )

    verdict = evaluate_proposal(proposal(), scorer)

    assert not verdict.accepted
    assert "may not drive change" in verdict.reason
