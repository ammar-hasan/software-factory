"""Conversation state across runs: bounded, resumable, and auditable (PRD FR-29).

FR-3.7 requires continuing a specialist's existing conversation across revisions. On a work
item that takes five passes, that conversation exceeds any context window -- so "continue the
conversation" cannot mean "send it all again", and the interesting question is what it means
instead.

Three answers, one per requirement:

* **Bounded state** (FR-29.1). A durable structured summary plus retrievable full history.
  The summary is what travels; the history stays addressable. Compaction is deterministic --
  same transcript, same summary -- because a summary that varies between compactions makes
  a replay produce a different run for reasons nobody can see.
* **Resumption, not replay** (FR-29.2). Resuming restores the structured state and the pack
  for the *current* stage. Replaying the prior transcript would re-establish the previous
  stage's framing, which is precisely what a stage change is meant to change.
* **Auditable carry-over** (FR-29.3). What one run carried into the next is inspectable, so
  "context was lost" is a diagnosable claim rather than a guess. This is the requirement
  that makes the other two debuggable: without it, a run that behaved oddly and a run that
  was handed the wrong state look identical from outside.

What is deliberately *not* here is a model call. Compaction that asks a model to summarise
is compaction that produces a different summary each time it runs, which forfeits both
determinism and auditability. The summary is assembled from facts the run already recorded:
decisions, open questions, constraints discovered, and what was tried.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime

from software_factory.definition.models import Stage
from software_factory.digests import digest_parts
from software_factory.memory.records import utc_now


class NoteKind(enum.StrEnum):
    """The kinds of thing worth carrying between runs.

    A short list on purpose. Everything a run produces is in the transcript; this is the
    subset a *later* run needs to not repeat work or re-litigate a settled question. A
    longer list would make the summary a second transcript.
    """

    DECISION = "decision"
    """Something settled. Carried so the next run does not reopen it."""

    CONSTRAINT = "constraint"
    """Something discovered about the world that bounds the solution."""

    ATTEMPT = "attempt"
    """Something tried that did not work. The most valuable kind and the most often lost:
    without it the next run tries it again and reaches the same wall."""

    OPEN_QUESTION = "open_question"
    """Something unresolved. Carried because an unanswered question that vanishes between
    runs becomes an assumption nobody made deliberately."""

    ARTIFACT = "artifact"
    """Something produced -- a branch, a file, a checkpoint -- that the next run acts on."""


@dataclass(frozen=True, slots=True)
class Note:
    """One carried fact, with where it came from.

    ``run_id`` is required: a summary line whose origin is unknown cannot be checked against
    the transcript that produced it, and an unverifiable summary is one a later reader has
    to either trust completely or discard completely.
    """

    kind: NoteKind
    text: str
    run_id: str
    stage: Stage
    at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if len(self.text) > NOTE_CHARACTER_BUDGET:
            # Truncated at construction with the elision stated, the way command output is
            # capped. A budget in notes bounds nothing when the text comes straight from
            # model output: `KIND_BUDGET` permits 55 notes of arbitrary length, and a
            # context window is measured in tokens.
            elided = len(self.text) - NOTE_CHARACTER_BUDGET
            object.__setattr__(
                self,
                "text",
                f"{self.text[:NOTE_CHARACTER_BUDGET]}… [{elided} characters elided]",
            )

    def render(self) -> str:
        return f"[{self.kind.value}] {self.text}  ({self.run_id}, {self.stage.value})"


@dataclass(frozen=True, slots=True)
class Compaction:
    """A record that a compaction happened, and what it dropped.

    Recorded rather than silent (FR-29.1). A conversation that shrinks with no record is one
    where "the agent forgot" and "the harness dropped it" are indistinguishable -- and those
    have completely different fixes.
    """

    at: datetime
    run_id: str
    notes_before: int
    notes_after: int
    dropped: tuple[str, ...]
    digest: str
    characters_removed: int = 0
    """How much text the compaction actually removed.

    The note count alone does not say whether a compaction helped: dropping twelve
    one-line notes and dropping one enormous one are the same number and very different
    outcomes, and the whole reason compaction exists is the size of what travels.
    """

    @property
    def dropped_count(self) -> int:
        return self.notes_before - self.notes_after

    def render(self) -> str:
        return (
            f"compacted {self.notes_before} -> {self.notes_after} notes at {self.at.isoformat()} "
            f"({self.dropped_count} dropped)"
        )


#: How many notes of each kind survive a compaction.
#:
#: Per kind rather than overall, because the kinds are not interchangeable. Twenty decisions
#: and no failed attempts is a summary that will send the next run straight back into the
#: wall the last one hit. Attempts get the largest budget for exactly that reason.
NOTE_CHARACTER_BUDGET = 800
"""How much of one note is carried forward.

