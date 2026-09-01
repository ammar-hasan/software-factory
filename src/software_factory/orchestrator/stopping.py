"""Stopping work that is already running (PRD FR-16, FR-25.5).

`StageMachine.cancel` exists and is properly guarded: cancelling a work item is a human
decision, and an agent cannot make it. But it acts *between* stages, on an item nobody is
currently executing. Nothing could stop a run in flight.

That gap is expensive rather than theoretical. A single live run against a hosted model
took ten minutes and a hundred thousand input tokens in one stage, and the only thing that
would have ended it early was the budget ceiling — which is a bound on the total, not a way
for a person to intervene before it is reached. A fleet nobody can stop is spend nobody can
stop, and the moment an operator most wants a stop button is the moment something is going
wrong in a way no ceiling anticipated.

Three decisions:

**The signal is a file, not a flag in memory.** The person stopping a run is at a different
terminal from the process running it, and often on a different machine sharing the state
directory. An in-process flag can only be set by the thing that is already too busy to
notice.

**It is checked between turns, not only between stages.** A stage is the unit a schedule
thinks in; a turn is the unit spend happens in. Checking only at stage boundaries means a
stop issued at minute one takes effect at minute ten, which is indistinguishable from not
working.

**Stopping is recorded with who asked.** `EMERGENCY_STOP` is a person-only capability, and
a stop that leaves no record is an unexplained gap in a run's history — the same gap an
operator will later be trying to explain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from software_factory.memory.records import utc_now

#: Where the signal lives inside the state directory.
STOP_FILE = "stop.json"

#: The value that means "everything", so an operator does not have to enumerate a fleet
#: they are trying to stop *because* they cannot see all of it.
ALL = "*"


@dataclass(frozen=True, slots=True)
class Stop:
    """One request to stop, and who made it."""

    subject: str
    by: str
    reason: str
    at: datetime

    @property
    def everything(self) -> bool:
        return self.subject == ALL

    def covers(self, work_item: str) -> bool:
        return self.everything or self.subject == work_item

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "by": self.by,
            "reason": self.reason,
            "at": self.at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class StopBook:
    """The stop signals a state directory holds.

    Deliberately tiny and deliberately a file. Nothing here caches: `stopped()` re-reads on
    every call because the whole point is to observe a change made by another process while
    this one is busy.
    """

    path: Path

    @classmethod
    def in_state(cls, state_dir: Path) -> StopBook:
        return cls(path=Path(state_dir) / STOP_FILE)

    def request(self, subject: str, *, by: str, reason: str) -> Stop:
        """Record a stop. Idempotent for one subject; a second request updates the reason."""
        stop = Stop(subject=subject, by=by, reason=reason, at=utc_now())
        current = {s.subject: s for s in self.all()}
        current[subject] = stop
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([s.as_dict() for s in current.values()], indent=2), encoding="utf-8"
        )
        return stop

    def clear(self, subject: str | None = None) -> int:
        """Withdraw one stop, or all of them. Returns how many were withdrawn."""
        current = self.all()
        keep = [] if subject is None else [s for s in current if s.subject != subject]
        if not keep:
            self.path.unlink(missing_ok=True)
            return len(current)
        self.path.write_text(json.dumps([s.as_dict() for s in keep], indent=2), encoding="utf-8")
        return len(current) - len(keep)

    def all(self) -> list[Stop]:
        """Every outstanding stop. An unreadable file is treated as no signal.

        Deliberately forgiving in this one direction. A malformed stop file must not crash
        a run: the failure mode of a broken *stop* is work continuing, which is the status
        quo, while raising here would turn a stray keystroke into an outage.
        """
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        stops: list[Stop] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                at = datetime.fromisoformat(str(entry.get("at", "")).replace("Z", "+00:00"))
            except ValueError:
                at = utc_now()
            stops.append(
                Stop(
                    subject=str(entry.get("subject", "")),
                    by=str(entry.get("by", "")),
                    reason=str(entry.get("reason", "")),
                    at=at,
                )
            )
        return stops

    def stopped(self, work_item: str) -> Stop | None:
        """The stop covering this work item, if any. Re-reads every call, on purpose."""
        for stop in self.all():
            if stop.covers(work_item):
                return stop
        return None
