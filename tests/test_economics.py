"""Spend caps, attribution, admission control, scheduling, and backpressure.

The theme: a budget on one run bounds one agent, and a hundred runs each inside their
budget is a hundred budgets' worth of spend. Everything here is about the bound that a
per-run budget cannot express.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from software_factory.economics import (
    Admitted,
    Backpressure,
    CapState,
    Cause,
    Charge,
    ConcurrencyLimiter,
    Ledgerless,
    Priority,
    Queued,
    Rejected,
    Scheduler,
    SourceLimits,
    SpendCap,
    fingerprint_of,
)
from software_factory.memory.records import utc_now

# ------------------------------------------------------------------------------- spend


def charge(units: float, **kwargs) -> Charge:
    base: dict[str, object] = {
        "units": units,
        "work_item_id": "wi-1",
        "agent": "builder",
        "stage": "BUILD",
        "cause": Cause.PRIMARY,
    }
    base.update(kwargs)
    return Charge(**base)  # type: ignore[arg-type]


def test_a_cap_reports_ok_below_its_warning_threshold() -> None:
    cap = SpendCap(scope="factory:payments", limit_units=100)

    assert cap.state_for(0) is CapState.OK
    assert cap.state_for(79) is CapState.OK


def test_a_cap_warns_before_it_acts() -> None:
    """A cap that only acts at the boundary gives an operator no chance to act before it."""
    cap = SpendCap(scope="factory:payments", limit_units=100)

    state = cap.state_for(85)

    assert state is CapState.WARNING
    assert state.accepts_new_work


def test_at_the_cap_intake_stops_and_running_work_finishes() -> None:
    """Killing in-flight runs at the cap discards everything already spent on them, which
    makes the cap cost more than not having one."""
    cap = SpendCap(scope="factory:payments", limit_units=100)

    state = cap.state_for(100)

    assert state is CapState.INTAKE_STOPPED
    assert not state.accepts_new_work
    assert state.continues_running_work


def test_over_the_hard_cap_everything_halts() -> None:
    cap = SpendCap(scope="factory:payments", limit_units=100)

    state = cap.state_for(130)

    assert state is CapState.HALTED
    assert not state.accepts_new_work
    assert not state.continues_running_work


def test_an_out_of_order_threshold_set_is_refused() -> None:
    """A set that halts before it warns is not a policy anyone meant to write."""
    with pytest.raises(ValueError, match="ordered"):
        SpendCap(scope="f", limit_units=100, warn_at=1.5, stop_intake_at=1.0)


def test_a_zero_cap_is_refused_rather_than_silently_refusing_everything() -> None:
    with pytest.raises(ValueError, match="refuses everything"):
        SpendCap(scope="f", limit_units=0)


def test_a_negative_charge_is_refused() -> None:
    """A correction is its own entry. A negative charge makes the total unreconstructible
    from the entries that produced it."""
    with pytest.raises(ValueError, match="cannot be negative"):
        charge(-5)


def test_spend_is_attributed_four_ways() -> None:
    """FR-26.5. "We spent 400 today" cannot be separated into work, retries, scoring and
    benchmarking, and those four have completely different answers to "is this a problem"."""
    accounting = Ledgerless(SpendCap(scope="factory:payments", limit_units=100))

    report = accounting.report(
        [
            charge(10, cause=Cause.PRIMARY, agent="builder", stage="BUILD"),
            charge(5, cause=Cause.RETRY, agent="builder", stage="BUILD"),
            charge(3, cause=Cause.SCORING, agent="critic", stage="REVIEW", work_item_id="wi-2"),
        ]
    )

    assert report.spent == 18
    assert report.by_cause == {"primary": 10, "retry": 5, "scoring": 3}
    assert report.by_agent == {"builder": 15, "critic": 3}
    assert report.by_stage == {"BUILD": 15, "REVIEW": 3}
    assert report.by_work_item == {"wi-1": 15, "wi-2": 3}


def test_overhead_is_everything_not_spent_on_primary_work() -> None:
    """A factory spending most of its money on retries and scoring is doing something
    wrong, and a single cost number cannot say so."""
    accounting = Ledgerless(SpendCap(scope="f", limit_units=100))

    report = accounting.report([charge(60, cause=Cause.PRIMARY), charge(40, cause=Cause.RETRY)])

    assert report.overhead_fraction == pytest.approx(0.4)


def test_charges_outside_the_window_do_not_count() -> None:
    """A daily cap that counted last week's spend would halt a factory that is under it."""
    accounting = Ledgerless(SpendCap(scope="f", limit_units=100, period=timedelta(days=1)))
    now = utc_now()

    report = accounting.report(
        [charge(90, at=now - timedelta(days=3)), charge(10, at=now)], now=now
    )

    assert report.spent == 10
    assert report.state is CapState.OK


