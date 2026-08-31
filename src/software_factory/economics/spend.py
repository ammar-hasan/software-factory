"""Spend caps, attribution, and what happens at each threshold (PRD FR-26.1, FR-26.5).

A budget on a single run (``harness.loop.Budget``) bounds one agent. It cannot bound a
factory: a hundred runs each inside their budget is a hundred budgets' worth of spend, and
"each run was within its limit" is the sentence that precedes every surprise invoice.

Three thresholds, with a declared behaviour at each, because the interesting question is not
"what is the cap" but "what happens as you approach it":

* **warn** -- the work continues and somebody is told. A cap that only acts at the boundary
  gives an operator no chance to act before it.
* **stop intake** -- work already started finishes; new work is refused. Killing in-flight
  runs at the cap wastes everything spent on them, which makes the cap *more* expensive.
* **halt** -- nothing more starts and running work is stopped. Reserved for the hard cap,
  because it destroys partial results.

Attribution (FR-26.5) is not an afterthought here: every charge names a work item, an agent,
a stage, and a **cause**. Without the cause, "we spent £400 today" cannot be separated into
work, retries, scoring, and benchmarking -- and those four have completely different
answers to "is this a problem".
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from software_factory.memory.records import utc_now


class Cause(enum.StrEnum):
    """Why this money was spent (FR-26.5).

    Retry and repair are separate from primary work on purpose. A factory whose spend is
    30% retries has a different problem from one whose spend is 30% benchmarking, and a
    single "cost" number cannot tell an operator which they have.
    """

    PRIMARY = "primary"
    RETRY = "retry"
    REPAIR = "repair"
    SCORING = "scoring"
    BENCHMARK = "benchmark"
    IMPROVEMENT = "improvement"
    ONBOARDING = "onboarding"


class CapState(enum.StrEnum):
    """Where spend sits against its cap, and therefore what the factory does."""

    OK = "ok"
    WARNING = "warning"
    INTAKE_STOPPED = "intake_stopped"
    HALTED = "halted"

    @property
    def accepts_new_work(self) -> bool:
        return self in (CapState.OK, CapState.WARNING)

    @property
    def continues_running_work(self) -> bool:
        """In-flight work finishes at every state but ``halted``.

        Killing a run at the cap discards everything already spent on it, which makes the
        cap cost more than not having one. Only the hard stop is worth that.
        """
        return self is not CapState.HALTED


@dataclass(frozen=True, slots=True)
class Charge:
    """One attributed unit of spend.

    ``units`` rather than a currency: a factory mixing local and hosted models has no single
    currency, and forcing one would either invent an exchange rate or drop the local runs
    from the total. The unit is whatever the ladder's per-token prices are denominated in.
    """

    units: float
    work_item_id: str
    agent: str
    stage: str
    cause: Cause
    at: datetime = field(default_factory=utc_now)
    run_id: str = ""
    tier: str = ""

    def __post_init__(self) -> None:
        if self.units < 0:
            raise ValueError("a charge cannot be negative; record a correction as its own entry")


@dataclass(frozen=True, slots=True)
class SpendCap:
    """A cap for one scope over one period, with its behaviour at each threshold."""

    scope: str
    limit_units: float
    period: timedelta = timedelta(days=1)
    warn_at: float = 0.8
    stop_intake_at: float = 1.0
    halt_at: float = 1.25

    def __post_init__(self) -> None:
        if self.limit_units <= 0:
            raise ValueError("a cap of zero or less refuses everything; omit the cap instead")
        if not 0 < self.warn_at <= self.stop_intake_at <= self.halt_at:
            raise ValueError(
                "thresholds must be ordered warn <= stop_intake <= halt; an out-of-order "
                "set would halt before it warned"
            )

    def state_for(self, spent: float) -> CapState:
        fraction = spent / self.limit_units
        if fraction >= self.halt_at:
            return CapState.HALTED
        if fraction >= self.stop_intake_at:
            return CapState.INTAKE_STOPPED
        if fraction >= self.warn_at:
            return CapState.WARNING
        return CapState.OK


def attribute_to_roots(charges: list[Charge], parents: dict[str, str]) -> dict[str, float]:
    """Fold each charge up to the run that ultimately caused it (FR-34.2).

    A child's spend counts against its parent, all the way to the root. Without this,
    delegation is a way to exceed a work item's budget by asking someone else to spend it:
    the budget bounds a run, and a run that can create runs bounds nothing.

    Cycles are impossible by construction -- `DelegationBook.record` refuses a run that
    already has a parent and refuses self-parenting -- but the walk is bounded anyway,
    because a ledger is a file and a file can be edited.
    """
    totals: dict[str, float] = {}
    for charge in charges:
        run = charge.run_id or charge.work_item_id
        seen: set[str] = set()
        while run in parents and run not in seen:
            seen.add(run)
            run = parents[run]
        totals[run] = totals.get(run, 0.0) + charge.units
    return totals


def charges_from(entries: Iterable[Any]) -> list[Charge]:
    """Fold ledger entries into charges.

    Here rather than in the CLI, which is where it used to live. A cap the operator reads
    with `sf spend` and a cap the coordinator enforces before starting a stage have to be
    the *same* computation -- otherwise "within budget" means two different things depending
    on who asks, and the one that stops work is the one nobody checked.
    """
    from software_factory.ledger.entry import EntryType

    charges: list[Charge] = []
    for entry in entries:
        if entry.type is not EntryType.MODEL_CALLED:
            continue
        payload = entry.payload
        units = float(payload.get("costUnits", 0.0) or 0.0)
        if not units:
            continue
        raw_cause = str(payload.get("cause", Cause.PRIMARY.value))
        charges.append(
            Charge(
                units=units,
                work_item_id=str(payload.get("workItem", entry.subject)),
                agent=str(payload.get("agent", "unknown")),
                stage=str(payload.get("stage", "unknown")),
                cause=Cause(raw_cause) if raw_cause in set(Cause) else Cause.PRIMARY,
                at=datetime.fromisoformat(entry.ts.replace("Z", "+00:00")),
                run_id=str(payload.get("run", "")),
                tier=str(payload.get("tier", "")),
            )
        )
    return charges


@dataclass(frozen=True, slots=True)
class SpendReport:
    """Spend against a cap, broken down the way FR-26.5 requires it to be."""

    scope: str
    window_start: datetime
    spent: float
    limit: float
    state: CapState
    by_cause: dict[str, float] = field(default_factory=dict)
    """Spend per `Cause`, over the causes actually present.

    Read `observed_causes` before drawing a conclusion from `overhead_fraction`: only the
    causes something emits can appear, and for a long time that was two of seven.
    """

    by_agent: dict[str, float] = field(default_factory=dict)
    by_stage: dict[str, float] = field(default_factory=dict)
    by_work_item: dict[str, float] = field(default_factory=dict)

    @property
    def fraction(self) -> float:
        return self.spent / self.limit if self.limit else 0.0

    @property
    def observed_causes(self) -> tuple[str, ...]:
        """Which causes actually appear in this window.

        `overhead_fraction` is a share of a denominator, and the denominator is only as
        good as the categories something emits. For a long time exactly two of the seven
        `Cause` values were ever written, by one call site, which attributed a run's
        *entire* spend to `repair` if it repaired at all -- so the number meant "the share
        belonging to runs that repaired at least once", over-counting a repaired run's
        primary work and booking all scoring and benchmarking as primary. Reporting which
        causes were seen is what lets a reader tell the two readings apart.
        """
        return tuple(sorted(self.by_cause))

    @property
    def overhead_fraction(self) -> float:
        """The share not spent on primary work.

        Named rather than derived at each call site because it is the number an operator
        actually asks for, and because "overhead" needs one definition: retries, repairs,
        scoring, benchmarking and improvement are all real costs of running a factory, and
        a factory spending most of its money on them is a factory doing something wrong.

        Qualified by `observed_causes`: a share over categories nothing emits is a share of
        nothing, however confident the number looks.
        """
        if not self.spent:
            return 0.0
        primary = self.by_cause.get(Cause.PRIMARY.value, 0.0)
        return (self.spent - primary) / self.spent

    def as_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "windowStart": self.window_start.isoformat(),
            "spent": round(self.spent, 4),
            "limit": self.limit,
            "fraction": round(self.fraction, 4),
            "state": self.state.value,
            "overheadFraction": round(self.overhead_fraction, 4),
            # Emitted beside the fraction rather than left for a reader to infer: a share
            # computed over two of seven categories reads exactly like one computed over
            # all of them, and only this line distinguishes them.
            "observedCauses": list(self.observed_causes),
            "byCause": {k: round(v, 4) for k, v in sorted(self.by_cause.items())},
            "byAgent": {k: round(v, 4) for k, v in sorted(self.by_agent.items())},
            "byStage": {k: round(v, 4) for k, v in sorted(self.by_stage.items())},
            "byWorkItem": {k: round(v, 4) for k, v in sorted(self.by_work_item.items())},
        }


class Ledgerless:
    """Spend accounting over a window, independent of where charges are stored.

    Named for what it is not: this does not own storage. Charges are ledger entries in a
    running factory and a list in a test, and the accounting must be identical either way --
    otherwise the number an operator sees and the number a test asserts are two different
    numbers with one name.
    """

    def __init__(self, cap: SpendCap) -> None:
        self.cap = cap

    def report(self, charges: Iterable[Charge], *, now: datetime | None = None) -> SpendReport:
        now = now or utc_now()
        start = now - self.cap.period
        # Bounded at both ends. With no upper bound a charge dated in the future counted
        # against today's cap, so a clock skew on one worker -- or a charge someone
        # backdated forwards -- could halt a factory that had spent nothing.
        window = [c for c in charges if start <= c.at <= now]

        by_cause: dict[str, float] = {}
        by_agent: dict[str, float] = {}
        by_stage: dict[str, float] = {}
        by_work_item: dict[str, float] = {}
        total = 0.0
        for charge in window:
            total += charge.units
            by_cause[charge.cause.value] = by_cause.get(charge.cause.value, 0.0) + charge.units
            by_agent[charge.agent] = by_agent.get(charge.agent, 0.0) + charge.units
            by_stage[charge.stage] = by_stage.get(charge.stage, 0.0) + charge.units
            by_work_item[charge.work_item_id] = (
                by_work_item.get(charge.work_item_id, 0.0) + charge.units
            )

        return SpendReport(
            scope=self.cap.scope,
            window_start=start,
            spent=total,
            limit=self.cap.limit_units,
            state=self.cap.state_for(total),
            by_cause=by_cause,
            by_agent=by_agent,
            by_stage=by_stage,
            by_work_item=by_work_item,
        )
