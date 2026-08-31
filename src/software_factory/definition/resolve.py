"""Effective-value resolution across the inheritance chain (PRD FR-2.10).

The chain is: factory ``agentDefaults`` -> agent -> automation override.

The one rule people get wrong: **maps replace, they do not merge.** An agent that
declares ``secrets`` replaces the default list rather than adding to it, so a
narrowing intent stays narrowing. Factory-wide ``secrets`` and ``mcpServers`` are
different -- they always apply, on top of whatever the level resolved to.

Every resolution is explicable: :func:`explain_execution` reports which level
supplied each field, which is what ``sf plan --explain`` prints.
"""

from __future__ import annotations

from dataclasses import dataclass

from software_factory.definition.models import (
    ExecutionDefaults,
    FactoryDocument,
    McpServerRef,
)

#: Fields whose maps/lists replace rather than merge.
_REPLACING = ("secrets", "mcp_servers", "tools", "effects")

_LEVELS = ("factory", "agent", "automation")


def resolve_execution(*layers: ExecutionDefaults | None) -> ExecutionDefaults:
    """Compose execution settings, later layers winning field by field.

    ``model``, ``tier`` and ``harness`` are mutually exclusive, so a later layer that
    declares any one of them clears the other two -- otherwise an agent switching from
    a tier to a pinned harness would inherit a contradictory pair.
    """
    resolved: dict[str, object] = {}
    for layer in layers:
        if layer is None:
            continue
        data = layer.model_dump(exclude_none=True, by_alias=False)
        if {"model", "tier", "harness"} & data.keys():
            for key in ("model", "tier", "harness"):
                resolved.pop(key, None)
        resolved.update(data)
    return ExecutionDefaults.model_validate(resolved)


def resolve_for_agent(
    factory: FactoryDocument, agent_execution: ExecutionDefaults
) -> ExecutionDefaults:
    """Resolve an agent's effective execution, adding always-on factory grants."""
    effective = resolve_execution(factory.agent_defaults, agent_execution)
    return _apply_factory_wide(factory, effective)


def resolve_for_automation(
    factory: FactoryDocument,
    agent_execution: ExecutionDefaults,
    automation_execution: ExecutionDefaults,
) -> ExecutionDefaults:
    """Resolve the settings a specific automation's runs execute with."""
    effective = resolve_execution(factory.agent_defaults, agent_execution, automation_execution)
    return _apply_factory_wide(factory, effective)


def _apply_factory_wide(
    factory: FactoryDocument, effective: ExecutionDefaults
) -> ExecutionDefaults:
    """Factory-wide secrets and tool servers always apply, on top of the resolved set."""
    data = effective.model_dump(exclude_none=True, by_alias=False)

    secrets: tuple[str, ...] = tuple(data.get("secrets") or ())
    merged_secrets = tuple(dict.fromkeys((*factory.secrets, *secrets)))
    if merged_secrets:
        data["secrets"] = merged_secrets

    servers: dict[str, McpServerRef] = dict(data.get("mcp_servers") or {})
    merged_servers = {**factory.mcp_servers, **servers}
    if merged_servers:
        data["mcp_servers"] = merged_servers

    return ExecutionDefaults.model_validate(data)


@dataclass(frozen=True, slots=True)
class FieldOrigin:
    """Where one resolved field's value came from."""

    field: str
    value: object
    source: str

    def render(self) -> str:
        return f"{self.field} = {self.value!r}  (from {self.source})"


def explain_execution(*layers: ExecutionDefaults | None) -> list[FieldOrigin]:
    """Explain which layer supplied each resolved field.

    Layers are named positionally after ``_LEVELS``; extra layers fall back to their
    index so this stays useful if the chain ever grows.
    """
    origins: dict[str, FieldOrigin] = {}
    for index, layer in enumerate(layers):
        if layer is None:
            continue
        name = _LEVELS[index] if index < len(_LEVELS) else f"layer[{index}]"
        for key, value in layer.model_dump(exclude_none=True, by_alias=False).items():
            if key in {"model", "tier", "harness"}:
                for other in ("model", "tier", "harness"):
                    if other != key:
                        origins.pop(other, None)
            origins[key] = FieldOrigin(field=key, value=value, source=name)
    return sorted(origins.values(), key=lambda origin: origin.field)


def replaced_fields(lower: ExecutionDefaults, higher: ExecutionDefaults) -> tuple[str, ...]:
    """Fields where ``higher`` replaced a non-empty collection from ``lower``.

    Surfaced by ``sf plan`` because replace-not-merge semantics silently drop grants
    an author may have assumed were additive.
    """
    replaced = []
    for field in _REPLACING:
        low = getattr(lower, field, None)
        high = getattr(higher, field, None)
        if low and high is not None and low != high:
            replaced.append(field)
    return tuple(replaced)
