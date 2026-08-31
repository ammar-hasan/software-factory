"""Metrics computed from the ledger, and the ones that honestly cannot be (PRD FR-15.3-15.5).

FR-15.2: all derived state must be rebuildable from the ledger. So every number here is a
fold over ledger entries and nothing is accumulated in a counter somewhere -- a counter can
drift from the events that produced it, and then the dashboard and the record disagree with
nobody able to say which is right.

Two rules run through the whole module and are more important than any individual metric:

* **A metric that needs an integration this factory does not have is `unavailable` with a
  reason, never zero** (FR-15.5, PR-9). "Changes merged: 0" reads as a factory that merges
  nothing. "Changes merged: unavailable -- no git-host adapter is configured" reads as a
  factory nobody has told about its git host. Those are different situations and a zero
  cannot distinguish them.
* **A cost derived from recorded usage is an estimate and says so** (FR-15.4), along with
  what it excludes. A number labelled with more confidence than it has is worse than no
  number, because it gets quoted.

FR-15.5's other half is here too: aggregate run counts include evaluation, benchmark and
improvement runs, and :class:`RunCounts` separates them -- a rising run count with flat
output is measurement activity, not work, and a single total cannot say which.
"""

from __future__ import annotations

import enum
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from software_factory.ledger.entry import EntryType, LedgerEntry
from software_factory.memory.records import utc_now


class Availability(enum.StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    """Needs an integration this factory does not have. Never rendered as zero."""

    INSUFFICIENT_DATA = "insufficient_data"
    """The integration exists and the window is too thin to say anything. Distinct from
    unavailable: one is fixed by configuration and the other by waiting."""


@dataclass(frozen=True, slots=True)
class Measure:
    """One metric, its value, and how much to trust it.

    ``value`` is ``None`` for anything but ``AVAILABLE``, so a caller that forgets to check
    availability gets a ``None`` rather than a plausible-looking zero.
    """

    name: str
    value: float | None
    availability: Availability = Availability.AVAILABLE
    unit: str = ""
    reason: str = ""
    estimate: bool = False
    excludes: tuple[str, ...] = ()
    sample: int = 0

    def __post_init__(self) -> None:
        if self.availability is not Availability.AVAILABLE and self.value is not None:
            raise ValueError(
                f"{self.name} is {self.availability.value} and carries a value; an "
                "unavailable metric rendered as a number is the failure PR-9 names"
            )
        if self.availability is not Availability.AVAILABLE and not self.reason.strip():
            raise ValueError(
                f"{self.name} is {self.availability.value} with no reason; 'unavailable' "
                "with no explanation tells a reader nothing they can act on"
            )
        if self.estimate and not self.excludes:
            raise ValueError(
                f"{self.name} is labelled an estimate and states no exclusions; FR-15.4 "
                "requires saying what an estimate leaves out"
            )

    def render(self) -> str:
        if self.availability is not Availability.AVAILABLE:
            return f"{self.name}: [{self.availability.value}] {self.reason}"
        assert self.value is not None
        mark = " (estimate)" if self.estimate else ""
        unit = f" {self.unit}" if self.unit else ""
        return f"{self.name}: {self.value:g}{unit}{mark}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "availability": self.availability.value,
            "unit": self.unit,
            "reason": self.reason,
            "estimate": self.estimate,
            "excludes": list(self.excludes),
            "sample": self.sample,
        }


def unavailable(name: str, reason: str) -> Measure:
    """A metric this factory cannot compute, and why. Never a zero."""
    return Measure(name=name, value=None, availability=Availability.UNAVAILABLE, reason=reason)


def insufficient(name: str, reason: str) -> Measure:
    return Measure(
        name=name, value=None, availability=Availability.INSUFFICIENT_DATA, reason=reason
    )


@dataclass(frozen=True, slots=True)
class RunCounts:
    """Runs, split by what they were for (FR-15.5).

    The split is the requirement, not a convenience. A factory whose run count doubled
    because it started benchmarking has not doubled its output, and one total cannot say so.
    """

    work: int = 0
    evaluation: int = 0
    benchmark: int = 0
    improvement: int = 0
    by_agent: dict[str, int] = field(default_factory=dict)
    by_stage: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)
    by_tier: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.work + self.evaluation + self.benchmark + self.improvement

    @property
    def measurement_share(self) -> float:
        """How much of the run count is the factory measuring itself."""
        return (self.total - self.work) / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "work": self.work,
            "evaluation": self.evaluation,
            "benchmark": self.benchmark,
            "improvement": self.improvement,
            "measurementShare": round(self.measurement_share, 4),
            "byAgent": dict(sorted(self.by_agent.items())),
            "byStage": dict(sorted(self.by_stage.items())),
            "byStatus": dict(sorted(self.by_status.items())),
            "byTier": dict(sorted(self.by_tier.items())),
            "note": (
                "Run counts include evaluation, benchmark and improvement runs. A rising "
                "total with flat output can be measurement activity rather than work."
                if self.total > self.work
                else (
                    "Run counts include evaluation, benchmark and improvement runs, but no "
                    "run in this window declared a purpose other than work — so a "
                    "measurement share of zero here is the absence of measurement runs, "
                    "not evidence that none were needed."
                )
            ),
        }


