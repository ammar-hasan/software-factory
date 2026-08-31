"""Inheritance resolution: factory defaults -> agent -> automation.

The rule people get wrong is that maps replace rather than merge, so most of these
tests are about a lower level *narrowing* rather than adding.
"""

from __future__ import annotations

from software_factory.definition.models import (
    ExecutionDefaults,
    FactoryDocument,
    HarnessSpec,
    McpServerRef,
)
from software_factory.definition.resolve import (
    explain_execution,
    replaced_fields,
    resolve_execution,
    resolve_for_agent,
    resolve_for_automation,
)


def defaults(**kwargs: object) -> ExecutionDefaults:
    return ExecutionDefaults.model_validate(kwargs)


def factory(**overrides: object) -> FactoryDocument:
    data: dict[str, object] = {
        "schemaVersion": "v1alpha1",
        "name": "payments",
        "repositories": [{"owner": "acme", "name": "payments-service"}],
        "agentDefaults": {"tier": "local-small"},
    }
    data.update(overrides)
    return FactoryDocument.model_validate(data)


def test_later_layers_win_field_by_field() -> None:
    resolved = resolve_execution(
        defaults(tier="local-small", runner="linux"),
        defaults(runner="macos"),
    )

    assert resolved.tier == "local-small"
    assert resolved.runner == "macos"


def test_declaring_a_harness_clears_an_inherited_tier() -> None:
    """model/tier/harness are mutually exclusive, so a later choice must clear the others."""
    resolved = resolve_execution(
        defaults(tier="local-small"),
        defaults(harness={"type": "external", "model": "pinned-model"}),
    )

    assert resolved.tier is None
    assert resolved.model is None
    assert isinstance(resolved.harness, HarnessSpec)
    assert resolved.harness.model == "pinned-model"


def test_declaring_a_model_clears_an_inherited_harness() -> None:
    resolved = resolve_execution(
        defaults(harness={"type": "external", "model": "pinned"}),
        defaults(model="direct-model"),
    )

    assert resolved.harness is None
    assert resolved.model == "direct-model"


def test_secrets_replace_rather_than_merge() -> None:
    """A narrowing declaration must stay narrowing (FR-2.10)."""
    resolved = resolve_execution(
        defaults(tier="t", secrets=["broad-a", "broad-b"]),
        defaults(secrets=["narrow"]),
    )

    assert resolved.secrets == ("narrow",)


def test_factory_wide_secrets_always_apply_on_top() -> None:
    resolved = resolve_for_agent(
        factory(secrets=["audit-token"]),
        defaults(secrets=["agent-token"]),
    )

    assert set(resolved.secrets or ()) == {"audit-token", "agent-token"}


def test_factory_wide_secrets_are_not_duplicated() -> None:
    resolved = resolve_for_agent(
        factory(secrets=["shared"]),
        defaults(secrets=["shared"]),
    )

    assert resolved.secrets == ("shared",)


def test_factory_wide_tool_servers_always_apply() -> None:
    resolved = resolve_for_agent(
        factory(mcpServers={"audit": {"id": "audit-server"}}),
        defaults(mcpServers={"local": {"command": "./tools/local"}}),
    )

    assert set(resolved.mcp_servers or {}) == {"audit", "local"}


def test_an_agent_cannot_shadow_a_factory_wide_server_away() -> None:
    """Factory-wide grants are floors, not defaults."""
    resolved = resolve_for_agent(
        factory(mcpServers={"audit": {"id": "audit-server"}}),
        defaults(mcpServers={}),
    )

    assert "audit" in (resolved.mcp_servers or {})
    assert isinstance((resolved.mcp_servers or {})["audit"], McpServerRef)


def test_automation_overrides_beat_the_agent() -> None:
    resolved = resolve_for_automation(
        factory(),
        defaults(runner="linux", tier="local-small"),
        defaults(runner="macos"),
    )

    assert resolved.runner == "macos"
    assert resolved.tier == "local-small"


def test_explain_names_the_layer_that_supplied_each_field() -> None:
    origins = {
        origin.field: origin.source
        for origin in explain_execution(
            defaults(tier="local-small", runner="linux"),
            defaults(runner="macos"),
        )
    }

    assert origins["tier"] == "factory"
    assert origins["runner"] == "agent"


def test_explain_drops_a_field_cleared_by_exclusivity() -> None:
    origins = {
        origin.field
        for origin in explain_execution(
            defaults(tier="local-small"),
            defaults(model="pinned"),
        )
    }

    assert "tier" not in origins
    assert "model" in origins


def test_replaced_fields_surfaces_silently_dropped_grants() -> None:
    lower = defaults(tier="t", secrets=["a", "b"], tools=["repo.read"])
    higher = defaults(secrets=["a"])

    assert replaced_fields(lower, higher) == ("secrets",)


def test_none_layers_are_skipped() -> None:
    resolved = resolve_execution(None, defaults(tier="t"), None)

    assert resolved.tier == "t"
