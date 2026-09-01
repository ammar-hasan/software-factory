"""Messages between agents, and the fleet state a watcher reads.

The gap this closes is narrower than "agents could not talk". They could: a coordinator
already carried state between stages of one work item. What did not exist was any way for
two agents working on *different* items to address each other, or for one to observe
another's run without polling a directory. A factory whose agents cannot ask each other a
question is a factory whose agents each solve the whole problem alone.

Two properties matter more than the feature, and both are here as tests rather than as
claims in a docstring:

  * A message and the run it is about cannot be observed out of order. They share one
    sequence because they share one log.
  * A stalled fleet is visible. Every run says `running`, spends nothing and reports no
    error, and the only thing distinguishing it from a healthy fleet is an unanswered
    question.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from software_factory.cli import app
from software_factory.errors import FactoryError
from software_factory.ledger import EntryType, Ledger
from software_factory.orchestrator.mailbox import Conversation, Kind, Mailbox, lifecycle

runner = CliRunner()


@pytest.fixture
def mailbox(tmp_path: Path) -> Mailbox:
    return Mailbox(ledger=Ledger(tmp_path / "ledger.jsonl"), state_dir=tmp_path)


# --------------------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------------------


def test_a_message_reaches_the_agent_it_names(mailbox: Mailbox) -> None:
    mailbox.send(sender="builder", recipient="reviewer", kind=Kind.HANDOFF, body="ready")

    found, left = mailbox.inbox("reviewer")

    assert left == 0
    assert [(m.sender, m.body) for m in found] == [("builder", "ready")]


def test_a_message_does_not_reach_anybody_else(mailbox: Mailbox) -> None:
    """An inbox that shows every message is a broadcast channel with extra steps."""
    mailbox.send(sender="builder", recipient="reviewer", kind=Kind.HANDOFF, body="ready")

    assert mailbox.inbox("verifier")[0] == []


def test_an_unaddressed_message_is_refused(mailbox: Mailbox) -> None:
    with pytest.raises(FactoryError):
        mailbox.send(sender="builder", recipient="   ", kind=Kind.STATUS, body="hello")


def test_an_empty_message_is_refused(mailbox: Mailbox) -> None:
    """A notification that something happened without saying what still costs attention."""
    with pytest.raises(FactoryError):
        mailbox.send(sender="builder", recipient="reviewer", kind=Kind.STATUS, body="  \n ")


def test_an_answer_must_name_its_question(mailbox: Mailbox) -> None:
    """Otherwise the asker is still waiting while the answer sits in their inbox."""
    with pytest.raises(FactoryError):
        mailbox.send(sender="a", recipient="b", kind=Kind.ANSWER, body="yes, use postgres")


def test_an_unknown_kind_is_refused_by_name(mailbox: Mailbox) -> None:
    with pytest.raises(FactoryError) as caught:
        mailbox.send(sender="a", recipient="b", kind="shout", body="hi")
    assert "shout" in str(caught.value)


def test_an_over_long_body_is_truncated_and_says_so(mailbox: Mailbox) -> None:
    """A truncation nobody is told about reads as `there was nothing else`."""
    message = mailbox.send(sender="a", recipient="b", kind=Kind.STATUS, body="x" * 100_000)

    assert message.truncated
    assert len(message.body) < 100_000


# --------------------------------------------------------------------------------------
# The ordering guarantee
# --------------------------------------------------------------------------------------


def test_a_result_sent_before_a_run_ends_has_a_lower_sequence(tmp_path: Path) -> None:
    """The reason messages live in the ledger rather than beside it.

    A parent that sees a child's terminal state must, by then, already be able to see the
    result the child sent. With two stores this holds *usually*: the message store flushes,
    the ledger flushes, and the order between them depends on which write returned first.
    With one log it holds by construction, and this test is the statement of that.
    """
    ledger = Ledger(tmp_path / "ledger.jsonl")
    mailbox = Mailbox(ledger=ledger, state_dir=tmp_path)

    ledger.append(
        EntryType.RUN_STARTED,
        actor="child",
        subject="wi-1",
        payload={"run": "run-1", "agent": "child", "workItem": "wi-1", "stage": "build"},
    )
    message = mailbox.send(
        sender="child", recipient="parent", kind=Kind.RESULT, body="built it", run="run-1"
    )
    finished = ledger.append(
        EntryType.RUN_FINISHED,
        actor="child",
        subject="wi-1",
        payload={"run": "run-1", "status": "completed"},
    )

    assert message.seq < finished.seq

    # And a watcher folding the log sees them in that order, not merely stored in it.
    seen = [e.seq for e in ledger.read()]
    assert seen == sorted(seen)


def test_a_watcher_reading_to_a_terminal_state_has_seen_the_result(tmp_path: Path) -> None:
    """The consequence a parent actually depends on.

    Reading only up to the sequence at which the child ended must already include the
    child's result. A parent that has to poll again after seeing `succeeded` is a parent
    that will sometimes read the result and sometimes read nothing.
    """
    ledger = Ledger(tmp_path / "ledger.jsonl")
    mailbox = Mailbox(ledger=ledger, state_dir=tmp_path)

    ledger.append(
        EntryType.RUN_STARTED,
        actor="child",
        subject="wi-1",
        payload={"run": "run-1", "agent": "child", "workItem": "wi-1", "stage": "build"},
    )
    mailbox.send(sender="child", recipient="parent", kind=Kind.RESULT, body="the answer is 4")
    ledger.append(
        EntryType.RUN_FINISHED,
        actor="child",
        subject="wi-1",
        payload={"run": "run-1", "status": "completed"},
    )

    lives = lifecycle(ledger.read())
    terminal = next(life for life in lives if life.state == "succeeded")

    delivered = [m for m in mailbox.inbox("parent")[0] if m.seq < terminal.seq]
    assert [m.body for m in delivered] == ["the answer is 4"]


# --------------------------------------------------------------------------------------
# Unanswered questions: what makes a stall visible
# --------------------------------------------------------------------------------------


def test_a_question_is_owed_until_it_is_answered(mailbox: Mailbox) -> None:
    question = mailbox.send(
        sender="builder", recipient="architect", kind=Kind.QUESTION, body="postgres or sqlite?"
    )

    assert [m.seq for m in mailbox.unanswered("architect")] == [question.seq]

    mailbox.send(
        sender="architect",
        recipient="builder",
        kind=Kind.ANSWER,
        body="sqlite",
        in_reply_to=question.seq,
    )

    assert mailbox.unanswered("architect") == []


def test_an_unrelated_answer_does_not_clear_a_question(mailbox: Mailbox) -> None:
    """Otherwise any reply looks like every reply, and a stall closes itself."""
    first = mailbox.send(sender="b", recipient="a", kind=Kind.QUESTION, body="which db?")
    second = mailbox.send(sender="b", recipient="a", kind=Kind.QUESTION, body="which region?")
    mailbox.send(sender="a", recipient="b", kind=Kind.ANSWER, body="sqlite", in_reply_to=first.seq)

    assert [m.seq for m in mailbox.unanswered("a")] == [second.seq]


def test_a_fleet_that_is_running_and_stalled_is_distinguishable(tmp_path: Path) -> None:
    """The failure mode this whole module exists for.

    Every run reports `running`. Nothing errored. Spend is flat. The only observable
    difference between this and a fleet making progress is a question nobody answered, and
    a fleet view that does not show it reports a busy factory that is doing nothing.
    """
    ledger = Ledger(tmp_path / "ledger.jsonl")
    mailbox = Mailbox(ledger=ledger, state_dir=tmp_path)
    for index in (1, 2, 3):
        ledger.append(
            EntryType.RUN_STARTED,
            actor=f"agent-{index}",
            subject=f"wi-{index}",
            payload={
                "run": f"run-{index}",
                "agent": f"agent-{index}",
                "workItem": f"wi-{index}",
                "stage": "build",
            },
        )
    mailbox.send(sender="agent-1", recipient="agent-2", kind=Kind.QUESTION, body="which schema?")

    lives = lifecycle(ledger.read())
    assert {life.state for life in lives} == {"running"}

    waiting = {life.agent for life in lives if mailbox.unanswered(life.agent)}
    assert waiting == {"agent-2"}


# --------------------------------------------------------------------------------------
# Delivery: cursors, resumption, and never losing a message
# --------------------------------------------------------------------------------------


def test_a_message_is_delivered_once_across_runs(mailbox: Mailbox) -> None:
    mailbox.send(sender="a", recipient="b", kind=Kind.STATUS, body="one")
    first, _ = mailbox.unread("b")
    mailbox.mark_read("b", max(m.seq for m in first))

    assert mailbox.unread("b")[0] == []


def test_a_message_that_arrives_mid_run_is_not_skipped(mailbox: Mailbox) -> None:
    """The cursor moves to what was *shown*, never to the head of the log.

    Advancing to the end would silently consume anything that landed while the run was
    thinking -- which is precisely when an operator sends a correction.
    """
    first = mailbox.send(sender="a", recipient="b", kind=Kind.STATUS, body="one")
    shown, _ = mailbox.unread("b")
    late = mailbox.send(sender="a", recipient="b", kind=Kind.STATUS, body="two")

    mailbox.mark_read("b", max(m.seq for m in shown))

    assert [m.seq for m in mailbox.unread("b")[0]] == [late.seq]
    assert first.seq < late.seq


def test_the_cursor_never_moves_backwards(mailbox: Mailbox) -> None:
    """Two runs of the same agent can finish out of order; the later cursor wins."""
    mailbox.send(sender="a", recipient="b", kind=Kind.STATUS, body="one")
    mailbox.send(sender="a", recipient="b", kind=Kind.STATUS, body="two")
    messages, _ = mailbox.inbox("b")

    mailbox.mark_read("b", messages[-1].seq)
    mailbox.mark_read("b", messages[0].seq)

    assert mailbox.cursor("b") == messages[-1].seq


def test_a_cursor_survives_a_fresh_mailbox(tmp_path: Path) -> None:
    """Agents run in separate processes; an in-memory cursor is no cursor."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    first = Mailbox(ledger=ledger, state_dir=tmp_path)
    message = first.send(sender="a", recipient="b", kind=Kind.STATUS, body="one")
    first.mark_read("b", message.seq)

    second = Mailbox(ledger=Ledger(tmp_path / "ledger.jsonl"), state_dir=tmp_path)

    assert second.unread("b")[0] == []


