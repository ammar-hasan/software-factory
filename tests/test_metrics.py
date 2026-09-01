"""Metrics folded from the ledger, and the ones that honestly cannot be computed.

Two rules matter more than any individual number here: a metric needing an integration the
factory does not have is *unavailable with a reason* rather than zero, and a cost derived
from recorded usage is an *estimate* that says what it excludes. Both are about a dashboard
that does not lie by omission.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from software_factory.ledger import EntryType, Ledger
from software_factory.memory.records import utc_now
from software_factory.observability import (
    Availability,
    Measure,
    Window,
    compute,
    insufficient,
    unavailable,
)

# --------------------------------------------------------------------------- fixtures


def ledger_with(tmp_path: Path, *appends) -> Ledger:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for entry_type, subject, payload in appends:
        ledger.append(entry_type, actor="conductor", subject=subject, payload=payload)
    return ledger


def run_started(subject: str, **payload):
    return (EntryType.RUN_STARTED, subject, payload)


def gate(subject: str, name: str, outcome: str, stage: str = "BUILD"):
    """A gate evaluation. `stage` is part of the de-duplication key.

    It used to be absent from every fixture here, so the only case exercised was the one
    where de-duplication is right -- and the cross-stage case, where the same key discarded
    a genuine failure at a later stage, was never constructed.
    """
    return (EntryType.GATE_EVALUATED, subject, {"gate": name, "outcome": outcome, "stage": stage})


def transition(subject: str, to: str, **payload):
    return (EntryType.WORK_ITEM_TRANSITION, subject, {"to": to, **payload})


def model_call(subject: str, units: float, work_item: str):
    return (EntryType.MODEL_CALLED, subject, {"costUnits": units, "workItem": work_item})


# ------------------------------------------------------------------------- the measure


def test_an_unavailable_metric_cannot_carry_a_value() -> None:
    """A metric rendered as a number when it could not be observed is the failure PR-9
    names: "changes merged: 0" reads as a factory that merges nothing."""
    with pytest.raises(ValueError, match="rendered as a number"):
        Measure(name="x", value=0.0, availability=Availability.UNAVAILABLE, reason="no adapter")


def test_an_unavailable_metric_must_say_why() -> None:
    with pytest.raises(ValueError, match="tells a reader nothing"):
        Measure(name="x", value=None, availability=Availability.UNAVAILABLE)


def test_an_estimate_must_state_what_it_excludes() -> None:
    """FR-15.4. A number labelled with more confidence than it has is worse than no number,
    because it gets quoted."""
    with pytest.raises(ValueError, match="states no exclusions"):
        Measure(name="cost", value=4.0, estimate=True)


def test_unavailable_and_insufficient_are_different_answers() -> None:
    """One is fixed by configuration and the other by waiting."""
    assert unavailable("x", "no git-host adapter").availability is Availability.UNAVAILABLE
    assert insufficient("x", "no runs yet").availability is Availability.INSUFFICIENT_DATA


def test_a_measure_renders_its_caveats() -> None:
    measure = Measure(
        name="cost_per_change", value=12.5, unit="units", estimate=True, excludes=("human review",)
    )

    assert "estimate" in measure.render()
    assert "unavailable" in unavailable("changes_merged", "no adapter").render()


# ------------------------------------------------------------------------- run counts


def test_run_counts_separate_work_from_measurement(tmp_path: Path) -> None:
    """FR-15.5. A factory whose run count doubled because it started benchmarking has not
    doubled its output, and one total cannot say so.

    This synthesises `purpose` values no writer in the system produces, so it proves the
    fold can split and not that the split ever happens. That gap is real and is why
    `RunCounts.as_dict` now says so when nothing in the window declared a purpose other than
    work -- see `test_run_counts_say_when_no_run_declared_a_purpose_other_than_work`.
    """
    ledger = ledger_with(
        tmp_path,
        run_started("r1", agent="builder", stage="BUILD", tier="local-small"),
        run_started("r2", agent="builder", stage="BUILD", tier="local-small"),
        run_started("r3", purpose="benchmark", agent="critic", stage="REVIEW"),
        run_started("r4", purpose="improvement", agent="critic", stage="REVIEW"),
    )

    runs = compute(ledger.read()).runs

    assert runs.total == 4
    assert runs.work == 2
    assert runs.benchmark == 1
    assert runs.improvement == 1
    assert runs.measurement_share == 0.5


def test_run_counts_break_down_by_agent_stage_and_tier(tmp_path: Path) -> None:
    ledger = ledger_with(
        tmp_path,
        run_started("r1", agent="builder", stage="BUILD", tier="local-small"),
        run_started("r2", agent="critic", stage="REVIEW", tier="mid"),
    )

    runs = compute(ledger.read()).runs

    assert runs.by_agent == {"builder": 1, "critic": 1}
    assert runs.by_stage == {"BUILD": 1, "REVIEW": 1}
    assert runs.by_tier == {"local-small": 1, "mid": 1}


def test_run_counts_carry_the_note_about_measurement_activity(tmp_path: Path) -> None:
    """The dashboard must say so, per FR-15.5, and a note nobody carries is a note nobody
    renders."""
    ledger = ledger_with(tmp_path, run_started("r1"), run_started("r2", purpose="benchmark"))

    assert "measurement activity" in str(compute(ledger.read()).runs.as_dict()["note"])


def test_run_counts_say_when_no_run_declared_a_purpose_other_than_work(tmp_path: Path) -> None:
    """Nothing anywhere wrote a purpose other than "work", so `measurementShare` was a
    structural zero presented as an observation about the factory."""
    ledger = ledger_with(tmp_path, run_started("r1"))

    note = str(compute(ledger.read()).runs.as_dict()["note"])

    assert "no run in this window declared" in note


# --------------------------------------------------------------------- missing adapters


def test_a_metric_needing_an_absent_integration_is_unavailable_not_zero(tmp_path: Path) -> None:
    ledger = ledger_with(tmp_path, run_started("r1"))

    report = compute(ledger.read(), integrations=frozenset())

    merged = report.measure("changes_merged")
    assert merged is not None
    assert merged.availability is Availability.UNAVAILABLE
    assert merged.value is None
    assert "git-host" in merged.reason


def test_the_reason_names_the_missing_integration(tmp_path: Path) -> None:
    """The difference between a dashboard that says "fix your configuration" and one that
    says the factory produces nothing."""
    report = compute(ledger_with(tmp_path, run_started("r1")).read())

    autonomy = report.measure("autonomy")
    assert autonomy is not None
    assert "no git-host adapter is configured" in autonomy.reason


def test_configuring_the_integration_changes_the_reason_rather_than_the_row(
    tmp_path: Path,
) -> None:
    """The row must not vanish, and it must not read as "nobody built this" once built.

    Two wrong answers have lived here. First the row *disappeared* when the adapter was
    configured, so an operator following the reason text's own instruction lost three
    metrics. Then it said "nothing in this build computes this metric yet", which was true
    and is now stale -- these three are computed. What remains correct is the distinction
    the availability states exist for: with the adapter configured and no change yet
    observed, the answer is `insufficient_data` (wait), not `unavailable` (fix your
    configuration) and never zero.
    """
    report = compute(
        ledger_with(tmp_path, run_started("r1")).read(), integrations=frozenset({"git-host"})
    )

    merged = report.measure("changes_merged")
    assert merged is not None
    assert merged.availability is Availability.INSUFFICIENT_DATA
    assert "only the repository can say" in merged.reason


def test_a_metric_that_needs_an_integration_nobody_implemented_still_gets_a_row(
    tmp_path: Path, monkeypatch
) -> None:
    """The third state, kept and still tested now that no shipped metric is in it.

    Configured-but-unimplemented is a real state a future metric will land in, and the
    branch that reports it is exactly the kind of guard that rots into dead code and is
    then deleted as unused -- reintroducing the vanishing row. Registering a metric that
    nothing computes exercises it directly.
    """
    from software_factory.observability import metrics as metrics_module

    monkeypatch.setitem(metrics_module.REQUIRES_INTEGRATION, "time_to_first_review", "git-host")

    report = compute(
        ledger_with(tmp_path, run_started("r1")).read(), integrations=frozenset({"git-host"})
    )

    found = report.measure("time_to_first_review")
    assert found is not None
    assert found.availability is Availability.UNAVAILABLE
    assert "nothing in this build computes" in found.reason


# -------------------------------------------------------------------------- gate rates


def test_the_gate_pass_rate_counts_first_attempts_only(tmp_path: Path) -> None:
    """A gate that passes on the fourth try has still failed, and counting every attempt
    would let a factory improve this by retrying more."""
    ledger = ledger_with(
        tmp_path,
        gate("wi-1", "tests-pass", "fail"),
        gate("wi-1", "tests-pass", "pass"),
        gate("wi-2", "tests-pass", "pass"),
    )

    measure = compute(ledger.read()).measure("gate_pass_rate")

    assert measure is not None
    assert measure.value == 0.5
    assert measure.sample == 2


def test_the_same_gate_at_a_later_stage_is_a_separate_attempt(tmp_path: Path) -> None:
    """The case the fixture above could not construct.

    Several gates legitimately run at more than one stage, and de-duplicating without the
    stage discarded the later evaluations as repeats -- so a pass at BUILD hid a failure at
    REVIEW and the pair reported a 100% pass rate. The first evaluation is also the one most
    likely to have passed, which makes the bias one-directional.
    """
    ledger = ledger_with(
        tmp_path,
        gate("wi-1", "secret-clean", "pass", stage="BUILD"),
        gate("wi-1", "secret-clean", "fail", stage="REVIEW"),
    )

    measure = compute(ledger.read()).measure("gate_pass_rate")

    assert measure is not None
    assert measure.value == 0.5
    assert measure.sample == 2


def test_no_gates_in_the_window_is_insufficient_data_not_zero(tmp_path: Path) -> None:
    ledger = ledger_with(tmp_path, run_started("r1"))

    measure = compute(ledger.read()).measure("gate_pass_rate")

    assert measure is not None
    assert measure.availability is Availability.INSUFFICIENT_DATA


# ------------------------------------------------------------------------ rework, cost


def test_the_rework_rate_counts_work_items_not_transitions(tmp_path: Path) -> None:
    ledger = ledger_with(
        tmp_path,
        transition("wi-1", "REVIEW"),
        transition("wi-1", "BUILD", backwards=True),
        transition("wi-1", "REVIEW"),
        transition("wi-2", "REVIEW"),
    )

    measure = compute(ledger.read()).measure("rework_rate")

    assert measure is not None
    assert measure.value == 0.5


def test_changes_opened_counts_a_work_item_once(tmp_path: Path) -> None:
    """A change updated four times is one change; counting updates would make a factory look
    more productive for revising more.

    The HANDOFF transition synthesised here is one the coordinator did not write, so this
    passed while the metric never computed on real data. `_default_path` now ends at
    HANDOFF and `test_a_work_item_runs_all_the_way_to_handoff` proves the real path reaches
    it; this stays as the unit check on the de-duplication itself.
    """
    ledger = ledger_with(
        tmp_path,
        transition("wi-1", "HANDOFF"),
        transition("wi-1", "HANDOFF"),
        transition("wi-2", "HANDOFF"),
    )

    measure = compute(ledger.read(), integrations=frozenset({"git-host"})).measure("changes_opened")

    assert measure is not None
    assert measure.value == 2.0


def test_cost_per_change_is_a_median_and_says_it_is_an_estimate(tmp_path: Path) -> None:
    """It derives from recorded token usage and declared prices, not a provider's bill.
    Median rather than mean because one runaway work item would otherwise define it."""
    ledger = ledger_with(
        tmp_path,
        model_call("r1", 10, "wi-1"),
        model_call("r2", 20, "wi-2"),
        model_call("r3", 900, "wi-3"),
        transition("wi-1", "HANDOFF"),
        transition("wi-2", "HANDOFF"),
        transition("wi-3", "HANDOFF"),
    )

    measure = compute(ledger.read()).measure("cost_per_change")

    assert measure is not None
    assert measure.value == 20.0
    assert measure.estimate
    assert "human review time" in measure.excludes


def test_cost_per_change_without_a_completed_change_is_insufficient(tmp_path: Path) -> None:
    ledger = ledger_with(tmp_path, model_call("r1", 10, "wi-1"))

    measure = compute(ledger.read()).measure("cost_per_change")

    assert measure is not None
    assert measure.availability is Availability.INSUFFICIENT_DATA


# ------------------------------------------------------------------------------ window


def test_entries_outside_the_window_do_not_count(tmp_path: Path) -> None:
    """A number without a window is a number nobody can act on."""
    ledger = ledger_with(tmp_path, run_started("r1"), run_started("r2"))
    past = Window(start=utc_now() - timedelta(days=30), end=utc_now() - timedelta(days=20))

    assert compute(ledger.read(), window=past).runs.total == 0


def test_a_report_carries_its_window(tmp_path: Path) -> None:
    report = compute(ledger_with(tmp_path, run_started("r1")).read())

    body = report.as_dict()
    assert body["window"]["start"] < body["window"]["end"]


def test_every_metric_is_a_fold_over_the_ledger(tmp_path: Path) -> None:
    """FR-15.2: derived state must be rebuildable from the ledger. Computing the same window
    twice from the same entries must produce the same report, or something is being
    accumulated somewhere that the ledger does not describe."""
    ledger = ledger_with(
        tmp_path,
        run_started("r1", agent="builder"),
        gate("wi-1", "tests-pass", "pass"),
        transition("wi-1", "HANDOFF"),
    )
    window = Window.last(timedelta(days=7))

    first = compute(ledger.read(), window=window).as_dict()
    second = compute(ledger.read(), window=window).as_dict()

    assert first == second
