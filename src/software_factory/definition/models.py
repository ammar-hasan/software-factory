"""Typed models for every file kind in a factory definition (PRD FR-2.1).

These models are the single source of truth for the definition schema: the loader
validates against them, ``sf schema`` exports JSON Schema from them, and the docs
are generated from them, so the three can never disagree (NFR-4.3).

Design notes that are easy to miss:

* ``model`` and ``harness`` are mutually exclusive everywhere they appear; ``model``
  is shorthand for the built-in harness. Enforcing this in one mixin keeps the rule
  from drifting between the four places it applies.
* Maps declared at a lower level *replace* rather than merge (FR-2.10). That is
  surprising if you expect deep-merge semantics, so ``resolve_*`` helpers below are
  the only supported way to compute an effective value.
"""

from __future__ import annotations

import enum
import re
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,62}$"
Name = Annotated[str, StringConstraints(pattern=NAME_PATTERN)]
"""Resource names: lowercase, dot/underscore/hyphen, 1-63 chars.

Deliberately narrow because names become directory names, ledger keys, and metric
labels; anything wider eventually collides with a filesystem or a query language.
"""

HANDLE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}$"
Handle = Annotated[str, StringConstraints(pattern=HANDLE_PATTERN)]


class Strict(BaseModel):
    """Base model that refuses unknown keys.

    A typo in a definition file must be an error with a line number, not a setting
    that silently does nothing (FR-2.4).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class AgentRole(enum.StrEnum):
    """Built-in agent roles (FR-3.2). Role drives default gates, pack weights and stage."""

    CONDUCTOR = "CONDUCTOR"
    SCOUT = "SCOUT"
    ARCHITECT = "ARCHITECT"
    BUILDER = "BUILDER"
    CRITIC = "CRITIC"
    PROVER = "PROVER"
    CUSTOM = "CUSTOM"


class Stage(enum.StrEnum):
    """Work-item stages (FR-4.2)."""

    INTAKE = "INTAKE"
    TRIAGE = "TRIAGE"
    DESIGN = "DESIGN"
    BUILD = "BUILD"
    REVIEW = "REVIEW"
    VERIFY = "VERIFY"
    HANDOFF = "HANDOFF"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


ROLE_STAGE: dict[AgentRole, Stage | None] = {
    AgentRole.CONDUCTOR: None,  # spans every stage
    AgentRole.SCOUT: Stage.TRIAGE,
    AgentRole.ARCHITECT: Stage.DESIGN,
    AgentRole.BUILDER: Stage.BUILD,
    AgentRole.CRITIC: Stage.REVIEW,
    AgentRole.PROVER: Stage.VERIFY,
    AgentRole.CUSTOM: None,
}


class Effect(enum.StrEnum):
    """Tool side-effect classes (HARNESS.md T-2)."""

    READ = "read"
    WRITE = "write"
    EXEC = "exec"
    NETWORK = "network"
    EXTERNAL = "external"


class NetworkPolicy(enum.StrEnum):
    NONE = "none"
    ALLOWLIST = "allowlist"
    OPEN = "open"


class CredentialStrategy(enum.StrEnum):
    """Whose authorization a run's repository actions carry (FR-17.1)."""

    EXECUTOR = "EXECUTOR"
    CREATOR = "CREATOR"


class Executor(enum.StrEnum):
    """Where a run's commands actually run (FR-8.2)."""

    LOCAL = "local"
    CONTAINER = "container"
    SSH_WORKER = "ssh-worker"
    CLOUD = "cloud"


class AuthSource(enum.StrEnum):
    MANAGED_SECRET = "managedSecret"
    WORKER_ENVIRONMENT = "workerEnvironment"


class HarnessAuth(Strict):
    source: AuthSource
    secret_name: Name | None = Field(default=None, alias="secretName")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _secret_required(self) -> Self:
        if self.source is AuthSource.MANAGED_SECRET and not self.secret_name:
            raise ValueError("`secretName` is required when `source: managedSecret`")
        return self


class HarnessSpec(Strict):
    """An explicit harness selection, for third-party harnesses or advanced options."""

    type: Name
    model: str | None = None
    tier: Name | None = None
    reasoning_level: Literal["low", "medium", "high"] | None = Field(
        default=None, alias="reasoningLevel"
    )
    auth: HarnessAuth | None = None

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _model_or_tier(self) -> Self:
        if self.model and self.tier:
            raise ValueError("declare `model` or `tier`, not both")
        return self