def test_a_report_renders_every_breakdown() -> None:
    accounting = Ledgerless(SpendCap(scope="factory:payments", limit_units=100))

    body = accounting.report([charge(50)]).as_dict()

    assert body["state"] == "ok"
    assert body["fraction"] == 0.5
    assert body["byCause"] == {"primary": 50.0}


# ------------------------------------------------------------------------ backpressure


def queued(item_id: str, source: str = "tracker", **kwargs) -> Queued:
    base: dict[str, object] = {"id": item_id, "source": source}
    base.update(kwargs)
    return Queued(**base)  # type: ignore[arg-type]


def test_the_same_signal_arriving_twice_is_admitted_once() -> None:
    """A signal storm sends the *same* alert from a provider that assigns a new id each
    time, so deduplicating by id deduplicates nothing."""
    gate = Backpressure()
    print_ = fingerprint_of("deploy failed", "payments", "prod")

    assert isinstance(gate.admit(queued("a", fingerprint=print_)), Admitted)
    duplicate = gate.admit(queued("b", fingerprint=print_))

    assert isinstance(duplicate, Rejected)
    assert duplicate.code == "intake.duplicate"


def test_a_fingerprint_is_stable_across_incidental_differences() -> None:
    assert fingerprint_of("Deploy Failed ", "PAYMENTS") == fingerprint_of(
        "deploy failed", "payments"
    )
    assert fingerprint_of("deploy failed", "payments") != fingerprint_of("deploy failed", "search")


def test_a_source_exceeding_its_rate_is_limited_with_a_retry_time() -> None:
    gate = Backpressure(SourceLimits(max_per_window=2, window=timedelta(minutes=10)))

    assert isinstance(gate.admit(queued("a")), Admitted)
    assert isinstance(gate.admit(queued("b")), Admitted)
    limited = gate.admit(queued("c"))

    assert isinstance(limited, Rejected)
    assert limited.code == "intake.rate_limited"
    assert limited.retry_after == timedelta(minutes=10)


def test_the_window_rolls() -> None:
    gate = Backpressure(SourceLimits(max_per_window=2, window=timedelta(minutes=10)))
    now = utc_now()
    gate.admit(queued("a"), now=now)
    gate.admit(queued("b"), now=now)

    later = gate.admit(queued("c"), now=now + timedelta(minutes=11))

    assert isinstance(later, Admitted)


def test_repeated_rate_limiting_parks_the_source() -> None:
    """A source producing work faster than the factory can do it is usually a storm, and a
    storm converted directly into spend is the failure FR-26.3 exists for."""
    gate = Backpressure(
        SourceLimits(max_per_window=1, window=timedelta(minutes=10), breaker_trips=2)
    )
    now = utc_now()
    gate.admit(queued("a"), now=now)

    first_trip = gate.admit(queued("b"), now=now)
    assert isinstance(first_trip, Rejected)
    assert first_trip.code == "intake.rate_limited"

    parked = gate.admit(queued("c"), now=now)
    assert isinstance(parked, Rejected)
    assert parked.code == "intake.breaker_tripped"


def test_a_parked_source_reopens_on_its_own() -> None:
    """A breaker that needs a human to reset turns a transient storm into an outage nobody
    notices ended."""
    limits = SourceLimits(
        max_per_window=1,
        window=timedelta(minutes=10),
        breaker_trips=1,
        breaker_cooldown=timedelta(hours=1),
    )
    gate = Backpressure(limits)
    now = utc_now()
    gate.admit(queued("a"), now=now)
    assert isinstance(gate.admit(queued("b"), now=now), Rejected)

    reopened = gate.admit(queued("c"), now=now + timedelta(hours=2))

    assert isinstance(reopened, Admitted)


def test_a_storm_of_identical_alerts_does_not_trip_the_breaker() -> None:
    """Deduplication runs first: rate-limiting before deduplicating would let a storm of
    identical alerts park a source over work that was never real."""
    gate = Backpressure(
        SourceLimits(max_per_window=2, window=timedelta(minutes=10), breaker_trips=2)
    )
    print_ = fingerprint_of("deploy failed", "payments")

    assert isinstance(gate.admit(queued("a", fingerprint=print_)), Admitted)
    for index in range(20):
        rejected = gate.admit(queued(f"dup{index}", fingerprint=print_))
        assert isinstance(rejected, Rejected)
        assert rejected.code == "intake.duplicate"

    assert gate.state_for("tracker").parked_until is None


