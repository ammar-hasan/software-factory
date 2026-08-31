"""Concurrent writers against the two append-only logs.

Both `ledger/log.py` and `memory/store.py` implement `flock`-based locking, both document
it as load-bearing, and nothing exercised it. `test_appends_from_two_handles_keep_one_chain`
is named for interleaving and appends strictly sequentially from one thread -- it would
pass with `_locked()` replaced by a no-op (T5).

These tests use real processes rather than threads: the lock is a file lock, its whole
purpose is to hold across processes, and a GIL-serialised thread test would prove less than
it appears to. They are slow by the standards of the rest of the suite and marked as such.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path

import pytest

from software_factory.ledger import EntryType, Ledger
from software_factory.memory import Kind, Lane, Memory, MemoryStore, Scope, Source, SourceKind
from software_factory.spec.units import TrustClass

pytestmark = [pytest.mark.integration, pytest.mark.slow]

WRITERS = 4
PER_WRITER = 25

#: Generous against a correct implementation (this suite runs in about a second) and
#: bounded against a broken one, which livelocks rather than failing.
TIMEOUT_S = 90


def _append_entries(args: tuple[str, int]) -> None:
    path, writer = args
    ledger = Ledger(Path(path))
    for index in range(PER_WRITER):
        ledger.append(
            EntryType.TOOL_CALLED,
            actor=f"worker-{writer}",
            subject=f"run-{writer}-{index}",
            # Large enough to cross the 8192-byte flush boundary, which is where a reader
            # without the shared lock could observe half a line (M32).
            payload={"blob": "x" * 12_000},
        )


def _append_memories(args: tuple[str, int]) -> None:
    path, writer = args
    store = MemoryStore(Path(path))
    store.load()
    for index in range(PER_WRITER):
        store.put(
            Memory(
                id=f"m-{writer}-{index}",
                lane=Lane.CANDIDATE,
                kind=Kind.FACT,
                scope=Scope.REPOSITORY,
                scope_ref="acme/svc",
                content="x" * 12_000,
                provenance=(Source(kind=SourceKind.RUN, ref=f"run-{writer}"),),
                trust=TrustClass.INTERNAL,
            ),
            op="seed",
            actor=f"worker-{writer}",
            reason="concurrency fixture",
        )


def _verify_repeatedly(path: str) -> int:
    """Read the ledger many times while writers are appending. Returns the reads that saw
    a well-formed log; a torn read would raise instead."""
    ledger = Ledger(Path(path))
    seen = 0
    for _ in range(40):
        seen = max(seen, len(list(ledger.read())))
    return seen


@pytest.mark.skipif(os.name != "posix", reason="flock is POSIX-only")
def test_concurrent_ledger_appends_produce_one_verifiable_chain(tmp_path: Path) -> None:
    """Four processes, twenty-five entries each, one unbroken hash chain.

    Under the current implementation this passes; it is here so a future change to the
    locking cannot silently break it, which is the state the suite was in.
    """
    path = str(tmp_path / "ledger.jsonl")
    context = mp.get_context("spawn")
    with context.Pool(WRITERS) as pool:
        # Timed out rather than left to block: with the lock removed these processes livelock
        # on a log they are corrupting, and a test that hangs tells a maintainer nothing.
        pool.map_async(_append_entries, [(path, writer) for writer in range(WRITERS)]).get(
            timeout=TIMEOUT_S
        )

    ledger = Ledger(Path(path))
    entries = list(ledger.read())

    assert len(entries) == WRITERS * PER_WRITER
    assert [entry.seq for entry in entries] == list(range(1, WRITERS * PER_WRITER + 1))
    ledger.verify()


@pytest.mark.skipif(os.name != "posix", reason="flock is POSIX-only")
def test_a_reader_never_observes_a_half_written_entry(tmp_path: Path) -> None:
    """A reader running alongside writers must not report tampering that never happened.

    Appends here exceed the 8192-byte TextIOWrapper flush boundary, so without the shared
    read lock a reader can see the first chunk of a line without the second and raise
    "malformed ledger entry" -- which is what `sf ledger verify` would print while a worker
    was appending, the case `log.py`'s own docstring names as expected.
    """
    path = str(tmp_path / "ledger.jsonl")
    context = mp.get_context("spawn")
    with context.Pool(WRITERS + 1) as pool:
        writers = pool.map_async(_append_entries, [(path, writer) for writer in range(WRITERS)])
        reader = pool.apply_async(_verify_repeatedly, (path,))
        writers.get(timeout=TIMEOUT_S)
        # A torn read raises inside the child and re-raises here.
        assert reader.get(timeout=TIMEOUT_S) >= 0

    Ledger(Path(path)).verify()


@pytest.mark.skipif(os.name != "posix", reason="flock is POSIX-only")
def test_concurrent_memory_writes_all_survive_a_reload(tmp_path: Path) -> None:
    path = str(tmp_path / "memory.jsonl")
    context = mp.get_context("spawn")
    with context.Pool(WRITERS) as pool:
        pool.map_async(_append_memories, [(path, writer) for writer in range(WRITERS)]).get(
            timeout=TIMEOUT_S
        )

    store = MemoryStore(Path(path))
    store.load()

    assert len(store.all()) == WRITERS * PER_WRITER


def test_a_torn_tail_is_recovered_rather_than_fatal(tmp_path: Path) -> None:
    """A crash between write and flush must not make the log permanently unreadable, and
    therefore permanently unwritable (C8). Simulated by truncating mid-line."""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    for index in range(3):
        ledger.append(EntryType.RUN_STARTED, actor="worker", subject=f"run-{index}")

    raw = path.read_bytes()
    path.write_bytes(raw + b'{"seq":4,"ts":"2026-08-31T00:00:00+00:00","typ')

    assert ledger.torn_tail()
    assert [entry.seq for entry in ledger.read()] == [1, 2, 3]

    ledger.append(EntryType.RUN_FINISHED, actor="worker", subject="run-2")

    assert [entry.seq for entry in ledger.read()] == [1, 2, 3, 4]
    ledger.verify()


def test_a_torn_memory_tail_is_recovered_rather_than_fatal(tmp_path: Path) -> None:
    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path)
    store.load()
    store.put(
        Memory(
            id="m1",
            lane=Lane.CANDIDATE,
            kind=Kind.FACT,
            scope=Scope.REPOSITORY,
            scope_ref="acme/svc",
            content="The importer strips a byte-order mark.",
            provenance=(Source(kind=SourceKind.RUN, ref="run-1"),),
            trust=TrustClass.INTERNAL,
        ),
        op="seed",
        actor="test",
        reason="fixture",
    )
    path.write_bytes(path.read_bytes() + b'{"op":"seed","memory":{"id":"m2"')

    reloaded = MemoryStore(path)
    reloaded.load()

    assert reloaded.get("m1") is not None
    assert reloaded.get("m2") is None