def test_a_message_to_an_agent_that_already_finished_is_still_delivered(tmp_path: Path) -> None:
    """A recipient's terminal state is not a delivery failure.

    An agent that finished can be started again, and the message it was sent while idle is
    the reason to start it. Dropping it makes "tell the reviewer to look again" silently
    do nothing.
    """
    ledger = Ledger(tmp_path / "ledger.jsonl")
    mailbox = Mailbox(ledger=ledger, state_dir=tmp_path)
    ledger.append(
        EntryType.RUN_STARTED,
        actor="reviewer",
        subject="wi-1",
        payload={"run": "run-1", "agent": "reviewer", "workItem": "wi-1", "stage": "review"},
    )
    ledger.append(
        EntryType.RUN_FINISHED,
        actor="reviewer",
        subject="wi-1",
        payload={"run": "run-1", "status": "completed"},
    )

    mailbox.send(sender="operator", recipient="reviewer", kind=Kind.STATUS, body="look again")

    assert [m.body for m in mailbox.unread("reviewer")[0]] == ["look again"]


def test_a_flooded_inbox_says_how_much_it_left_behind(mailbox: Mailbox) -> None:
    for index in range(60):
        mailbox.send(sender="a", recipient="b", kind=Kind.STATUS, body=f"note {index}")

    shown, left = mailbox.inbox("b", limit=10)

    assert len(shown) == 10
    assert left == 50


