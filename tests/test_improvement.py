"""The self-improvement loop: clustering, anti-thrash, drift detection, and telemetry.

The theme is that a loop proposing changes to the factory that judges its own proposals has
three non-obvious failure modes and one obvious one, and every test here is about one of
them. The obvious one -- proposing is not writing -- is structural: nothing in this module
applies a change, and there is no path from it to a definition taking effect.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from software_factory.evals.scorers import ImprovementProposal, Label, ProposalVerdict, Scorer
from software_factory.improvement import (
    Cluster,
    Failure,
    LoopState,
    ProposalRecord,
    ProposalStatus,
    Refused,
    check_effectiveness,
    cluster_failures,
    detect_drift,
    disable,
    may_propose,
    settle,
    submit,
    suspend_for_drift,
    telemetry,
)
from software_factory.memory.records import utc_now

# --------------------------------------------------------------------------- clustering


def failure(run: str, **kwargs) -> Failure:
    base: dict[str, object] = {
        "run_id": run,
        "work_item_id": f"wi-{run}",
        "stage": "BUILD",
        "agent": "builder",
        "gate": "tests-pass",
        "failure_class": "assertion",
    }
    base.update(kwargs)
    return Failure(**base)  # type: ignore[arg-type]


def test_failures_sharing_a_cause_share_a_signature() -> None:
    """Structural facts, not text: two runs failing the same gate at the same stage on the
    same failure class are the same problem."""
    assert failure("a").signature() == failure("b").signature()


def test_the_run_and_work_item_are_not_part_of_the_signature() -> None:
    """Including either would give every failure its own signature, which is a clusterer
    that never clusters."""
    assert failure("a", work_item_id="wi-99").signature() == failure("b").signature()


def test_a_different_gate_is_a_different_problem() -> None:
    assert failure("a").signature() != failure("b", gate="secret-clean").signature()


def test_failures_below_the_minimum_are_not_worth_a_diagnosis_run() -> None:
    """Not a claim that one failure does not matter -- the gate that caught it already
    blocked the work. It is a claim about where an improvement run's budget goes: a proposal
    drawn from one instance is a proposal fitted to one instance."""
    assert cluster_failures([failure("a"), failure("b")]) == []


def test_a_pattern_clusters() -> None:
    clusters = cluster_failures([failure(f"r{i}") for i in range(5)])

    assert len(clusters) == 1
    assert clusters[0].size == 5
    assert len(clusters[0].work_items) == 5


def test_clusters_are_returned_largest_first() -> None:
    """A loop with a budget should spend it on the pattern costing the most."""
    failures = [failure(f"a{i}") for i in range(3)]
    failures += [failure(f"b{i}", gate="secret-clean") for i in range(6)]

    clusters = cluster_failures(failures)

    assert [c.size for c in clusters] == [6, 3]


def test_a_signature_whose_details_diverge_splits() -> None:
    """A coarse signature makes one diagnosis cover several causes."""
    encoding = [
        failure(f"e{i}", detail="UnicodeDecodeError decoding the CSV header as utf-8")
        for i in range(3)
    ]
    timeouts = [
        failure(f"t{i}", detail="the migration step exceeded its wall clock and was killed")
        for i in range(3)
    ]

    clusters = cluster_failures(encoding + timeouts)

    assert len(clusters) == 2


def test_failures_with_no_detail_are_not_scattered() -> None:
    """Absence of text is not evidence of a different cause, and treating it as such would
    scatter every failure whose gate reported a bare verdict."""
    clusters = cluster_failures([failure(f"r{i}") for i in range(5)])

    assert len(clusters) == 1


def test_a_cluster_counts_distinct_work_items() -> None:
    """Forty failures across two work items is a flaky pair; six across six is a pattern."""
    repeated = [failure(f"r{i}", work_item_id="wi-1") for i in range(6)]

    cluster = cluster_failures(repeated)[0]

    assert cluster.size == 6
    assert cluster.work_items == ("wi-1",)


# ---------------------------------------------------------------------------- the loop


def scorer(name: str = "tests-actually-run", **kwargs) -> Scorer:
    base: dict[str, object] = {
        "name": name,
        "labels": (Label("ran", 1.0), Label("not_run", 0.0)),
        "passing_score": 1.0,
        "agreement": 0.9,
        "kappa": 0.8,
        "labelled_sample": 60,
        "outcome_partner": "O-3 defect escape",
        # FR-14.1: opt-in per scorer. Enabling it on a scorer authorises investigating
        # *that* scorer's failures -- not the factory in general -- and the default is off.
        "self_improvement": True,
    }
    base.update(kwargs)
    return Scorer(**base)  # type: ignore[arg-type]


def a_cluster(signature: str = "sig-1") -> Cluster:
    return Cluster(signature=signature, failures=tuple(failure(f"r{i}") for i in range(4)))


def test_a_healthy_loop_may_propose() -> None:
    assert may_propose(LoopState(), a_cluster(), target="agents/builder", scorer=scorer()) is None


def test_a_disabled_loop_refuses_everything() -> None:
    """A loop that cannot show it improves outcomes is a cost, and the default is off."""
    state = LoopState()
    disable(state, "adopted proposals moved outcomes by -0.2%")

    refused = may_propose(state, a_cluster(), target="agents/builder", scorer=scorer())

    assert isinstance(refused, Refused)
    assert refused.code == "loop.disabled"


def test_self_improvement_is_off_until_a_scorer_opts_in() -> None:
    """FR-14.1. Enabling the loop on a scorer authorises investigating that scorer's
    failures; a factory does not enable it globally."""
    refused = may_propose(
        LoopState(), a_cluster(), target="agents/builder", scorer=scorer(self_improvement=False)
    )

    assert isinstance(refused, Refused)
    assert refused.code == "loop.scorer_untrusted"
    assert "not enabled" in refused.message


def test_a_scorer_with_no_outcome_partner_may_not_drive_the_loop() -> None:
    """Without one there is no way to tell an improvement from a scorer that learned to be
    easier to satisfy."""
    refused = may_propose(
        LoopState(), a_cluster(), target="agents/builder", scorer=scorer(outcome_partner=None)
    )

    assert isinstance(refused, Refused)
    assert refused.code == "loop.scorer_untrusted"


def test_a_suspended_scorer_may_not_drive_the_loop() -> None:
    state = LoopState()
    suspend_for_drift(
        state,
        detect_drift(scorer(), scorer_delta=0.30, outcome_delta=0.01),  # type: ignore[arg-type]
    )

    refused = may_propose(state, a_cluster(), target="agents/builder", scorer=scorer())

    assert isinstance(refused, Refused)
    assert refused.code == "loop.scorer_suspended"
    assert "re-validate" in refused.remediation


def test_the_open_proposal_cap_holds() -> None:
    """A reviewer's attention is the scarce resource. Twenty open proposals is not twice as
    much improvement as ten; it is a queue nobody works through."""
    state = LoopState(
        records=[
            ProposalRecord(
                id=f"p{i}",
                target=f"agents/a{i}",
                scorer="s",
                signature=f"sig-{i}",
                status=ProposalStatus.OPEN,
            )
            for i in range(5)
        ]
    )

    refused = may_propose(state, a_cluster(), target="agents/builder", scorer=scorer())

    assert isinstance(refused, Refused)
    assert refused.code == "loop.too_many_open"


def test_a_target_proposed_against_recently_is_left_alone() -> None:
    """Proposing sooner proposes against evidence the last change has not had time to
    alter."""
    now = utc_now()
    state = LoopState(
        records=[
            ProposalRecord(
                id="p1",
                target="agents/builder",
                scorer="s",
                signature="sig-0",
                status=ProposalStatus.ADOPTED,
                opened_at=now - timedelta(days=2),
            )
        ]
    )

    refused = may_propose(state, a_cluster(), target="agents/builder", scorer=scorer(), now=now)

    assert isinstance(refused, Refused)
    assert refused.code == "loop.cooling"


def test_the_cooling_period_ends() -> None:
    now = utc_now()
    state = LoopState(
        records=[
            ProposalRecord(
                id="p1",
                target="agents/builder",
                scorer="s",
                signature="sig-0",
                status=ProposalStatus.ADOPTED,
                opened_at=now - timedelta(days=30),
            )
        ]
    )

    assert (
        may_propose(state, a_cluster(), target="agents/builder", scorer=scorer(), now=now) is None
    )


def test_a_rejected_signature_does_not_return_without_new_evidence() -> None:
    """ "No" must not cost the reviewer the same effort every week."""
    state = LoopState(
        records=[
            ProposalRecord(
                id="p1",
                target="agents/critic",
                scorer="s",
                signature="sig-1",
                status=ProposalStatus.REJECTED,
                opened_at=utc_now() - timedelta(days=90),
            )
        ]
    )

    refused = may_propose(state, a_cluster("sig-1"), target="agents/builder", scorer=scorer())

    assert isinstance(refused, Refused)
    assert refused.code == "loop.already_rejected"


def test_new_evidence_reopens_a_rejected_signature() -> None:
    state = LoopState(
        records=[
            ProposalRecord(
                id="p1",
                target="agents/critic",
                scorer="s",
                signature="sig-1",
                status=ProposalStatus.REJECTED,
                opened_at=utc_now() - timedelta(days=90),
            )
        ]
    )

    assert (
        may_propose(
            state,
            a_cluster("sig-1"),
            target="agents/builder",
            scorer=scorer(),
            new_evidence=True,
        )
        is None
    )


# ----------------------------------------------------------------------- rubric drift


def test_a_scorer_outrunning_its_outcome_partner_is_flagged() -> None:
    """A scorer whose pass rate rises while its outcome stays flat has probably learned to
    be easier to satisfy. That is a trend, not visible in any one proposal."""
    finding = detect_drift(scorer(), scorer_delta=0.25, outcome_delta=0.02)

    assert finding is not None
    assert finding.gap == pytest.approx(0.23)
    assert "easier to satisfy" in finding.describe()


def test_a_scorer_moving_with_its_outcome_is_not_drift() -> None:
    assert detect_drift(scorer(), scorer_delta=0.20, outcome_delta=0.18) is None


def test_a_scorer_getting_stricter_is_not_drift() -> None:
    """A different signal: the scorer may have got harder, which is not capture, and
    reporting it here would bury the case this exists for."""
    assert detect_drift(scorer(), scorer_delta=-0.15, outcome_delta=0.0) is None


def test_a_scorer_without_an_outcome_partner_cannot_be_checked_for_drift() -> None:
    assert detect_drift(scorer(outcome_partner=None), scorer_delta=0.5, outcome_delta=0.0) is None


# ------------------------------------------------------------------ loop effectiveness


def adopted(index: int, effect: float | None) -> ProposalRecord:
    return ProposalRecord(
        id=f"p{index}",
        target=f"agents/a{index}",
        scorer="s",
        signature=f"sig-{index}",
        status=ProposalStatus.ADOPTED,
        outcome_effect=effect,
    )


def test_a_loop_whose_adopted_changes_move_nothing_switches_itself_off() -> None:
    """FR-14.7a.4. A loop that cannot show it works is a defect and has to show as one."""
    state = LoopState(records=[adopted(i, 0.001) for i in range(4)])

    reason = check_effectiveness(state)

    assert reason is not None
    assert "below" in reason


def test_a_loop_that_moves_outcomes_keeps_running() -> None:
    state = LoopState(records=[adopted(i, 0.05) for i in range(4)])

    assert check_effectiveness(state) is None


def test_one_measurement_is_not_enough_to_disable_a_loop() -> None:
    """Disabling on one measurement disables for noise; never disabling is the failure."""
    state = LoopState(records=[adopted(0, -0.10)])

    assert check_effectiveness(state) is None


def test_unmeasured_adoptions_are_not_read_as_ineffective_ones() -> None:
    """`None` is different from zero, and conflating them would let an unmeasured loop
    report as an ineffective one."""
    state = LoopState(records=[adopted(i, None) for i in range(5)])

    assert check_effectiveness(state) is None
    assert telemetry(state).measured == 0
    assert telemetry(state).mean_outcome_effect is None


# --------------------------------------------------------------------------- telemetry


def proposal(**kwargs) -> ImprovementProposal:
    base: dict[str, object] = {
        "target": "agents/builder",
        "kind": "prompt",
        "rationale": "the builder omits the failing case from its summary",
        "regressions_addressed": ("run-1", "run-2"),
        "metric_delta": 0.12,
        "counter_metrics": {"cost_per_change": 0.0, "rework_rate": 0.0, "human_review_cost": 0.0},
        "holdout_delta": 0.08,
    }
    base.update(kwargs)
    return ImprovementProposal(**base)  # type: ignore[arg-type]


def test_an_accepted_proposal_is_recorded_open_never_adopted() -> None:
    """FR-14.5: nothing is auto-adopted. The record says a human has something to look at."""
    state = LoopState()

    record = submit(
        state,
        proposal(),
        ProposalVerdict(True, "validated"),
        proposal_id="p1",
        scorer_name="tests-actually-run",
        signature="sig-1",
    )

    assert record.status is ProposalStatus.OPEN
    assert record.evidence == ("run-1", "run-2")


def test_a_refused_proposal_is_recorded_rejected() -> None:
    """Recorded rather than dropped: the anti-thrash rule needs to know it was tried."""
    state = LoopState()

    record = submit(
        state,
        proposal(),
        ProposalVerdict(False, "held-out performance did not move"),
        proposal_id="p1",
        scorer_name="tests-actually-run",
        signature="sig-1",
    )

    assert record.status is ProposalStatus.REJECTED
    assert state.rejected_signatures()["sig-1"].id == "p1"


def test_telemetry_counts_every_outcome() -> None:
    state = LoopState()
    submit(
        state,
        proposal(),
        ProposalVerdict(True, "ok"),
        proposal_id="p1",
        scorer_name="s",
        signature="a",
    )
    submit(
        state,
        proposal(),
        ProposalVerdict(True, "ok"),
        proposal_id="p2",
        scorer_name="s",
        signature="b",
    )
    submit(
        state,
        proposal(),
        ProposalVerdict(False, "no"),
        proposal_id="p3",
        scorer_name="s",
        signature="c",
    )
    settle(state, "p1", ProposalStatus.ADOPTED, outcome_effect=0.05)
    settle(state, "p2", ProposalStatus.REVERTED)

    numbers = telemetry(state)

    assert numbers.adopted == 1
    assert numbers.rejected == 1
    assert numbers.reverted == 1
    assert numbers.mean_outcome_effect == pytest.approx(0.05)
    assert numbers.as_dict()["adoptionRate"] == 0.5


def test_settling_an_unknown_proposal_returns_nothing() -> None:
    assert settle(LoopState(), "missing", ProposalStatus.ADOPTED) is None
