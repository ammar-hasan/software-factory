"""Deterministic section builders for the Awareness Pack (PRD FR-9.2, FR-9.3).

Eight of the ten pack sections are built here from sources that need no model: version
history, the ledger, the spec, the memory fabric, the skill registry, and static
inspection of the repository. That is the concrete form of "compute the computable"
(PR-6), and it is the reason a pack looks the same for a small model as for a large one.

Every builder returns ``(items, degradation)``. A source that is unavailable degrades one
section with a stated reason rather than failing the pack, and the reason reaches the
agent so it can see the shape of its own blind spot.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from software_factory.definition.models import AgentRole, Stage
from software_factory.harness.awareness import Citation, CitationKind, Item, Origin
from software_factory.ledger import EntryType, Ledger
from software_factory.memory.records import Scope
from software_factory.memory.retrieval import RetrievalRequest, retrieve
from software_factory.memory.store import MemoryStore
from software_factory.skills.registry import SkillRegistry
from software_factory.spec.units import SpecStore

Builder = Callable[[], tuple[list[Item], str | None]]

HISTORY_WINDOW = 200
MAX_ITEMS = 40


def _git(args: list[str], cwd: Path) -> str | None:
    """Run a read-only git query. Returns ``None`` when git cannot answer."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


# --------------------------------------------------------------------------- terrain


def terrain_builder(root: Path, surface: set[str]) -> Builder:
    """The shape of the code being touched: modules, then their neighbours.

    Altitude matters more than volume. This emits paths and their import neighbours, not
    file contents -- reading a file is ``repo.read``'s job, and the section names that
    tool so the agent knows where to get more.
    """

    def build() -> tuple[list[Item], str | None]:
        if not root.is_dir():
            return [], "workspace unavailable"

        files = sorted(
            str(path.relative_to(root))
            for path in root.rglob("*")
            if path.is_file() and not _skipped(path.relative_to(root))
        )
        if not files:
            return [], "repository appears empty"

        items: list[Item] = []
        focus = surface or set(files[:5])

        for path in sorted(focus)[:10]:
            items.append(
                Item(
                    content=f"{path} — in the change surface",
                    citation=Citation(kind=CitationKind.FILE, ref=path),
                )
            )

        neighbours = _neighbours(root, focus, files)
        for path, why in list(neighbours.items())[:15]:
            items.append(
                Item(
                    content=f"{path} — {why}",
                    citation=Citation(kind=CitationKind.FILE, ref=path),
                )
            )

        remaining = [f for f in files if f not in focus and f not in neighbours]
        for path in remaining[: max(0, MAX_ITEMS - len(items))]:
            items.append(Item(content=path, citation=Citation(kind=CitationKind.FILE, ref=path)))

        return items, None

    return build


_SKIP_PARTS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".factory"}


def _skipped(relative: Path) -> bool:
    return any(part in _SKIP_PARTS for part in relative.parts)


_IMPORT = re.compile(r"^\s*(?:from|import)\s+([\w.]+)", re.MULTILINE)


def _neighbours(root: Path, focus: set[str], files: list[str]) -> dict[str, str]:
    """Files that import, or are imported by, the change surface.

    A cheap static approximation rather than a real import graph: it is deterministic,
    needs no language server, and answers the question that actually matters -- what else
    is likely to break.
    """
    found: dict[str, str] = {}
    stems = {Path(path).stem for path in focus}

    for path in files:
        if path in focus or not path.endswith(".py"):
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        modules = {module.split(".")[-1] for module in _IMPORT.findall(text)}
        if modules & stems:
            found[path] = "imports the change surface"

    for path in sorted(focus):
        if not path.endswith(".py"):
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for module in {m.split(".")[-1] for m in _IMPORT.findall(text)}:
            for candidate in files:
                if Path(candidate).stem == module and candidate not in focus:
                    found.setdefault(candidate, "imported by the change surface")
    return found


# ------------------------------------------------------------------------- precedent


