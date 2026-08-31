"""Admission control, fair scheduling, and backpressure (PRD FR-26.2, FR-26.3, FR-26.4).

Three failure modes, each with its own mechanism, because one mechanism cannot address all
three and pretending otherwise is how a factory ends up with a queue length as its only
control:

* **One source consumes the factory.** A busy repository with an active tracker produces
  more work than a quiet one, and a plain priority queue hands the whole factory to it.
  :class:`Scheduler` is round-robin across sources with priority *within* a source, plus an
  ageing term so a low-priority item cannot wait forever.
* **A signal storm converts directly into spend.** A failing deploy emits thousands of
  alerts; each one looks like a legitimate work item. :class:`Backpressure` deduplicates by
  fingerprint, rate-limits per source, and trips a circuit breaker that parks the source and
  says so.
* **Provider limits are discovered by hitting them.** :class:`ConcurrencyLimiter` bounds
  in-flight work globally and per agent, so the factory declines work it cannot run rather
  than starting it and being throttled halfway through.

None of this is a queue with a length limit. A length limit drops the newest item, which is
the one an operator is most likely to be watching.
"""

from __future__ import annotations

import enum
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from software_factory.digests import digest_parts
from software_factory.memory.records import utc_now


class Priority(enum.IntEnum):
    """Lower is more urgent, so the natural sort is the scheduling order."""

    URGENT = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


#: How much a queued item's effective priority improves per hour of waiting.
#:
#: Ageing rather than a starvation timeout: a timeout produces a cliff where a LOW item
#: suddenly outranks everything, and an operator watching the queue sees an inexplicable
#: reordering. A gradient is legible -- an item that has waited longer is a bit more urgent.
AGEING_PER_HOUR = 0.25

MAX_AGEING_BANDS = 1.0
"""How far ageing may lift an item: one band, never more.

`Priority` bands are one apart, so this keeps an aged item ahead of its equally-declared
peers and behind anything declared two bands above it. Starvation is a real problem and
this still solves it; inverting an operator's declared order is a different problem and
this no longer causes it.
"""


@dataclass(frozen=True, slots=True)
class Queued:
    """One work item waiting to start."""

    id: str
    source: str
    priority: Priority = Priority.NORMAL
    fingerprint: str = ""
    queued_at: datetime = field(default_factory=utc_now)

    def effective_priority(self, now: datetime | None = None) -> float:
        """Declared priority, improved by how long this has waited -- by at most one band.

        Unbounded, the gradient crossed bands: `LOW` is 3 and `URGENT` is 0, so a LOW item
        that had waited thirteen hours outranked an incident filed that minute. Overnight is
        longer than thirteen hours, so any factory with a routine backlog started each
        morning with every aged chore above a fresh incident, and a hundred aged items
        delayed it by a hundred slots.

        The comment defending the gradient says a timeout would show "an inexplicable
        reordering" to an operator watching the queue. An urgent incident sorted below
        yesterday's chores is precisely that, so the fix keeps the gradient and caps its
        reach: an item is lifted within its band and never past the one above, which
        prevents starvation without inverting the order the operator declared.
        """
        waited_hours = ((now or utc_now()) - self.queued_at).total_seconds() / 3600
        aged = float(self.priority) - waited_hours * AGEING_PER_HOUR
        return max(aged, float(self.priority) - MAX_AGEING_BANDS)


def fingerprint_of(*parts: str) -> str:
    """A stable fingerprint for deduplication.

    Content-addressed rather than id-based: a signal storm sends the *same* alert from a
    provider that assigns a new id each time, so deduplicating by id deduplicates nothing.

    Length-prefixed, because the parts are attacker-supplied text and a separator that can
    appear inside one lets a colliding fingerprint be constructed on purpose -- which would
    make a real alert read as a duplicate of a fabricated one and be dropped.
    """
    return digest_parts(*(part.strip().lower() for part in parts), length=16)


@dataclass(frozen=True, slots=True)
class Admitted:
    item: Queued


