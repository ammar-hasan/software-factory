"""JSON Schema export, generated from the same models that validate (PRD FR-2.2).

Generating from the parser is what keeps documentation from drifting away from
behaviour (NFR-4.3). It is not free of gaps: pydantic emits structural constraints, but
the cross-field rules in ``model_validator`` (model/tier/harness exclusivity, the scorer
threshold split, the network/allowlist pairing) have no JSON Schema representation. So
each schema carries an explicit ``x-semanticRules`` list naming those rules, rather than
letting a consumer believe schema-validity means the loader will accept the file.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from software_factory import SCHEMA_VERSIONS
from software_factory.definition.models import (
    AgentDefinition,
    AutomationDefinition,
    FactoryDocument,
    RunnerDefinition,
    ScorerDefinition,
    SkillDefinition,
)

_KINDS: dict[str, type[BaseModel]] = {
    "factory": FactoryDocument,
    "agent": AgentDefinition,
    "automation": AutomationDefinition,
    "runner": RunnerDefinition,
    "scorer": ScorerDefinition,
    "skill": SkillDefinition,
}

#: Rules the loader enforces that JSON Schema cannot express. Documented per kind so a
#: consumer knows exactly where schema-validity stops being sufficient.
_SEMANTIC_RULES: dict[str, tuple[str, ...]] = {
    "factory": (
        "agentDefaults declares exactly one of `model`, `tier`, or `harness`",
        "repositories are unique by owner/name",
        "at most one tracker integration",
        "ladder tier names are unique, and defaultTier/ceilingTier/scaffoldBelow resolve",
        "each tier's workingSetCeiling does not exceed its contextWindow",
    ),
    "agent": (
        "`model`, `tier`, and `harness` are mutually exclusive",
        "exactly one agent in a factory declares role CONDUCTOR",
        "`fallback` names a different, existing agent",
        "a named `runner` exists under runners/",
    ),
    "automation": (
        "a `schedule` provider trigger declares a schedule block, and no other provider does",
        "`agent` names an existing agent",
    ),
    "runner": (
        "`network: allowlist` requires a non-empty networkAllowlist, and vice versa",
        "a linux runner declares platform.image, pinned by digest",
        "macOS runners are aarch64 only",
    ),
    "scorer": (
        "label values are unique",
        "at least one label scores at or above passingScore, and at least one below",
        "`agents` name existing agents",
        "the rubric body is non-empty",
    ),
    "skill": (
        "the `name` field matches the containing directory name",
        "a non-draft skill declares owners and reviewBy",
        "a trial or active skill declares at least one eval",
        "a deprecated skill names its supersededBy successor",
        "the body must not claim to grant access or bypass a control",
    ),
}


def schema_kinds() -> tuple[str, ...]:
    """The file kinds a schema can be exported for."""
    return tuple(_KINDS)


def export_schema(kind: str) -> dict[str, Any]:
    """Return the JSON Schema for one definition file kind.

    Raises ``KeyError`` for an unknown kind; the CLI turns that into a message listing
    what is accepted.
    """
    model = _KINDS[kind]
    schema = model.model_json_schema(by_alias=True, mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://software-factory.dev/schema/{SCHEMA_VERSIONS[-1]}/{kind}.json"
    schema["title"] = f"{kind} ({SCHEMA_VERSIONS[-1]})"
    schema["x-semanticRules"] = list(_SEMANTIC_RULES.get(kind, ()))
    return schema


def export_all() -> dict[str, dict[str, Any]]:
    """Every schema, keyed by kind. Used to publish the whole set in one document."""
    return {kind: export_schema(kind) for kind in _KINDS}