def precedent_builder(ledger: Ledger, surface: set[str]) -> Builder:
    """What was tried on this surface before, and how it went.

    The highest-value section for avoiding repeated failure, and it exists only because
    the ledger does. A factory with no history correctly reports that it has none.
    """

    def build() -> tuple[list[Item], str | None]:
        if ledger.is_empty():
            return [], "no prior runs recorded yet"

        outcomes: dict[str, list[str]] = {}
        touched: dict[str, set[str]] = {}
        for entry in ledger.query(type=EntryType.RUN_FINISHED, limit=HISTORY_WINDOW):
            status = str(entry.payload.get("status", "unknown"))
            reason = str(entry.payload.get("reason") or "")
            outcomes.setdefault(entry.subject, []).append(
                f"{entry.payload.get('stage', '?')}:{status}{f' ({reason})' if reason else ''}"
            )
            paths = entry.payload.get("paths") or []
            if isinstance(paths, list):
                touched.setdefault(entry.subject, set()).update(str(path) for path in paths)

        # Work on the same files comes first. Precedent that is not surface-scoped is
        # mostly noise, and this section's whole value is "someone tried this here before".
        def overlap(subject: str) -> int:
            return len(touched.get(subject, set()) & surface)

        ranked = sorted(outcomes, key=lambda subject: (-overlap(subject), subject))

        items = []
        for subject in ranked[:MAX_ITEMS]:
            shared = sorted(touched.get(subject, set()) & surface)[:3]
            where = f" (touched {', '.join(shared)})" if shared else ""
            items.append(
                Item(
                    content=f"work item {subject}{where}: {', '.join(outcomes[subject][:4])}",
                    citation=Citation(kind=CitationKind.RUN, ref=subject),
                )
            )

        note = (
            None
            if any(overlap(subject) for subject in ranked)
            else "no prior work recorded on this change surface"
        )
        return items, note

    return build


# --------------------------------------------------------------------------- hazards


def hazards_builder(root: Path, ledger: Ledger, surface: set[str]) -> Builder:
    """What breaks around here. Mechanically derived, never guessed.

    Reverts and churn come from version history; repeated gate failures come from the
    ledger. Both are facts about the repository, which is why they belong in a
    deterministic section.
    """

    def build() -> tuple[list[Item], str | None]:
        items: list[Item] = []
        degradation: str | None = None

        log = _git(["log", f"-{HISTORY_WINDOW}", "--pretty=format:%h|%s"], root)
        if log is None:
            degradation = "version history unavailable"
        else:
            reverts = [
                line for line in log.splitlines() if re.search(r"\brevert\b", line, re.IGNORECASE)
            ]
            for line in reverts[:8]:
                sha, _, subject = line.partition("|")
                items.append(
                    Item(
                        content=f"reverted: {subject.strip()}",
                        citation=Citation(kind=CitationKind.COMMIT, ref=sha),
                    )
                )

            churn = _git(["log", f"-{HISTORY_WINDOW}", "--name-only", "--pretty=format:"], root)
            if churn:
                counts = Counter(
                    line.strip()
                    for line in churn.splitlines()
                    if line.strip() and not _skipped(Path(line.strip()))
                )
                # Surface files rank above unrelated churn: a hazard in code the agent
                # will not touch is not a hazard for this run.
                ranked_churn = sorted(
                    counts.items(),
                    key=lambda pair: (pair[0] not in surface, -pair[1], pair[0]),
                )
                for path, count in ranked_churn[:8]:
                    if count < 3 and path not in surface:
                        continue
                    items.append(
                        Item(
                            content=f"{path} changed {count} times recently — high churn",
                            citation=Citation(kind=CitationKind.FILE, ref=path),
                        )
                    )

        if not ledger.is_empty():
            failures = Counter(
                str(entry.payload.get("gate"))
                for entry in ledger.query(type=EntryType.GATE_EVALUATED, limit=HISTORY_WINDOW)
                if entry.payload.get("outcome") == "fail"
            )
            for gate, count in failures.most_common(5):
                items.append(
                    Item(
                        content=f"gate {gate} has failed {count} time(s) in recent runs",
                        citation=Citation(kind=CitationKind.POLICY, ref=f"gate:{gate}"),
                    )
                )

        if not items and degradation is None:
            degradation = "no hazards found for this surface"
        return items, degradation

    return build