class Budget(Strict):
    """Hard bounds on a run (FR-3.11). Whichever binds first ends the run."""

    wall_clock_s: int | None = Field(default=None, alias="wallClockSeconds", gt=0)
    tool_calls: int | None = Field(default=None, alias="toolCalls", gt=0)
    tokens: int | None = Field(default=None, gt=0)
    cost_units: float | None = Field(default=None, alias="costUnits", gt=0)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class McpServerRef(Strict):
    """A reference to a tool server, by id or by command, never by inline secret."""

    id: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env_secrets: tuple[Name, ...] = Field(default=(), alias="envSecrets")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _one_of(self) -> Self:
        if bool(self.id) == bool(self.command):
            raise ValueError("declare exactly one of `id` or `command`")
        return self


class ExecutionDefaults(Strict):
    """The execution keys shared by factory defaults, agents, and automations.

    Every field is optional at every level; :func:`resolve_execution` composes them.
    """

    model: str | None = None
    tier: Name | None = None
    harness: HarnessSpec | None = None
    runner: Name | None = None
    executor: Executor | None = None
    worker_host: str | None = Field(default=None, alias="workerHost")
    environment_id: str | None = Field(default=None, alias="environmentId")
    secrets: tuple[Name, ...] | None = None
    mcp_servers: dict[str, McpServerRef] | None = Field(default=None, alias="mcpServers")
    tools: tuple[str, ...] | None = None
    effects: tuple[Effect, ...] | None = None
    budget: Budget | None = None
    credential_strategy: CredentialStrategy | None = Field(default=None, alias="credentialStrategy")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _model_harness_exclusive(self) -> Self:
        declared = [k for k in ("model", "tier", "harness") if getattr(self, k) is not None]
        if len(declared) > 1:
            raise ValueError(
                "`model`, `tier`, and `harness` are mutually exclusive; declared: "
                + ", ".join(declared)
            )
        return self


class Repository(Strict):
    owner: str = Field(min_length=1)
    name: str = Field(min_length=1)

    def slug(self) -> str:
        return f"{self.owner}/{self.name}"


class IntegrationType(enum.StrEnum):
    CHAT = "chat"
    TRACKER = "tracker"
    GIT_HOST = "git-host"
    SIGNAL = "signal"


class Integration(Strict):
    type: IntegrationType
    provider: Name
    config: dict[str, Any] = Field(default_factory=dict)


class Tier(Strict):
    """One rung of the routing ladder (HARNESS.md §8.1)."""

    name: Name
    provider: Name
    model: str
    context_window: int = Field(alias="contextWindow", gt=0)
    working_set_ceiling: int = Field(alias="workingSetCeiling", gt=0)
    cost_per_mtok_in: float = Field(default=0.0, alias="costPerMTokIn", ge=0)
    cost_per_mtok_out: float = Field(default=0.0, alias="costPerMTokOut", ge=0)
    capabilities: tuple[str, ...] = ()
    local: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _ceiling_fits(self) -> Self:
        if self.working_set_ceiling > self.context_window:
            raise ValueError(
                "`workingSetCeiling` must not exceed `contextWindow`; the ceiling exists "
                "because quality degrades before the window is full"
            )
        return self


