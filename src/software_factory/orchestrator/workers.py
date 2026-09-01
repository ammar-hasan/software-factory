"""Routing work to the machine that can run it (PRD FR-8.2, FR-25.7).

`ExecutionDefaults.workerHost` already existed and named exactly one machine. That is
enough for a factory with one worker and wrong for every other shape: a fleet has several
machines, they are not interchangeable, and which one a piece of work needs is a property
of the *work* rather than of the agent that happens to pick it up. A build that needs a GPU,
an integration test that needs the staging network, a UI check that needs a browser -- each
is a requirement the work item carries, and a single hostname in the definition cannot
express any of them.

So work declares **labels it requires** and workers declare **labels they have**, and the
pool matches them. Five decisions, each of which is a failure this module exists to avoid:

**A requirement that matches no worker is an error naming the label.** The tempting
alternative -- fall back to local -- is the worst possible behaviour: work that asked for a
GPU runs on a CPU box and produces results that are *wrong* rather than missing, and
nothing in the record says why. Missing capacity must look like missing capacity.

**Capacity is counted on disk, not in memory.** Workers are shared between processes by
definition; a count held in one coordinator means every coordinator believes the pool is
idle and all of them dispatch to the same machine at once.

**Draining lets running work finish.** A drain that killed in-flight leases would be a
crash with a nicer name, and an operator who needs a machine back at some point is not the
same as one who needs it back *now* -- the second is `sf stop`, which already exists.

**Reclaiming a stale lease is recorded.** A worker whose process died holds capacity until
somebody notices, so leases expire. But an expiry is a guess: the run may still be alive and
merely slow. Recording the reclaim is what makes "this ran twice" diagnosable rather than a
mystery, and it is why the expiry is generous by default.

**No worker configured is `unavailable`, all workers busy is `insufficient_data`.** They
need different responses -- one is a configuration to fix, the other is a wait -- and a
router that reports "no" for both sends an operator to edit a file that was already correct.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from software_factory.errors import ErrorCode, FactoryError
from software_factory.memory.records import utc_now

try:  # pragma: no cover - platform dependent
    import fcntl

    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    _HAVE_FCNTL = False

#: Where leases live inside the state directory.
LEASE_FILE = "workers.json"

#: How long a lease survives without a heartbeat before the pool may reclaim it.
#: Deliberately long. A short expiry reclaims a machine from a run that was merely slow and
#: then dispatches the same work twice, which is worse than a machine idle for an hour.
DEFAULT_LEASE_S = 3600


class Availability(StrEnum):
    """Why the pool could or could not place a piece of work.

    Three values rather than a boolean, and never a fourth meaning zero. `UNAVAILABLE` is a
    configuration to fix; `SATURATED` is a wait; and collapsing them into "no" sends an
    operator to edit a definition file that was already correct.
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    SATURATED = "saturated"


class WorkerError(FactoryError):
    """A pool this factory will not route against."""

    code = ErrorCode.DEFINITION_INVALID


@dataclass(frozen=True, slots=True)
class Worker:
    """One machine the factory may dispatch to.

    `labels` are capabilities the machine *has*, not a description of it. "gpu" and
    "staging-network" route work; "amaya's laptop" does not, and a label nothing requires
    is dead weight in a matching decision somebody will later have to explain.
    """

    name: str
    host: str
    labels: frozenset[str] = frozenset()
    capacity: int = 1
    draining: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Worker:
        name = str(raw.get("name", "")).strip()
        host = str(raw.get("host", "")).strip()
        if not name or not host:
            raise WorkerError(
                "a worker needs a name and a host",
                remediation="Give it both. A worker with no host is a name nothing can reach.",
            )
        capacity = int(raw.get("capacity", 1))
        if capacity < 1:
            raise WorkerError(
                f"worker {name!r} has capacity {capacity}",
                remediation=(
                    "Use at least 1, or mark it `draining`. A worker with capacity 0 is "
                    "configured to be useless, which is almost never what was meant."
                ),
            )
        return cls(
            name=name,
            host=host,
            labels=frozenset(
                str(label).strip() for label in raw.get("labels", ()) if str(label).strip()
            ),
            capacity=capacity,
            draining=bool(raw.get("draining", False)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "labels": sorted(self.labels),
            "capacity": self.capacity,
            "draining": self.draining,
        }

    def satisfies(self, required: frozenset[str]) -> bool:
        return required <= self.labels


@dataclass(frozen=True, slots=True)
class Lease:
    """One claim on one worker, held by one run."""

    worker: str
    host: str
    run: str
    at: datetime
    expires_at: datetime

    def expired(self, *, now: datetime | None = None) -> bool:
        return (now or utc_now()) >= self.expires_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "worker": self.worker,
            "host": self.host,
            "run": self.run,
            "at": self.at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class Placement:
    """Where a piece of work will run, or why it will not.

    Carries the reason even when it succeeded, because "why did this land on worker-3" is
    asked as often as "why did this land nowhere", and a placement that only explains its
    failures answers half the questions an operator has.
    """

    availability: Availability
    lease: Lease | None = None
    reason: str = ""
    missing: frozenset[str] = frozenset()

    @property
    def placed(self) -> bool:
        return self.lease is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability.value,
            "lease": self.lease.as_dict() if self.lease else None,
            "reason": self.reason,
            "missing": sorted(self.missing),
        }


