"""Conversation state across runs: bounded, resumable, auditable.

FR-3.7 asks a specialist to continue its conversation across revisions; on a five-pass work
item that conversation exceeds any context window. These tests are about what "continue"
means when "send it all again" is not available.
"""

from __future__ import annotations

import pytest

from software_factory.definition.models import Stage
from software_factory.harness.conversation import (
    KIND_BUDGET,
    ConversationState,
    Note,
    NoteKind,
    compact,
    resume,
)

# --------------------------------------------------------------------------- fixtures


def note(kind: NoteKind, text: str, run: str = "run-1") -> Note:
    return Note(kind=kind, text=text, run_id=run, stage=Stage.BUILD)


def state(*notes: Note, **kwargs) -> ConversationState:
    base = ConversationState(work_item_id="wi-1", agent="builder", **kwargs)
    for item in notes:
        base.add(item)
    return base


# ------------------------------------------------------------------------ carried state


def test_a_note_records_where_it_came_from() -> None:
    """A summary line whose origin is unknown cannot be checked against the transcript that
    produced it, and an unverifiable summary must be either trusted completely or discarded
    completely."""
    carried = note(NoteKind.DECISION, "the importer strips the BOM, the exporter does not")

    assert carried.run_id == "run-1"
    assert "run-1" in carried.render()


def test_the_first_run_is_told_it_is_the_first() -> None:
    """An empty summary reads as "nothing was carried", which is the same text a *lost*
    state would produce."""
    assert "first run" in state().render()


def test_the_summary_groups_by_kind() -> None:
    carried = state(
        note(NoteKind.DECISION, "use lstrip rather than removeprefix"),
        note(NoteKind.ATTEMPT, "codecs.BOM_UTF8 comparison; fails on a bare BOM"),
    )

    rendered = carried.render()

    assert "Decision:" in rendered
    assert "Attempt:" in rendered


def test_the_carried_state_has_a_digest() -> None:
    """Lets "what one run carried into the next" be checked rather than described: two runs
    claiming the same carried state and digesting differently were handed different things."""
    first = state(note(NoteKind.DECISION, "a"))
    second = state(note(NoteKind.DECISION, "a"))
    third = state(note(NoteKind.DECISION, "b"))

    assert first.digest() == second.digest()
    assert first.digest() != third.digest()


# -------------------------------------------------------------------------- compaction


def test_nothing_to_compact_returns_nothing() -> None:
    """A ledger full of no-op compaction records would bury the ones that mattered."""
    assert compact(state(note(NoteKind.DECISION, "a")), run_id="run-2") is None


def test_compaction_trims_to_budget_and_records_what_it_dropped() -> None:
    """A conversation that shrinks with no record is one where "the agent forgot" and "the
    harness dropped it" are indistinguishable, and those have different fixes."""
    carried = state(
        *(note(NoteKind.DECISION, f"decision {i}") for i in range(20)),
    )

    record = compact(carried, run_id="run-2")

    assert record is not None
    assert record.notes_after == KIND_BUDGET[NoteKind.DECISION]
    assert record.dropped_count == 20 - KIND_BUDGET[NoteKind.DECISION]
    assert "decision 0" in record.dropped


def test_the_budget_is_per_kind_not_overall() -> None:
    """Twenty decisions and no failed attempts is a summary that sends the next run straight
    back into the wall the last one hit."""
    carried = state(
        *(note(NoteKind.DECISION, f"decision {i}") for i in range(30)),
        *(note(NoteKind.ATTEMPT, f"attempt {i}") for i in range(3)),
    )

    compact(carried, run_id="run-2")

    assert len(carried.of_kind(NoteKind.ATTEMPT)) == 3
    assert len(carried.of_kind(NoteKind.DECISION)) == KIND_BUDGET[NoteKind.DECISION]


def test_attempts_get_the_largest_budget() -> None:
    """The most valuable kind and the most often lost: without it the next run tries the
    same thing and reaches the same wall."""
    assert KIND_BUDGET[NoteKind.ATTEMPT] == max(KIND_BUDGET.values())


def test_the_most_recent_notes_of_a_kind_survive() -> None:
    """An early constraint a later run overturned is exactly the note whose loss costs
    least."""
    carried = state(*(note(NoteKind.DECISION, f"d{i}") for i in range(20)))

    compact(carried, run_id="run-2")

    texts = [n.text for n in carried.of_kind(NoteKind.DECISION)]
    assert texts[-1] == "d19"
    assert "d0" not in texts