@dataclass(frozen=True, slots=True)
class Rejected:
    """Why an item was not admitted. Never a bare False, and never silent."""

    code: str
    message: str
    remediation: str
    retry_after: timedelta | None = None


@dataclass(slots=True)
class SourceLimits:
    """Per-source rate limit and circuit breaker (FR-26.3)."""

    max_per_window: int = 30
    window: timedelta = timedelta(minutes=10)
    #: Consecutive rate-limit trips before the source is parked outright.
    breaker_trips: int = 3
    #: How long a parked source stays parked. It reopens on its own: a breaker that needs a
    #: human to reset it turns a transient storm into an outage nobody notices ended.
    breaker_cooldown: timedelta = timedelta(hours=1)


@dataclass(slots=True)
class SourceState:
    admitted_at: deque[datetime] = field(default_factory=deque)
    consecutive_trips: int = 0
    parked_until: datetime | None = None
    seen_fingerprints: dict[str, datetime] = field(default_factory=dict)


class Backpressure:
    """Deduplication, rate limiting, and circuit breaking, per source (FR-26.3).

    Order matters and encodes the policy: deduplicate first (cheapest, and a duplicate is
    not evidence of load), then rate-limit, then break. Rate-limiting before deduplicating
    would let a storm of *identical* alerts trip the breaker and park a source over work
    that was never real.
    """

    def __init__(
        self,
        limits: SourceLimits | None = None,
        *,
        dedupe_window: timedelta = timedelta(hours=6),
    ) -> None:
        self.limits = limits or SourceLimits()
        self.dedupe_window = dedupe_window
        self._sources: dict[str, SourceState] = {}

    def state_for(self, source: str) -> SourceState:
        return self._sources.setdefault(source, SourceState())

    def admit(self, item: Queued, *, now: datetime | None = None) -> Admitted | Rejected:
        now = now or utc_now()
        state = self.state_for(item.source)

        if state.parked_until is not None:
            if now < state.parked_until:
                return Rejected(
                    "intake.source_parked",
                    f"source {item.source!r} is parked until {state.parked_until.isoformat()}",
                    (
                        "It exceeded its rate limit repeatedly, which usually means a signal "
                        "storm rather than real work. Investigate the source; it reopens on "
                        "its own."
                    ),
                    retry_after=state.parked_until - now,
                )
            # Cooldown elapsed. Reopen and forget the trips: a source that misbehaved an
            # hour ago starts even, or a breaker that has tripped once trips forever.
            state.parked_until = None
            state.consecutive_trips = 0
            state.admitted_at.clear()

        if item.fingerprint:
            self._expire_fingerprints(state, now)
            seen = state.seen_fingerprints.get(item.fingerprint)
            if seen is not None:
                return Rejected(
                    "intake.duplicate",
                    f"an item with fingerprint {item.fingerprint} arrived at {seen.isoformat()}",
                    (
                        "The same signal is already being handled. Comment on the existing "
                        "work item rather than opening a second one."
                    ),
                )

        self._expire_admissions(state, now)
        if len(state.admitted_at) >= self.limits.max_per_window:
            state.consecutive_trips += 1
            if state.consecutive_trips >= self.limits.breaker_trips:
                state.parked_until = now + self.limits.breaker_cooldown
                return Rejected(
                    "intake.breaker_tripped",
                    (
                        f"source {item.source!r} hit its rate limit "
                        f"{state.consecutive_trips} times and is parked for "
                        f"{self.limits.breaker_cooldown}"
                    ),
                    (
                        "A source producing work faster than the factory can do it is "
                        "usually a storm. Fix the source, or raise its limit deliberately."
                    ),
                    retry_after=self.limits.breaker_cooldown,
                )
            return Rejected(
                "intake.rate_limited",
                (
                    f"source {item.source!r} has queued "
                    f"{len(state.admitted_at)} items in {self.limits.window}"
                ),
                "Wait for the window to roll, or raise the source's limit deliberately.",
                retry_after=self.limits.window,
            )

        state.admitted_at.append(now)
        state.consecutive_trips = 0
        if item.fingerprint:
            state.seen_fingerprints[item.fingerprint] = now
        return Admitted(item=item)

    def _expire_admissions(self, state: SourceState, now: datetime) -> None:
        cutoff = now - self.limits.window
        while state.admitted_at and state.admitted_at[0] < cutoff:
            state.admitted_at.popleft()

    def _expire_fingerprints(self, state: SourceState, now: datetime) -> None:
        cutoff = now - self.dedupe_window
        state.seen_fingerprints = {
            key: seen for key, seen in state.seen_fingerprints.items() if seen >= cutoff
        }


