"""Answering a reviewer's question about a handed-off change (PRD FR-32).

A reviewer looking at a change could not ask why it was made that way. Every piece was
already there -- FR-18.8 replies in place, FR-4.5 keeps the work item addressable, FR-29
keeps the conversation -- and only the capability was missing, which is the shape of gap
worth closing first.

Two rules make this useful rather than dangerous, and they are the same rule twice.

**Answering does not re-run anything** (FR-32.2). An answer produced by re-running is an
answer about a *different* execution: the reviewer asked "why did you do that" and would be
told what a second run would do, which is a different question with a plausible-sounding
answer. So this reads the record and nothing else.

**An answer that is not in the record says so** (FR-32.3). "The record does not say" is an
answer. A reconstruction is not, and this is the point where a human is most likely to
believe one -- they are reading a change they did not write, from a system that has been
right so far, in a hurry.

The retrieval is deliberately not a model call. A model summarising the record would be a
fourth place the record could be misquoted, and the notes are already the summary: FR-29's
whole design is that a conversation carries what a later reader needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from software_factory.harness.conversation import ConversationState, Note, NoteKind
from software_factory.ledger.entry import EntryType

#: Words carrying no signal about which note answers a question. Not a stopword list for
#: retrieval quality -- it is short on purpose -- but the ones that would otherwise match
#: every note and make every answer look equally relevant.
_NOISE_WORDS = """
a an and are as at be but by did do does for from had has have how i if in is it its
of on or that the their them then there these they this to was were what when where
which who why will with would you your
"""
NOISE = frozenset(_NOISE_WORDS.split())

#: How many notes an answer may cite. An answer that quotes everything has not answered.
MAX_CITED = 4


@dataclass(frozen=True, slots=True)
class Citation:
    """One note the answer rests on, with where it came from."""

    kind: NoteKind
    text: str
    run_id: str
    stage: str

    def render(self) -> str:
        return f"[{self.kind.value} · {self.stage} · {self.run_id}] {self.text}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "run": self.run_id,
            "stage": self.stage,
        }


@dataclass(frozen=True, slots=True)
class Answer:
    """What the record says, or that it does not say.

    `answered` is a separate field from an empty citation list because the two are read
    differently by anyone building on this: "we looked and the record is silent" is a fact
    about the record, and a caller that inferred it from emptiness would eventually infer it
    from a bug instead.
    """

    question: str
    work_item_id: str
    answered: bool
    citations: tuple[Citation, ...] = ()
    note: str = ""

    def render(self) -> str:
        if not self.answered:
            return self.note
        lines = [f"From the record of {self.work_item_id}:"]
        lines += [f"  - {citation.render()}" for citation in self.citations]
        lines.append("")
        lines.append(self.note)
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "workItem": self.work_item_id,
            "answered": self.answered,
            "citations": [c.as_dict() for c in self.citations],
            "note": self.note,
        }


NOT_RECORDED = (
    "The record does not say. This answers from what the run wrote down at the time -- its "
    "decisions, what it tried, the constraints it found -- and nothing here bears on the "
    "question. Re-running would produce an answer about a different execution, so it is not "
    "offered."
)

UNKNOWN_ITEM = (
    "No conversation is recorded for that work item. Either it has not run, or its state "
    "was not persisted; `sf ledger tail` shows what the factory has."
)


@dataclass(slots=True)
class Explainer:
    """Answers questions about work items from their recorded conversations."""

    conversations: dict[str, ConversationState] = field(default_factory=dict)

    @classmethod
    def from_ledger(cls, entries: Any) -> Explainer:
        """Rebuild what each work item's runs wrote down.

        The notes travel in the `RUN_FINISHED` payload rather than being reconstructed from
        the conversation objects, because those live in a coordinator that has exited by the
        time anyone reads the change.
        """
        explainer = cls()
        for entry in entries:
            if entry.type is not EntryType.RUN_FINISHED:
                continue
            carried = entry.payload.get("carried")
            if not isinstance(carried, list):
                continue
            state = explainer.conversations.setdefault(
                str(entry.payload.get("workItem", entry.subject)),
                ConversationState(
                    work_item_id=str(entry.payload.get("workItem", entry.subject)),
                    agent="factory",
                ),
            )
            for raw in carried:
                if not isinstance(raw, dict):
                    continue
                kind = str(raw.get("kind", ""))
                if kind not in set(NoteKind):
                    continue
                from software_factory.definition.models import Stage

                stage = str(raw.get("stage", ""))
                state.notes.append(
                    Note(
                        kind=NoteKind(kind),
                        text=str(raw.get("text", "")),
                        run_id=str(raw.get("run", entry.subject)),
                        stage=Stage(stage) if stage in set(Stage) else Stage.BUILD,
                    )
                )
        return explainer

    def answer(self, work_item_id: str, question: str) -> Answer:
        """What the record says about this question (FR-32.1)."""
        state = self.conversations.get(work_item_id)
        if state is None:
            return Answer(
                question=question,
                work_item_id=work_item_id,
                answered=False,
                note=UNKNOWN_ITEM,
            )

        wanted = _terms(question)
        scored = [
            (score, note) for note in state.notes if (score := len(wanted & _terms(note.text))) > 0
        ]
        if not scored:
            return Answer(
                question=question,
                work_item_id=work_item_id,
                answered=False,
                note=NOT_RECORDED,
            )

        # Highest overlap first, then the order the run wrote them -- a decision made early
        # usually explains the ones after it, and a reader who meets the consequence before
        # the cause has to work backwards.
        order = {id(note): index for index, note in enumerate(state.notes)}
        scored.sort(key=lambda pair: (-pair[0], order[id(pair[1])]))

        citations = tuple(
            Citation(kind=note.kind, text=note.text, run_id=note.run_id, stage=note.stage.value)
            for _score, note in scored[:MAX_CITED]
        )
        return Answer(
            question=question,
            work_item_id=work_item_id,
            answered=True,
            citations=citations,
            note=(
                "Quoted from what the run recorded at the time, not reconstructed. If this "
                "does not answer the question, the record does not contain the answer."
            ),
        )


def _terms(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9_./-]+", text.lower()) if word not in NOISE}
