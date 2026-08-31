"""The append-only, hash-chained ledger (PRD FR-15.1, FR-15.2).

Storage is one JSONL file. That is a deliberate choice: it is inspectable with
``tail``, diffable, greppable, trivially backed up, and it needs no service -- which
is what makes local mode the reference implementation rather than a cut-down one
(PR-2).

Concurrency is handled with an advisory lock around append. Two processes appending
to one ledger is expected (a worker and a CLI), and interleaved writes would break
the chain, so appends serialise.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from software_factory.errors import LedgerError
from software_factory.ledger.entry import GENESIS, EntryType, LedgerEntry, utc_now

try:  # pragma: no cover - platform dependent
    import fcntl

    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    _HAVE_FCNTL = False


class Ledger:
    """An append-only hash-chained log backed by a JSONL file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    # ---------------------------------------------------------------- writing

    def append(
        self,
        type: EntryType,
        actor: str,
        subject: str,
        payload: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        """Append one entry and return it, sealed.

        The tail is re-read under the lock rather than cached, so a second process
        appending between our calls cannot fork the chain.
        """
        with self._locked():
            if self.torn_tail():
                # A previous write did not complete. Dropping the fragment is the only way
                # the log can ever be written again, and it is safe: an incomplete line
                # was never a sealed entry.
                self._truncate_torn_unlocked()
            seq, prev_hash = self._tail_unlocked()
            entry = LedgerEntry(
                seq=seq + 1,
                ts=utc_now(),
                type=type,
                actor=actor,
                subject=subject,
                payload=payload or {},
                prev_hash=prev_hash,
            ).sealed()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(entry.to_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return entry

    # ---------------------------------------------------------------- reading

    def __iter__(self) -> Iterator[LedgerEntry]:
        return self.read()

    def read(self) -> Iterator[LedgerEntry]:
        """Yield every entry in order.

        A malformed line in the *middle* is tampering and raises. A malformed *final*
        line with no trailing newline is a torn tail -- a crash or a full disk between
        write and flush -- and is skipped, because the alternative is that one such event
        makes the ledger permanently unreadable and therefore permanently unwritable.
        Use :meth:`repair` to drop it, and :meth:`torn_tail` to detect it.
        """
        if not self.path.exists():
            return
        # Snapshot under the shared lock, then yield outside it. Two reasons for the split:
        # a generator that holds a lock across an arbitrary consumer's work deadlocks the
        # first caller who appends mid-iteration, and the `_unlocked` internals below run
        # with the exclusive lock already held -- flock is per open file description, so a
        # second acquisition from the same process blocks against the first.
        with self._locked(shared=True):
            raw = self.path.read_text(encoding="utf-8")
        yield from self._entries(raw)

    def _read_unlocked(self) -> Iterator[LedgerEntry]:
        """`read` without acquiring the lock, for callers that already hold it."""
        if not self.path.exists():
            return
        yield from self._entries(self.path.read_text(encoding="utf-8"))

    def _entries(self, raw: str) -> Iterator[LedgerEntry]:
        lines = raw.splitlines()
        complete = raw.endswith("\n")

        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield LedgerEntry.from_json(stripped)
            except (ValueError, KeyError) as exc:
                if number == len(lines) and not complete:
                    return  # torn tail: incomplete final write, not tampering
                raise LedgerError(
                    f"{self.path}:{number}: malformed ledger entry: {exc}",
                    remediation=(
                        "The ledger is append-only and must not be edited. Restore it from "
                        "backup, or `sf ledger verify` to find the first divergence."
                    ),
                ) from exc

    def query(
        self,
        *,
        type: EntryType | None = None,
        actor: str | None = None,
        subject: str | None = None,
        since_seq: int = 0,
        limit: int | None = None,
    ) -> list[LedgerEntry]:
        """Filtered read. Cheap enough for a local ledger; an index backs the hosted one."""
        found: list[LedgerEntry] = []
        for entry in self.read():
            if entry.seq <= since_seq:
                continue
            if type is not None and entry.type is not type:
                continue
            if actor is not None and entry.actor != actor:
                continue
            if subject is not None and entry.subject != subject:
                continue
            found.append(entry)
            if limit is not None and len(found) >= limit:
                break
        return found

    def tail(self) -> tuple[int, str]:
        """Return ``(last_seq, last_hash)``, or ``(0, GENESIS)`` for an empty ledger."""
        with self._locked():
            return self._tail_unlocked()

    # ------------------------------------------------------------ verification

    def verify(self) -> None:
        """Verify sequence, chaining, and per-entry hashes.

        Raises :class:`LedgerError` naming the first divergence, because the first one
        is the only one that tells you anything -- everything after it is downstream.
        """
        expected_seq = 1
        prev_hash = GENESIS
        for entry in self.read():
            if entry.seq != expected_seq:
                raise LedgerError(
                    f"{self.path}: sequence break at entry {entry.seq}: expected {expected_seq}",
                    remediation="An entry was inserted or removed. Restore from backup.",
                )
            if entry.prev_hash != prev_hash:
                raise LedgerError(
                    f"{self.path}: chain break at entry {entry.seq}: "
                    f"prev_hash {entry.prev_hash[:12]}... does not match {prev_hash[:12]}...",
                    remediation="An earlier entry was edited. Restore from backup.",
                )
            if not entry.verify():
                raise LedgerError(
                    f"{self.path}: content hash mismatch at entry {entry.seq}",
                    remediation="This entry was edited after it was written. Restore from backup.",
                )
            expected_seq += 1
            prev_hash = entry.hash

        if self.torn_tail():
            raise LedgerError(
                f"{self.path}: the final entry is incomplete (a write did not finish)",
                remediation=(
                    "This is a torn tail, not tampering. `sf ledger repair` drops the "
                    "fragment; the entries before it verify."
                ),
            )

    def is_empty(self) -> bool:
        return not self.path.exists() or self.path.stat().st_size == 0

    def torn_tail(self) -> bool:
        """True when the file ends mid-line, i.e. a write did not complete."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return False
        with self.path.open("rb") as handle:
            handle.seek(-1, 2)
            return handle.read(1) != b"\n"

    def repair(self) -> int:
        """Truncate a torn tail back to the last complete line. Returns bytes dropped.

        Only ever removes an incomplete final line. A malformed *complete* line is
        tampering and this will not touch it.
        """
        with self._locked():
            if not self.torn_tail():
                return 0
            raw = self.path.read_bytes()
            cut = raw.rfind(b"\n")
            keep = raw[: cut + 1] if cut != -1 else b""
            dropped = len(raw) - len(keep)
            with self.path.open("wb") as handle:
                handle.write(keep)
                handle.flush()
                os.fsync(handle.fileno())
            return dropped

    # ------------------------------------------------------------------ internal

    def _truncate_torn_unlocked(self) -> None:
        raw = self.path.read_bytes()
        cut = raw.rfind(b"\n")
        with self.path.open("wb") as handle:
            handle.write(raw[: cut + 1] if cut != -1 else b"")
            handle.flush()
            os.fsync(handle.fileno())

    def _tail_unlocked(self) -> tuple[int, str]:
        last: LedgerEntry | None = None
        for last in self._read_unlocked():  # noqa: B007 - we want the final value
            pass
        return (last.seq, last.hash) if last else (0, GENESIS)

    @contextmanager
    def _locked(self, *, shared: bool = False) -> Iterator[None]:
        """Hold the ledger lock. ``shared`` lets concurrent readers in but excludes writers.

        Readers used to take no lock at all. An append is one buffered `write()`, but
        TextIOWrapper flushes in 8192-byte chunks, and PACK_ASSEMBLED and TOOL_CALLED
        payloads routinely exceed that -- so a reader could observe the first chunk without
        the second and raise "malformed ledger entry", reporting tampering that never
        happened. `sf ledger verify` running while a worker appends is the case this
        module's own docstring names as expected.
        """
        if not _HAVE_FCNTL:  # pragma: no cover - Windows
            yield
            return
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            try:
                yield
            finally:
                with suppress(OSError):
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
