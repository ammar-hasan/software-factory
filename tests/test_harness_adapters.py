"""Harness adapters: which runtime a rung selects, and what an unwired one does.

The point of the contract is that a stage can run on a harness other than the built-in
one, so these tests are mostly about the two ways that promise breaks: a pinned harness
that silently gets served by `loom`, and a name nobody implements reaching a run.
"""

from __future__ import annotations

import json

import pytest

from software_factory.definition.models import AgentRole, Effect, Ladder
from software_factory.harness import BlastRadius, Grants, RoutingState
from software_factory.harness.adapters import (
    ClaudeCodeHarness,
    CodexHarness,
    HarnessRequest,
    NativeHarness,
    UnknownHarnessError,
    canonical_harness,
    known_harnesses,
    resolve_harness,
)
from software_factory.harness.awareness import (
    AwarenessPack,
    Citation,
    CitationKind,
    Item,
    PackAssembler,
    SectionId,
    Snapshot,
)
from software_factory.harness.loop import Budget, RunStatus
from software_factory.harness.routing import Escalation, Trigger, may_escalate
from software_factory.harness.tools import ToolRegistry
from software_factory.memory.records import utc_now
from software_factory.providers import StubProvider, says

OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["summary", "calibration"],
    "properties": {"summary": {"type": "string"}, "calibration": {"type": "object"}},
}


def pack() -> AwarenessPack:
    builder = PackAssembler(role=AgentRole.BUILDER, budget_tokens=2000)
    builder.register(
        SectionId.MISSION,
        lambda: (
            [
                Item(
                    content="Fix BOM handling in the CSV importer.",
                    citation=Citation(kind=CitationKind.WORK_ITEM, ref="wi-1"),
                )
            ],
            None,
        ),
    )
    return builder.assemble(
        Snapshot(
            commit="abc",
            definition_revision="d1",
            memory_revision="m1",
            ledger_seq=1,
            skill_revision="s1",
            assembled_at=utc_now(),
        )
    )


def ladder(*, harness: str | None = None, runner: str | None = None) -> Ladder:
    """A two-rung ladder whose upper rung may change harness and runner, not only model."""
    return Ladder.model_validate(
        {
            "tiers": [
                {
                    "name": "local-small",
                    "provider": "local",
                    "model": "small",
                    "contextWindow": 32000,
                    "workingSetCeiling": 20000,
                    "maxOutputTokens": 3000,
                    "local": True,
                },
                {
                    "name": "mid",
                    "provider": "local",
                    "model": "mid",
                    "harness": harness,
                    "runner": runner,
                    "contextWindow": 128000,
                    "workingSetCeiling": 90000,
                    "maxOutputTokens": 8000,
                },
            ],
            "defaultTier": "local-small",
            "ceilingTier": "mid",
            "maxEscalations": 2,
        }
    )


def request(provider: StubProvider | None, **overrides: object) -> HarnessRequest:
    defaults: dict[str, object] = {
        "provider": provider,
        "registry": ToolRegistry(),
        "grants": Grants(tools=frozenset(), effects=frozenset({Effect.READ})),
        "pack": pack(),
        "contract": BlastRadius(writable_paths=("workspace/",)),
        "budget": Budget(),
        "routing": RoutingState(ladder=ladder(), current="local-small"),
        "role_prompt": "You make the change and prove it.",
        "task": "The importer mangles BOM headers.",
        "output_schema": OUTPUT_SCHEMA,
    }
    return HarnessRequest(**{**defaults, **overrides})  # type: ignore[arg-type]


def valid_output() -> str:
    return json.dumps(
        {
            "summary": "Fixed the BOM handling.",
            "calibration": {
                "criteria": [
                    {"id": "C1", "confidence": 0.9, "evidence": ["tests/test_bom.py::test_bom"]}
                ],
                "unknowns": ["whether other importers share the bug"],
            },
        }
    )


def test_the_built_in_harness_runs_the_turn_loop() -> None:
    provider = StubProvider([says(valid_output())])

    result = NativeHarness().run(request(provider))

    assert result.status is RunStatus.COMPLETED
    assert result.output is not None
    assert result.output["summary"] == "Fixed the BOM handling."
    assert provider.models == ["small"]


def test_an_unwired_harness_refuses_instead_of_falling_back() -> None:
    """A stage that reports Codex and ran `loom` corrupts every later comparison of the two."""
    provider = StubProvider([says(valid_output())])

    result = CodexHarness().run(request(provider))

    assert result.status is RunStatus.SETUP_FAILED
    assert "codex" in (result.reason or "")
    assert provider.calls == [], "the pinned harness fell through to the built-in one"


def test_an_unwired_harness_says_so_before_a_run_starts() -> None:
    available, reason = ClaudeCodeHarness().available()

    assert available is False
    assert "claude-code" in reason


def test_the_built_in_harness_needs_a_provider() -> None:
    result = NativeHarness().run(request(None))

    assert result.status is RunStatus.SETUP_FAILED


def test_a_declaration_selects_the_harness_it_names() -> None:
    assert isinstance(resolve_harness("claude-code"), ClaudeCodeHarness)
    assert isinstance(resolve_harness("codex"), CodexHarness)


def test_declaring_no_harness_is_the_built_in_one() -> None:
    """An agent that pins a model or a tier is running on `loom`, and is not a fourth engine."""
    assert isinstance(resolve_harness(None), NativeHarness)
    assert canonical_harness(None) == "loom"


def test_oz_and_loom_are_one_engine_under_two_names() -> None:
    """The independence checks compare harnesses, so a second spelling is a false negative."""
    assert canonical_harness("oz") == canonical_harness("loom")
    assert resolve_harness("oz") is resolve_harness("loom")


def test_an_unknown_harness_names_the_ones_that_exist() -> None:
    with pytest.raises(UnknownHarnessError) as caught:
        resolve_harness("warp-agent")

    assert "claude-code" in str(caught.value)
    assert set(known_harnesses()) == {"claude-code", "codex", "loom"}


def test_a_rung_may_change_the_harness_and_the_runner_not_only_the_model() -> None:
    state = RoutingState(ladder=ladder(harness="codex", runner="big"), current="local-small")
    state.record_gate_failure("tests-pass", "sig-1")
    state.record_gate_failure("tests-pass", "sig-1")

    outcome = may_escalate(state, Trigger.GATE_REPEAT)

    assert isinstance(outcome, Escalation)
    assert state.tier.harness == "codex"
    assert state.tier.runner == "big"


def test_a_rung_that_names_no_harness_leaves_the_engine_alone() -> None:
    state = RoutingState(ladder=ladder(), current="mid")

    assert state.tier.harness is None
    assert isinstance(resolve_harness(state.tier.harness), NativeHarness)