# --------------------------------------------------------------------------------------
# Threads
# --------------------------------------------------------------------------------------


def test_a_thread_is_the_question_and_its_replies(mailbox: Mailbox) -> None:
    question = mailbox.send(sender="b", recipient="a", kind=Kind.QUESTION, body="which db?")
    mailbox.send(sender="c", recipient="a", kind=Kind.STATUS, body="unrelated")
    answer = mailbox.send(
        sender="a", recipient="b", kind=Kind.ANSWER, body="sqlite", in_reply_to=question.seq
    )

    assert [m.seq for m in mailbox.thread(question.seq)] == [question.seq, answer.seq]


# --------------------------------------------------------------------------------------
# What an agent is told at the start of a run
# --------------------------------------------------------------------------------------


def test_an_agent_with_nothing_waiting_is_told_nothing(mailbox: Mailbox) -> None:
    """An empty heading is still text the model pays for and reasons about."""
    assert Conversation.for_agent(mailbox, "b").render() == ""
    assert Conversation.for_agent(mailbox, "b").empty


def test_an_agent_is_told_what_it_owes_an_answer_to(mailbox: Mailbox) -> None:
    mailbox.send(sender="builder", recipient="architect", kind=Kind.QUESTION, body="which db?")

    rendered = Conversation.for_agent(mailbox, "architect").render()

    assert "which db?" in rendered
    assert "not answered" in rendered


def test_an_agent_is_told_what_arrived_since_it_last_ran(mailbox: Mailbox) -> None:
    mailbox.send(sender="operator", recipient="builder", kind=Kind.STATUS, body="use the new api")

    assert "use the new api" in Conversation.for_agent(mailbox, "builder").render()