def test_one_source_being_parked_does_not_park_another() -> None:
    gate = Backpressure(
        SourceLimits(max_per_window=1, window=timedelta(minutes=10), breaker_trips=1)
    )
    now = utc_now()
    gate.admit(queued("a", source="alerts"), now=now)
    gate.admit(queued("b", source="alerts"), now=now)

    other = gate.admit(queued("c", source="tracker"), now=now)

    assert isinstance(other, Admitted)


# --------------------------------------------------------------------------- scheduling


def test_sources_take_turns() -> None:
    """A global priority queue hands the factory to whichever source labels its work most
    urgently, which is a property of that source's culture rather than of the work."""
    schedule = Scheduler()
    for index in range(3):
        schedule.enqueue(queued(f"busy-{index}", source="busy"))
    schedule.enqueue(queued("quiet-0", source="quiet"))

    order = [item.id for item in schedule.drain(4)]

    assert order[:2] == ["busy-0", "quiet-0"]


def test_priority_orders_within_a_source() -> None:
    schedule = Scheduler()
    schedule.enqueue(queued("routine", priority=Priority.LOW))
    schedule.enqueue(queued("incident", priority=Priority.URGENT))

    assert schedule.next().id == "incident"


def test_waiting_improves_an_items_position() -> None:
    """Ageing rather than a starvation timeout: a timeout produces a cliff where a LOW item
    suddenly outranks everything and the queue reorders inexplicably."""
    now = utc_now()
    schedule = Scheduler()
    schedule.enqueue(queued("old-low", priority=Priority.LOW, queued_at=now - timedelta(hours=12)))
    schedule.enqueue(queued("new-normal", priority=Priority.NORMAL, queued_at=now))

    assert schedule.next(now=now).id == "old-low"


def test_a_recently_queued_low_item_still_waits() -> None:
    """The fix must not invert priority outright."""
    now = utc_now()
    schedule = Scheduler()
    schedule.enqueue(queued("fresh-low", priority=Priority.LOW, queued_at=now))
    schedule.enqueue(queued("fresh-high", priority=Priority.HIGH, queued_at=now))

    assert schedule.next(now=now).id == "fresh-high"


def test_queue_depth_is_reported_per_source() -> None:
    schedule = Scheduler()
    schedule.enqueue(queued("a", source="tracker"))
    schedule.enqueue(queued("b", source="tracker"))
    schedule.enqueue(queued("c", source="chat"))

    assert schedule.depth_by_source() == {"chat": 1, "tracker": 2}
    assert len(schedule) == 3


def test_the_same_item_cannot_be_queued_twice() -> None:
    schedule = Scheduler()
    schedule.enqueue(queued("a"))

    with pytest.raises(ValueError, match="already queued"):
        schedule.enqueue(queued("a"))


def test_an_empty_scheduler_yields_nothing() -> None:
    assert Scheduler().next() is None
    assert Scheduler().drain(5) == []


# -------------------------------------------------------------------------- concurrency


def test_the_factory_declines_work_it_cannot_run() -> None:
    """A run refused before it starts costs nothing; one throttled halfway through has
    already spent money."""
    limiter = ConcurrencyLimiter(total=2, per_agent=2)

    assert limiter.acquire("builder", "r1") is None
    assert limiter.acquire("critic", "r2") is None
    refused = limiter.acquire("scout", "r3")

    assert isinstance(refused, Rejected)
    assert refused.code == "schedule.at_capacity"


def test_one_agent_cannot_saturate_the_factory() -> None:
    limiter = ConcurrencyLimiter(total=8, per_agent=2)
    limiter.acquire("builder", "r1")
    limiter.acquire("builder", "r2")

    refused = limiter.acquire("builder", "r3")

    assert isinstance(refused, Rejected)
    assert refused.code == "schedule.agent_at_capacity"
    assert limiter.acquire("critic", "r4") is None


def test_releasing_frees_capacity_and_is_idempotent() -> None:
    """A run released twice is a bookkeeping error, not a reason to raise inside a `finally`
    that is already cleaning up after a failure."""
    limiter = ConcurrencyLimiter(total=1, per_agent=1)
    limiter.acquire("builder", "r1")

    limiter.release("builder", "r1")
    limiter.release("builder", "r1")

    assert limiter.in_flight == 0
    assert limiter.acquire("builder", "r2") is None


def test_a_limit_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="admits nothing"):
        ConcurrencyLimiter(total=0)