@dataclass(frozen=True, slots=True)
class Window:
    """The period a report covers. Carried on the report so a number is never undated."""

    start: datetime
    end: datetime

    @classmethod
    def last(cls, period: timedelta, *, now: datetime | None = None) -> Window:
        end = now or utc_now()
        return cls(start=end - period, end=end)

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end


@dataclass(frozen=True, slots=True)
class Report:
    """Every metric for one window."""

    window: Window
    runs: RunCounts
    measures: tuple[Measure, ...]

    def measure(self, name: str) -> Measure | None:
        return next((m for m in self.measures if m.name == name), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "window": {
                "start": self.window.start.isoformat(),
                "end": self.window.end.isoformat(),
            },
            "runs": self.runs.as_dict(),
            "measures": [m.as_dict() for m in self.measures],
        }


#: Which integrations a metric needs to be computable at all.
#:
#: Declared as data so `sf metrics` can say *which* integration is missing rather than a
#: generic "unavailable", and so adding an integration does not mean hunting for the metrics
#: it unblocks.
REQUIRES_INTEGRATION: dict[str, str] = {
    "changes_opened": "git-host",
    "changes_merged": "git-host",
    "autonomy": "git-host",
    "cycle_time_to_merge": "git-host",
}
"""Metrics that cannot be observed from the ledger alone.

`changes_opened` belongs here for the same reason as the other three, and its absence was
the sharper bug: it reported `0 changes` as an *established* value, directly beneath three
measures correctly saying that reporting zero would read as a factory producing none.
Opening a change is an act on a git host; a local run reaching HANDOFF is not evidence one
happened.
"""


def compute(
    entries: Iterable[LedgerEntry],
    *,
    window: Window | None = None,
    integrations: frozenset[str] = frozenset(),
) -> Report:
    """Fold the ledger into a report.

    ``integrations`` names what this factory actually has. Metrics needing something absent
    come back unavailable with that name in the reason -- which is the difference between a
    dashboard that says "fix your configuration" and one that says a factory merges nothing.
    """
    window = window or Window.last(timedelta(days=7))
    in_window = [entry for entry in entries if window.contains(_ts(entry))]

    runs = _count_runs(in_window)
    measures: list[Measure] = [
        _gate_pass_rate(in_window),
        _escalation_rate(in_window, runs.total),
        _rework_rate(in_window),
        _cost_per_change(in_window),
    ]
    if "git-host" in integrations:
        measures.append(_changes_opened(in_window))

    for name, integration in sorted(REQUIRES_INTEGRATION.items()):
        if integration not in integrations:
            measures.append(
                unavailable(
                    name,
                    f"no {integration} adapter is configured, so this cannot be observed; "
                    "reporting zero here would read as a factory that produces none",
                )
            )

    return Report(window=window, runs=runs, measures=tuple(measures))


def _ts(entry: LedgerEntry) -> datetime:
    return datetime.fromisoformat(entry.ts.replace("Z", "+00:00"))


def _count_runs(entries: list[LedgerEntry]) -> RunCounts:
    work = evaluation = benchmark = improvement = 0
    by_agent: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}

    for entry in entries:
        if entry.type is EntryType.RUN_STARTED:
            purpose = str(entry.payload.get("purpose", "work"))
            match purpose:
                case "evaluation":
                    evaluation += 1
                case "benchmark":
                    benchmark += 1
                case "improvement":
                    improvement += 1
                case _:
                    work += 1
            agent = str(entry.payload.get("agent", entry.actor))
            stage = str(entry.payload.get("stage", "unknown"))
            tier = str(entry.payload.get("tier", "unknown"))
            by_agent[agent] = by_agent.get(agent, 0) + 1
            by_stage[stage] = by_stage.get(stage, 0) + 1
            by_tier[tier] = by_tier.get(tier, 0) + 1
        elif entry.type is EntryType.RUN_FINISHED:
            status = str(entry.payload.get("status", "unknown"))
            by_status[status] = by_status.get(status, 0) + 1

    return RunCounts(
        work=work,
        evaluation=evaluation,
        benchmark=benchmark,
        improvement=improvement,
        by_agent=by_agent,
        by_stage=by_stage,
        by_status=by_status,
        by_tier=by_tier,
    )


