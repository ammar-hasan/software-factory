"""Workspaces: more than one factory in one tree (PRD FR-1.5, FR-1.4).

FR-1.5 has always said multiple factories may share a definition tree via a workspace file
listing factory roots, "so a monorepo of factories is reviewable in one change". There was
no workspace model, no loader, no command and no view: every command took one factory root
and every metric folded one ledger.

Two consequences, and the second is the sharper one.

**A team-per-factory arrangement had no tooling.** FR-1.3 requires that repository groups
needing different policies be *separate factories*, so the moment a second policy is needed
the product tells you to make a second factory — and then offers nothing that can see both.

**FR-1.4 could not fire.** It is a P0 requirement that `sf lint` warn "when two factories in
the same tree overlap on a repository", which is not a check any single-factory lint can
perform. The requirement was unimplementable rather than unimplemented, and the difference
matters: nobody was going to notice by reading the code of a command that only ever sees
one factory.

The overlap rule is a warning and not an error, deliberately. Two factories sharing a
repository is usually a mistake — two policies over one codebase, with whichever intake
matched first deciding which applied — but it is legitimate while one is being split out of
the other, and a hard error would make the safe intermediate state of a migration
impossible.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ConfigDict, Field, ValidationError

from software_factory import SCHEMA_VERSIONS
from software_factory.definition import frontmatter as fm
from software_factory.definition.models import Name, Strict
from software_factory.errors import (
    DefinitionError,
    Severity,
    ValidationIssue,
    ValidationReport,
)

#: The file that makes a directory a workspace.
WORKSPACE_FILE = "workspace.yaml"

#: Where a factory's ledger and run state live, relative to its own root.
DEFAULT_STATE_DIR = ".factory"


class WorkspaceMember(Strict):
    """One factory in a workspace.

    ``state`` is per-member rather than global because a workspace is a way to *describe*
    an existing arrangement, not to impose one, and a factory that already keeps its ledger
    somewhere else must be listable without being moved.
    """

    path: str = Field(min_length=1)
    state: str = DEFAULT_STATE_DIR


class WorkspaceDocument(Strict):
    """``workspace.yaml`` -- the root document."""

    schema_version: str = Field(alias="schemaVersion")
    name: Name
    description: str | None = None
    factories: tuple[WorkspaceMember, ...] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


@dataclass(frozen=True, slots=True)
class LoadedFactory:
    """One member, loaded or not.

    A member that failed to load is *kept* with its reason rather than dropped. A workspace
    listing four factories and reporting three is a workspace that hides the broken one,
    and the broken one is the whole reason to look.
    """

    path: Path
    state: Path
    definition: Any | None = None
    error: str = ""

    @property
    def name(self) -> str:
        if self.definition is None:
            return self.path.name
        return str(self.definition.factory.name)

    @property
    def repositories(self) -> tuple[str, ...]:
        if self.definition is None:
            return ()
        return tuple(r.slug() for r in self.definition.factory.repositories)

    @property
    def ledger_path(self) -> Path:
        return self.state / "ledger.jsonl"

    @property
    def loaded(self) -> bool:
        return self.definition is not None


@dataclass(slots=True)
class Workspace:
    """Every factory in one tree, and what is true across them."""

    root: Path
    document: WorkspaceDocument
    factories: tuple[LoadedFactory, ...] = ()
    report: ValidationReport = field(default_factory=ValidationReport)

    @property
    def loaded(self) -> tuple[LoadedFactory, ...]:
        return tuple(f for f in self.factories if f.loaded)

    def overlaps(self) -> dict[str, tuple[str, ...]]:
        """Repositories claimed by more than one factory (FR-1.4).

        Only over factories that loaded. A factory whose definition failed to parse has no
        knowable repositories, and reporting it as overlapping nothing would read as a
        clean answer to a question that was not asked.
        """
        claims: dict[str, list[str]] = {}
        for factory in self.loaded:
            for repository in factory.repositories:
                claims.setdefault(repository, []).append(factory.name)
        return {
            repository: tuple(sorted(names))
            for repository, names in sorted(claims.items())
            if len(names) > 1
        }

    def duplicate_names(self) -> dict[str, int]:
        """Two factories with one name is an ambiguity in every command that takes one."""
        counts: dict[str, int] = {}
        for factory in self.loaded:
            counts[factory.name] = counts.get(factory.name, 0) + 1
        return {name: n for name, n in sorted(counts.items()) if n > 1}


def load_workspace(root: Path, *, strict: bool = False) -> Workspace:
    """Load a workspace and every factory in it.

    Never raises for a *member* that fails to load -- see :class:`LoadedFactory`. It raises
    only when the workspace file itself is missing or unreadable, because there is then no
    workspace to report about.
    """
    from software_factory.definition.loader import load, load_strict

    root = Path(root).resolve()
    path = root / WORKSPACE_FILE
    if not path.is_file():
        raise DefinitionError(
            f"no {WORKSPACE_FILE} at {root}",
            remediation=(
                f"Create a {WORKSPACE_FILE} listing your factory roots, or point `sf "
                "workspace` at the directory containing one."
            ),
        )

    raw = fm.parse_yaml_file(path)
    declared = raw.get("schemaVersion")
    if declared not in SCHEMA_VERSIONS:
        raise DefinitionError(
            f"{path}: unsupported or missing schemaVersion {declared!r}",
            remediation=f"Set `schemaVersion` to one of: {', '.join(SCHEMA_VERSIONS)}.",
        )
    try:
        document = WorkspaceDocument.model_validate(raw)
    except ValidationError as exc:
        raise DefinitionError(
            f"{path} is not a valid workspace document",
            remediation="Fix the reported fields; every member needs a `path`.",
            detail=exc.errors(),
        ) from exc

    report = ValidationReport()
    factories: list[LoadedFactory] = []
    seen: set[Path] = set()

    for member in document.factories:
        member_root = (root / member.path).resolve()
        if member_root in seen:
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="workspace.duplicate_member",
                    message=f"{member.path} is listed more than once",
                    path=path,
                )
            )
            continue
        seen.add(member_root)
        state = (member_root / member.state).resolve()
        try:
            definition = load_strict(member_root) if strict else load(member_root)[0]
        except Exception as exc:
            factories.append(
                LoadedFactory(path=member_root, state=state, error=f"{type(exc).__name__}: {exc}")
            )
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="workspace.member_unloadable",
                    message=f"{member.path} could not be loaded: {exc}",
                    path=path,
                )
            )
            continue
        factories.append(LoadedFactory(path=member_root, state=state, definition=definition))

    workspace = Workspace(root=root, document=document, factories=tuple(factories), report=report)
    _check_across(workspace, path)
    return workspace


def _check_across(workspace: Workspace, path: Path) -> None:
    """The checks that only exist because there is more than one factory."""
    for repository, names in workspace.overlaps().items():
        workspace.report.add(
            ValidationIssue(
                severity=Severity.WARNING,
                code="workspace.repository_overlap",
                message=(
                    f"{repository} is claimed by {len(names)} factories "
                    f"({', '.join(names)}); FR-1.3 says one factory applies one policy, so "
                    "two policies over one repository means whichever intake matches first "
                    "decides which applies"
                ),
                path=path,
            )
        )
    for name, count in workspace.duplicate_names().items():
        workspace.report.add(
            ValidationIssue(
                severity=Severity.ERROR,
                code="workspace.duplicate_name",
                message=(
                    f"{count} factories are named {name!r}; a name is a factory's identity "
                    "(FR-1.1) and every command that takes one would be ambiguous"
                ),
                path=path,
            )
        )


@dataclass(frozen=True, slots=True)
class FactorySummary:
    """One factory's numbers, for a side-by-side."""

    name: str
    path: str
    loaded: bool
    error: str = ""
    repositories: tuple[str, ...] = ()
    agents: int = 0
    automations: int = 0
    skills: int = 0
    runs: int | None = None
    handoffs: int | None = None
    ledger: str = ""
    ledger_present: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "loaded": self.loaded,
            "error": self.error,
            "repositories": list(self.repositories),
            "agents": self.agents,
            "automations": self.automations,
            "skills": self.skills,
            "runs": self.runs,
            "handoffs": self.handoffs,
            "ledger": self.ledger,
            "ledgerPresent": self.ledger_present,
        }


