"""The harness: pack assembly, tool grants, blast radius, calibration, and routing.

The theme running through these tests is that the harness never lets text decide
anything that matters. Grants come from configuration, escalation needs a recorded
trigger, and confidence without evidence is rewritten to zero.
"""

from __future__ import annotations

import pytest

from software_factory.definition.models import AgentRole, Effect, Ladder
from software_factory.harness import (
    BlastRadius,
    Calibration,
    CalibrationCriterion,
    Citation,
    CitationKind,
    Decomposition,
    Escalation,
    EscalationRefused,
    Example,
    Grants,
    Item,
    Origin,
    PackAssembler,
    RoutingState,
    Scaffold,
    SectionId,
    Snapshot,
    Step,
    Tool,
    ToolFailure,
    ToolRegistry,
    ToolRegistryError,
    ToolSuccess,
    Trigger,
    VerifierClass,
    calibration_error,
    estimate_tokens,
    may_escalate,
    scaffolds_for,
    starting_tier,
)
from software_factory.harness.awareness import SECTION_TITLES
from software_factory.memory.records import utc_now

# --------------------------------------------------------------------------- fixtures


def snapshot(seed: int = 0, commit: str = "abc123") -> Snapshot:
    return Snapshot(
        commit=commit,
        definition_revision="def-1",
        memory_revision="mem-1",
        ledger_seq=42,
        skill_revision="skill-1",
        assembled_at=utc_now().replace(microsecond=0),
        seed=seed,
    )


def item(content: str, ref: str = "src/importers/csv.py", **kwargs) -> Item:
    return Item(
        content=content,
        citation=Citation(kind=CitationKind.FILE, ref=ref),
        **kwargs,
    )


def assembler(role: AgentRole = AgentRole.BUILDER, budget: int = 4000) -> PackAssembler:
    return PackAssembler(role=role, budget_tokens=budget)


def fill(pack_assembler: PackAssembler, **sections: list[Item]) -> None:
    for section_id in SectionId:
        items = sections.get(section_id.value.replace("-", "_"), [])
        pack_assembler.register(section_id, lambda captured=items: (list(captured), None))


def ladder() -> Ladder:
    return Ladder.model_validate(
        {
            "tiers": [
                {
                    "name": "local-small",
                    "provider": "local",
                    "model": "small",
                    "contextWindow": 32000,
                    "workingSetCeiling": 20000,
                    "local": True,
                    "capabilities": ["code", "tools"],
                },
                {
                    "name": "mid",
                    "provider": "local",
                    "model": "mid",
                    "contextWindow": 128000,
                    "workingSetCeiling": 90000,
                    "capabilities": ["code", "tools", "reasoning"],
                },
                {
                    "name": "large",
                    "provider": "hosted",
                    "model": "large",
                    "contextWindow": 400000,
                    "workingSetCeiling": 250000,
                    "capabilities": ["code", "tools", "reasoning", "vision"],
                },
            ],
            "defaultTier": "local-small",
            "ceilingTier": "large",
            "scaffoldAtOrBelow": "local-small",
            "maxEscalations": 2,
        }
    )


def echo_tool(name: str = "repo.read", effect: Effect = Effect.READ) -> Tool:
    return Tool(
        name=name,
        description="Read a file range.",
        effect=effect,
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        output_schema={"type": "string"},
        handler=lambda args: ToolSuccess(value=f"content of {args['path']}"),
        examples=(Example(inputs={"path": "a.py"}, output="content of a.py"),),
    )


# ------------------------------------------------------------------------ pack basics


def test_the_same_snapshot_produces_the_same_digest() -> None:
    """Determinism is a property of replay, so the snapshot is what must fix the pack."""
    snap = snapshot()

    def build() -> str:
        builder = assembler()
        fill(builder, mission=[item("Fix the BOM bug.", "work-1")])
        return builder.assemble(snap).digest()

    assert build() == build()


def test_a_different_snapshot_is_distinguishable_from_the_pack() -> None:
    """The two questions are separate and the pack answers both.

    `digest()` answers "did the reader see the same text", and must not move because the
    clock did -- the snapshot carries `assembled_at`, so mixing it in made two identical
    packs assembled a microsecond apart differ while claiming content equality (N2). "Was
    this assembled from the same state" is the snapshot's own digest, which `as_dict`
    reports beside it.
    """
    builder = assembler()
    fill(builder, mission=[item("Fix the BOM bug.", "work-1")])

    first = builder.assemble(snapshot(commit="abc123"))
    second = builder.assemble(snapshot(commit="def456"))

    assert first.digest() == second.digest()
    assert first.snapshot.digest() != second.snapshot.digest()
    assert first.as_dict()["snapshot"] != second.as_dict()["snapshot"]


