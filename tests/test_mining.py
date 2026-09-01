"""Reading the ledger back for things worth keeping.

Every run this factory has done is recorded, and until now none of it was read back. A
factory that learns only from what somebody remembered to write down learns the things that
were easy to notice, which are rarely the expensive ones.

Mining is easy to build badly, and the tests concentrate on the four ways it goes wrong:
counting repetitions as corroboration, promoting boilerplate because it is frequent, eating
its own output, and speaking at all from a history too thin to read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from software_factory.improvement.mining import Confidence, Mine
from software_factory.ledger import EntryType, Ledger
from software_factory.memory.records import Kind, Lane


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.jsonl")


def runs(ledger: Ledger, count: int) -> list[str]:
    ids = []
    for index in range(count):
        run = f"run-{index}"
        ledger.append(
            EntryType.RUN_STARTED,
            actor="builder",
            subject=f"wi-{index}",
            payload={"run": run, "agent": "builder", "workItem": f"wi-{index}", "stage": "build"},
        )
        ids.append(run)
    return ids


def gate_finding(
    ledger: Ledger, run: str, *, criterion: str, locator: str, remediation: str = "do X"
) -> None:
    ledger.append(
        EntryType.GATE_EVALUATED,
        actor="builder",
        subject=run,
        payload={
            "run": run,
            "gate": "spec-delta",
            "outcome": "fail",
            "findings": [{"criterion": criterion, "locator": locator, "remediation": remediation}],
        },
    )


def tools(ledger: Ledger, run: str, names: list[str]) -> None:
    for name in names:
        ledger.append(
            EntryType.TOOL_CALLED, actor="builder", subject=run, payload={"run": run, "tool": name}
        )


# --------------------------------------------------------------------------------------
# Not speaking from too little
# --------------------------------------------------------------------------------------


def test_a_thin_history_reports_insufficient_data(ledger: Ledger) -> None:
    """Four runs cannot distinguish a convention from a coincidence, and the cost of a
    wrong canonical memory is paid by every later pack."""
    for run in runs(ledger, 3):
        gate_finding(ledger, run, criterion="tests-first", locator=f"file{run}.py")

    findings = Mine().run(ledger.read())

    assert findings.confidence is Confidence.INSUFFICIENT_DATA
    assert findings.memories == ()


def test_nothing_recurring_is_not_the_same_as_nothing_to_read(ledger: Ledger) -> None:
    """One says the history is too thin to read, the other says it was read and held
    nothing. An operator responds to them differently."""
    for index, run in enumerate(runs(ledger, 6)):
        gate_finding(ledger, run, criterion=f"unique-{index}", locator=f"f{index}.py")

    findings = Mine().run(ledger.read())

    assert findings.confidence is Confidence.AVAILABLE
    assert findings.memories == ()
    assert "nothing recurred" in findings.reason


# --------------------------------------------------------------------------------------
# Corroboration counts sources, not repetitions
# --------------------------------------------------------------------------------------


def test_the_same_source_repeated_is_one_observation(ledger: Ledger) -> None:
    """The store already refuses corroboration from a shared source. A miner that counted
    runs instead would launder straight around it."""
    for run in runs(ledger, 6):
        gate_finding(ledger, run, criterion="tests-first", locator="importer.py")

    findings = Mine().run(ledger.read())

    assert findings.memories == ()
    assert any("1 distinct source" in note for note in findings.discarded)


def test_two_distinct_sources_are_enough(ledger: Ledger) -> None:
    ids = runs(ledger, 6)
    gate_finding(ledger, ids[0], criterion="tests-first", locator="importer.py")
    gate_finding(ledger, ids[1], criterion="tests-first", locator="exporter.py")

    findings = Mine().run(ledger.read())

    assert [c.content for c in findings.memories] == ["tests-first: do X"]


def test_a_mined_memory_is_a_candidate_never_canon(ledger: Ledger) -> None:
    """Admission control, corroboration rules and quarantine live somewhere else already.

    A miner that wrote to canon would be a second door into memory with none of them behind
    it — which is the whole reason this module returns proposals and writes nothing.
    """
    ids = runs(ledger, 6)
    gate_finding(ledger, ids[0], criterion="tests-first", locator="a.py")
    gate_finding(ledger, ids[1], criterion="tests-first", locator="b.py")

    candidate = Mine().run(ledger.read()).memories[0]

    assert not hasattr(candidate, "lane") or candidate.lane is Lane.CANDIDATE  # type: ignore[attr-defined]
    assert candidate.kind is Kind.CONVENTION
    assert candidate.confidence == 0.5


def test_support_does_not_inflate_confidence(ledger: Ledger) -> None:
    """Support is already in the provenance, where admission control reads it. A miner that
    also raised confidence would be voting twice with one observation."""
    ids = runs(ledger, 10)
    for index in range(5):
        gate_finding(ledger, ids[index], criterion="tests-first", locator=f"f{index}.py")

    candidate = Mine().run(ledger.read()).memories[0]

    assert candidate.confidence == 0.5
    assert len(candidate.provenance) == 5


# --------------------------------------------------------------------------------------
# Frequency is not importance
# --------------------------------------------------------------------------------------


def test_something_in_almost_every_run_is_discarded_not_promoted(ledger: Ledger) -> None:
    """The counter-intuitive one, and the reason it is here.

    The most common thing in any transcript is boilerplate. An observation appearing in
    nine runs out of ten describes the harness, not the repository — so proposing it as a
    convention teaches the factory something it already does. A naive frequency count does
    the exact opposite and ranks it first.
    """
    ids = runs(ledger, 10)
    for index, run in enumerate(ids):
        gate_finding(ledger, run, criterion="calibration-present", locator=f"f{index}.py")

    findings = Mine().run(ledger.read())

    assert findings.memories == ()
    assert any("describes the harness" in note for note in findings.discarded)


def test_something_in_half_the_runs_is_kept(ledger: Ledger) -> None:
    """The threshold has to admit a real repository convention, or it admits nothing."""
    ids = runs(ledger, 10)
    for index in range(5):
        gate_finding(ledger, ids[index], criterion="tests-first", locator=f"f{index}.py")

    assert len(Mine().run(ledger.read()).memories) == 1


def test_what_was_discarded_is_reported(ledger: Ledger) -> None:
    """An operator who sees only what was proposed cannot tell a quiet miner from a
    saturated one."""
    ids = runs(ledger, 10)
    for index, run in enumerate(ids):
        gate_finding(ledger, run, criterion="everywhere", locator=f"f{index}.py")

    assert Mine().run(ledger.read()).discarded != ()


# --------------------------------------------------------------------------------------
# Not eating its own output
# --------------------------------------------------------------------------------------


def test_a_run_that_read_a_mined_memory_is_not_evidence_for_it(ledger: Ledger) -> None:
    """The loop that manufactures consensus.

    A memory mined from runs, injected into later packs, and mined again out of those runs
    is the first observation being read back. Counting it as a second is how a miner talks
    itself into certainty, and excluding those runs is the only thing keeping the loop open.
    """
    # Ten runs, of which five carry the finding: below the saturation threshold, so if this
    # observation is discarded it is because of the taint rule and nothing else. The first
    # version used six runs and tainted five of them, which put the observation at 100% of
    # runs -- it was discarded as boilerplate, and passed with the taint check disabled.
    ids = runs(ledger, 10)
    gate_finding(ledger, ids[0], criterion="tests-first", locator="a.py")
    for run in ids[1:5]:
        ledger.append(
            EntryType.PACK_ASSEMBLED,
            actor="builder",
            subject=run,
            payload={"run": run, "memories": ["mined:run:run-0"]},
        )
        gate_finding(ledger, run, criterion="tests-first", locator=f"{run}.py")

    findings = Mine().run(ledger.read())

    assert findings.memories == (), "a mined memory corroborated itself"
    assert any("distinct source" in note for note in findings.discarded), (
        "discarded for the wrong reason -- the taint rule should leave one source, not zero"
    )


def test_an_ordinary_pack_citation_does_not_taint_a_run(ledger: Ledger) -> None:
    """Only mined memories feed back. Excluding every run that read *any* memory would
    silence mining the moment the factory had one."""
    ids = runs(ledger, 6)
    for index, run in enumerate(ids[:2]):
        ledger.append(
            EntryType.PACK_ASSEMBLED,
            actor="builder",
            subject=run,
            payload={"run": run, "memories": ["mem-authored-by-a-person"]},
        )
        gate_finding(ledger, run, criterion="tests-first", locator=f"f{index}.py")

    assert len(Mine().run(ledger.read()).memories) == 1


def test_mined_provenance_is_marked_so_a_later_pass_recognises_it(ledger: Ledger) -> None:
    ids = runs(ledger, 6)
    gate_finding(ledger, ids[0], criterion="tests-first", locator="a.py")
    gate_finding(ledger, ids[1], criterion="tests-first", locator="b.py")

    candidate = Mine().run(ledger.read()).memories[0]

    assert all(source.ref.startswith("mined:") for source in candidate.provenance)


# --------------------------------------------------------------------------------------
# Questions somebody already answered
# --------------------------------------------------------------------------------------


def _question(ledger: Ledger, *, asker: str, body: str) -> int:
    entry = ledger.append(
        EntryType.AGENT_MESSAGE,
        actor=asker,
        subject="architect",
        payload={"kind": "question", "body": body, "inReplyTo": 0},
    )
    return int(entry.seq)


def _answer(ledger: Ledger, *, by: str, body: str, to: int, run: str = "") -> None:
    ledger.append(
        EntryType.AGENT_MESSAGE,
        actor=by,
        subject="builder",
        payload={"kind": "answer", "body": body, "inReplyTo": to, "run": run},
    )


def test_an_answer_given_twice_by_different_agents_becomes_a_candidate(ledger: Ledger) -> None:
    """The highest-value thing in the log: somebody already worked this out, and the next
    agent will ask again because nothing wrote it down."""
    ids = runs(ledger, 6)
    first = _question(ledger, asker="builder", body="which database?")
    _answer(
        ledger, by="architect", body="sqlite, the deployment is single-node", to=first, run=ids[0]
    )
    second = _question(ledger, asker="reviewer", body="which database?")
    _answer(ledger, by="lead", body="sqlite, the deployment is single-node", to=second, run=ids[1])

    facts = [c for c in Mine().run(ledger.read()).memories if c.kind is Kind.FACT]

    assert [c.content for c in facts] == ["sqlite, the deployment is single-node"]


def test_one_agent_answering_twice_is_one_source(ledger: Ledger) -> None:
    """It is one opinion, stated twice."""
    ids = runs(ledger, 6)
    for index in range(3):
        asked = _question(ledger, asker="builder", body="which database?")
        _answer(ledger, by="architect", body="sqlite", to=asked, run=ids[index])

    assert [c for c in Mine().run(ledger.read()).memories if c.kind is Kind.FACT] == []


def test_an_unanswered_question_is_not_mined(ledger: Ledger) -> None:
    """An unanswered question is a stall, which the fleet view reports. Turning it into a
    memory would record the confusion as though it were the resolution."""
    runs(ledger, 6)
    _question(ledger, asker="builder", body="which database?")
    _question(ledger, asker="reviewer", body="which database?")

    assert Mine().run(ledger.read()).memories == ()


def test_an_answer_with_no_question_is_ignored(ledger: Ledger) -> None:
    """A reply to a sequence nothing wrote is a corrupt or truncated log, not a fact."""
    runs(ledger, 6)
    _answer(ledger, by="architect", body="sqlite", to=999)
    _answer(ledger, by="lead", body="sqlite", to=998)

    assert Mine().run(ledger.read()).memories == ()


# --------------------------------------------------------------------------------------
# Sequences with no name
# --------------------------------------------------------------------------------------


def test_a_recurring_tool_pair_becomes_a_skill_idea(ledger: Ledger) -> None:
    ids = runs(ledger, 6)
    for run in ids[:3]:
        tools(ledger, run, ["repo.search", "repo.read", "file.write"])

    ideas = Mine().run(ledger.read()).skills

    assert any(idea.sequence == ("repo.search", "repo.read") for idea in ideas)


def test_a_skill_idea_is_not_a_lifecycle_proposal(ledger: Ledger) -> None:
    """Every operation the skill registry knows acts on a record that already exists.

    Borrowing that type for a sequence nothing has authored would put a name into the
    lifecycle with nothing behind it, and the first thing to touch it would be looking up a
    record that was never written.
    """
    from software_factory.skills.registry import Proposal

    ids = runs(ledger, 6)
    for run in ids[:3]:
        tools(ledger, run, ["repo.search", "repo.read"])

    ideas = Mine().run(ledger.read()).skills

    assert ideas
    assert not any(isinstance(idea, Proposal) for idea in ideas)
    assert all(idea.name and idea.rationale for idea in ideas)


def test_a_tool_repeated_immediately_is_not_a_sequence(ledger: Ledger) -> None:
    """`repo.read -> repo.read` is reading two files, not a procedure."""
    ids = runs(ledger, 6)
    for run in ids[:3]:
        tools(ledger, run, ["repo.read", "repo.read", "repo.read"])

    assert Mine().run(ledger.read()).skills == ()


def test_a_sequence_in_one_run_is_not_proposed(ledger: Ledger) -> None:
    """One run doing something twice is one run's habit."""
    ids = runs(ledger, 6)
    tools(ledger, ids[0], ["repo.search", "repo.read", "repo.search", "repo.read"])

    assert Mine().run(ledger.read()).skills == ()


# --------------------------------------------------------------------------------------
# Nothing is written
# --------------------------------------------------------------------------------------


def test_mining_writes_nothing_to_the_ledger(ledger: Ledger) -> None:
    """The design decision, asserted rather than described.

    A miner that wrote would be a second door into memory with none of the admission
    control behind it, and its output would then be indistinguishable in the log from
    something a run actually observed.
    """
    ids = runs(ledger, 6)
    gate_finding(ledger, ids[0], criterion="tests-first", locator="a.py")
    gate_finding(ledger, ids[1], criterion="tests-first", locator="b.py")
    before = len(list(ledger.read()))

    Mine().run(ledger.read())

    assert len(list(ledger.read())) == before