def summarise(workspace: Workspace, *, window: Any = None) -> list[FactorySummary]:
    """A row per factory, with run counts where a ledger exists.

    `runs` is `None` rather than 0 when a factory has no ledger yet. The distinction is the
    same one the metrics layer makes everywhere else: a factory nobody has run and a factory
    whose ledger we could not find are different facts, and the second must not render as
    the first — which, on a comparison table, would read as one team's factory doing nothing.
    """
    from software_factory.ledger.entry import EntryType
    from software_factory.ledger.log import Ledger

    summaries: list[FactorySummary] = []
    for factory in workspace.factories:
        if not factory.loaded:
            summaries.append(
                FactorySummary(
                    name=factory.name,
                    path=str(factory.path),
                    loaded=False,
                    error=factory.error,
                    ledger=str(factory.ledger_path),
                )
            )
            continue

        definition = factory.definition
        assert definition is not None  # `factory.loaded` is exactly this check
        runs: int | None = None
        handoffs: int | None = None
        present = factory.ledger_path.is_file()
        if present:
            entries = list(Ledger(factory.ledger_path).read())
            if window is not None:
                entries = [e for e in entries if window.contains(_ts(e))]
            runs = sum(1 for e in entries if e.type is EntryType.RUN_STARTED)
            handoffs = len(
                {
                    str(e.subject)
                    for e in entries
                    if e.type is EntryType.WORK_ITEM_TRANSITION and e.payload.get("to") == "HANDOFF"
                }
            )

        summaries.append(
            FactorySummary(
                name=factory.name,
                path=str(factory.path),
                loaded=True,
                repositories=factory.repositories,
                agents=len(definition.agents),
                automations=len(definition.automations),
                skills=len(definition.skills),
                runs=runs,
                handoffs=handoffs,
                ledger=str(factory.ledger_path),
                ledger_present=present,
            )
        )
    return summaries


def _ts(entry: Any) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(str(entry.ts).replace("Z", "+00:00"))


def scaffold_workspace(root: Path, *, name: str, members: Iterable[str]) -> Path:
    """Write a workspace file listing `members`. Returns the path written."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / WORKSPACE_FILE
    lines = [
        f'schemaVersion: "{SCHEMA_VERSIONS[0]}"',
        f"name: {name}",
        "factories:",
        *[f"  - path: {member}" for member in members],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