def test_every_item_is_cited() -> None:
    builder = assembler()
    builder.register(
        SectionId.MISSION,
        lambda: (
            [
                item("Cited.", "work-1"),
                Item(content="Uncited.", citation=Citation(kind=CitationKind.FILE, ref="")),
            ],
            None,
        ),
    )

    pack = builder.assemble(snapshot())

    mission = pack.section(SectionId.MISSION)
    assert mission is not None
    assert [i.content for i in mission.items] == ["Cited."]


def test_model_generated_content_is_labelled() -> None:
    builder = assembler()
    builder.register(
        SectionId.CONVENTIONS,
        lambda: ([item("Prefer explicit imports.", "mem-1", origin=Origin.MODEL_GENERATED)], None),
    )

    rendered = builder.assemble(snapshot()).render()

    assert "[model_generated]" in rendered


def test_a_missing_source_is_an_omission_with_a_reason() -> None:
    builder = assembler()
    builder.register(SectionId.MISSION, lambda: ([item("Do the thing.", "w-1")], None))

    pack = builder.assemble(snapshot())

    omitted = dict(pack.omissions)
    assert omitted[SectionId.TERRAIN.value] == "no source registered"


def test_a_failing_builder_degrades_one_section_not_the_pack() -> None:
    def explode() -> tuple[list[Item], str | None]:
        raise RuntimeError("index unavailable")

    builder = assembler()
    builder.register(SectionId.MISSION, lambda: ([item("Do the thing.", "w-1")], None))
    builder.register(SectionId.TERRAIN, explode)

    pack = builder.assemble(snapshot())

    assert pack.section(SectionId.MISSION) is not None
    assert any("builder failed" in reason for _, reason in pack.degradations)


def test_a_declared_degradation_is_stated_in_the_pack() -> None:
    """An agent must be able to see the shape of its own blind spot."""
    builder = assembler()
    builder.register(SectionId.HAZARDS, lambda: ([], "CI history unavailable offline"))

    pack = builder.assemble(snapshot())

    assert ("hazards", "CI history unavailable offline") in pack.degradations
    assert "CI history unavailable offline" in pack.render()


# --------------------------------------------------------------------------- budgeting


def test_over_budget_sections_drop_whole_items_and_say_how_many() -> None:
    """Half an item is worse than an absent one: a reader cannot tell what is missing."""
    builder = assembler(budget=400)
    builder.register(
        SectionId.PRECEDENT,
        lambda: ([item("x" * 200, f"run-{index}") for index in range(10)], None),
    )

    pack = builder.assemble(snapshot())

    section = pack.section(SectionId.PRECEDENT)
    assert section is not None
    assert section.truncated > 0
    assert section.tokens() <= section.budget_tokens
    assert "more available via" in section.render()


def test_mission_and_contract_are_never_dropped() -> None:
    builder = assembler(budget=10)
    builder.register(SectionId.MISSION, lambda: ([item("y" * 800, "work-1")], None))
    builder.register(SectionId.CONTRACT, lambda: ([item("z" * 800, "policy-1")], None))

    pack = builder.assemble(snapshot())

    assert pack.section(SectionId.MISSION).items
    assert pack.section(SectionId.CONTRACT).items


def test_protected_items_survive_budgeting() -> None:
    """A contradicted spec unit must reach the agent whatever the budget says."""
    builder = assembler(budget=100)
    builder.register(
        SectionId.SPEC_SLICE,
        lambda: (
            [
                item("PAY-1 is contradicted.", "PAY-1", protected=True),
                *[item("x" * 200, f"PAY-{n}") for n in range(2, 8)],
            ],
            None,
        ),
    )

    pack = builder.assemble(snapshot())

    contents = [i.content for i in pack.section(SectionId.SPEC_SLICE).items]
    assert "PAY-1 is contradicted." in contents


