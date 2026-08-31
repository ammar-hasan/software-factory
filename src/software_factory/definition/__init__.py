"""Factory definition: models, parsing, loading, validation, and resolution.

The definition is the factory's only source of truth (PRD PR-1). Everything in this
package is pure with respect to the running system: it reads files and produces
typed values, and never executes anything.
"""

from software_factory.definition.loader import (
    Definition,
    LoadedAgent,
    LoadedAutomation,
    LoadedRunner,
    LoadedScorer,
    LoadedSkill,
    load,
    load_strict,
)
from software_factory.definition.models import (
    AgentDefinition,
    AgentRole,
    AutomationDefinition,
    Effect,
    ExecutionDefaults,
    FactoryDocument,
    RunnerDefinition,
    ScorerDefinition,
    SkillDefinition,
    SkillStatus,
    Stage,
)
from software_factory.definition.resolve import (
    explain_execution,
    resolve_execution,
    resolve_for_agent,
    resolve_for_automation,
)
from software_factory.definition.validate import lint, validate

__all__ = [
    "AgentDefinition",
    "AgentRole",
    "AutomationDefinition",
    "Definition",
    "Effect",
    "ExecutionDefaults",
    "FactoryDocument",
    "LoadedAgent",
    "LoadedAutomation",
    "LoadedRunner",
    "LoadedScorer",
    "LoadedSkill",
    "RunnerDefinition",
    "ScorerDefinition",
    "SkillDefinition",
    "SkillStatus",
    "Stage",
    "explain_execution",
    "lint",
    "load",
    "load_strict",
    "resolve_execution",
    "resolve_for_agent",
    "resolve_for_automation",
    "validate",
]