# ----------------------------------------------------------------------- conventions


def conventions_builder(
    store: MemoryStore,
    *,
    scope_ref: str,
    query: str,
    surface: set[str],
    include_candidate: bool = False,
    limit: int = 10,
) -> Builder:
    """Canon-lane memories about how this team does things.

    The one section that may legitimately carry model-derived content, so every item
    cites its memory id and unverified items are labelled inline. An agent should be able
    to run ``sf memory why`` on anything it was told here.
    """

    def build() -> tuple[list[Item], str | None]:
        try:
            store.load()
        except Exception as exc:
            return [], f"memory unavailable: {exc}"

        result = retrieve(
            store,
            RetrievalRequest(
                query=query,
                scopes=((Scope.REPOSITORY, scope_ref),),
                surfaces=frozenset(surface),
                include_candidate=include_candidate,
                limit=limit,
            ),
        )
        if not result.memories:
            return [], "no established conventions recorded for this repository yet"

        items = [
            Item(
                content=memory.render(),
                citation=Citation(kind=CitationKind.MEMORY, ref=memory.id),
                origin=Origin.MODEL_GENERATED,
                confidence=memory.confidence,
            )
            for memory in result.memories
        ]
        note = (
            f"{result.dropped_disputed} disputed memory(ies) withheld"
            if result.dropped_disputed
            else None
        )
        return items, note

    return build


# ---------------------------------------------------------------------------- skills


def skills_builder(
    registry: SkillRegistry,
    *,
    role: AgentRole,
    stage: Stage,
    surface: set[str],
    task: str,
    limit: int = 7,
) -> Builder:
    """The ranked, bounded skill offer.

    Records what was offered and why the rest were not, which is the raw data the
    selection-quality metrics are computed from.
    """

    def build() -> tuple[list[Item], str | None]:
        offer = registry.offer(role=role, stage=stage, surfaces=surface, task=task, limit=limit)
        if not offer.offered:
            return [], "no skills apply to this role and stage"

        items = []
        for name in offer.offered:
            record = registry.get(name)
            if record is None:
                continue
            items.append(
                Item(
                    content=f"{name}: {record.description}",
                    citation=Citation(kind=CitationKind.POLICY, ref=f"skill:{name}"),
                )
            )
        return items, None

    return build


# ------------------------------------------------------------------------ spec slice


def spec_slice_builder(spec: SpecStore, surface: set[str]) -> Builder:
    """Active spec units governing the change surface.

    Contradicted units are marked ``protected`` so budgeting can never drop them: an
    agent must not work a surface while unaware that its intent is in dispute.
    """

    def build() -> tuple[list[Item], str | None]:
        if not spec.units:
            return [], "no spec units defined yet — run `sf spec induct` to bootstrap"

        units = spec.slice_for(surface)
        if not units:
            return [], "no spec units govern this change surface"

        items = []
        for unit in units[:MAX_ITEMS]:
            criteria = "; ".join(c.statement for c in unit.acceptance[:3])
            items.append(
                Item(
                    content=f"{unit.id} — {unit.intent}"
                    + (f" Acceptance: {criteria}" if criteria else ""),
                    citation=Citation(kind=CitationKind.SPEC, ref=unit.id),
                    origin=Origin.HUMAN_AUTHORED,
                )
            )
        return items, None

    return build


# -------------------------------------------------------------------- open questions


def open_questions_builder(ledger: Ledger, work_item_id: str) -> Builder:
    """Unresolved questions from earlier stages, so an agent knows what it need not resolve."""

    def build() -> tuple[list[Item], str | None]:
        if ledger.is_empty():
            return [], None
        items = []
        for entry in ledger.query(
            type=EntryType.RUN_FINISHED, subject=work_item_id, limit=HISTORY_WINDOW
        ):
            for question in entry.payload.get("unknowns", []) or []:
                items.append(
                    Item(
                        content=str(question),
                        citation=Citation(kind=CitationKind.RUN, ref=entry.subject),
                    )
                )
        return items, None

    return build