@dataclass
class WorkerPool:
    """The workers a factory may dispatch to, and who currently holds each one."""

    workers: tuple[Worker, ...] = ()
    state_dir: Path | None = None
    lease_seconds: int = DEFAULT_LEASE_S

    @classmethod
    def from_dicts(
        cls, raw: list[dict[str, Any]], *, state_dir: Path | None = None, **kwargs: Any
    ) -> WorkerPool:
        workers = tuple(Worker.from_dict(entry) for entry in raw)
        names = [worker.name for worker in workers]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            # Two workers sharing a name share a lease slot, so one of them silently gets
            # double the capacity it declared -- and which one depends on file order.
            raise WorkerError(
                f"two workers are named {', '.join(duplicates)}",
                remediation="Names are how leases are keyed; give each worker its own.",
            )
        return cls(workers=workers, state_dir=state_dir, **kwargs)

    # ------------------------------------------------------------------ routing

    def candidates(self, required: frozenset[str] | set[str] | None = None) -> list[Worker]:
        """Workers that have every required label and are not draining."""
        needed = frozenset(required or ())
        return [w for w in self.workers if not w.draining and w.satisfies(needed)]

    def place(self, run: str, *, requires: frozenset[str] | set[str] | None = None) -> Placement:
        """Claim a worker for `run`, or say precisely why none is available.

        Least-loaded first, then by name. Deterministic on purpose: a router that breaks
        ties randomly cannot be reproduced from the ledger, and "it went to a different
        machine that time" is the explanation nobody can check.
        """
        needed = frozenset(requires or ())
        if not self.workers:
            # Names the requirement even though nothing could satisfy it. "No workers are
            # configured" leaves an operator knowing they must add a machine and not which
            # kind, so the first thing they do is come back and ask.
            return Placement(
                availability=Availability.UNAVAILABLE,
                reason=(
                    "no workers are configured, and this needs label(s): "
                    + ", ".join(sorted(needed))
                    if needed
                    else "no workers are configured"
                ),
                missing=needed,
            )

        matching = self.candidates(needed)
        if not matching:
            # Draining workers are considered when *explaining*, though not when matching.
            # An operator told "no worker has label `gpu`" while the GPU box sits draining
            # goes and buys a second one. The distinction between "you have none" and
            # "yours is unavailable right now" is the whole value of the message.
            drained = [w for w in self.workers if w.draining and w.satisfies(needed)]
            if drained:
                return Placement(
                    availability=Availability.UNAVAILABLE,
                    reason=(
                        "every worker with those labels is draining: "
                        + ", ".join(sorted(w.name for w in drained))
                    ),
                    missing=frozenset(),
                )
            # Name the labels no worker has, rather than the requirement as a whole. An
            # operator told "no worker matches {gpu, linux, staging}" has to check three;
            # told "no worker has label `gpu`" has to add one.
            have: set[str] = set()
            for worker in self.workers:
                have |= worker.labels
            missing = needed - have
            reason = (
                f"no worker has label(s): {', '.join(sorted(missing))}"
                if missing
                else "no worker has that combination of labels"
            )
            return Placement(
                availability=Availability.UNAVAILABLE, reason=reason, missing=missing or needed
            )

        with self._locked():
            leases = self._live_leases()
            load: dict[str, int] = {}
            for lease in leases.values():
                load[lease.worker] = load.get(lease.worker, 0) + 1

            free = [w for w in matching if load.get(w.name, 0) < w.capacity]
            if not free:
                busiest = ", ".join(sorted(w.name for w in matching))
                return Placement(
                    availability=Availability.SATURATED,
                    reason=f"every matching worker is at capacity ({busiest})",
                    missing=frozenset(),
                )

            chosen = min(free, key=lambda w: (load.get(w.name, 0), w.name))
            now = utc_now()
            lease = Lease(
                worker=chosen.name,
                host=chosen.host,
                run=run,
                at=now,
                expires_at=now + timedelta(seconds=self.lease_seconds),
            )
            leases[run] = lease
            self._write(leases)

        return Placement(
            availability=Availability.AVAILABLE,
            lease=lease,
            reason=f"{chosen.name} has {', '.join(sorted(needed)) or 'no requirements'}",
        )

    def release(self, run: str) -> bool:
        """Give a worker back. Returns whether a lease was actually held.

        The boolean matters: releasing twice is common (a `finally` plus an explicit call)
        and harmless, but releasing a run that never held a lease means the caller lost
        track of which run it was, and silently returning `None` hides that.
        """
        with self._locked():
            leases = self._live_leases()
            held = leases.pop(run, None)
            if held is not None:
                self._write(leases)
            return held is not None

    def heartbeat(self, run: str) -> bool:
        """Push a lease's expiry out. Returns whether the lease was still held.

        `False` is the interesting answer: the pool reclaimed this run's worker while it was
        still working, so somebody else may now be on that machine. A caller that ignores it
        will overwrite whatever the new holder is doing.
        """
        with self._locked():
            leases = self._live_leases()
            held = leases.get(run)
            if held is None:
                return False
            leases[run] = Lease(
                worker=held.worker,
                host=held.host,
                run=held.run,
                at=held.at,
                expires_at=utc_now() + timedelta(seconds=self.lease_seconds),
            )
            self._write(leases)
            return True

    def leases(self) -> list[Lease]:
        """Every live lease, oldest first."""
        with self._locked():
            return sorted(self._live_leases().values(), key=lambda lease: lease.at)

    def reclaimed(self) -> list[Lease]:
        """Leases that expired and were taken back, so a double-run is diagnosable.

        Read rather than inferred. Without this, a run that was reclaimed and one that
        finished cleanly leave identical traces, and "did this execute twice" has no answer.
        """
        return [lease for lease in self._read().get("reclaimed", []) if isinstance(lease, Lease)]

    def summarise(self) -> dict[str, Any]:
        """Per-worker load, for a fleet view.

        `free` can be negative in exactly one situation -- capacity was lowered while leases
        were held -- and it is reported rather than clamped, because clamping turns an
        over-committed machine into a full one and hides the edit that caused it.
        """
        leases = self.leases()
        load: dict[str, int] = {}
        for lease in leases:
            load[lease.worker] = load.get(lease.worker, 0) + 1
        return {
            "workers": [
                {
                    **worker.as_dict(),
                    "inUse": load.get(worker.name, 0),
                    "free": worker.capacity - load.get(worker.name, 0),
                }
                for worker in self.workers
            ],
            "leases": [lease.as_dict() for lease in leases],
        }

    # ------------------------------------------------------------------ storage

    @property
    def _path(self) -> Path | None:
        return (self.state_dir / LEASE_FILE) if self.state_dir else None

    @property
    def _lock_path(self) -> Path | None:
        return (self.state_dir / f"{LEASE_FILE}.lock") if self.state_dir else None

    @contextmanager
    def _locked(self) -> Iterator[None]:
        path = self._lock_path
        if not _HAVE_FCNTL or path is None:  # pragma: no cover - Windows / memory-only
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                with suppress(OSError):
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict[str, Any]:
        path = self._path
        if path is None or not path.exists():
            return {"leases": {}, "reclaimed": []}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Forgiving in one direction only, and this is the safe one: an unreadable
            # lease file makes the pool look idle, so work is dispatched. The opposite --
            # treating it as fully leased -- would stop the factory on a corrupt cache file.
            return {"leases": {}, "reclaimed": []}
        leases = {
            run: _lease(run, entry)
            for run, entry in (raw.get("leases") or {}).items()
            if isinstance(entry, dict)
        }
        reclaimed = [
            _lease(str(entry.get("run", "")), entry)
            for entry in (raw.get("reclaimed") or [])
            if isinstance(entry, dict)
        ]
        return {
            "leases": {run: lease for run, lease in leases.items() if lease},
            "reclaimed": [lease for lease in reclaimed if lease],
        }

    def _live_leases(self) -> dict[str, Lease]:
        """Held leases, with expired ones reclaimed and recorded. Call under the lock."""
        state = self._read()
        held: dict[str, Lease] = state["leases"]
        now = utc_now()
        expired = [run for run, lease in held.items() if lease.expired(now=now)]
        if expired:
            state["reclaimed"] = (state["reclaimed"] + [held[run] for run in expired])[-200:]
            for run in expired:
                del held[run]
            self._write(held, reclaimed=state["reclaimed"])
        return held

    def _write(self, leases: dict[str, Lease], *, reclaimed: list[Lease] | None = None) -> None:
        path = self._path
        if path is None:
            return
        if reclaimed is None:
            reclaimed = self._read()["reclaimed"]
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "leases": {run: lease.as_dict() for run, lease in leases.items()},
            "reclaimed": [lease.as_dict() for lease in reclaimed],
        }
        # Written through a temporary file in the same directory, then renamed. A partial
        # write here is a pool that looks idle to every reader, which is the one failure
        # that dispatches two runs to one machine.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(path)


def _lease(run: str, raw: dict[str, Any]) -> Lease | None:
    try:
        return Lease(
            worker=str(raw["worker"]),
            host=str(raw["host"]),
            run=run or str(raw.get("run", "")),
            at=datetime.fromisoformat(str(raw["at"])),
            expires_at=datetime.fromisoformat(str(raw["expiresAt"])),
        )
    except (KeyError, TypeError, ValueError):
        return None
