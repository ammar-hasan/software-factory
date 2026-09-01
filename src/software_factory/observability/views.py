"""The dashboard's six views, as data (PRD FR-15.6, FR-15.8).

Each view is a function from the ledger (plus whatever local state it needs) to a plain
dictionary. The rendering -- HTML, terminal table, JSON over a socket -- is a separate
concern, and keeping it separate is what makes FR-15.8's "the same application, hosted or
local" true rather than aspirational: hosted deployment differs in transport and
authentication, not in what a view contains.

The load-bearing decision here is the **needs-attention** flag on the activity board. A
board that lists everything sorted by date is a board nobody reads twice. The flag is
computed from the work item's own state -- blocked, parked, stale, reworked repeatedly --
rather than from a model's opinion, so it means the same thing on every viewing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from software_factory.definition.models import Stage
from software_factory.ledger.entry import EntryType, LedgerEntry
from software_factory.memory.records import utc_now
from software_factory.observability.metrics import Report, Window, compute
from software_factory.orchestrator.workitem import Blocker, WorkItem

#: How long a work item may sit in one stage before the board flags it.
#:
#: Not a deadline -- the factory has no authority to impose one -- but a threshold past
#: which "nothing has happened" is more likely to be a stall than progress.
STALE_AFTER = timedelta(hours=24)

#: Returning to an earlier stage this many times suggests the work item is not converging.
REWORK_ATTENTION = 2


@dataclass(frozen=True, slots=True)
class Attention:
    """Why a work item needs a human's eye. Empty means it does not."""

    reasons: tuple[str, ...] = ()

    @property
    def needed(self) -> bool:
        return bool(self.reasons)

    def render(self) -> str:
        return "; ".join(self.reasons)


def needs_attention(item: WorkItem, *, now: datetime | None = None) -> Attention:
    """Compute the flag from the work item's own state.

    Deliberately mechanical. A flag a model sets is a flag that means something different
    each time it is set, and a board whose priority ordering changes between viewings is one
    people stop trusting.
    """
    now = now or utc_now()
    reasons: list[str] = []

    if item.blocker is not None:
        action = item.blocker_action or "no action stated"
        reasons.append(f"blocked ({item.blocker.value}): {action}")

    if item.blocker is Blocker.AWAITING_HUMAN:
        # Already covered above, but named separately because it is the one an operator can
        # always clear themselves, and burying it in a list of blockers hides that.
        reasons.append("waiting on a person, not on the factory")

    last_moved = item.history[-1].at if item.history else item.created_at
    idle = now - last_moved
    if idle >= STALE_AFTER and not item.terminal:
        reasons.append(f"no movement for {int(idle.total_seconds() // 3600)}h")

    rework = item.returned_to_earlier_stage()
    if rework >= REWORK_ATTENTION:
        reasons.append(f"returned to an earlier stage {rework} times; it is not converging")

    return Attention(reasons=tuple(reasons))


