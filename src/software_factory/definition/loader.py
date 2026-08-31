"""Whole-tree, atomic definition loading (PRD FR-2.3).

A definition either loads completely or not at all. There is no path that *applies* half
a tree, because a factory running on half a definition is worse than a factory running on
yesterday's -- :func:`load_strict` is that path and it raises.

:func:`load` is the diagnostic path, and it does return a partial tree: `sf validate`
exists to report every problem at once, and stopping at the first unparseable file would
make it report one. What it must not do is let the partial tree be mistaken for a whole
one, so every name whose file failed is recorded in ``Definition.unloaded`` and the
cross-reference pass in :mod:`.validate` suppresses findings that are merely downstream of
that absence -- otherwise one typo in the conductor's file produced `factory.no_conductor`
plus an `agent.unknown_fallback` for every agent pointing at it, and the real error was
buried in phantoms.

The loader is deliberately I/O-bound and pure otherwise: it reads files, builds
models, and collects issues. Cross-reference checking lives in :mod:`.validate`, and
effective-value resolution in :mod:`.resolve`, so each is testable alone.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from software_factory import SCHEMA_VERSIONS
from software_factory.definition import frontmatter as fm
from software_factory.definition.models import (
    AgentDefinition,
    AutomationDefinition,
    FactoryDocument,
    RunnerDefinition,
    ScorerDefinition,
    SkillDefinition,
)
from software_factory.errors import (
    DefinitionError,
    Severity,
    ValidationIssue,
    ValidationReport,
)

FACTORY_FILE = "factory.yaml"
AGENT_FILE = "agent.md"
AUTOMATION_FILE = "automation.md"
SCORER_FILE = "scorer.md"
SKILL_FILE = "SKILL.md"

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class LoadedAgent:
    name: str
    path: Path
    definition: AgentDefinition
    prompt: str
    skills: tuple[LoadedSkill, ...] = ()


@dataclass(frozen=True, slots=True)
class LoadedAutomation:
    name: str
    path: Path
    definition: AutomationDefinition
    prompt: str


@dataclass(frozen=True, slots=True)
class LoadedRunner:
    name: str
    path: Path
    definition: RunnerDefinition


@dataclass(frozen=True, slots=True)
class LoadedScorer:
    name: str
    path: Path
    definition: ScorerDefinition
    rubric: str


@dataclass(frozen=True, slots=True)
class LoadedSkill:
    name: str
    path: Path
    definition: SkillDefinition
    body: str
    owner_agent: str | None = None

    @property
    def scope(self) -> str:
        return f"agent:{self.owner_agent}" if self.owner_agent else "factory"


@dataclass(slots=True)
class Definition:
    """A fully loaded, structurally valid factory definition."""

    root: Path
    factory: FactoryDocument
    agents: dict[str, LoadedAgent] = field(default_factory=dict)
    automations: dict[str, LoadedAutomation] = field(default_factory=dict)
    runners: dict[str, LoadedRunner] = field(default_factory=dict)
    scorers: dict[str, LoadedScorer] = field(default_factory=dict)
    skills: dict[str, LoadedSkill] = field(default_factory=dict)
    unloaded: set[str] = field(default_factory=set)
    """Names whose files failed to parse, so they are absent from the maps above.

    A cross-reference finding about one of these is a consequence of the parse failure,
    not an independent problem, and reporting both makes the real one harder to find.
    """

    def conductor(self) -> LoadedAgent | None:
        from software_factory.definition.models import AgentRole

        for agent in self.agents.values():
            if agent.definition.role is AgentRole.CONDUCTOR:
                return agent
        return None

    def skills_for(self, agent_name: str) -> list[LoadedSkill]:
        """Factory-wide skills plus the agent's own, most-specific last (FR-7.2)."""
        agent = self.agents.get(agent_name)
        own = list(agent.skills) if agent else []
        return [*self.skills.values(), *own]