class Ladder(Strict):
    """The ordered escalation ladder for a factory (FR-11.3)."""

    tiers: tuple[Tier, ...] = Field(min_length=1)
    default_tier: Name | None = Field(default=None, alias="defaultTier")
    ceiling_tier: Name | None = Field(default=None, alias="ceilingTier")
    max_escalations: int = Field(default=2, alias="maxEscalations", ge=0)
    scaffold_at_or_below: Name | None = Field(default=None, alias="scaffoldAtOrBelow")
    """The highest tier that still receives scaffolding.

    Inclusive, and named so. As `scaffoldBelow` the name read as exclusive while the
    code was inclusive, and the two readings disagree exactly where it matters: the
    lowest tier is the one that needs scaffolding most.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _names_unique_and_resolvable(self) -> Self:
        names = [t.name for t in self.tiers]
        if len(names) != len(set(names)):
            raise ValueError("tier names must be unique")
        for key in ("default_tier", "ceiling_tier", "scaffold_at_or_below"):
            value = getattr(self, key)
            if value is not None and value not in names:
                raise ValueError(f"`{key}` references unknown tier {value!r}; known: {names}")
        return self

    def index_of(self, name: str) -> int:
        for index, tier in enumerate(self.tiers):
            if tier.name == name:
                return index
        raise KeyError(name)


class PrincipalDefinition(Strict):
    """``principals/<id>.yaml`` -- one actor this factory recognises (FR-25.1, FR-25.2).

    Authority is configuration for the same reason everything else here is: a repository
    review is the only place a capability grant can be seen by someone other than the person
    who made it.
    """

    id: Name
    kind: Literal["person", "agent", "automation", "plane"] = "person"
    display_name: str | None = Field(default=None, alias="displayName")
    groups: tuple[Name, ...] = ()
    capabilities: tuple[str, ...] = ()
    identities: tuple[str, ...] = ()
    """Provider identities as ``provider:handle``, e.g. ``git-host:amaya``.

    Explicit rather than inferred. An identity the factory has not been told about may
    trigger intake -- anyone can open an issue -- but may not make a decision, and guessing
    that `git-host:amaya` and `chat:amaya` are the same person is exactly the guess that
    turns an intake channel into an authorisation channel.
    """
    active: bool = True

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _identities_are_qualified(self) -> Self:
        for identity in self.identities:
            if ":" not in identity or identity.startswith(":") or identity.endswith(":"):
                raise ValueError(
                    f"identity {identity!r} must be `provider:handle`; a bare handle cannot "
                    "be resolved, because the same name on two providers is two people"
                )
        return self


class FactoryDocument(Strict):
    """``factory.yaml`` -- the root document (FR-2.1)."""

    schema_version: str = Field(alias="schemaVersion")
    name: Name
    description: str | None = None
    handle: Handle | None = None
    repositories: tuple[Repository, ...] = Field(min_length=1)
    integrations: tuple[Integration, ...] = ()
    secrets: tuple[Name, ...] = ()
    mcp_servers: dict[str, McpServerRef] = Field(default_factory=dict, alias="mcpServers")
    ladder: Ladder | None = None
    agent_defaults: ExecutionDefaults = Field(alias="agentDefaults")
    credential_strategy: CredentialStrategy = Field(
        default=CredentialStrategy.EXECUTOR, alias="credentialStrategy"
    )

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _defaults_declare_a_model(self) -> Self:
        d = self.agent_defaults
        if d.model is None and d.tier is None and d.harness is None:
            raise ValueError(
                "`agentDefaults` must declare exactly one of `model`, `tier`, or `harness`"
            )
        return self

    @model_validator(mode="after")
    def _repositories_unique(self) -> Self:
        slugs = [r.slug() for r in self.repositories]
        if len(slugs) != len(set(slugs)):
            raise ValueError("repositories must be unique")
        return self

    @model_validator(mode="after")
    def _one_tracker(self) -> Self:
        trackers = [i for i in self.integrations if i.type is IntegrationType.TRACKER]
        if len(trackers) > 1:
            raise ValueError(
                "declare at most one tracker integration; two trackers means two sources "
                "of truth for one work item"
            )
        return self


class AgentDefinition(Strict):
    """``agents/<name>/agent.md`` frontmatter, plus the prompt body."""

    description: str | None = None
    role: AgentRole = AgentRole.CUSTOM
    concurrency: int = Field(default=1, ge=1)
    allow_shared_blind_spot: bool = Field(default=False, alias="allowSharedBlindSpot")
    fallback: Name | None = None
    memory_scopes: tuple[str, ...] = Field(default=(), alias="memoryScopes")
    read_candidate_memory: bool = Field(default=False, alias="readCandidateMemory")
    execution: ExecutionDefaults = Field(default_factory=ExecutionDefaults)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class TriggerSchedule(Strict):
    name: Name | None = None
    cron: str = Field(min_length=1)


class AuthorTrust(enum.StrEnum):
    """Whether an automation accepts events from authors this factory does not know.

    A declared field rather than a filter key. As a key inside `filter` it was read as
    policy *and* left in the predicate, so `authorTrust: any` disabled the author check and
    simultaneously required the event to carry an attribute of that name -- making the
    automation inert for real traffic and live for anything an event author chose to set.
    """

    KNOWN = "known"
    ANY = "any"


class Trigger(Strict):
    provider: Name
    event: Name
    filter: dict[str, Any] = Field(default_factory=dict)
    author_trust: AuthorTrust = Field(default=AuthorTrust.KNOWN, alias="authorTrust")
    """FR-18.6: restrictive by default. Accepting strangers is chosen, never inherited."""

    schedule: TriggerSchedule | None = None

    @model_validator(mode="after")
    def _author_trust_is_not_a_filter_key(self) -> Self:
        """Refuse the old spelling rather than silently ignoring it.

        A definition written against the previous behaviour would otherwise keep its
        `authorTrust` key in the predicate and go on matching nothing, which is the exact
        silent failure this refusal exists to end.
        """
        if any(key.lower() == "authortrust" for key in self.filter):
            raise ValueError(
                "`authorTrust` is policy, not a filter predicate; declare it beside "
                "`filter`, not inside it. Inside the filter it also required the event to "
                "carry an attribute of that name, so the automation matched only events "
                "that set it"
            )
        return self

    @model_validator(mode="after")
    def _schedule_only_on_schedule_provider(self) -> Self:
        if self.provider == "schedule" and self.schedule is None:
            raise ValueError("a `schedule` trigger requires a `schedule` block")
        if self.provider != "schedule" and self.schedule is not None:
            raise ValueError("`schedule` is only valid on a `schedule` provider trigger")
        return self


class AutomationDefinition(Strict):
    """``automations/<name>/automation.md`` frontmatter, plus the starting prompt."""

    enabled: bool = True
    agent: Name | None = None
    triggers: tuple[Trigger, ...] = Field(min_length=1)
    execution: ExecutionDefaults = Field(default_factory=ExecutionDefaults)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class InstanceShape(Strict):
    vcpus: int = Field(default=2, gt=0)
    memory_gb: int = Field(default=4, alias="memoryGb", gt=0)
    disk_gb: int = Field(default=20, alias="diskGb", gt=0)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Platform(Strict):
    os: Literal["linux", "macos"] = "linux"
    arch: Literal["x86_64", "aarch64"] = "x86_64"
    image: str | None = None

    @model_validator(mode="after")
    def _macos_is_aarch64(self) -> Self:
        if self.os == "macos" and self.arch != "aarch64":
            raise ValueError("macOS runners are aarch64 only")
        return self


class RunnerDefinition(Strict):
    """``runners/<name>.yaml`` -- the compute a run executes on (FR-8.1)."""

    description: str | None = None
    platform: Platform = Field(default_factory=Platform)
    instance_shape: InstanceShape = Field(default_factory=InstanceShape, alias="instanceShape")
    setup_commands: tuple[str, ...] = Field(default=(), alias="setupCommands")
    env: dict[str, str] = Field(default_factory=dict)
    network: NetworkPolicy = NetworkPolicy.ALLOWLIST
    network_allowlist: tuple[str, ...] = Field(default=(), alias="networkAllowlist")
    writable_paths: tuple[str, ...] = Field(default=(), alias="writablePaths")
    timeout_s: int = Field(default=3600, alias="timeoutSeconds", gt=0)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _allowlist_consistency(self) -> Self:
        if self.network is NetworkPolicy.ALLOWLIST and not self.network_allowlist:
            raise ValueError(
                "`network: allowlist` requires a non-empty `networkAllowlist`; use "
                "`network: none` to deny all egress"
            )
        if self.network is not NetworkPolicy.ALLOWLIST and self.network_allowlist:
            raise ValueError("`networkAllowlist` is only meaningful with `network: allowlist`")
        return self


class ScorerLabel(Strict):
    value: Name
    score: float = Field(ge=0.0, le=1.0)
    description: str | None = None


class ScorerDefinition(Strict):
    """``scorers/<name>/scorer.md`` -- a sampling classifier over completed runs (FR-13.6)."""

    name: Name
    description: str | None = None
    agents: tuple[Name, ...] = Field(min_length=1)
    labels: tuple[ScorerLabel, ...] = Field(min_length=2)
    passing_score: float = Field(alias="passingScore", ge=0.0, le=1.0)
    sampling_rate: int = Field(default=25, alias="samplingRate", ge=0, le=100)
    judge: HarnessSpec
    self_improvement: bool = Field(default=False, alias="selfImprovement")
    version: int = Field(default=1, ge=1)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _threshold_separates(self) -> Self:
        values = [label.value for label in self.labels]
        if len(values) != len(set(values)):
            raise ValueError("label values must be unique")
        if not any(label.score >= self.passing_score for label in self.labels):
            raise ValueError("at least one label must score at or above `passingScore`")
        if not any(label.score < self.passing_score for label in self.labels):
            raise ValueError("at least one label must score below `passingScore`")
        return self


class SkillStatus(enum.StrEnum):
    DRAFT = "draft"
    TRIAL = "trial"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class SkillAppliesTo(Strict):
    roles: tuple[AgentRole, ...] = ()
    stages: tuple[Stage, ...] = ()
    surfaces: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()


class SkillArgument(Strict):
    """One named input a skill accepts when it is invoked directly.

    Skills were only ever *selected* by the registry for a run. A skill that can also be
    invoked -- "run the triage skill over this backlog" -- needs a way to be told what to
    act on, and a free-text prompt is not that: it cannot be validated, cannot be defaulted,
    and cannot be checked before a model is paid to discover the argument was missing.
    """

    description: str = Field(min_length=1, max_length=300)
    required: bool = True
    default: str | None = None

    @model_validator(mode="after")
    def _a_required_argument_has_no_default(self) -> Self:
        """A default makes an argument optional. Declaring both says two things at once,
        and the reader has to guess which one the code believes."""
        if self.required and self.default is not None:
            raise ValueError(
                "an argument with a default is not required; set `required: false` or "
                "remove the default"
            )
        return self


class SkillDefinition(Strict):
    """``skills/<name>/SKILL.md`` or ``agents/<a>/skills/<name>/SKILL.md`` (FR-7.1)."""

    name: Name
    description: str = Field(min_length=20, max_length=500)
    version: int = Field(default=1, ge=1)
    status: SkillStatus = SkillStatus.DRAFT
    applies_to: SkillAppliesTo = Field(default_factory=SkillAppliesTo, alias="appliesTo")
    owners: tuple[str, ...] = ()
    review_by: str | None = Field(default=None, alias="reviewBy")
    evals: tuple[str, ...] = ()
    supersedes: tuple[Name, ...] = ()
    superseded_by: Name | None = Field(default=None, alias="supersededBy")
    sample_fraction: float = Field(default=0.25, alias="sampleFraction", ge=0.0, le=1.0)
    arguments: dict[str, SkillArgument] = Field(default_factory=dict)
    """Named inputs, when this skill can be invoked directly as well as selected.

    Empty for most skills: a skill that only ever informs a run needs no arguments, and
    requiring them would make the common case ceremonial."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _non_draft_requires_stewardship(self) -> Self:
        """Non-draft skills need an owner, a review date, and an eval (FR-7.13, FR-7.4)."""
        if self.status is SkillStatus.DRAFT:
            return self
        missing = []
        if not self.owners:
            missing.append("owners")
        if not self.review_by:
            missing.append("reviewBy")
        if self.status in (SkillStatus.TRIAL, SkillStatus.ACTIVE) and not self.evals:
            missing.append("evals")
        if missing:
            raise ValueError(
                f"a `{self.status.value}` skill requires: {', '.join(missing)}; "
                "leave it `draft` until it has them"
            )
        if self.status is SkillStatus.DEPRECATED and not self.superseded_by:
            raise ValueError("a deprecated skill must name its `supersededBy` successor")
        return self


_GENERIC = re.compile(
    r"^(?:\W|\b(?:a|an|the|this|skill|helper|utility|tool|for|to|and|or|use|used|when)\b)+$",
    re.IGNORECASE,
)


def description_is_specific(description: str) -> bool:
    """True when a skill description carries at least one domain-bearing term.

    A description made only of connective and generic words cannot discriminate
    between skills at selection time, which is the failure skills.md K-1 is about.
    """
    return not _GENERIC.match(description.strip())