# --------------------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("completed", "succeeded"),
        ("gate_failed", "blocked"),
        ("contract_violation", "blocked"),
        ("budget_exceeded", "failed"),
        ("provider_failed", "failed"),
        ("cancelled", "cancelled"),
    ],
)
def test_a_run_status_maps_to_what_a_watcher_needs(
    tmp_path: Path, status: str, expected: str
) -> None:
    """`blocked` is separated from `failed` because waiting helps one and not the other."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        EntryType.RUN_STARTED,
        actor="a",
        subject="wi-1",
        payload={"run": "run-1", "agent": "a", "workItem": "wi-1", "stage": "build"},
    )
    ledger.append(
        EntryType.RUN_FINISHED,
        actor="a",
        subject="wi-1",
        payload={"run": "run-1", "status": status},
    )

    assert [life.state for life in lifecycle(ledger.read())] == [expected]


def test_an_unknown_status_is_a_failure_not_a_success(tmp_path: Path) -> None:
    """Availability discipline: an unmapped state must never read as healthy."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        EntryType.RUN_STARTED,
        actor="a",
        subject="wi-1",
        payload={"run": "run-1", "agent": "a", "workItem": "wi-1", "stage": "build"},
    )
    ledger.append(
        EntryType.RUN_FINISHED,
        actor="a",
        subject="wi-1",
        payload={"run": "run-1", "status": "invented_later"},
    )

    assert [life.state for life in lifecycle(ledger.read())] == ["failed"]


def test_a_finish_without_a_start_is_not_invented(tmp_path: Path) -> None:
    """A run nobody saw begin is a truncated log, not a run in an unknown state."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        EntryType.RUN_FINISHED,
        actor="a",
        subject="wi-1",
        payload={"run": "run-1", "status": "completed"},
    )

    assert lifecycle(ledger.read()) == []


# --------------------------------------------------------------------------------------
# The CLI
# --------------------------------------------------------------------------------------


def _factory(tmp_path: Path) -> Path:
    state = tmp_path / ".factory"
    state.mkdir()
    Ledger(state / "ledger.jsonl").append(
        EntryType.RUN_STARTED,
        actor="builder",
        subject="wi-1",
        payload={"run": "run-1", "agent": "builder", "workItem": "wi-1", "stage": "build"},
    )
    return state


def test_the_cli_sends_and_reads(tmp_path: Path) -> None:
    state = _factory(tmp_path)

    sent = runner.invoke(
        app,
        [
            "agent",
            "send",
            "builder",
            "use the new api",
            "--from",
            "amaya",
            "--json",
            "--state",
            str(state),
        ],
    )
    assert sent.exit_code == 0, sent.output

    read = runner.invoke(app, ["agent", "inbox", "builder", "--json", "--state", str(state)])
    assert read.exit_code == 0, read.output
    payload = json.loads(read.stdout)
    assert [m["body"] for m in payload["messages"]] == ["use the new api"]


def test_reading_an_inbox_does_not_consume_it(tmp_path: Path) -> None:
    """An operator diagnosing a stall must not become the reason the agent never saw it."""
    state = _factory(tmp_path)
    runner.invoke(app, ["agent", "send", "builder", "look again", "--json", "--state", str(state)])

    runner.invoke(app, ["agent", "inbox", "builder", "--json", "--state", str(state)])

    after = runner.invoke(
        app, ["agent", "inbox", "builder", "--unread", "--json", "--state", str(state)]
    )
    assert [m["body"] for m in json.loads(after.stdout)["messages"]] == ["look again"]


def test_the_cli_refuses_a_factory_that_has_no_ledger(tmp_path: Path) -> None:
    """A mailbox conjured on an empty directory answers `no messages` for every question."""
    result = runner.invoke(
        app, ["agent", "inbox", "builder", "--json", "--state", str(tmp_path / "nowhere")]
    )

    assert result.exit_code != 0
    assert "no ledger" in result.output


def test_the_fleet_view_shows_who_is_waiting(tmp_path: Path) -> None:
    state = _factory(tmp_path)
    runner.invoke(
        app,
        [
            "agent",
            "send",
            "builder",
            "which db?",
            "--kind",
            "question",
            "--json",
            "--state",
            str(state),
        ],
    )

    result = runner.invoke(app, ["agent", "lifecycle", "--json", "--state", str(state)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["runs"][0]["state"] == "running"
    assert payload["unanswered"] == {"builder": 1}
