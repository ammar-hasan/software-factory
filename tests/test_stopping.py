"""Stopping work that is already running.

`StageMachine.cancel` exists and is properly guarded, but it acts *between* stages on an
item nobody is executing. Nothing could stop a run in flight.

The gap is expensive rather than theoretical: a live run against a hosted model took ten
minutes and a hundred thousand input tokens in a single stage, and the only thing that
would have ended it early was the budget ceiling — a bound on the total, not a way for a
person to intervene before it is reached.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from software_factory.cli import app
from software_factory.orchestrator.stopping import ALL, StopBook

runner = CliRunner()


def test_a_stop_is_visible_to_a_different_process(tmp_path: Path) -> None:
    """The person stopping a run is at a different terminal from the process running it.

    An in-process flag can only be set by the thing that is already too busy to notice.
    """
    StopBook.in_state(tmp_path).request("wi-1", by="amaya", reason="wrong branch")

    fresh = StopBook.in_state(tmp_path)

    stop = fresh.stopped("wi-1")
    assert stop is not None
    assert stop.by == "amaya"
    assert stop.reason == "wrong branch"


def test_a_stop_on_one_item_does_not_stop_another(tmp_path: Path) -> None:
    StopBook.in_state(tmp_path).request("wi-1", by="amaya", reason="x")

    assert StopBook.in_state(tmp_path).stopped("wi-2") is None


def test_stopping_everything_covers_items_nobody_enumerated(tmp_path: Path) -> None:
    """An operator stopping a fleet usually cannot see all of it — that is why they are
    stopping it."""
    StopBook.in_state(tmp_path).request(ALL, by="amaya", reason="runaway spend")

    assert StopBook.in_state(tmp_path).stopped("anything-at-all") is not None


def test_a_stop_is_withdrawn_explicitly_and_never_expires(tmp_path: Path) -> None:
    """A stop that expired on its own is one an operator has to keep re-issuing to be sure
    of, which is the opposite of what a stop is for."""
    book = StopBook.in_state(tmp_path)
    book.request("wi-1", by="amaya", reason="x")

    assert book.clear("wi-1") == 1
    assert book.stopped("wi-1") is None


def test_a_malformed_stop_file_lets_work_continue(tmp_path: Path) -> None:
    """Forgiving in exactly one direction, on purpose.

    The failure mode of a broken stop file is work continuing, which is the status quo.
    Raising here would turn a stray keystroke in a state directory into an outage.
    """
    (tmp_path / "stop.json").write_text("{not json", encoding="utf-8")

    assert StopBook.in_state(tmp_path).all() == []


# ------------------------------------------------------------------ the CLI


def test_sf_stop_requires_who_and_why(tmp_path: Path) -> None:
    """`EMERGENCY_STOP` is person-only, and a stop with no record is an unexplained gap in
    a run's history — exactly the gap somebody will later be trying to explain."""
    result = runner.invoke(
        app, ["stop", "now", "wi-1", "--by", "  ", "--reason", "x", "--state", str(tmp_path)]
    )

    assert result.exit_code == 2
    assert StopBook.in_state(tmp_path).all() == []


def test_sf_stop_records_the_decision_in_the_ledger(tmp_path: Path) -> None:
    from software_factory.ledger import EntryType, Ledger

    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(EntryType.RUN_STARTED, actor="builder", subject="wi-1", payload={})

    result = runner.invoke(
        app,
        [
            "stop",
            "now",
            "wi-1",
            "--by",
            "amaya",
            "--reason",
            "runaway",
            "--state",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    decisions = [
        e for e in Ledger(tmp_path / "ledger.jsonl").read() if e.type is EntryType.HUMAN_DECISION
    ]
    assert decisions and decisions[0].payload["decision"] == "emergency_stop"
    assert decisions[0].actor == "amaya"


def test_sf_stop_list_and_clear_round_trip(tmp_path: Path) -> None:
    runner.invoke(
        app,
        ["stop", "now", "wi-1", "--by", "amaya", "--reason", "x", "--state", str(tmp_path)],
    )

    listed = runner.invoke(app, ["stop", "list", "--state", str(tmp_path), "--json"])
    assert len(json.loads(listed.stdout)["stops"]) == 1

    runner.invoke(app, ["stop", "clear", "--state", str(tmp_path)])
    again = runner.invoke(app, ["stop", "list", "--state", str(tmp_path), "--json"])
    assert json.loads(again.stdout)["stops"] == []