def test_compaction_preserves_insertion_order() -> None:
    """`notes` is a log. A compaction that reordered it would make two states with the same
    content digest differently while claiming to carry the same thing."""
    carried = state(
        *(
            note(NoteKind.DECISION if index % 2 else NoteKind.ATTEMPT, f"n{index}")
            for index in range(40)
        )
    )

    compact(carried, run_id="run-2")

    positions = [int(n.text.removeprefix("n")) for n in carried.notes]
    assert positions == sorted(positions)


def test_compaction_is_deterministic() -> None:
    """A summary that varies between compactions makes a replay produce a different run for
    reasons nobody can see. No model call is involved, which is why."""

    def compacted() -> str:
        carried = state(*(note(NoteKind.DECISION, f"d{i}") for i in range(20)))
        compact(carried, run_id="run-2")
        return carried.digest()

    assert compacted() == compacted()


def test_compaction_is_deterministic_across_processes() -> None:
    """Determinism *within* one process is not the property a replay needs.

    Calling the same function twice in one interpreter shares that interpreter's hash seed,
    so it cannot see the class of non-determinism that matters here -- and the digest is
    what a replay compares. A subprocess with a different `PYTHONHASHSEED` can.
    """
    import os
    import subprocess
    import sys

    script = (
        "from software_factory.definition.models import Stage;"
        "from software_factory.harness.conversation import "
        "ConversationState, Note, NoteKind, compact;"
        "s = ConversationState(work_item_id='wi-1', agent='builder');"
        "[s.add(Note(kind=NoteKind.DECISION, text=f'd{i}', run_id=f'r{i}', "
        "stage=Stage.BUILD)) for i in range(20)];"
        "compact(s, run_id='run-2');"
        "print(s.digest())"
    )
    digests = {
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout.strip()
        for seed in ("0", "1", "random")
    }

    assert len(digests) == 1, digests


def test_the_summary_says_that_notes_were_compacted_away() -> None:
    """A reader who cannot see that something was dropped will read the summary as
    complete."""
    carried = state(*(note(NoteKind.DECISION, f"d{i}") for i in range(20)))
    compact(carried, run_id="run-2")

    assert "compacted away" in carried.render()
    assert "retrievable" in carried.render()


# -------------------------------------------------------------------------- resumption


def test_a_resumption_records_what_was_handed_over() -> None:
    """Without this, a run that behaved oddly and a run handed the wrong state look identical
    from outside -- and the second is a harness bug that would be blamed on the model."""
    carried = state(note(NoteKind.DECISION, "a"), transcript_refs=["run-1"])

    record = resume(carried, stage=Stage.REVIEW)

    assert record.carried_notes == 1
    assert record.carried_digest == carried.digest()
    assert record.previous_runs == ("run-1",)


def test_a_resumption_loads_the_current_stage_not_the_previous_transcript() -> None:
    """FR-29.2. Replaying the transcript would re-establish the previous stage's framing,
    which is precisely what a stage change exists to replace."""
    record = resume(state(), stage=Stage.REVIEW)

    assert record.stage is Stage.REVIEW
    assert "does not replay the prior transcript" in str(record.as_dict()["note"])


def test_resuming_changes_nothing() -> None:
    """It is a record of what happened, not an action."""
    carried = state(note(NoteKind.DECISION, "a"))
    before = carried.digest()

    resume(carried, stage=Stage.REVIEW)

    assert carried.digest() == before


@pytest.mark.parametrize("kind", list(NoteKind))
def test_every_note_kind_has_a_budget(kind: NoteKind) -> None:
    """A kind with no budget silently takes the default, which is a policy nobody wrote."""
    assert kind in KIND_BUDGET


@pytest.mark.parametrize("kind", list(NoteKind))
def test_a_partial_budget_falls_back_to_the_declared_one(kind: NoteKind) -> None:
    """The docstring above names the silent-default hazard and never exercised the path
    where it is actually taken.

    `compact` read `budget.get(kind, 10)`, so a caller overriding one kind got 10 for every
    other -- a number matching none of the declared budgets, quietly rewriting the policy
    for the kinds they did not mention.
    """
    declared = KIND_BUDGET[kind]
    carried = state(*(note(kind, f"n{i}") for i in range(declared)))

    # An override for a *different* kind must leave this one at its declared budget.
    other = next(k for k in NoteKind if k is not kind)
    assert compact(carried, run_id="run-2", budget={other: 1}) is None