def test_role_weights_shift_the_budget() -> None:
    """A critic gets more spec and hazards; a scout gets more terrain."""
    from software_factory.harness.awareness import ROLE_WEIGHTS

    assert (
        ROLE_WEIGHTS[AgentRole.CRITIC][SectionId.HAZARDS]
        > (ROLE_WEIGHTS[AgentRole.ARCHITECT][SectionId.HAZARDS])
    )
    assert (
        ROLE_WEIGHTS[AgentRole.SCOUT][SectionId.TERRAIN]
        > (ROLE_WEIGHTS[AgentRole.CRITIC][SectionId.TERRAIN])
    )


def test_token_estimation_is_deterministic_and_never_zero_for_content() -> None:
    assert estimate_tokens("hello") == estimate_tokens("hello")
    assert estimate_tokens("a") >= 1
    assert estimate_tokens("") == 0


def test_every_section_declares_a_retrieval_tool() -> None:
    """The pack is the opening context, not the total: more must always be fetchable."""
    assert set(SECTION_TITLES) == set(SectionId)
    assert all(tool for _title, tool in SECTION_TITLES.values())


# ------------------------------------------------------------------------ tool registry


def test_a_tool_without_an_example_cannot_be_registered() -> None:
    registry = ToolRegistry()
    tool = Tool(
        name="repo.read",
        description="Read a file.",
        effect=Effect.READ,
        input_schema={"type": "object"},
        output_schema={"type": "string"},
        handler=lambda _args: ToolSuccess(value=""),
        examples=(),
    )

    with pytest.raises(ToolRegistryError, match="worked example"):
        registry.register(tool)


def test_a_tool_without_schemas_cannot_be_registered() -> None:
    registry = ToolRegistry()
    tool = Tool(
        name="repo.read",
        description="Read a file.",
        effect=Effect.READ,
        input_schema={},
        output_schema={},
        handler=lambda _args: ToolSuccess(value=""),
        examples=(Example(inputs={}, output=""),),
    )

    with pytest.raises(ToolRegistryError, match="schemas are required"):
        registry.register(tool)


def test_an_ungranted_tool_is_refused_and_recorded() -> None:
    """A denial the model could route around is not a control."""
    registry = ToolRegistry()
    registry.register(echo_tool())

    outcome = registry.call("repo.read", {"path": "a.py"}, grants=Grants())

    assert isinstance(outcome, ToolFailure)
    assert outcome.kind.value == "denied"
    assert registry.violations


def test_an_ungranted_effect_is_an_escalating_violation() -> None:
    """Asking for exec when only read was granted targets a capability boundary."""
    registry = ToolRegistry()
    registry.register(echo_tool("proc.run", Effect.EXEC))

    registry.call(
        "proc.run",
        {"path": "x"},
        grants=Grants(tools=frozenset({"proc.run"}), effects=frozenset({Effect.READ})),
    )

    assert registry.escalating_violations()


def test_a_granted_tool_runs() -> None:
    registry = ToolRegistry()
    registry.register(echo_tool())

    outcome = registry.call(
        "repo.read",
        {"path": "a.py"},
        grants=Grants(tools=frozenset({"repo.read"}), effects=frozenset({Effect.READ})),
    )

    assert isinstance(outcome, ToolSuccess)
    assert outcome.value == "content of a.py"


def test_a_missing_required_argument_is_a_typed_failure() -> None:
    registry = ToolRegistry()
    registry.register(echo_tool())

    outcome = registry.call("repo.read", {}, grants=Grants(tools=frozenset({"repo.read"})))

    assert isinstance(outcome, ToolFailure)
    assert outcome.kind.value == "invalid_input"
    assert "repo.read(path" in outcome.remediation


def test_an_unknown_tool_is_recorded_as_a_violation() -> None:
    registry = ToolRegistry()

    outcome = registry.call("nonexistent", {}, grants=Grants(allow_all_tools=True))

    assert isinstance(outcome, ToolFailure)
    assert registry.violations[0].reason == "unknown tool"


def test_a_raising_handler_never_crashes_the_turn_loop() -> None:
    registry = ToolRegistry()
    tool = echo_tool()
    exploding = Tool(
        name=tool.name,
        description=tool.description,
        effect=tool.effect,
        input_schema=tool.input_schema,
        output_schema=tool.output_schema,
        handler=lambda _args: (_ for _ in ()).throw(RuntimeError("boom")),
        examples=tool.examples,
    )
    registry.register(exploding)

    outcome = registry.call(
        "repo.read", {"path": "a.py"}, grants=Grants(tools=frozenset({"repo.read"}))
    )

    assert isinstance(outcome, ToolFailure)
    assert outcome.kind.value == "internal"


