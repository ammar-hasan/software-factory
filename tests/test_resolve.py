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


def test_an_agent_that_declares_nothing_inherits_the_factory_secrets() -> None:
    resolved = resolve_for_agent(factory(secrets=["audit-token"]), defaults(tier="t"))

    assert resolved.secrets == ("audit-token",)


def test_an_agent_that_declares_secrets_replaces_the_factory_set() -> None:
    """Factory-wide grants are defaults, not floors.

    They used to be re-added on top, so an agent narrowing its grants got them back --
    which meant an agent could never actually narrow, and default-deny was not the rule
    the module claimed it was.
    """
    resolved = resolve_for_agent(
        factory(secrets=["prod-db-password"]),
        defaults(secrets=["agent-token"]),
    )

    assert resolved.secrets == ("agent-token",)


def test_an_agent_can_narrow_to_no_secrets_at_all() -> None:
    """An explicit empty list is a decision, and the safer direction has to win."""
    resolved = resolve_for_agent(
        factory(secrets=["prod-db-password", "deploy-token"]),
        defaults(secrets=[]),
    )

    assert not resolved.secrets


def test_duplicate_secret_names_are_collapsed() -> None:
    resolved = resolve_for_agent(factory(), defaults(secrets=["shared", "shared"]))

    assert resolved.secrets == ("shared",)


def test_an_agent_that_declares_nothing_inherits_the_factory_tool_servers() -> None:
    resolved = resolve_for_agent(
        factory(mcpServers={"audit": {"id": "audit-server"}}), defaults(tier="t")
    )

    assert set(resolved.mcp_servers or {}) == {"audit"}
    assert isinstance((resolved.mcp_servers or {})["audit"], McpServerRef)


def test_an_agent_that_declares_tool_servers_replaces_the_factory_set() -> None:
    resolved = resolve_for_agent(
        factory(mcpServers={"audit": {"id": "audit-server"}}),
        defaults(mcpServers={"local": {"command": "./tools/local"}}),
    )

    assert set(resolved.mcp_servers or {}) == {"local"}


def test_an_agent_can_narrow_to_no_tool_servers() -> None:
    resolved = resolve_for_agent(
        factory(mcpServers={"audit": {"id": "audit-server"}}),
        defaults(mcpServers={}),
    )

    assert not (resolved.mcp_servers or {})


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
