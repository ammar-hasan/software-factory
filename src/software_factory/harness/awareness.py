"""Awareness Pack assembly (PRD FR-9, docs/harness/awareness.md).

An agent's output quality is bounded above by what it knew when it started, and this is
where that bound is set. Two properties carry the design:

* **Deterministic over a captured snapshot.** Assembly first records the commit, the
  definition revision, the store revisions, the clock instant used for every freshness
  computation, and the seed; the pack is then a pure function of that snapshot. Two runs
  at different times legitimately differ -- what must be reproducible is *replay*.
* **Everything cited.** An item without a resolvable citation is a defect and is not
  emitted. There are no uncited assertions in a pack, which is what lets an agent (and a
  reviewer) follow any claim back to its source.
"""

from __future__ import annotations

import enum
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from software_factory.definition.models import AgentRole


class Origin(enum.StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL_GENERATED = "model_generated"
    HUMAN_AUTHORED = "human_authored"


class CitationKind(enum.StrEnum):
    FILE = "file"
    RUN = "run"
    MEMORY = "memory"
    SPEC = "spec"
    COMMIT = "commit"
    TEST = "test"
    CI = "ci"
    POLICY = "policy"
    WORK_ITEM = "work_item"


class SectionId(enum.StrEnum):
    MISSION = "mission"
    SPEC_SLICE = "spec-slice"
    TERRAIN = "terrain"
    PRECEDENT = "precedent"
    HAZARDS = "hazards"
    CONVENTIONS = "conventions"
    TOOLBELT = "toolbelt"
    SKILLS = "skills"
    CONTRACT = "contract"
    OPEN_QUESTIONS = "open-questions"


#: Sections that are never dropped, whatever the budget says. A pack without its mission
#: or its contract is not a smaller pack, it is a broken one (awareness.md A-10).
PROTECTED: frozenset[SectionId] = frozenset({SectionId.MISSION, SectionId.CONTRACT})

#: Fractions of the pack budget, by role. Starting values, not settled ones: pack
#: telemetry measures which sections were actually used, and reweighting is an ordinary
#: improvement proposal with benchmark evidence behind it (PRD OQ-1).
ROLE_WEIGHTS: dict[AgentRole, dict[SectionId, float]] = {
    AgentRole.SCOUT: {
        SectionId.MISSION: 0.10,
        SectionId.SPEC_SLICE: 0.10,
        SectionId.TERRAIN: 0.25,
        SectionId.PRECEDENT: 0.20,
        SectionId.HAZARDS: 0.20,
        SectionId.CONVENTIONS: 0.05,
        SectionId.TOOLBELT: 0.04,
        SectionId.SKILLS: 0.03,
        SectionId.CONTRACT: 0.02,
        SectionId.OPEN_QUESTIONS: 0.01,
    },
    AgentRole.ARCHITECT: {
        SectionId.MISSION: 0.12,
        SectionId.SPEC_SLICE: 0.30,
        SectionId.TERRAIN: 0.12,
        SectionId.PRECEDENT: 0.10,
        SectionId.HAZARDS: 0.06,
        SectionId.CONVENTIONS: 0.10,
        SectionId.TOOLBELT: 0.05,
        SectionId.SKILLS: 0.05,
        SectionId.CONTRACT: 0.05,
        SectionId.OPEN_QUESTIONS: 0.05,
    },
    AgentRole.BUILDER: {
        SectionId.MISSION: 0.10,
        SectionId.SPEC_SLICE: 0.15,
        SectionId.TERRAIN: 0.25,
        SectionId.PRECEDENT: 0.08,
        SectionId.HAZARDS: 0.12,
        SectionId.CONVENTIONS: 0.12,
        SectionId.TOOLBELT: 0.08,
        SectionId.SKILLS: 0.05,
        SectionId.CONTRACT: 0.03,
        SectionId.OPEN_QUESTIONS: 0.02,
    },
    AgentRole.CRITIC: {
        SectionId.MISSION: 0.10,
        SectionId.SPEC_SLICE: 0.25,
        SectionId.TERRAIN: 0.10,
        SectionId.PRECEDENT: 0.08,
        SectionId.HAZARDS: 0.20,
        SectionId.CONVENTIONS: 0.12,
        SectionId.TOOLBELT: 0.05,
        SectionId.SKILLS: 0.05,
        SectionId.CONTRACT: 0.03,
        SectionId.OPEN_QUESTIONS: 0.02,
    },
    AgentRole.PROVER: {
        SectionId.MISSION: 0.12,
        SectionId.SPEC_SLICE: 0.20,
        SectionId.TERRAIN: 0.10,
        SectionId.PRECEDENT: 0.05,
        SectionId.HAZARDS: 0.18,
        SectionId.CONVENTIONS: 0.05,
        SectionId.TOOLBELT: 0.15,
        SectionId.SKILLS: 0.05,
        SectionId.CONTRACT: 0.05,
        SectionId.OPEN_QUESTIONS: 0.05,
    },
    AgentRole.CONDUCTOR: {
        SectionId.MISSION: 0.20,
        SectionId.SPEC_SLICE: 0.10,
        SectionId.TERRAIN: 0.05,
        SectionId.PRECEDENT: 0.15,
        SectionId.HAZARDS: 0.05,
        SectionId.CONVENTIONS: 0.05,
        SectionId.TOOLBELT: 0.10,
        SectionId.SKILLS: 0.10,
        SectionId.CONTRACT: 0.10,
        SectionId.OPEN_QUESTIONS: 0.10,
    },
}
ROLE_WEIGHTS[AgentRole.CUSTOM] = ROLE_WEIGHTS[AgentRole.BUILDER]

#: How much of the tier's effective working set the pack may occupy, leaving room for
#: the run itself (awareness.md A-11).
PACK_BUDGET_FRACTION = 0.35


@dataclass(frozen=True, slots=True)
class Citation:
    kind: CitationKind
    ref: str
    locator: str = ""

    def render(self) -> str:
        return f"{self.kind.value}:{self.ref}{':' + self.locator if self.locator else ''}"


@dataclass(frozen=True, slots=True)
class Item:
    """One piece of context. Always cited; model-generated content always labelled."""

    content: str
    citation: Citation
    origin: Origin = Origin.DETERMINISTIC
    confidence: float | None = None
    protected: bool = False
    """Never dropped by budgeting -- used for contradicted spec units (awareness.md §3.2)."""

    def tokens(self) -> int:
        return estimate_tokens(self.content)

    def render(self) -> str:
        mark = "" if self.origin is Origin.DETERMINISTIC else f" [{self.origin.value}]"
        return f"- {self.content}{mark}  <{self.citation.render()}>"


@dataclass(slots=True)
class Section:
    id: SectionId
    title: str
    retrieval_tool: str
    items: list[Item] = field(default_factory=list)
    budget_tokens: int = 0
    truncated: int = 0

    def tokens(self) -> int:
        return sum(item.tokens() for item in self.items)

    def render(self) -> str:
        lines = [f"## {self.title}"]
        lines.extend(item.render() for item in self.items)
        if self.truncated:
            lines.append(f"- [{self.truncated} more available via `{self.retrieval_tool}`]")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The inputs a pack was assembled from. Replay reproduces the pack from this alone."""

    commit: str
    definition_revision: str
    memory_revision: str
    ledger_seq: int
    skill_revision: str
    assembled_at: datetime
    seed: int = 0

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "commit": self.commit,
                    "definition": self.definition_revision,
                    "memory": self.memory_revision,
                    "ledger": self.ledger_seq,
                    "skills": self.skill_revision,
                    "at": self.assembled_at.isoformat(),
                    "seed": self.seed,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()


@dataclass(slots=True)
class AwarenessPack:
    """The assembled context handed to an agent at run start."""

    snapshot: Snapshot
    role: AgentRole
    budget_tokens: int
    sections: list[Section] = field(default_factory=list)
    omissions: list[tuple[str, str]] = field(default_factory=list)
    degradations: list[tuple[str, str]] = field(default_factory=list)

    def section(self, section_id: SectionId) -> Section | None:
        for section in self.sections:
            if section.id is section_id:
                return section
        return None

    def tokens(self) -> int:
        return sum(section.tokens() for section in self.sections)

    def citations(self) -> list[str]:
        return [item.citation.render() for section in self.sections for item in section.items]

    def digest(self) -> str:
        """Content digest. Two packs with identical content have identical digests."""
        material = "\n".join(
            f"{section.id.value}|{item.citation.render()}|{item.content}"
            for section in self.sections
            for item in section.items
        )
        return hashlib.sha256(
            (self.snapshot.digest() + "||" + material).encode("utf-8")
        ).hexdigest()

    def render(self) -> str:
        parts = [section.render() for section in self.sections if section.items]
        if self.degradations:
            parts.append(
                "## Degraded\n"
                + "\n".join(f"- {name}: {reason}" for name, reason in self.degradations)
            )
        return "\n\n".join(parts)

    def as_dict(self) -> dict[str, object]:
        return {
            "digest": self.digest(),
            "snapshot": self.snapshot.digest(),
            "role": self.role.value,
            "budgetTokens": self.budget_tokens,
            "usedTokens": self.tokens(),
            "sections": [
                {
                    "id": s.id.value,
                    "items": len(s.items),
                    "tokens": s.tokens(),
                    "budget": s.budget_tokens,
                    "truncated": s.truncated,
                }
                for s in self.sections
            ],
            "omissions": [{"section": n, "reason": r} for n, r in self.omissions],
            "degradations": [{"section": n, "reason": r} for n, r in self.degradations],
        }


def estimate_tokens(text: str) -> int:
    """Cheap, deterministic token estimate.

    Deliberately not a real tokenizer: budgeting has to be reproducible across machines
    and model families, and a per-provider tokenizer would make the same inputs produce
    different packs. Roughly four characters per token, floored at one per non-empty item.
    """
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


#: A section builder returns items and, optionally, a degradation reason.
Builder = Callable[[], tuple[list[Item], str | None]]

SECTION_TITLES: dict[SectionId, tuple[str, str]] = {
    SectionId.MISSION: ("Mission", "workitem.get"),
    SectionId.SPEC_SLICE: ("Spec slice", "spec.get"),
    SectionId.TERRAIN: ("Terrain", "repo.graph"),
    SectionId.PRECEDENT: ("Precedent", "ledger.query"),
    SectionId.HAZARDS: ("Hazards", "repo.history"),
    SectionId.CONVENTIONS: ("Conventions", "memory.query"),
    SectionId.TOOLBELT: ("Toolbelt", "tools.list"),
    SectionId.SKILLS: ("Skills", "skills.list"),
    SectionId.CONTRACT: ("Contract", "policy.get"),
    SectionId.OPEN_QUESTIONS: ("Open questions", "workitem.questions"),
}


class PackAssembler:
    """Builds packs from registered section builders.

    Builders are registered rather than hard-coded so a section can be tested alone and
    so an unavailable source degrades one section rather than failing the pack (PR-9).
    """

    def __init__(self, *, role: AgentRole, budget_tokens: int) -> None:
        self.role = role
        self.budget_tokens = budget_tokens
        self._builders: dict[SectionId, Builder] = {}

    def register(self, section_id: SectionId, builder: Builder) -> None:
        self._builders[section_id] = builder

    def assemble(self, snapshot: Snapshot) -> AwarenessPack:
        pack = AwarenessPack(snapshot=snapshot, role=self.role, budget_tokens=self.budget_tokens)
        weights = ROLE_WEIGHTS[self.role]

        for section_id in SectionId:
            title, tool = SECTION_TITLES[section_id]
            builder = self._builders.get(section_id)
            if builder is None:
                pack.omissions.append((section_id.value, "no source registered"))
                continue
            try:
                items, degradation = builder()
            except Exception as exc:
                pack.degradations.append((section_id.value, f"builder failed: {exc!r}"))
                continue
            if degradation:
                pack.degradations.append((section_id.value, degradation))

            items = [item for item in items if _is_citable(item)]
            section = Section(
                id=section_id,
                title=title,
                retrieval_tool=tool,
                items=items,
                budget_tokens=int(self.budget_tokens * weights.get(section_id, 0.0)),
            )
            pack.sections.append(section)

        _apply_budget(pack)
        return pack


def _is_citable(item: Item) -> bool:
    """An item without a resolvable citation is a defect, and is dropped rather than shown."""
    return bool(item.citation.ref)


def _apply_budget(pack: AwarenessPack) -> None:
    """Trim to budget by dropping whole items from the tail of each section.

    Never mid-item: half an item is worse than an absent one, because a reader cannot
    tell which half they are missing. Protected sections and protected items survive
    regardless.
    """
    for section in pack.sections:
        if section.id in PROTECTED:
            continue
        while section.tokens() > section.budget_tokens and section.items:
            for index in range(len(section.items) - 1, -1, -1):
                if not section.items[index].protected:
                    section.items.pop(index)
                    section.truncated += 1
                    break
            else:
                break  # everything left is protected