def overview(
    entries: Iterable[LedgerEntry],
    *,
    window: Window | None = None,
    integrations: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Metrics and their trend. The first view, and the one that must not flatter."""
    entries = list(entries)
    current = compute(entries, window=window, integrations=integrations)
    previous_window = _preceding(current.window)
    previous = compute(entries, window=previous_window, integrations=integrations)

    return {
        "view": "overview",
        "current": current.as_dict(),
        "previous": previous.as_dict(),
        "trend": _trend(current, previous),
        "series": daily_series(entries, current.window),
    }


def work_items_from(entries: Iterable[Any]) -> list[WorkItem]:
    """Rebuild work items from the ledger, for the activity board.

    FR-15.2 says all derived state is rebuildable from the ledger, and the board was the one
    view that did not do it: `sf dash` served the ledger and rendered an empty activity
    table with a note saying it was "empty by construction". It was empty because nothing
    reconstructed it -- `WORK_ITEM_CREATED` carries the title and class, the transitions
    carry every move with where it came from, and `WORK_ITEM_BLOCKED` carries the blocker
    and the action that clears it. That is the whole board.

    A note about what a ledger genuinely cannot supply stays on the view: the request body
    and the source permalink are not written, so the reconstructed items carry the id,
    title, stage and blocker and say nothing they cannot support.
    """
    from software_factory.ledger.entry import EntryType
    from software_factory.orchestrator.workitem import (
        SourceContext,
        Transition,
        WorkClass,
        WorkItem,
    )

    items: dict[str, WorkItem] = {}
    for entry in entries:
        subject = str(entry.subject)
        at = datetime.fromisoformat(entry.ts.replace("Z", "+00:00"))
        payload = entry.payload

        if entry.type is EntryType.WORK_ITEM_CREATED:
            raw_class = str(payload.get("workClass", WorkClass.CHORE.value))
            items[subject] = WorkItem(
                id=subject,
                factory=str(payload.get("factory", "")),
                title=str(payload.get("title", subject)),
                request="",
                source=SourceContext(
                    provider=str(payload.get("provider", "ledger")),
                    kind="reconstructed",
                    ref=str(payload.get("origin", "")),
                ),
                work_class=(
                    WorkClass(raw_class) if raw_class in set(WorkClass) else WorkClass.CHORE
                ),
                created_at=at,
            )
            continue

        item = items.get(subject)
        if item is None:
            continue

        if entry.type is EntryType.WORK_ITEM_TRANSITION and not payload.get("terminal"):
            source = str(payload.get("from", ""))
            target = str(payload.get("to", ""))
            if source in set(Stage) and target in set(Stage):
                item.history.append(
                    Transition(
                        from_stage=Stage(source),
                        to_stage=Stage(target),
                        actor=str(entry.actor),
                        reason=str(payload.get("reason", "")),
                        at=at,
                    )
                )
                item.stage = Stage(target)
        elif entry.type is EntryType.WORK_ITEM_BLOCKED:
            raw_blocker = str(payload.get("blocker", ""))
            if raw_blocker in set(Blocker):
                item.blocker = Blocker(raw_blocker)
                item.blocker_action = str(payload.get("action", ""))

    return sorted(items.values(), key=lambda i: i.created_at)


def activity_board(
    items: Iterable[WorkItem],
    *,
    stage: Stage | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Work items by stage, filterable, with the needs-attention flag (FR-15.6).

    Flagged items sort first regardless of stage. The board exists to answer "what should I
    look at", and an ordering that buries the answer under everything else does not.
    """
    rows = []
    for item in sorted(items, key=lambda i: i.id):
        if stage is not None and item.stage is not stage:
            continue
        attention = needs_attention(item, now=now)
        rows.append(
            {
                "id": item.id,
                "title": item.title,
                "stage": item.stage.value,
                "workClass": item.work_class.value,
                "origin": item.source.ref,
                "needsAttention": attention.needed,
                "why": attention.render(),
                "rework": item.returned_to_earlier_stage(),
            }
        )
    rows.sort(key=lambda row: (not row["needsAttention"], str(row["stage"]), str(row["id"])))
    return {
        "view": "activity",
        "stage": stage.value if stage else None,
        "workItems": rows,
        "needingAttention": sum(1 for row in rows if row["needsAttention"]),
    }


def run_inspector(entries: Iterable[LedgerEntry], run_id: str) -> dict[str, Any]:
    """Everything recorded about one run: pack, tools, gates, escalations, cost.

    Reconstructed from the ledger rather than from a run object, because the ledger is what
    survives -- and a run inspector that only works while the run is in memory is one that
    cannot be used for the runs anyone actually wants to inspect.
    """
    relevant = [e for e in entries if e.subject == run_id or e.payload.get("run") == run_id]
    if not relevant:
        return {
            "view": "run",
            "error": "run.unknown",
            "message": f"no ledger entries for run {run_id!r}",
            "remediation": "Check the id, or the run may predate this ledger segment.",
        }

    cost = sum(
        float(e.payload.get("costUnits", 0.0) or 0)
        for e in relevant
        if e.type is EntryType.MODEL_CALLED
    )
    return {
        "view": "run",
        "run": run_id,
        "entries": [
            {"seq": e.seq, "at": e.ts, "type": e.type.value, "payload": e.payload} for e in relevant
        ],
        "gates": [
            {"gate": e.payload.get("gate"), "outcome": e.payload.get("outcome")}
            for e in relevant
            if e.type is EntryType.GATE_EVALUATED
        ],
        "toolCalls": sum(1 for e in relevant if e.type is EntryType.TOOL_CALLED),
        "escalations": [e.payload for e in relevant if e.type is EntryType.ESCALATION],
        "violations": [e.payload for e in relevant if e.type is EntryType.VIOLATION],
        "costUnits": round(cost, 4),
        "costNote": (
            "An estimate from recorded usage and declared prices, not from provider billing."
        ),
    }


def definition_view(definition: Any) -> dict[str, Any]:
    """What this factory is configured to be, and who may change it.

    Takes the loaded definition rather than reading files, so the view shows what is *in
    effect* -- which can differ from what is on disk if the definition changed since the
    factory loaded it, and that difference is the thing worth seeing.
    """
    return {
        "view": "definition",
        "factory": definition.factory.name,
        "repositories": [r.slug() for r in definition.factory.repositories],
        "agents": sorted(definition.agents),
        "automations": sorted(definition.automations),
        "runners": sorted(definition.runners),
        "scorers": sorted(definition.scorers),
        "skills": sorted(definition.skills),
        "principals": sorted(definition.principals),
        "unloaded": sorted(definition.unloaded),
        "note": (
            "This is the definition in effect, which can differ from what is on disk if it "
            "changed since the factory loaded it."
        ),
    }


def evaluation_view(
    entries: Iterable[LedgerEntry],
    *,
    proposals: Iterable[Any] = (),
) -> dict[str, Any]:
    """Scorers, benchmarks, and improvement proposals."""
    entries = list(entries)
    scores: dict[str, dict[str, int]] = {}
    for entry in entries:
        if entry.type is not EntryType.SCORE_RECORDED:
            continue
        scorer = str(entry.payload.get("scorer", "unknown"))
        outcome = str(entry.payload.get("outcome", "unknown"))
        scores.setdefault(scorer, {})[outcome] = scores.setdefault(scorer, {}).get(outcome, 0) + 1

    return {
        "view": "evaluation",
        "scorers": {
            name: {
                "outcomes": dict(sorted(counts.items())),
                "sampled": sum(counts.values()),
            }
            for name, counts in sorted(scores.items())
        },
        "proposals": [
            {
                "id": p.id,
                "target": p.target,
                "status": p.status.value,
                "evidence": list(p.evidence),
            }
            for p in proposals
        ],
    }


def registry_view(
    *,
    memory_stats: dict[str, Any] | None = None,
    skills: Iterable[Any] = (),
) -> dict[str, Any]:
    """Memory health and skill health side by side.

    Together because they answer one question -- is what this factory has learned still
    worth carrying -- and separating them makes a reader hold two pages in their head to
    answer it.
    """
    return {
        "view": "registry",
        "memory": memory_stats or {"available": False, "reason": "no memory store configured"},
        "skills": [
            {
                "name": s.name,
                "status": s.status.value,
                "precision": round(s.metrics.precision, 4),
                "recall": round(s.metrics.recall, 4),
                "offered": s.metrics.offered,
                "helped": s.metrics.helped,
            }
            for s in sorted(skills, key=lambda s: s.name)
        ],
    }


#: The most buckets a series carries. Beyond this the buckets widen rather than multiply:
#: a year rendered as 365 points is a chart three pixels wide per point, which is noise
#: drawn precisely.
MAX_BUCKETS = 60


def daily_series(entries: Iterable[LedgerEntry], window: Window) -> dict[str, Any]:
    """Activity over the window, bucketed, for a chart rather than a single number.

    A metric is a number and a trend is two numbers; neither answers "when did that
    happen". A factory whose gate failures all landed on one afternoon and one whose
    failures are spread evenly have the same pass rate and are not the same factory, and
    only a series can tell them apart.

    Bucket width is derived from the window so a 1-day view is hourly and a year is
    weekly. The width is *returned*, because a chart whose x-axis meaning is implicit is a
    chart a reader will misread once and stop trusting.
    """
    entries = list(entries)
    span = window.end - window.start
    total_seconds = max(span.total_seconds(), 1.0)
    buckets = min(MAX_BUCKETS, max(1, int(total_seconds // 3600)))
    width = total_seconds / buckets

    runs = [0] * buckets
    handoffs = [0] * buckets
    failures = [0] * buckets
    cost = [0.0] * buckets

    for entry in entries:
        at = _ts(entry)
        if not window.contains(at):
            continue
        index = min(buckets - 1, int((at - window.start).total_seconds() // width))
        if entry.type is EntryType.RUN_STARTED:
            runs[index] += 1
        elif entry.type is EntryType.MODEL_CALLED:
            cost[index] += float(entry.payload.get("costUnits", 0.0) or 0)
        elif entry.type is EntryType.WORK_ITEM_TRANSITION:
            handoffs[index] += entry.payload.get("to") == "HANDOFF"
        elif entry.type is EntryType.GATE_EVALUATED:
            # Anything that is not a pass is a failure. Counting only a literal "fail"
            # would let "refused" and "error" render as a clean bar.
            failures[index] += str(entry.payload.get("outcome", "")).lower() not in (
                "pass",
                "passed",
                "true",
            )

    return {
        "buckets": buckets,
        "bucketSeconds": int(width),
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "runs": runs,
        "handoffs": handoffs,
        "gateFailures": failures,
        "costUnits": [round(value, 4) for value in cost],
    }


def _ts(entry: LedgerEntry) -> datetime:
    return datetime.fromisoformat(str(entry.ts).replace("Z", "+00:00"))


def _preceding(window: Window) -> Window:
    span = window.end - window.start
    return Window(start=window.start - span, end=window.start)


def _trend(current: Report, previous: Report) -> dict[str, Any]:
    """Change between the two windows, for metrics available in both.

    A metric unavailable in either window has no trend rather than a trend of zero: "no
    change" and "we could not look" are different, and the second must not render as the
    first.
    """
    trend: dict[str, Any] = {"runs": current.runs.total - previous.runs.total}
    for measure in current.measures:
        before = previous.measure(measure.name)
        if measure.value is None or before is None or before.value is None:
            trend[measure.name] = None
            continue
        trend[measure.name] = round(measure.value - before.value, 4)
    return trend


#: How many runs the index carries. A ledger accumulates runs forever, and a page that
#: renders every one of them gets slower every day until nobody opens it.
RUN_INDEX_LIMIT = 250


def run_index(entries: Iterable[LedgerEntry], *, limit: int = RUN_INDEX_LIMIT) -> dict[str, Any]:
    """Every run the ledger records, newest first, with what it cost and how it ended.

    The run inspector could only be reached by somebody who already knew a run id, and
    nothing in the product produced one -- the dashboard asked for it through a browser
    ``prompt()``. An inspector whose index is "remember the id" is an inspector nobody
    opens, so this is the missing half of FR-15.6 rather than a convenience.

    Lifecycle entries are keyed by subject and everything else by ``payload["run"]``,
    matching :func:`run_inspector`, so a run's cost here and its cost there are the same
    number computed the same way.
    """
    runs: dict[str, dict[str, Any]] = {}
    order: dict[str, int] = {}

    def slot(run_id: str, seq: int) -> dict[str, Any] | None:
        if run_id not in runs:
            return None
        order[run_id] = max(order.get(run_id, 0), seq)
        return runs[run_id]

    for entry in entries:
        payload = entry.payload
        if entry.type is EntryType.RUN_STARTED:
            # `payload["run"]` first: the lifecycle entries are *subjected* to the work item
            # (precedent is queried by it), and the run's own id travels in the payload
            # alongside every model call that references it. Keying on the subject folded
            # every stage of one work item into a single row -- three rows for twelve runs,
            # each showing zero model calls, because the calls were filed under an id the
            # index was not looking at.
            run_id = str(payload.get("run") or entry.subject)
            runs[run_id] = {
                "id": run_id,
                "agent": str(payload.get("agent", "")),
                "stage": str(payload.get("stage", "")),
                "purpose": str(payload.get("purpose", "")),
                "tier": str(payload.get("tier", "")),
                "workItem": str(payload.get("workItem", "")),
                "startedAt": entry.ts,
                "finishedAt": None,
                "status": "running",
                "reason": "",
                "costUnits": 0.0,
                "modelCalls": 0,
                "toolCalls": 0,
                "gatesFailed": 0,
                "escalations": 0,
                "violations": 0,
            }
            order[run_id] = entry.seq
            continue

        if entry.type is EntryType.RUN_FINISHED:
            row = slot(str(payload.get("run") or entry.subject), entry.seq)
            if row is None:
                continue
            row["status"] = str(payload.get("status", "unknown"))
            row["reason"] = str(payload.get("reason", ""))
            row["finishedAt"] = entry.ts
            continue

        row = slot(str(payload.get("run", "")), entry.seq)
        if row is None:
            continue
        if entry.type is EntryType.MODEL_CALLED:
            row["modelCalls"] += 1
            row["costUnits"] += float(payload.get("costUnits", 0.0) or 0)
        elif entry.type is EntryType.TOOL_CALLED:
            row["toolCalls"] += 1
        elif entry.type is EntryType.GATE_EVALUATED:
            if str(payload.get("outcome", "")).lower() not in ("pass", "passed", "true"):
                row["gatesFailed"] += 1
        elif entry.type is EntryType.ESCALATION:
            row["escalations"] += 1
        elif entry.type is EntryType.VIOLATION:
            row["violations"] += 1

    rows = sorted(runs.values(), key=lambda r: order[str(r["id"])], reverse=True)
    for row in rows:
        row["costUnits"] = round(float(row["costUnits"]), 4)

    shown = rows[:limit]
    return {
        "view": "runs",
        "runs": shown,
        "total": len(rows),
        "shown": len(shown),
        "truncated": len(rows) > len(shown),
        "costNote": (
            "An estimate from recorded usage and declared prices, not from provider billing."
        ),
    }