def load(
    root: Path, *, report: ValidationReport | None = None
) -> tuple[Definition, ValidationReport]:
    """Load the definition tree at ``root``, reporting every problem rather than the first.

    Returns a definition and a report. The definition is *partial* when ``report`` carries
    errors: files that failed to parse are absent from its maps and named in
    ``definition.unloaded``. Callers that need a whole tree must use :func:`load_strict`,
    which is the only function that applies one.
    """
    report = report or ValidationReport()
    root = root.resolve()

    factory_path = root / FACTORY_FILE
    if not factory_path.is_file():
        raise DefinitionError(
            f"no {FACTORY_FILE} at {root}",
            remediation=f"Run `sf init` here, or point at a directory containing {FACTORY_FILE}.",
        )

    raw = fm.parse_yaml_file(factory_path)
    _check_schema_version(raw, factory_path, report)
    if not report.ok:
        # A tree whose schema version we cannot interpret must not be parsed further:
        # every downstream field error would be noise about the wrong schema.
        raise DefinitionError(
            f"{factory_path}: unsupported or missing schemaVersion",
            remediation=f"Set `schemaVersion` to one of: {', '.join(SCHEMA_VERSIONS)}.",
            detail=[i.as_dict() for i in report.errors],
        )

    try:
        factory = FactoryDocument.model_validate(raw)
    except ValidationError as exc:
        _record_pydantic(
            exc, factory_path, report, line_lookup=partial(fm.yaml_line_of, factory_path)
        )
        raise DefinitionError(
            f"{factory_path} is not a valid factory document",
            remediation="Fix the reported fields; run `sf validate` for the full list.",
            detail=[i.as_dict() for i in report.errors],
        ) from exc

    definition = Definition(root=root, factory=factory)
    _load_agents(root, definition, report)
    _load_automations(root, definition, report)
    _load_runners(root, definition, report)
    _load_scorers(root, definition, report)
    _load_skills(root / "skills", definition.skills, report, owner_agent=None)
    return definition, report


def load_strict(root: Path) -> Definition:
    """Load, run cross-reference validation, and raise unless the tree is clean."""
    from software_factory.definition.validate import validate

    definition, report = load(root)
    validate(definition, report)
    report.raise_if_failed()
    return definition


def _check_schema_version(raw: dict[str, object], path: Path, report: ValidationReport) -> None:
    declared = raw.get("schemaVersion")
    if declared is None:
        report.add(
            ValidationIssue(
                severity=Severity.ERROR,
                code="schema.missing",
                message="`schemaVersion` is required",
                path=path,
                line=1,
                key="schemaVersion",
                accepted=SCHEMA_VERSIONS,
                remediation=f"Add `schemaVersion: {SCHEMA_VERSIONS[0]}` at the top of the file.",
            )
        )
        return
    if declared not in SCHEMA_VERSIONS:
        report.add(
            ValidationIssue(
                severity=Severity.ERROR,
                code="schema.unsupported",
                message=f"unsupported schemaVersion {declared!r}",
                path=path,
                line=fm.yaml_line_of(path, "schemaVersion"),
                key="schemaVersion",
                accepted=SCHEMA_VERSIONS,
                remediation=(
                    "Upgrade this build, or set schemaVersion to one of: "
                    + ", ".join(SCHEMA_VERSIONS)
                ),
            )
        )


def _load_agents(root: Path, definition: Definition, report: ValidationReport) -> None:
    base = root / "agents"
    if not base.is_dir():
        return
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        path = directory / AGENT_FILE
        if not path.is_file():
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="agent.missing_file",
                    message=f"agent directory {directory.name!r} has no {AGENT_FILE}",
                    path=directory,
                    remediation=f"Add {AGENT_FILE}, or remove the directory.",
                )
            )
            continue
        doc = fm.parse(path)
        parsed = _build(AgentDefinition, _lift_execution(doc.frontmatter), path, doc, report)
        if parsed is None:
            definition.unloaded.add(directory.name)
            continue
        skills: dict[str, LoadedSkill] = {}
        _load_skills(directory / "skills", skills, report, owner_agent=directory.name)
        definition.agents[directory.name] = LoadedAgent(
            name=directory.name,
            path=path,
            definition=parsed,
            prompt=doc.body,
            skills=tuple(skills.values()),
        )


def _load_automations(root: Path, definition: Definition, report: ValidationReport) -> None:
    base = root / "automations"
    if not base.is_dir():
        return
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        path = directory / AUTOMATION_FILE
        if not path.is_file():
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="automation.missing_file",
                    message=f"automation directory {directory.name!r} has no {AUTOMATION_FILE}",
                    path=directory,
                    remediation=f"Add {AUTOMATION_FILE}, or remove the directory.",
                )
            )
            continue
        doc = fm.parse(path)
        parsed = _build(AutomationDefinition, _lift_execution(doc.frontmatter), path, doc, report)
        if parsed is None:
            definition.unloaded.add(directory.name)
            continue
        definition.automations[directory.name] = LoadedAutomation(
            name=directory.name, path=path, definition=parsed, prompt=doc.body
        )


