"""Reading what already happened for things worth keeping (PRD FR-29, V69).

Every run this factory has ever done is in the ledger: what was tried, which gates
complained, which questions one agent asked another, and what the answer turned out to be.
None of it was ever read back. A factory that learns only from what somebody remembered to
write down learns the things that were easy to notice, which are rarely the expensive ones.

Mining reads the ledger and produces **candidates**: proposed memories, proposed skill
changes, proposed spec deltas. It writes nothing. That is not caution, it is the design --
admission control, corroboration rules, quarantine and the skill lifecycle all live
somewhere else already, and a miner that wrote directly would be a second door into memory
with none of them behind it.

Four things make the difference between mining and pattern-matching noise:

**Corroboration counts distinct sources, not repetitions.** Two runs that read the same file
and drew the same conclusion are one observation, however many times they ran. The store
already refuses corroboration from a shared source; the miner must not launder around it by
counting runs.

**Frequency is not importance.** The most common thing in any transcript is boilerplate. An
observation that appears in nearly every run describes the harness, not the repository, so
anything above a saturation threshold is discarded rather than promoted -- the opposite of
what a naive frequency count does, and the reason a naive one produces "the agent called
repo.read" as its top finding.

**Nothing mined is evidence for itself.** A memory mined from runs, injected into later
packs, and mined again out of those runs is a loop that manufactures consensus: the second
observation is the first one being read back. Runs whose packs cited a mined memory are
excluded from the evidence for that memory, which is the only thing keeping the loop open.

**Too little history reports `insufficient_data`, not "nothing to learn".** A factory with
four runs has not established that its conventions are stable; it has four runs.
"""

from __future__ import annotations

import enum
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from software_factory.ledger.entry import EntryType
from software_factory.memory.records import (
    Candidate,
    Kind,
    Scope,
    Source,
    SourceKind,
)

#: Distinct sources an observation needs before it is worth proposing. Two, matching the
#: store's own corroboration rule -- a single source is a claim, not a pattern.
MIN_SOURCES = 2

#: Fraction of runs above which an observation is treated as boilerplate. An observation in
#: nine runs out of ten describes the harness, not the repository, and proposing it as a
#: convention teaches the factory something it already does.
SATURATION = 0.8

#: Runs below which nothing is proposed at all. Four runs cannot distinguish a convention
#: from a coincidence, and the cost of a wrong canonical memory is paid by every later pack.
MIN_RUNS = 5


class Confidence(enum.StrEnum):
    """Whether the history supports a proposal at all.

    The same three-way discipline the rest of the factory uses. `INSUFFICIENT_DATA` is not
    "no findings": one says the history is too thin to read, the other says it was read and
    held nothing, and an operator responds to them differently.
    """

    AVAILABLE = "available"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class Observation:
    """One thing that happened more than once, and what it happened in.

    `sources` rather than a count. Which distinct things attest to this is the question
    corroboration turns on, and a count cannot answer it after the fact.
    """

    what: str
    kind: str
    sources: frozenset[str]
    runs: frozenset[str]
    detail: str = ""

    @property
    def support(self) -> int:
        return len(self.sources)

    def as_dict(self) -> dict[str, Any]:
        return {
            "what": self.what,
            "kind": self.kind,
            "sources": sorted(self.sources),
            "runs": sorted(self.runs),
            "support": self.support,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class SkillIdea:
    """A proposal to *author* a skill that does not exist yet.

    Deliberately not a `skills.registry.Proposal`. Every operation that registry knows --
    promote, evolve, merge, split, sunset -- acts on a skill record that already exists, and
    a mined tool sequence is not one. Borrowing the lifecycle type would put a name into the
    lifecycle machinery with nothing behind it, and the first thing to touch it would be
    looking up a record that was never written.

    So this is a suggestion to a person or an authoring agent: here is a sequence the fleet
    keeps rediscovering, and nothing names it.
    """

    name: str
    sequence: tuple[str, ...]
    rationale: str
    evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sequence": list(self.sequence),
            "rationale": self.rationale,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class Findings:
    """Everything mining proposes, and why it could not propose more."""

    confidence: Confidence
    reason: str
    memories: tuple[Candidate, ...] = ()
    skills: tuple[SkillIdea, ...] = ()
    observations: tuple[Observation, ...] = ()
    discarded: tuple[str, ...] = ()
    """Observations rejected, with the reason. Reported rather than dropped: an operator
    who sees only what was proposed cannot tell a quiet miner from a saturated one."""
    runs_read: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence.value,
            "reason": self.reason,
            "runsRead": self.runs_read,
            "memories": [
                {
                    "kind": c.kind.value,
                    "scope": c.scope.value,
                    "content": c.content,
                    "confidence": c.confidence,
                    "provenance": [s.identity() for s in c.provenance],
                }
                for c in self.memories
            ],
            "skills": [p.as_dict() for p in self.skills],
            "observations": [o.as_dict() for o in self.observations],
            "discarded": list(self.discarded),
        }