def _gate_pass_rate(entries: list[LedgerEntry]) -> Measure:
    """First-attempt pass rate per gate, aggregated.

    First attempt only: a gate that passes on the fourth try has still failed, and counting
    every attempt would let a factory improve this number by retrying more.

    Keyed by *stage* as well as work item and gate. Several gates legitimately run at more
    than one stage, and without the stage in the key the later evaluations were discarded
    as repeats of the first -- so a `secret-clean` pass at BUILD hid a `secret-clean`
    failure at REVIEW and the pair reported as a 100% pass rate. The first evaluation is
    also the one most likely to have passed, which makes the bias one-directional.
    """
    seen: set[tuple[str, str, str]] = set()
    passed = attempted = 0
    for entry in entries:
        if entry.type is not EntryType.GATE_EVALUATED:
            continue
        key = (
            str(entry.subject),
            str(entry.payload.get("gate", "")),
            str(entry.payload.get("stage", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        attempted += 1
        if entry.payload.get("outcome") == "pass":
            passed += 1
    if not attempted:
        return insufficient("gate_pass_rate", "no gates ran in this window")
    return Measure(
        name="gate_pass_rate",
        value=round(passed / attempted, 4),
        unit="share",
        sample=attempted,
    )


def _escalation_rate(entries: list[LedgerEntry], runs: int) -> Measure:
    escalated = len({e.subject for e in entries if e.type is EntryType.ESCALATION})
    if not runs:
        return insufficient("escalation_rate", "no runs in this window")
    return Measure(
        name="escalation_rate", value=round(escalated / runs, 4), unit="share", sample=runs
    )


def _rework_rate(entries: list[LedgerEntry]) -> Measure:
    """Share of work items that went backwards at least once."""
    items: set[str] = set()
    reworked: set[str] = set()
    for entry in entries:
        if entry.type is not EntryType.WORK_ITEM_TRANSITION:
            continue
        items.add(str(entry.subject))
        if entry.payload.get("backwards"):
            reworked.add(str(entry.subject))
    if not items:
        return insufficient("rework_rate", "no work items moved in this window")
    return Measure(
        name="rework_rate",
        value=round(len(reworked) / len(items), 4),
        unit="share",
        sample=len(items),
    )


def _changes_opened(entries: list[LedgerEntry]) -> Measure:
    """Counted once, in the period first observed (FR-15.3).

    Deduplicated by work item: a change updated four times is one change, and counting
    updates would make a factory look more productive for revising more.
    """
    opened = {
        str(entry.subject)
        for entry in entries
        if entry.type is EntryType.WORK_ITEM_TRANSITION and entry.payload.get("to") == "HANDOFF"
    }
    return Measure(
        name="changes_opened", value=float(len(opened)), unit="changes", sample=len(opened)
    )


def _cost_per_change(entries: list[LedgerEntry]) -> Measure:
    """Median cost of work items that reached handoff in the window.

    An estimate, and labelled one: it derives from recorded token usage and the ladder's
    declared prices, not from a provider's bill. Median rather than mean because one runaway
    work item would otherwise define the number.
    """
    per_item: dict[str, float] = {}
    handed_off: set[str] = set()
    priced_any = False
    for entry in entries:
        if entry.type is EntryType.MODEL_CALLED:
            item = str(entry.payload.get("workItem", entry.subject))
            per_item[item] = per_item.get(item, 0.0) + float(
                entry.payload.get("costUnits", 0.0) or 0
            )
            # `priced` says whether the tier that served this call declared a price. A zero
            # meaning "nobody configured a price" used to render identically to a zero
            # meaning "this was free" -- and the `excludes` tuple listed four things the
            # estimate left out, never the one that produced the zero.
            priced_any = priced_any or bool(entry.payload.get("priced", True))
        elif entry.type is EntryType.WORK_ITEM_TRANSITION and entry.payload.get("to") == "HANDOFF":
            handed_off.add(str(entry.subject))

    costs = [per_item[item] for item in sorted(handed_off) if item in per_item]
    if not costs:
        return insufficient(
            "cost_per_change",
            "no work item both incurred cost and reached handoff in this window",
        )
    if not priced_any:
        return insufficient(
            "cost_per_change",
            "no tier declares a price, so every recorded cost is zero by configuration "
            "rather than by observation",
        )
    return Measure(
        name="cost_per_change",
        value=round(statistics.median(costs), 4),
        unit="cost units",
        estimate=True,
        excludes=(
            "provider billing adjustments",
            "local compute and electricity",
            "human review time",
            "runs that had not reached handoff when the window closed",
        ),
        sample=len(costs),
    )