def _load_runners(root: Path, definition: Definition, report: ValidationReport) -> None:
    base = root / "runners"
    if not base.is_dir():
        return
    for path in sorted(base.glob("*.yaml")):
        raw = fm.parse_yaml_file(path)
        try:
            parsed = RunnerDefinition.model_validate(raw)
        except ValidationError as exc:
            _record_pydantic(exc, path, report, line_lookup=partial(fm.yaml_line_of, path))
            definition.unloaded.add(path.stem)
            continue
        definition.runners[path.stem] = LoadedRunner(name=path.stem, path=path, definition=parsed)


def _load_scorers(root: Path, definition: Definition, report: ValidationReport) -> None:
    base = root / "scorers"
    if not base.is_dir():
        return
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        path = directory / SCORER_FILE
        if not path.is_file():
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="scorer.missing_file",
                    message=f"scorer directory {directory.name!r} has no {SCORER_FILE}",
                    path=directory,
                    remediation=f"Add {SCORER_FILE}, or remove the directory.",
                )
            )
            continue
        doc = fm.parse(path)
        parsed = _build(ScorerDefinition, doc.frontmatter, path, doc, report)
        if parsed is None:
            definition.unloaded.add(directory.name)
            continue
        if not doc.body.strip():
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="scorer.empty_rubric",
                    message=f"scorer {parsed.name!r} has no rubric",
                    path=path,
                    line=doc.body_start_line,
                    remediation="Write the judge's criteria in the body, after the frontmatter.",
                )
            )
            continue
        definition.scorers[parsed.name] = LoadedScorer(
            name=parsed.name, path=path, definition=parsed, rubric=doc.body
        )


def _load_skills(
    base: Path,
    into: dict[str, LoadedSkill],
    report: ValidationReport,
    *,
    owner_agent: str | None,
) -> None:
    if not base.is_dir():
        return
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        path = directory / SKILL_FILE
        if not path.is_file():
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="skill.missing_file",
                    message=f"skill directory {directory.name!r} has no {SKILL_FILE}",
                    path=directory,
                    remediation=f"Add {SKILL_FILE}, or remove the directory.",
                )
            )
            continue
        doc = fm.parse(path)
        parsed = _build(SkillDefinition, doc.frontmatter, path, doc, report)
        if parsed is None:
            continue
        if parsed.name != directory.name:
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="skill.name_mismatch",
                    message=(
                        f"skill declares name {parsed.name!r} but lives in directory "
                        f"{directory.name!r}"
                    ),
                    path=path,
                    line=doc.line_of("name"),
                    key="name",
                    remediation="Make the `name` field match the directory name.",
                )
            )
            continue
        into[parsed.name] = LoadedSkill(
            name=parsed.name,
            path=path,
            definition=parsed,
            body=doc.body,
            owner_agent=owner_agent,
        )


def _lift_execution(frontmatter: dict[str, object]) -> dict[str, object]:
    """Move execution keys into a nested ``execution`` block.

    Definition files declare execution keys flat (``model:``, ``runner:``) because
    that is what an author wants to write; the models keep them in one nested object
    because that is what inheritance needs to compose. This is the only place the two
    shapes meet.
    """
    from software_factory.definition.models import ExecutionDefaults

    execution_keys = {
        name
        for field_name, info in ExecutionDefaults.model_fields.items()
        for name in (field_name, info.alias)
        if name
    }
    lifted = {k: v for k, v in frontmatter.items() if k in execution_keys}
    rest = {k: v for k, v in frontmatter.items() if k not in execution_keys}
    if lifted:
        rest["execution"] = lifted
    return rest


def _build(
    model: type[ModelT],
    data: dict[str, object],
    path: Path,
    doc: fm.Document,
    report: ValidationReport,
) -> ModelT | None:
    """Validate ``data`` into ``model``, recording located issues instead of raising.

    Returning ``None`` rather than raising lets the loader collect every problem in a
    tree in one pass, which is what makes `sf validate` useful.
    """
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        _record_pydantic(exc, path, report, line_lookup=doc.line_of)
        return None


def _record_pydantic(
    exc: ValidationError,
    path: Path,
    report: ValidationReport,
    *,
    line_lookup: Callable[[str], int | None],
) -> None:
    lookup = line_lookup
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        top = str(error["loc"][0]) if error["loc"] else ""
        report.add(
            ValidationIssue(
                severity=Severity.ERROR,
                code=f"field.{error['type']}",
                message=f"{location}: {error['msg']}",
                path=path,
                line=lookup(top),
                key=location,
                remediation="Correct the field to match the documented schema (`sf schema`).",
            )
        )