@dataclass
class Mine:
    """Reads a ledger and proposes what is worth keeping."""

    scope: Scope = Scope.REPOSITORY
    scope_ref: str = ""
    min_sources: int = MIN_SOURCES
    min_runs: int = MIN_RUNS
    saturation: float = SATURATION

    def run(self, entries: Any) -> Findings:
        rows = list(entries)
        runs = {
            str(entry.payload.get("run") or entry.subject)
            for entry in rows
            if entry.type is EntryType.RUN_STARTED
        }
        if len(runs) < self.min_runs:
            return Findings(
                confidence=Confidence.INSUFFICIENT_DATA,
                reason=(
                    f"{len(runs)} run(s); {self.min_runs} are needed before a repeated "
                    "observation is distinguishable from a coincidence"
                ),
                runs_read=len(runs),
            )

        tainted = self._tainted(rows)
        observations = [
            *self._from_gates(rows, tainted),
            *self._from_answers(rows, tainted),
            *self._from_tools(rows, tainted),
        ]

        kept: list[Observation] = []
        discarded: list[str] = []
        for observation in observations:
            if observation.support < self.min_sources:
                discarded.append(
                    f"{observation.what}: {observation.support} distinct source(s), "
                    f"{self.min_sources} needed"
                )
                continue
            share = len(observation.runs) / len(runs)
            if share >= self.saturation:
                # The counter-intuitive one, and the reason it is here: an observation in
                # nearly every run is describing the harness, not the repository.
                discarded.append(
                    f"{observation.what}: appears in {share:.0%} of runs, which describes "
                    "the harness rather than this repository"
                )
                continue
            kept.append(observation)

        memories = tuple(self._candidate(o) for o in kept if o.kind in ("gate", "answer"))
        skills = tuple(self._proposal(o) for o in kept if o.kind == "tool-sequence")
        return Findings(
            confidence=Confidence.AVAILABLE,
            reason=(
                f"{len(kept)} observation(s) over {len(runs)} run(s)"
                if kept
                else f"read {len(runs)} run(s); nothing recurred on distinct sources"
            ),
            memories=memories,
            skills=skills,
            observations=tuple(kept),
            discarded=tuple(discarded),
            runs_read=len(runs),
        )

    # ------------------------------------------------------------------ self-feeding

    def _tainted(self, rows: list[Any]) -> set[str]:
        """Runs whose packs cited a previously mined memory.

        Excluded from the evidence for that memory. A memory mined from runs, injected into
        later packs and mined again out of those runs is the first observation being read
        back, and counting it as a second is how a miner manufactures its own consensus.
        """
        tainted: set[str] = set()
        for entry in rows:
            if entry.type is not EntryType.PACK_ASSEMBLED:
                continue
            cited = entry.payload.get("memories") or entry.payload.get("citedMemories") or []
            if any(str(memory).startswith("mined:") for memory in cited):
                tainted.add(str(entry.payload.get("run") or entry.subject))
        return tainted

    # ------------------------------------------------------------------ extractors

    def _from_gates(self, rows: list[Any], tainted: set[str]) -> list[Observation]:
        """A gate finding the factory keeps producing is a convention it keeps missing.

        Keyed on the criterion, not the message. Messages carry file names and line numbers,
        so keying on them makes every occurrence unique and the whole extractor silent.
        """
        by_criterion: dict[str, tuple[set[str], set[str], str]] = {}
        for entry in rows:
            if entry.type is not EntryType.GATE_EVALUATED:
                continue
            run = str(entry.payload.get("run") or entry.subject)
            if run in tainted:
                continue
            for finding in entry.payload.get("findings", []) or []:
                criterion = str(finding.get("criterion") or "")
                if not criterion:
                    continue
                sources, runs, _remediation = by_criterion.setdefault(
                    criterion, (set(), set(), str(finding.get("remediation") or ""))
                )
                # The *locator* is the source: two findings on the same file are one
                # observation about that file, however many runs produced them.
                sources.add(str(finding.get("locator") or entry.subject))
                runs.add(run)
        return [
            Observation(
                what=criterion,
                kind="gate",
                sources=frozenset(sources),
                runs=frozenset(runs),
                detail=detail,
            )
            for criterion, (sources, runs, detail) in by_criterion.items()
        ]

    def _from_answers(self, rows: list[Any], tainted: set[str]) -> list[Observation]:
        """A question one agent keeps asking, and the answer it keeps getting.

        The highest-value thing in the log: somebody already worked this out, and the next
        agent will ask again because nothing wrote it down. Only answered questions count --
        an unanswered one is a stall, which the fleet view reports and memory must not.
        """
        questions: dict[int, tuple[str, str]] = {}
        answers: dict[str, tuple[set[str], set[str]]] = {}
        for entry in rows:
            if entry.type is not EntryType.AGENT_MESSAGE:
                continue
            payload = entry.payload
            if payload.get("kind") == "question":
                questions[int(entry.seq)] = (str(payload.get("body", "")), str(entry.actor))
            elif payload.get("kind") == "answer":
                asked = questions.get(int(payload.get("inReplyTo") or 0))
                if asked is None:
                    continue
                run = str(payload.get("run") or "")
                if run and run in tainted:
                    continue
                body = str(payload.get("body", "")).strip()
                sources, runs = answers.setdefault(body, (set(), set()))
                # The person answering is the source. The same agent answering twice is one
                # source: it is one opinion, stated twice.
                sources.add(f"agent:{entry.actor}")
                runs.add(run or f"seq:{entry.seq}")
        return [
            Observation(
                what=body,
                kind="answer",
                sources=frozenset(sources),
                runs=frozenset(runs),
                detail="answered more than once",
            )
            for body, (sources, runs) in answers.items()
        ]

    def _from_tools(self, rows: list[Any], tainted: set[str]) -> list[Observation]:
        """A tool sequence that recurs *within* runs is a procedure with no name yet.

        Adjacent pairs rather than longer chains. A pair recurs often enough to be evidence;
        a five-step chain recurs once and would make every run its own unique "pattern".

        Two rules separate a procedure from ordinary interleaving, and both were learned by
        running this against a real trial's ledger. Without them it proposed fourteen
        "skills", of which ten were the same five pairs counted in both directions:

        **A pair whose reverse also recurs is not a procedure.** `test.run -> proc.run` and
        `proc.run -> test.run` together say the agent alternates between two tools, which is
        what building looks like. Naming either as a procedure describes the medium, not a
        method, and naming both is visibly absurd.

        **A procedure repeats inside a run, not merely across runs.** Two runs that each did
        `repo.read -> proc.run` once are two runs that read a file and then ran something --
        the most ordinary thing an agent does. Counting distinct runs as "distinct sources"
        also quietly reintroduced the repetition-as-corroboration this module refuses
        everywhere else: for the other extractors a source is a file or a person, and here it
        had collapsed to the run itself.
        """
        per_run: dict[str, list[str]] = {}
        for entry in rows:
            if entry.type is not EntryType.TOOL_CALLED:
                continue
            run = str(entry.payload.get("run") or entry.subject)
            if run in tainted:
                continue
            per_run.setdefault(run, []).append(str(entry.payload.get("tool", "")))

        #: Times a pair must appear inside one run before that run attests to it.
        repeats = 2
        pairs: dict[str, tuple[set[str], set[str]]] = {}
        for run, tools in per_run.items():
            seen_here: Counter[str] = Counter()
            for first, second in pairwise(tools):
                if not first or not second or first == second:
                    continue
                seen_here[f"{first} -> {second}"] += 1
            for key, count in seen_here.items():
                if count < repeats:
                    continue
                sources, runs = pairs.setdefault(key, (set(), set()))
                sources.add(f"run:{run}")
                runs.add(run)

        reciprocal = {key for key in pairs if " -> ".join(reversed(key.split(" -> "))) in pairs}
        return [
            Observation(
                what=key,
                kind="tool-sequence",
                sources=frozenset(sources),
                runs=frozenset(runs),
                detail=f"repeated within {len(runs)} run(s)",
            )
            for key, (sources, runs) in pairs.items()
            if key not in reciprocal
        ]

    # ------------------------------------------------------------------ proposals

    def _candidate(self, observation: Observation) -> Candidate:
        """A proposed memory. Candidate lane, never canon.

        `mined:` prefixes every source id so a later mining pass can recognise its own
        output and refuse to count it as fresh evidence.

        Confidence is fixed at the floor rather than scaled with support. Support is already
        in the provenance, where admission control reads it, and a miner that also inflated
        confidence would be voting twice with one observation.
        """
        return Candidate(
            kind=Kind.CONVENTION if observation.kind == "gate" else Kind.FACT,
            scope=self.scope,
            scope_ref=self.scope_ref,
            # For a gate, the remediation *is* part of the claim: "tests-first" alone is a
            # label, and "tests-first: write the failing test at the parent commit" is
            # something a later run can act on. For an answer, the answer is already the
            # whole claim, and appending how it was found ("answered more than once") puts
            # a note about the mining into the content of the memory itself.
            content=(
                f"{observation.what}: {observation.detail}"
                if observation.kind == "gate" and observation.detail
                else observation.what
            ),
            provenance=tuple(
                Source(kind=SourceKind.RUN, ref=f"mined:{source}", locator=observation.what)
                for source in sorted(observation.sources)
            ),
            confidence=0.5,
            evidence=tuple(sorted(observation.runs)),
        )

    def _proposal(self, observation: Observation) -> SkillIdea:
        sequence = tuple(part.strip() for part in observation.what.split("->"))
        return SkillIdea(
            name="-then-".join(part.replace(".", "-") for part in sequence),
            sequence=sequence,
            rationale=(
                f"`{observation.what}` recurred in {len(observation.runs)} run(s) with no "
                "skill naming it, so every agent rediscovers the sequence"
            ),
            evidence=tuple(sorted(observation.runs)),
        )
