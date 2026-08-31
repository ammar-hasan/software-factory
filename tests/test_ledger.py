"""The ledger: append-only, hash-chained, and verifiable.

Tamper tests write directly to the JSONL file, because the whole point is that an
edit made outside the API is detectable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from software_factory.errors import LedgerError
from software_factory.ledger import GENESIS, EntryType, Ledger, LedgerEntry


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.jsonl")


def test_empty_ledger_tails_to_genesis(ledger: Ledger) -> None:
    assert ledger.is_empty()
    assert ledger.tail() == (0, GENESIS)


def test_append_seals_and_chains(ledger: Ledger) -> None:
    first = ledger.append(EntryType.RUN_STARTED, actor="builder", subject="run-1")
    second = ledger.append(EntryType.RUN_FINISHED, actor="builder", subject="run-1")

    assert first.seq == 1
    assert first.prev_hash == GENESIS
    assert second.seq == 2
    assert second.prev_hash == first.hash
    assert first.verify() and second.verify()


def test_round_trips_through_json(ledger: Ledger) -> None:
    written = ledger.append(
        EntryType.GATE_EVALUATED,
        actor="critic",
        subject="run-7",
        payload={"gate": "tests-pass", "outcome": "fail", "findings": 2},
    )

    (read_back,) = list(ledger.read())

    assert read_back == written
    assert read_back.payload["gate"] == "tests-pass"


def test_verify_accepts_a_well_formed_chain(ledger: Ledger) -> None:
    for index in range(20):
        ledger.append(EntryType.TOOL_CALLED, actor="builder", subject=f"run-{index}")

    ledger.verify()


def test_editing_an_entry_is_detected(ledger: Ledger) -> None:
    ledger.append(EntryType.RUN_STARTED, actor="builder", subject="run-1")
    ledger.append(EntryType.RUN_FINISHED, actor="builder", subject="run-1")

    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["actor"] = "someone-else"
    lines[0] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(LedgerError, match="content hash mismatch"):
        ledger.verify()


def test_removing_an_entry_is_detected(ledger: Ledger) -> None:
    for index in range(3):
        ledger.append(EntryType.TOOL_CALLED, actor="builder", subject=f"run-{index}")

    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    ledger.path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")

    with pytest.raises(LedgerError, match="sequence break"):
        ledger.verify()


def test_reordering_entries_is_detected(ledger: Ledger) -> None:
    for index in range(3):
        ledger.append(EntryType.TOOL_CALLED, actor="builder", subject=f"run-{index}")

    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    ledger.path.write_text("\n".join([lines[0], lines[2], lines[1]]) + "\n", encoding="utf-8")

    with pytest.raises(LedgerError, match="sequence break"):
        ledger.verify()


def test_a_malformed_line_names_its_line_number(ledger: Ledger) -> None:
    ledger.append(EntryType.RUN_STARTED, actor="builder", subject="run-1")
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    with pytest.raises(LedgerError, match=r"ledger.jsonl:2"):
        list(ledger.read())


def test_query_filters_by_type_actor_and_subject(ledger: Ledger) -> None:
    ledger.append(EntryType.RUN_STARTED, actor="builder", subject="run-1")
    ledger.append(EntryType.RUN_STARTED, actor="critic", subject="run-2")
    ledger.append(EntryType.GATE_EVALUATED, actor="critic", subject="run-2")

    assert len(ledger.query(type=EntryType.RUN_STARTED)) == 2
    assert len(ledger.query(actor="critic")) == 2
    assert len(ledger.query(subject="run-2", type=EntryType.GATE_EVALUATED)) == 1


def test_query_respects_since_seq_and_limit(ledger: Ledger) -> None:
    for index in range(10):
        ledger.append(EntryType.TOOL_CALLED, actor="builder", subject=f"run-{index}")

    assert [e.seq for e in ledger.query(since_seq=7)] == [8, 9, 10]
    assert len(ledger.query(limit=3)) == 3


def test_payloads_hash_order_independently(ledger: Ledger) -> None:
    """Key order must not change an entry's identity, or diffs become meaningless."""
    one = LedgerEntry(
        seq=1,
        ts="2026-01-01T00:00:00.000000Z",
        type=EntryType.RUN_STARTED,
        actor="a",
        subject="s",
        payload={"x": 1, "y": 2},
    ).sealed()
    other = LedgerEntry(
        seq=1,
        ts="2026-01-01T00:00:00.000000Z",
        type=EntryType.RUN_STARTED,
        actor="a",
        subject="s",
        payload={"y": 2, "x": 1},
    ).sealed()

    assert one.hash == other.hash


def test_appends_from_two_handles_keep_one_chain(tmp_path: Path) -> None:
    """A worker and a CLI append to the same ledger; interleaving must not fork it."""
    path = tmp_path / "ledger.jsonl"
    first, second = Ledger(path), Ledger(path)

    first.append(EntryType.RUN_STARTED, actor="worker", subject="run-1")
    second.append(EntryType.RUN_STARTED, actor="cli", subject="run-2")
    first.append(EntryType.RUN_FINISHED, actor="worker", subject="run-1")

    first.verify()
    assert [e.seq for e in first.read()] == [1, 2, 3]