Long enough for a real constraint or a real failed attempt stated in prose, short enough
that fifty-five of them do not exceed a context window. The count budget below bounds the
wrong unit on its own: a single note can be a megabyte, and `render()` is what travels into
the next run's prompt.
"""

SUMMARY_CHARACTER_BUDGET = 24_000
"""The total carried summary. Compaction triggers on this as well as on the counts.

Per-note truncation alone still permits 55 x 800 characters, which is a large fraction of a
small model's window spent on history rather than on the task.
"""

KIND_BUDGET: dict[NoteKind, int] = {
    NoteKind.DECISION: 12,
    NoteKind.CONSTRAINT: 10,
    NoteKind.ATTEMPT: 15,
    NoteKind.OPEN_QUESTION: 10,
    NoteKind.ARTIFACT: 8,
}


@dataclass(slots=True)
class ConversationState:
    """What a specialist carries from one run to the next on one work item."""

    work_item_id: str
    agent: str
    notes: list[Note] = field(default_factory=list)
    compactions: list[Compaction] = field(default_factory=list)
    transcript_refs: list[str] = field(default_factory=list)
    """Ledger references to the full transcripts. The history stays retrievable; it just
    does not travel."""

    def add(self, note: Note) -> Note:
        self.notes.append(note)
        return note

    def of_kind(self, kind: NoteKind) -> list[Note]:
        return [note for note in self.notes if note.kind is kind]

    def digest(self) -> str:
        """Content digest over the carried notes.

        Lets FR-29.3's "what one run carried into the next" be *checked* rather than
        described: two runs claiming the same carried state and digesting differently were
        handed different things.

        Each field is its own length-prefixed part, and `stage` is one of them. Joining with
        `:` was not injective *within* a note -- the collision class `digests.py` exists to
        avoid -- and omitting the stage meant two genuinely different carried states
        digested identically, so the converse of the property above did not hold.
        """
        parts: list[str] = []
        for note in self.notes:
            parts += [note.kind.value, note.text, note.run_id, note.stage.value]
        return digest_parts(*parts)

    def render(self) -> str:
        """The summary a resuming run is given. Grouped by kind, oldest first within a kind.

        Oldest first because a constraint discovered early usually bounds everything after
        it, and a reader who meets the consequences before the cause has to work backwards.
        """
        if not self.notes:
            return "No prior state: this is the first run on this work item."
        lines = [f"Carried from {len(self.transcript_refs)} previous run(s):"]
        for kind in NoteKind:
            of_kind = self.of_kind(kind)
            if not of_kind:
                continue
            lines.append(f"\n{kind.value.replace('_', ' ').title()}:")
            lines.extend(f"  - {note.text}  ({note.run_id})" for note in of_kind)
        if self.compactions:
            last = self.compactions[-1]
            lines.append(
                f"\n[{last.dropped_count} earlier note(s) were compacted away; the full "
                f"transcripts remain retrievable.]"
            )
        return "\n".join(lines)


def compact(
    state: ConversationState,
    *,
    run_id: str,
    budget: dict[NoteKind, int] | None = None,
    now: datetime | None = None,
) -> Compaction | None:
    """Trim the carried notes to budget, deterministically.

    Returns ``None`` when nothing needed dropping, so a caller can tell "compaction ran and
    found nothing to do" from "compaction dropped things" -- and a ledger full of no-op
    compaction records would bury the ones that mattered.

    Within a kind the *most recent* notes survive. That is the opposite of the render order
    and deliberately so: rendering wants the cause before the consequence, and dropping
    wants to keep what is still true. An early constraint that a later run overturned is
    exactly the note whose loss costs least.
    """
    budget = budget or KIND_BUDGET
    kept: list[Note] = []
    dropped: list[str] = []

    for kind in NoteKind:
        of_kind = state.of_kind(kind)
        # Falling back to `KIND_BUDGET`, not to a literal. A caller passing `{DECISION: 3}`
        # silently got 10 for every other kind -- a number that appears nowhere else and
        # matches none of the declared budgets, so a partial override quietly rewrote the
        # policy for the kinds it did not mention.
        limit = budget.get(kind, KIND_BUDGET.get(kind, 10))
        if len(of_kind) <= limit:
            kept.extend(of_kind)
            continue
        survivors = of_kind[-limit:]
        kept.extend(survivors)
        dropped.extend(note.text for note in of_kind[:-limit])

    # A second pass on total size. Counts alone let a conversation inside every per-kind
    # budget still exceed a window, which is the unit that actually binds.
    order_for_size = {id(note): index for index, note in enumerate(state.notes)}
    while sum(len(note.text) for note in kept) > SUMMARY_CHARACTER_BUDGET and len(kept) > 1:
        oldest = min(kept, key=lambda note: order_for_size[id(note)])
        kept.remove(oldest)
        dropped.append(oldest.text)

    if not dropped:
        return None

    before = len(state.notes)
    characters_before = sum(len(note.text) for note in state.notes)
    # Restored to the original insertion order, not grouped: `notes` is a log, and a
    # compaction that reordered it would make two states with the same content digest
    # differently while claiming to carry the same thing.
    order = {id(note): index for index, note in enumerate(state.notes)}
    state.notes = sorted(kept, key=lambda note: order[id(note)])

    record = Compaction(
        at=now or utc_now(),
        run_id=run_id,
        notes_before=before,
        notes_after=len(state.notes),
        dropped=tuple(dropped),
        digest=state.digest(),
        characters_removed=characters_before - sum(len(n.text) for n in state.notes),
    )
    state.compactions.append(record)
    return record


@dataclass(frozen=True, slots=True)
class Resumption:
    """What a run was handed when it resumed, and where it came from (FR-29.3).

    Constructed at resumption and recorded, so "context was lost" is a claim somebody can
    check. Without this, a run that behaved oddly and a run handed the wrong state look
    identical from outside -- and the second is a harness bug that would be attributed to
    the model.
    """

    work_item_id: str
    agent: str
    stage: Stage
    """The *current* stage's pack is loaded, not the previous stage's transcript. Replaying
    the transcript would re-establish framing that the stage change exists to replace."""

    carried_notes: int
    carried_digest: str
    previous_runs: tuple[str, ...]
    at: datetime = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, object]:
        return {
            "workItem": self.work_item_id,
            "agent": self.agent,
            "stage": self.stage.value,
            "carriedNotes": self.carried_notes,
            "carriedDigest": self.carried_digest,
            "previousRuns": list(self.previous_runs),
            "at": self.at.isoformat(),
            "note": (
                "A resumption restores structured state and the current stage's pack. It "
                "does not replay the prior transcript, which remains retrievable."
            ),
        }


def resume(state: ConversationState, *, stage: Stage, now: datetime | None = None) -> Resumption:
    """Record what this run is being handed. Reads nothing and changes nothing."""
    return Resumption(
        work_item_id=state.work_item_id,
        agent=state.agent,
        stage=stage,
        carried_notes=len(state.notes),
        carried_digest=state.digest(),
        previous_runs=tuple(state.transcript_refs),
        at=now or utc_now(),
    )