class Scheduler:
    """Round-robin across sources, priority and ageing within one (FR-26.2).

    The alternative -- one global priority queue -- gives the whole factory to whichever
    source labels its work most urgently, which is a property of that source's culture
    rather than of the work. Round-robin makes a busy source wait its turn without making
    its urgent work wait behind another source's routine work.
    """

    def __init__(self) -> None:
        self._queues: dict[str, list[Queued]] = {}
        self._order: deque[str] = deque()

    def enqueue(self, item: Queued) -> None:
        queue = self._queues.setdefault(item.source, [])
        if any(existing.id == item.id for existing in queue):
            raise ValueError(f"{item.id!r} is already queued")
        queue.append(item)
        if item.source not in self._order:
            self._order.append(item.source)

    def __len__(self) -> int:
        return sum(len(queue) for queue in self._queues.values())

    def depth_by_source(self) -> dict[str, int]:
        return {source: len(queue) for source, queue in sorted(self._queues.items()) if queue}

    def next(self, *, now: datetime | None = None) -> Queued | None:
        """The next item to start, or ``None`` when nothing is queued."""
        now = now or utc_now()
        for _ in range(len(self._order)):
            source = self._order[0]
            self._order.rotate(-1)
            queue = self._queues.get(source)
            if not queue:
                continue
            queue.sort(key=lambda item: (item.effective_priority(now), item.queued_at, item.id))
            return queue.pop(0)
        return None

    def drain(self, limit: int, *, now: datetime | None = None) -> list[Queued]:
        """Up to ``limit`` items in scheduling order. The batch a scheduler tick starts."""
        taken: list[Queued] = []
        while len(taken) < limit:
            item = self.next(now=now)
            if item is None:
                break
            taken.append(item)
        return taken


class ConcurrencyLimiter:
    """Bounds in-flight work globally and per agent (FR-26.4).

    Proactive, unlike FR-11.10's reactive backoff: the point is to *not* discover a
    provider's limit by being rate-limited in the middle of a run that has already spent
    money. A run refused before it starts costs nothing.
    """

    def __init__(self, *, total: int = 8, per_agent: int = 3) -> None:
        if total < 1 or per_agent < 1:
            raise ValueError("a limit below one admits nothing; omit the limiter instead")
        self.total = total
        self.per_agent = per_agent
        self._running: dict[str, set[str]] = {}

    @property
    def in_flight(self) -> int:
        return sum(len(runs) for runs in self._running.values())

    def running_for(self, agent: str) -> int:
        return len(self._running.get(agent, set()))

    def acquire(self, agent: str, run_id: str) -> Rejected | None:
        """``None`` when the run may start, otherwise why not."""
        if self.in_flight >= self.total:
            return Rejected(
                "schedule.at_capacity",
                f"{self.in_flight} runs are in flight, at the factory limit of {self.total}",
                "Wait for a run to finish, or raise the factory's concurrency limit.",
            )
        if self.running_for(agent) >= self.per_agent:
            return Rejected(
                "schedule.agent_at_capacity",
                f"agent {agent!r} already has {self.per_agent} runs in flight",
                (
                    "One agent saturating the factory starves the others. Wait, or raise "
                    "this agent's limit deliberately."
                ),
            )
        self._running.setdefault(agent, set()).add(run_id)
        return None

    def release(self, agent: str, run_id: str) -> None:
        """Idempotent: a run released twice is a bookkeeping error, not a reason to raise
        in a `finally` block that is already cleaning up after a failure."""
        self._running.get(agent, set()).discard(run_id)
