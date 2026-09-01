"""Scheduled triggers: the half of `TriggerSchedule` that was never built.

`TriggerSchedule` is a declared model with a validated cron expression. `Trigger` requires
it when the provider is `schedule` and forbids it otherwise, so an author who misuses it
gets a helpful error. Nothing then read it. A factory could declare a nightly sweep, pass
`sf validate` and `sf lint` clean, and never run it once -- which is worse than an absent
feature, because an absent feature is discovered in the documentation and this one is
discovered a month later by noticing that something never happened.

Three decisions here are the substance:

**A missed window fires once, not once per occurrence.** A factory that was off for three
days with an hourly sweep does not have seventy-two pieces of work waiting; it has one, and
seventy-one identical duplicates of it. Firing them all is a spend event with no
corresponding value, and it arrives at exactly the moment an operator is least able to
watch -- just after bringing something back up. The skipped count is *recorded* rather than
discarded, because "we were down and it did not run" is a fact an operator needs.

**Due-ness is derived from the ledger.** What fired and when is a ledger entry like
everything else, so the schedule survives a restart, is auditable, and can be rebuilt. A
scheduler holding its state in memory forgets across a restart and re-fires, and one
holding it in a side file has a second source of truth to reconcile.

**The parser refuses what it does not understand.** Every cron implementation that
"helpfully" accepts an unparseable field ends up running something at a time nobody
intended. A field this does not recognise is an error at load, not a silent `*`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from software_factory.errors import ErrorCode, FactoryError

#: How far back a single evaluation will look for a missed occurrence.
#:
#: Beyond this the answer is "this factory was down", not "this trigger is due", and
#: searching further is a scan whose cost grows with how long nobody was watching.
MAX_LOOKBACK = timedelta(days=30)

#: The resolution cron works at. Everything here steps in whole minutes.
STEP = timedelta(minutes=1)

_NAMED_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}
_NAMED_DAYS = {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}

_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
_FIELD_NAMES = ("minute", "hour", "day-of-month", "month", "day-of-week")


class CronError(FactoryError):
    """A cron expression this scheduler will not guess at."""

    code = ErrorCode.DEFINITION_INVALID


@dataclass(frozen=True, slots=True)
class Cron:
    """A parsed five-field cron expression, evaluated in UTC.

    UTC deliberately and not the host's zone. A schedule that means a different instant on
    two machines is a schedule that fires twice or not at all when a factory moves, and
    twice a year it would silently shift by an hour.
    """

    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    dom_restricted: bool
    dow_restricted: bool
    source: str

    @classmethod
    def parse(cls, expression: str) -> Cron:
        fields = expression.split()
        if len(fields) != 5:
            raise CronError(
                f"a cron expression has five fields; {expression!r} has {len(fields)}",
                remediation=(
                    "Write `minute hour day-of-month month day-of-week`, e.g. `0 3 * * *` "
                    "for 03:00 UTC daily."
                ),
            )
        parsed = [
            _parse_field(raw, low, high, name)
            for raw, (low, high), name in zip(fields, _FIELD_RANGES, _FIELD_NAMES, strict=True)
        ]
        return cls(
            minutes=parsed[0],
            hours=parsed[1],
            days=parsed[2],
            months=parsed[3],
            weekdays=parsed[4],
            # Standard cron: when *both* day-of-month and day-of-week are restricted they
            # are ORed, not ANDed -- `0 0 1 * 1` means the first of the month *and* every
            # Monday. Getting this wrong makes a schedule fire far less often than its
            # author expects, which is the direction nobody notices.
            dom_restricted=fields[2] != "*",
            dow_restricted=fields[4] != "*",
            source=expression,
        )

    def matches(self, at: datetime) -> bool:
        # Converted, not read as-is. Reading the fields off an offset-aware timestamp made
        # 03:00+05:00 -- which is 22:00 UTC the day before -- match `0 3 * * *`, so a
        # caller passing a local time fired the schedule at the wrong instant and a caller
        # passing UTC did not. A naive timestamp is treated as UTC, which is what the rest
        # of this module produces.
        at = _floor_minute(at)
        if at.minute not in self.minutes or at.hour not in self.hours:
            return False
        if at.month not in self.months:
            return False
        dom = at.day in self.days
        dow = (at.weekday() + 1) % 7 in self.weekdays
        if self.dom_restricted and self.dow_restricted:
            return dom or dow
        if self.dom_restricted:
            return dom
        if self.dow_restricted:
            return dow
        return True

    def previous(self, *, before: datetime, floor: datetime | None = None) -> datetime | None:
        """The most recent occurrence at or before `before`, or None within the lookback."""
        cursor = _floor_minute(before)
        limit = floor or (cursor - MAX_LOOKBACK)
        while cursor >= limit:
            if self.matches(cursor):
                return cursor
            cursor -= STEP
        return None

    def next(self, *, after: datetime) -> datetime | None:
        """The first occurrence strictly after `after`, within the lookback horizon."""
        cursor = _floor_minute(after) + STEP
        limit = cursor + MAX_LOOKBACK
        while cursor <= limit:
            if self.matches(cursor):
                return cursor
            cursor += STEP
        return None

    def occurrences_between(self, start: datetime, end: datetime) -> int:
        """How many times this would have fired in `(start, end]`.

        Used only to *report* what a missed window skipped. Counting is cheap; running them
        is not, which is the whole reason the count exists separately from the firing.
        """
        count = 0
        cursor = _floor_minute(start) + STEP
        end = _floor_minute(end)
        while cursor <= end:
            if self.matches(cursor):
                count += 1
            cursor += STEP
        return count


def _floor_minute(at: datetime) -> datetime:
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    return at.astimezone(UTC).replace(second=0, microsecond=0)


def _parse_field(raw: str, low: int, high: int, name: str) -> frozenset[int]:
    values: set[int] = set()
    for part in raw.split(","):
        values |= _parse_part(part, low, high, name, raw)
    if not values:
        raise CronError(
            f"the {name} field {raw!r} selects nothing",
            remediation=f"Use a value between {low} and {high}, a range, `*`, or `*/n`.",
        )
    return frozenset(values)


def _parse_part(part: str, low: int, high: int, name: str, whole: str) -> set[int]:
    body, _, step_text = part.partition("/")
    if step_text:
        if not step_text.isdigit() or int(step_text) < 1:
            raise CronError(
                f"the {name} field {whole!r} has step {step_text!r}, which is not a positive "
                "whole number",
                remediation="Write `*/15` for every fifteenth value.",
            )
        step = int(step_text)
    else:
        step = 1

    if body in ("*", ""):
        start, stop = low, high
    elif "-" in body[1:]:
        left, _, right = body.partition("-")
        start, stop = _value(left, low, high, name, whole), _value(right, low, high, name, whole)
        if start > stop:
            raise CronError(
                f"the {name} field {whole!r} has range {body!r}, which runs backwards",
                remediation="Write ranges low-high, and use two parts to wrap: `22-23,0-2`.",
            )
    else:
        start = stop = _value(body, low, high, name, whole)

    return set(range(start, stop + 1, step))


def _value(token: str, low: int, high: int, name: str, whole: str) -> int:
    text = token.strip().lower()
    if name == "month" and text in _NAMED_MONTHS:
        return _NAMED_MONTHS[text]
    if name == "day-of-week" and text in _NAMED_DAYS:
        return _NAMED_DAYS[text]
    if name == "day-of-week" and text == "7":
        # Both 0 and 7 mean Sunday in every cron anyone has used. Accepting only one of
        # them is the kind of difference that makes a schedule silently never fire.
        return 0
    if not re.fullmatch(r"\d+", text):
        raise CronError(
            f"the {name} field {whole!r} contains {token!r}, which is not a number this "
            "scheduler recognises",
            remediation=(
                "Use numbers, `*`, ranges and steps. Named months (jan) and days (mon) are "
                "accepted in their own fields."
            ),
        )
    number = int(text)
    if not low <= number <= high:
        raise CronError(
            f"the {name} field {whole!r} contains {number}, outside {low}-{high}",
            remediation=f"Use a value between {low} and {high}.",
        )
    return number


@dataclass(frozen=True, slots=True)
class ScheduledTrigger:
    """One schedule declared by one automation."""

    automation: str
    event: str
    cron: Cron
    name: str = ""

    @property
    def id(self) -> str:
        """Stable across restarts and across edits to unrelated fields.

        Keyed on the automation and the schedule's declared name rather than on the cron
        text: changing `0 3 * * *` to `0 4 * * *` is a reschedule of the same job, and an id
        derived from the expression would make it a brand new one with no history -- which
        would fire it immediately on the first evaluation after the edit.
        """
        return f"{self.automation}:{self.name or self.event}"


@dataclass(frozen=True, slots=True)
class Due:
    """A trigger that should fire now, and what firing it stands in for."""

    trigger: ScheduledTrigger
    occurrence: datetime
    """The scheduled instant this firing represents -- not the wall clock, so two runs of
    the scheduler a few seconds apart cannot both claim the same occurrence."""

    skipped: int = 0
    """Occurrences between the last firing and this one that will not be run.

    Recorded rather than discarded. A backlog of identical sweeps is one piece of work and
    seventy-one duplicates, but "we were down and it did not run" is still a fact an
    operator needs, and a scheduler that quietly swallows it is a scheduler that hides an
    outage.
    """

    last_fired: datetime | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger.id,
            "automation": self.trigger.automation,
            "event": self.trigger.event,
            "cron": self.trigger.cron.source,
            "occurrence": self.occurrence.isoformat(),
            "skipped": self.skipped,
            "lastFired": None if self.last_fired is None else self.last_fired.isoformat(),
        }


@dataclass(slots=True)
class Schedule:
    """The schedules a factory declares, and what the ledger says has fired.

    Held together because "is this due" is a question about both, and a scheduler that
    reads only the definition re-fires everything on every evaluation.
    """

    triggers: tuple[ScheduledTrigger, ...] = ()
    last_fired: dict[str, datetime] = field(default_factory=dict)

    @classmethod
    def from_definition(cls, definition: Any) -> Schedule:
        """Every `schedule` trigger across every enabled automation.

        Disabled automations are skipped rather than listed as never-due: `enabled: false`
        is how an operator turns something off, and a scheduler that ignored it would make
        that switch a lie.
        """
        triggers: list[ScheduledTrigger] = []
        for name, automation in sorted(definition.automations.items()):
            if not automation.definition.enabled:
                continue
            for trigger in automation.definition.triggers:
                if trigger.provider != "schedule" or trigger.schedule is None:
                    continue
                triggers.append(
                    ScheduledTrigger(
                        automation=name,
                        event=str(trigger.event),
                        cron=Cron.parse(trigger.schedule.cron),
                        name=str(trigger.schedule.name or ""),
                    )
                )
        return cls(triggers=tuple(triggers))

    def with_history(self, entries: Iterable[Any]) -> Schedule:
        """Fold in what the ledger says already fired."""
        from software_factory.ledger.entry import EntryType

        fired: dict[str, datetime] = {}
        for entry in entries:
            if entry.type is not EntryType.SCHEDULE_FIRED:
                continue
            occurrence = entry.payload.get("occurrence")
            if not isinstance(occurrence, str):
                continue
            try:
                at = datetime.fromisoformat(occurrence.replace("Z", "+00:00"))
            except ValueError:
                continue
            key = str(entry.subject)
            if key not in fired or at > fired[key]:
                fired[key] = at
        self.last_fired = fired
        return self

    def due(self, *, now: datetime) -> list[Due]:
        """Which triggers should fire now, at most once each."""
        now = _floor_minute(now)
        pending: list[Due] = []
        for trigger in self.triggers:
            last = self.last_fired.get(trigger.id)
            occurrence = trigger.cron.previous(before=now, floor=last)
            if occurrence is None:
                continue
            if last is not None and occurrence <= last:
                continue
            skipped = (
                trigger.cron.occurrences_between(last, occurrence) - 1 if last is not None else 0
            )
            pending.append(
                Due(
                    trigger=trigger,
                    occurrence=occurrence,
                    skipped=max(0, skipped),
                    last_fired=last,
                )
            )
        return sorted(pending, key=lambda d: (d.occurrence, d.trigger.id))

    def upcoming(self, *, now: datetime) -> list[tuple[ScheduledTrigger, datetime | None]]:
        """Each trigger and when it next fires, for `sf schedule list`."""
        return [(t, t.cron.next(after=now)) for t in self.triggers]


def describe(cron: Cron) -> str:
    """A short human reading of a cron expression.

    Not a full natural-language renderer -- those get subtly wrong and are then trusted. It
    states the common shapes exactly and falls back to the expression itself, which is
    never wrong.
    """
    if cron.source == "* * * * *":
        return "every minute"
    minutes = _sorted(cron.minutes)
    hours = _sorted(cron.hours)
    if (
        len(minutes) == 1
        and len(hours) == 1
        and not cron.dom_restricted
        and not cron.dow_restricted
    ):
        return f"daily at {hours[0]:02d}:{minutes[0]:02d} UTC"
    if len(minutes) == 1 and len(hours) == 24 and not cron.dom_restricted:
        return f"hourly at :{minutes[0]:02d} UTC"
    return f"cron {cron.source} (UTC)"


def _sorted(values: frozenset[int]) -> Sequence[int]:
    return sorted(values)