def test_ungranted_tools_are_not_even_listed() -> None:
    """An agent should not spend attention on doors that are locked."""
    registry = ToolRegistry()
    registry.register(echo_tool("repo.read", Effect.READ))
    registry.register(echo_tool("proc.run", Effect.EXEC))

    listed = registry.granted(
        Grants(tools=frozenset({"repo.read"}), effects=frozenset({Effect.READ}))
    )

    assert [tool.name for tool in listed] == ["repo.read"]


# ------------------------------------------------------------------------ blast radius


def test_the_courage_clause_is_derived_from_the_enforced_contract() -> None:
    """Deriving it mechanically stops it from overstating the agent's actual freedom."""
    contract = BlastRadius(
        writable_paths=("workspace/",),
        external_actions=frozenset({"source.comment"}),
    )

    clause = contract.courage_clause()

    assert "workspace/" in clause
    assert "source.comment" in clause
    assert "costs nothing" in clause


def test_without_checkpoints_the_clause_says_so_instead_of_promising_undo() -> None:
    clause = BlastRadius(checkpoints=False).courage_clause()

    assert "No checkpoints are available" in clause
    assert "costs nothing" not in clause


def test_the_clause_names_no_external_actions_when_none_are_granted() -> None:
    assert "are: none" in BlastRadius().courage_clause()


# ------------------------------------------------------------------------ calibration


def test_uncited_confidence_is_rewritten_to_zero() -> None:
    """Confidence without evidence is not confidence."""
    calibration = Calibration(
        criteria=[
            CalibrationCriterion("C1", 0.9, evidence=("tests/test_a.py::test_x",)),
            CalibrationCriterion("C2", 0.95, evidence=()),
        ]
    ).normalise()

    by_id = {c.criterion_id: c for c in calibration.criteria}
    assert by_id["C1"].confidence == 0.9
    assert by_id["C2"].confidence == 0.0
    assert calibration.rewritten == ["C2"]


def test_the_rewrite_is_recorded_so_the_claim_can_still_be_scored() -> None:
    calibration = Calibration(criteria=[CalibrationCriterion("C1", 0.99, evidence=())]).normalise()

    assert calibration.as_dict()["rewrittenForMissingEvidence"] == ["C1"]


def test_overall_confidence_is_recomputed_after_rewriting() -> None:
    calibration = Calibration(
        criteria=[
            CalibrationCriterion("C1", 1.0, evidence=("e",)),
            CalibrationCriterion("C2", 1.0, evidence=()),
        ]
    ).normalise()

    assert calibration.overall_confidence == pytest.approx(0.5)


def test_confident_and_wrong_is_penalised_harder_than_uncertain_and_wrong() -> None:
    assert calibration_error(0.95, observed_pass=False) > calibration_error(
        0.3, observed_pass=False
    )


# --------------------------------------------------------------------------- routing


def test_a_run_starts_at_the_lowest_capable_tier() -> None:
    assert starting_tier(ladder(), required=frozenset({"code"})) == "local-small"
    assert starting_tier(ladder(), required=frozenset({"reasoning"})) == "mid"
    assert starting_tier(ladder(), required=frozenset({"vision"})) == "large"


def test_escalation_requires_a_recorded_trigger() -> None:
    """'The model seems to be struggling' is not a trigger."""
    state = RoutingState(ladder=ladder(), current="local-small")

    outcome = may_escalate(state, Trigger.GATE_REPEAT)

    assert isinstance(outcome, EscalationRefused)
    assert outcome.code == "escalation.no_repeat"


def test_two_identical_gate_failures_justify_escalation() -> None:
    state = RoutingState(ladder=ladder(), current="local-small")
    state.record_gate_failure("tests-pass", "sig-1")
    state.record_gate_failure("tests-pass", "sig-1")

    outcome = may_escalate(state, Trigger.GATE_REPEAT)

    assert isinstance(outcome, Escalation)
    assert outcome.to_tier == "mid"
    assert state.current == "mid"


def test_two_different_gate_failures_do_not_justify_escalation() -> None:
    """Different failures mean progress on a hard task; the same one twice means stuck."""
    state = RoutingState(ladder=ladder(), current="local-small")
    state.record_gate_failure("tests-pass", "sig-1")
    state.record_gate_failure("build-green", "sig-2")

    assert isinstance(may_escalate(state, Trigger.GATE_REPEAT), EscalationRefused)


def test_low_confidence_must_try_retrieval_before_spending_a_tier() -> None:
    """Retrieval is cheaper than a bigger model and fixes more context problems."""
    state = RoutingState(ladder=ladder(), current="local-small")

    outcome = may_escalate(state, Trigger.LOW_CONFIDENCE, confidence=0.2)

    assert isinstance(outcome, EscalationRefused)
    assert outcome.code == "escalation.retrieval_first"


def test_low_confidence_after_retrieval_justifies_escalation() -> None:
    state = RoutingState(ladder=ladder(), current="local-small", retrieval_attempted=True)

    assert isinstance(may_escalate(state, Trigger.LOW_CONFIDENCE, confidence=0.2), Escalation)


def test_sufficient_confidence_does_not_escalate() -> None:
    state = RoutingState(ladder=ladder(), current="local-small", retrieval_attempted=True)

    outcome = may_escalate(state, Trigger.LOW_CONFIDENCE, confidence=0.9)

    assert isinstance(outcome, EscalationRefused)
    assert outcome.code == "escalation.confident_enough"


def test_an_explicit_escalation_must_state_a_checkable_reason() -> None:
    state = RoutingState(ladder=ladder(), current="local-small")

    assert isinstance(may_escalate(state, Trigger.EXPLICIT, detail="  "), EscalationRefused)
    assert isinstance(
        may_escalate(state, Trigger.EXPLICIT, detail="the change spans three services"),
        Escalation,
    )


def test_escalation_is_bounded_by_the_ladder_budget() -> None:
    state = RoutingState(ladder=ladder(), current="local-small")
    for _ in range(2):
        may_escalate(state, Trigger.EXPLICIT, detail="justified")

    outcome = may_escalate(state, Trigger.EXPLICIT, detail="justified again")

    assert isinstance(outcome, EscalationRefused)
    assert outcome.code == "escalation.budget"


def test_escalation_stops_at_the_ceiling_tier() -> None:
    rungs = ladder().model_copy(update={"ceiling_tier": "mid", "max_escalations": 5})
    state = RoutingState(ladder=rungs, current="mid")

    outcome = may_escalate(state, Trigger.EXPLICIT, detail="justified")

    assert isinstance(outcome, EscalationRefused)
    assert outcome.code == "escalation.ceiling"


def test_schema_failures_must_exhaust_the_repair_budget_first() -> None:
    state = RoutingState(ladder=ladder(), current="local-small", schema_failures=1)

    assert isinstance(may_escalate(state, Trigger.SCHEMA_REPEAT), EscalationRefused)

    state.schema_failures = 3
    assert isinstance(may_escalate(state, Trigger.SCHEMA_REPEAT), Escalation)


# ------------------------------------------------------------------------ scaffolding


def test_scaffolds_apply_at_and_below_the_threshold_tier() -> None:
    assert scaffolds_for(ladder(), "local-small") == frozenset(Scaffold)
    assert scaffolds_for(ladder(), "mid") == frozenset()


def test_a_plan_of_unverifiable_steps_is_not_reported_as_scaffolded() -> None:
    """Claiming a safety property the run does not have is worse than admitting it."""
    plan = Decomposition(
        steps=[
            Step("s1", "Read the importer.", VerifierClass.NONE),
            Step("s2", "Think about encodings.", VerifierClass.NONE),
            Step("s3", "Write the fix.", VerifierClass.DETERMINISTIC, "pytest -k bom"),
        ],
        source="self",
    )

    assert not plan.is_scaffolded
    assert len(plan.unverifiable_steps()) == 2


def test_a_mostly_checkable_plan_is_scaffolded() -> None:
    plan = Decomposition(
        steps=[
            Step("s1", "Add the failing test.", VerifierClass.DETERMINISTIC, "pytest -k bom"),
            Step("s2", "Fix strip_bom.", VerifierClass.DETERMINISTIC, "pytest -k bom"),
            Step("s3", "Tidy imports.", VerifierClass.HEURISTIC, "ruff check"),
        ],
        source="skill",
    )

    assert plan.is_scaffolded
    assert plan.verified_fraction == pytest.approx(2 / 3)


def test_decomposition_prefers_a_skill_over_the_run_itself() -> None:
    """The hardest reasoning in a run should not land on the tier least able to do it."""
    from software_factory.harness.routing import decomposition_source_preference

    order = decomposition_source_preference()

    assert order[0] == "skill"
    assert order[-1] == "self"
