"""Multi-agent orchestration patterns.

A factory that runs one agent at a time through one linear path does one thing well and
nothing else. These are the shapes a fleet is actually put to work in.

They are one engine and five constructors, which is the design claim worth testing: a cycle
check written once has to hold for a supervisor as well as for a hand-written graph, and the
tests below check the general engine and then that each named shape inherits it.

What the tests concentrate on is not that the shapes are built correctly — that is a
constructor and it either works or it visibly does not. It is the four places an
orchestrator silently returns the wrong answer: a default join rule, a race dressed up as a
selection, a skip counted as a failure, and a cycle that turns into a hang.
"""

from __future__ import annotations

import pytest

from software_factory.errors import FactoryError
from software_factory.orchestrator.patterns import (
    Execution,
    Join,
    Outcome,
    Plan,
    Step,
    StepState,
    critic,
    dag,
    fan_out,
    supervisor,
    swarm,
)


def wins(*names: str) -> dict[str, Outcome]:
    return {n: Outcome(step=n, state=StepState.SUCCEEDED) for n in names}


def scripted(results: dict[str, Outcome]) -> Execution:
    """A runner that answers from a table, and refuses a step it was not told about.

    Refusing rather than defaulting: a test whose runner invents a success for an unexpected
    step passes when the engine runs the wrong thing, which is the one behaviour these
    tests exist to catch.
    """

    def run(step: Step) -> Outcome:
        if step.name not in results:
            raise AssertionError(f"engine ran an unexpected step: {step.name}")
        return results[step.name]

    return Execution(runner=run)


def fails(name: str, detail: str = "boom") -> Outcome:
    return Outcome(step=name, state=StepState.FAILED, detail=detail)


# --------------------------------------------------------------------------------------
# The graph
# --------------------------------------------------------------------------------------


def test_steps_run_in_dependency_order(tmp_path=None) -> None:
    plan = dag(
        "p",
        [
            Step(name="c", request="third", needs=("b",)),
            Step(name="a", request="first"),
            Step(name="b", request="second", needs=("a",)),
        ],
        join=Join.ALL,
    )

    assert [[s.name for s in wave] for s in [plan.order()] for wave in s] == [["a"], ["b"], ["c"]]


def test_independent_steps_share_a_wave(tmp_path=None) -> None:
    """The grouping is the parallelism. A caller that wants a fan-out concurrent should not
    have to recompute which steps may go together."""
    plan = fan_out("p", ["one", "two", "three"], join=Join.ALL)

    assert [len(wave) for wave in plan.order()] == [3]
    assert plan.width == 3


def test_a_cycle_is_refused_when_the_plan_is_built(tmp_path=None) -> None:
    """Not discovered by a runtime timeout.

    A plan that hangs gives an operator a stuck fleet and nothing to read; a plan that
    refuses gives them the two step names pointing at each other.
    """
    with pytest.raises(FactoryError) as caught:
        dag(
            "p",
            [
                Step(name="a", request="x", needs=("b",)),
                Step(name="b", request="y", needs=("a",)),
            ],
            join=Join.ALL,
        )

    assert "cycle" in str(caught.value)
    assert "a" in str(caught.value) and "b" in str(caught.value)


def test_a_step_depending_on_itself_is_refused(tmp_path=None) -> None:
    with pytest.raises(FactoryError):
        dag("p", [Step(name="a", request="x", needs=("a",))], join=Join.ALL)


def test_a_dependency_on_a_step_that_does_not_exist_is_refused(tmp_path=None) -> None:
    """It would wait forever, which is a hang with a typo behind it."""
    with pytest.raises(FactoryError) as caught:
        dag("p", [Step(name="a", request="x", needs=("ghost",))], join=Join.ALL)

    assert "ghost" in str(caught.value)


def test_two_steps_with_one_name_are_refused(tmp_path=None) -> None:
    with pytest.raises(FactoryError):
        dag(
            "p",
            [Step(name="a", request="x"), Step(name="a", request="y")],
            join=Join.ALL,
        )


def test_an_empty_plan_is_refused(tmp_path=None) -> None:
    """It would succeed without doing anything, which is the most flattering wrong answer."""
    with pytest.raises(FactoryError):
        dag("p", [], join=Join.ALL)


def test_a_step_with_no_request_is_refused(tmp_path=None) -> None:
    with pytest.raises(FactoryError):
        Step(name="a", request="   ")


def test_an_oversized_plan_is_refused(tmp_path=None) -> None:
    """A supervisor that emits ten thousand steps has misunderstood the task in a way that
    should stop rather than bill."""
    with pytest.raises(FactoryError):
        fan_out("p", [f"do {i}" for i in range(500)], join=Join.ALL)


# --------------------------------------------------------------------------------------
# Join rules
# --------------------------------------------------------------------------------------


def test_all_is_satisfied_only_when_every_step_succeeds() -> None:
    plan = fan_out("p", ["one", "two"], join=Join.ALL)

    result = scripted(wins("p-1", "p-2")).run(plan)

    assert result.satisfied


def test_all_is_not_satisfied_by_one_failure() -> None:
    plan = fan_out("p", ["one", "two"], join=Join.ALL)
    outcomes = {**wins("p-1"), "p-2": fails("p-2")}

    result = Execution(runner=lambda s: outcomes[s.name], stop_early=False).run(plan)

    assert not result.satisfied
    assert "p-2" in result.reason


def test_any_is_satisfied_by_one_success() -> None:
    plan = fan_out("p", ["one", "two"], join=Join.ANY)
    outcomes = {"p-1": fails("p-1"), **wins("p-2")}

    result = Execution(runner=lambda s: outcomes[s.name], stop_early=False).run(plan)

    assert result.satisfied
    assert result.chosen is not None and result.chosen.step == "p-2"


def test_a_quorum_counts(tmp_path=None) -> None:
    plan = fan_out("p", ["a", "b", "c"], join=Join.QUORUM, quorum=2)
    outcomes = {**wins("p-1", "p-2"), "p-3": fails("p-3")}

    result = Execution(runner=lambda s: outcomes[s.name], stop_early=False).run(plan)

    assert result.satisfied


def test_a_quorum_that_is_not_met_says_how_short_it_fell() -> None:
    plan = fan_out("p", ["a", "b", "c"], join=Join.QUORUM, quorum=3)
    outcomes = {**wins("p-1"), "p-2": fails("p-2"), "p-3": fails("p-3")}

    result = Execution(runner=lambda s: outcomes[s.name], stop_early=False).run(plan)

    assert not result.satisfied
    assert "1 of 3" in result.reason


def test_a_quorum_of_zero_is_refused() -> None:
    """It is satisfied by a plan where every step failed."""
    with pytest.raises(FactoryError):
        fan_out("p", ["a", "b"], join=Join.QUORUM, quorum=0)


def test_a_quorum_larger_than_the_plan_is_refused() -> None:
    """It can never be met, so the plan is guaranteed to fail after paying for every step."""
    with pytest.raises(FactoryError):
        fan_out("p", ["a", "b"], join=Join.QUORUM, quorum=3)


def test_a_join_rule_must_be_named() -> None:
    """There is no default, and this is the test that says so.

    A fan-in that quietly accepts the first success turns a five-way check into a one-way
    one, and the four steps that disagreed were paid for and discarded.
    """
    with pytest.raises(TypeError):
        fan_out("p", ["a", "b"])  # type: ignore[call-arg]


# --------------------------------------------------------------------------------------
# Skipped is not failed
# --------------------------------------------------------------------------------------


def test_a_step_whose_dependency_failed_is_skipped_not_failed() -> None:
    """ "We tried and it did not work" and "we never found out" lead to different actions."""
    plan = dag(
        "p",
        [Step(name="a", request="x"), Step(name="b", request="y", needs=("a",))],
        join=Join.ALL,
    )
    outcomes = {"a": fails("a")}

    result = Execution(runner=lambda s: outcomes[s.name], stop_early=False).run(plan)

    states = {o.step: o.state for o in result.outcomes}
    assert states == {"a": StepState.FAILED, "b": StepState.SKIPPED}


def test_a_skipped_step_says_what_it_was_waiting_for() -> None:
    plan = dag(
        "p",
        [Step(name="a", request="x"), Step(name="b", request="y", needs=("a",))],
        join=Join.ALL,
    )
    outcomes = {"a": fails("a")}

    result = Execution(runner=lambda s: outcomes[s.name], stop_early=False).run(plan)

    skipped = next(o for o in result.outcomes if o.step == "b")
    assert "a" in skipped.detail


def test_a_plan_with_skips_reports_unknown_rather_than_rejected() -> None:
    """A plan whose steps were abandoned has not been tested and found wanting."""
    plan = dag(
        "p",
        [Step(name="a", request="x"), Step(name="b", request="y", needs=("a",))],
        join=Join.ALL,
    )
    outcomes = {"a": fails("a")}

    result = Execution(runner=lambda s: outcomes[s.name], stop_early=False).run(plan)

    assert len(result.skipped) == 1
    assert len(result.failed) == 1


# --------------------------------------------------------------------------------------
# Stopping early
# --------------------------------------------------------------------------------------


def test_any_stops_once_it_has_a_success() -> None:
    """The remaining steps cost money to confirm a decision already made."""
    plan = fan_out("p", ["a", "b", "c"], join=Join.ANY)
    ran: list[str] = []

    def run(step: Step) -> Outcome:
        ran.append(step.name)
        return Outcome(step=step.name, state=StepState.SUCCEEDED)

    result = Execution(runner=run).run(plan)

    assert ran == ["p-1"]
    assert result.satisfied


def test_the_steps_stopping_early_saves_are_recorded_as_skipped() -> None:
    """So the saving is visible, rather than looking like a plan that was smaller."""
    plan = fan_out("p", ["a", "b", "c"], join=Join.ANY)

    result = Execution(runner=lambda s: Outcome(step=s.name, state=StepState.SUCCEEDED)).run(plan)

    assert [o.state for o in result.outcomes] == [
        StepState.SUCCEEDED,
        StepState.SKIPPED,
        StepState.SKIPPED,
    ]


def test_all_stops_at_the_first_failure() -> None:
    plan = fan_out("p", ["a", "b", "c"], join=Join.ALL)
    ran: list[str] = []

    def run(step: Step) -> Outcome:
        ran.append(step.name)
        return Outcome(step=step.name, state=StepState.FAILED)

    Execution(runner=run).run(plan)

    assert ran == ["p-1"]


def test_a_quorum_stops_once_it_cannot_be_met() -> None:
    """Continuing would pay for every remaining step to confirm a failure already certain."""
    plan = fan_out("p", ["a", "b", "c"], join=Join.QUORUM, quorum=3)
    ran: list[str] = []

    def run(step: Step) -> Outcome:
        ran.append(step.name)
        return Outcome(step=step.name, state=StepState.FAILED)

    result = Execution(runner=run).run(plan)

    assert ran == ["p-1"]
    assert not result.satisfied


def test_a_swarm_never_stops_early() -> None:
    """The best answer cannot be known before the last one arrives."""
    plan = swarm("p", "solve it", attempts=3)
    ran: list[str] = []

    def run(step: Step) -> Outcome:
        ran.append(step.name)
        return Outcome(step=step.name, state=StepState.SUCCEEDED, score=1.0)

    Execution(runner=run).run(plan)

    assert len(ran) == 3


# --------------------------------------------------------------------------------------
# Swarm: scored, never raced
# --------------------------------------------------------------------------------------


def test_a_swarm_picks_the_highest_score() -> None:
    plan = swarm("p", "solve it", attempts=3)
    scores = {"p-attempt-1": 0.4, "p-attempt-2": 0.9, "p-attempt-3": 0.7}

    result = Execution(
        runner=lambda s: Outcome(step=s.name, state=StepState.SUCCEEDED, score=scores[s.name])
    ).run(plan)

    assert result.chosen is not None
    assert result.chosen.step == "p-attempt-2"


def test_an_unscored_swarm_is_not_satisfied_by_picking_the_first() -> None:
    """The single most important behaviour here.

    Every attempt may have succeeded and the swarm still has no answer, because nothing
    ranked them. Picking the first is the race this pattern exists to avoid: first-past-the-
    post selects for speed, and the fastest answer is the one that did the least work.
    """
    plan = swarm("p", "solve it", attempts=3)

    result = Execution(runner=lambda s: Outcome(step=s.name, state=StepState.SUCCEEDED)).run(plan)

    assert not result.satisfied
    assert result.chosen is None
    assert "score" in result.reason


def test_a_swarm_ignores_the_scores_of_failed_attempts() -> None:
    """A high score on a step that failed is a scorer reading a broken result."""
    plan = swarm("p", "solve it", attempts=2)
    outcomes = {
        "p-attempt-1": Outcome(step="p-attempt-1", state=StepState.FAILED, score=9.9),
        "p-attempt-2": Outcome(step="p-attempt-2", state=StepState.SUCCEEDED, score=0.1),
    }

    result = Execution(runner=lambda s: outcomes[s.name]).run(plan)

    assert result.chosen is not None and result.chosen.step == "p-attempt-2"


def test_a_swarm_of_one_is_refused() -> None:
    with pytest.raises(FactoryError):
        swarm("p", "solve it", attempts=1)


def test_a_partial_agent_list_is_refused() -> None:
    """It silently gives the remaining attempts the default agent, which is not a swarm of
    the agents that were listed."""
    with pytest.raises(FactoryError):
        swarm("p", "solve it", attempts=3, agents=["a", "b"])


def test_a_swarm_gives_each_attempt_its_named_agent() -> None:
    plan = swarm("p", "solve it", attempts=2, agents=["fast", "careful"])

    assert [step.agent for step in plan.steps] == ["fast", "careful"]


# --------------------------------------------------------------------------------------
# Critic
# --------------------------------------------------------------------------------------


def test_a_critic_runs_after_the_producer() -> None:
    plan = critic("p", produce="write it", review="check it")

    assert [[s.name for s in wave] for wave in plan.order()] == [["p-produce"], ["p-review"]]


def test_an_agent_cannot_review_its_own_work() -> None:
    """Self-review is a rubber stamp with a latency cost, and the easiest possible way to
    build a review step that never rejects anything."""
    with pytest.raises(FactoryError):
        critic("p", produce="write", review="check", producer="ana", reviewer="ana")


def test_a_critic_that_rejects_makes_the_plan_unsatisfied() -> None:
    plan = critic("p", produce="write it", review="check it")
    outcomes = {**wins("p-produce"), "p-review": fails("p-review", "the claim is unsupported")}

    result = Execution(runner=lambda s: outcomes[s.name], stop_early=False).run(plan)

    assert not result.satisfied


def test_a_critic_never_runs_when_production_failed() -> None:
    """Reviewing work that was never produced spends a run to conclude nothing."""
    plan = critic("p", produce="write it", review="check it")
    ran: list[str] = []

    def run(step: Step) -> Outcome:
        ran.append(step.name)
        return Outcome(step=step.name, state=StepState.FAILED)

    Execution(runner=run, stop_early=False).run(plan)

    assert ran == ["p-produce"]


# --------------------------------------------------------------------------------------
# Supervisor
# --------------------------------------------------------------------------------------


def test_workers_wait_for_the_plan() -> None:
    plan = supervisor("p", plan_request="split it", worker_requests=["one", "two"], join=Join.ALL)

    waves = [[s.name for s in wave] for wave in plan.order()]
    assert waves == [["p-plan"], ["p-worker-1", "p-worker-2"]]


def test_a_supervisor_with_no_workers_is_refused() -> None:
    """One run with an extra hop."""
    with pytest.raises(FactoryError):
        supervisor("p", plan_request="x", worker_requests=[], join=Join.ALL)


def test_every_worker_is_skipped_when_planning_fails() -> None:
    plan = supervisor("p", plan_request="split it", worker_requests=["one", "two"], join=Join.ALL)
    outcomes = {"p-plan": fails("p-plan")}

    result = Execution(runner=lambda s: outcomes[s.name], stop_early=False).run(plan)

    assert len(result.skipped) == 2
    assert len(result.failed) == 1


# --------------------------------------------------------------------------------------
# One engine, five shapes
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plan",
    [
        fan_out("f", ["a", "b"], join=Join.ALL),
        swarm("s", "solve", attempts=2),
        critic("c", produce="write", review="check"),
        supervisor("v", plan_request="x", worker_requests=["a"], join=Join.ALL),
        dag("d", [Step(name="only", request="x")], join=Join.ALL),
    ],
)
def test_every_named_pattern_is_a_validated_plan(plan: Plan) -> None:
    """The design claim: five constructors over one engine, not five engines.

    If a shape bypassed the engine it would also bypass the cycle check, the name check and
    the join rules — so a fix in one place would leave the other four wrong.
    """
    assert plan.order()
    assert plan.width >= 1
    assert plan.as_dict()["steps"]


def test_the_plan_carries_its_shape_for_the_record() -> None:
    """A fan-out of twenty otherwise appears in the ledger as twenty unrelated runs, and the
    cost of the whole operation is unattributable."""
    assert swarm("s", "solve", attempts=2).as_dict()["pattern"] == "swarm"
    assert critic("c", produce="w", review="r").as_dict()["pattern"] == "critic"


def test_results_come_back_in_declared_order_not_completion_order() -> None:
    """So a re-run of the same plan produces the same joined output.

    Completion order is a property of how busy the machines were, and a result assembled in
    it cannot be compared against yesterday's.
    """
    plan = fan_out("p", ["a", "b", "c"], join=Join.QUORUM, quorum=1)

    result = Execution(
        runner=lambda s: Outcome(step=s.name, state=StepState.SUCCEEDED),
        stop_early=False,
    ).run(plan)

    assert [o.step for o in result.outcomes] == ["p-1", "p-2", "p-3"]


# --------------------------------------------------------------------------------------
# Through a real coordinator
# --------------------------------------------------------------------------------------


def test_a_plan_runs_every_step_as_a_real_work_item(tmp_path) -> None:
    """A step becomes a work item because that is what the rest of the factory understands.

    A plan that invented a lighter unit of work would have a second kind of run that gates,
    spend accounting and the ledger do not apply to — and the first thing anyone would ask
    of a fan-out is what it cost.
    """
    from software_factory.orchestrator.patterns import fan_out

    coord, provider = _real_coordinator(tmp_path, steps=2)

    result = coord.run_plan(fan_out("triage", ["look at a", "look at b"], join=Join.ALL))

    # Both *succeeded*, not merely both present. `len(outcomes) == 2` counts skips, so the
    # first version of this passed while one step failed and `ALL` correctly skipped the
    # other -- which is indistinguishable from a plan that ran one step.
    assert [o.state for o in result.outcomes] == [StepState.SUCCEEDED, StepState.SUCCEEDED], [
        (o.step, o.state.value, o.detail) for o in result.outcomes
    ]
    assert result.satisfied
    assert provider.calls != []


def test_a_plan_is_one_ledger_entry_joining_its_runs(tmp_path) -> None:
    """A fan-out of twenty otherwise appears as twenty unrelated runs, and the cost of the
    whole operation is unattributable — which is the number somebody asks for after the
    bill arrives."""
    from software_factory.ledger import EntryType
    from software_factory.orchestrator.patterns import fan_out

    coord, _ = _real_coordinator(tmp_path, steps=2)

    coord.run_plan(fan_out("triage", ["look at a", "look at b"], join=Join.ALL))

    entries = [e for e in coord.ledger.read() if e.type is EntryType.PLAN_EXECUTED]
    assert len(entries) == 1
    payload = entries[0].payload
    assert payload["plan"]["pattern"] == "fan-out"
    assert sorted(payload["workItems"]) == ["triage-1", "triage-2"]


def test_the_join_reaches_every_work_item_the_plan_created(tmp_path) -> None:
    """Both directions: given a plan find its runs, given a run find what it was part of."""
    from software_factory.ledger import EntryType
    from software_factory.orchestrator.patterns import fan_out

    coord, _ = _real_coordinator(tmp_path, steps=2)
    coord.run_plan(fan_out("triage", ["look at a", "look at b"], join=Join.ALL))

    entries = list(coord.ledger.read())
    plan_entry = next(e for e in entries if e.type is EntryType.PLAN_EXECUTED)
    created = {e.subject for e in entries if e.type is EntryType.WORK_ITEM_CREATED}

    assert set(plan_entry.payload["workItems"].values()) <= created


def test_a_step_is_scored_from_its_gates_not_its_own_opinion(tmp_path) -> None:
    """Self-reported scores turn a swarm into a confidence contest, and the least careful
    attempt is usually the most confident one."""
    from software_factory.orchestrator.patterns import swarm

    coord, _ = _real_coordinator(tmp_path, steps=2)

    result = coord.run_plan(swarm("s", "fix the importer", attempts=2))

    scored = [o for o in result.outcomes if o.score is not None]
    assert scored, "no step was scored, so a swarm could not choose between them"
    assert all(0.0 <= (o.score or 0) <= 1.0 for o in scored)


def _real_coordinator(tmp_path, *, steps: int):
    """A real factory, real git repository, real gates -- only the model stubbed.

    Per-stage payloads rather than one shape reused. A single payload passed the schema for
    triage and failed it at build, so the first version of these tests fanned out to two
    steps, watched the first fail, and had `ALL` correctly skip the second -- which looked
    exactly like a plan that only ran one step.
    """
    import json
    import subprocess

    from software_factory.definition import load_strict
    from software_factory.orchestrator.coordinator import local_coordinator
    from software_factory.providers import StubProvider, says
    from software_factory.scaffold import init_factory

    root = tmp_path / "factory"
    root.mkdir()
    init_factory(root, name="demo", owner="acme", repo="demo")

    source = tmp_path / "repo"
    source.mkdir()
    (source / "importer.py").write_text("def strip_bom(t):\n    return t\n", encoding="utf-8")
    for command in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.test"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "initial"],
    ):
        subprocess.run(["git", *command], cwd=source, check=True, capture_output=True)

    def stage_output(**fields: object) -> str:
        base: dict[str, object] = {
            "calibration": {
                "criteria": [{"id": "C1", "confidence": 0.8, "evidence": ["repo.read x"]}],
                "unknowns": [],
            }
        }
        base.update(fields)
        return json.dumps(base)

    one_item = [
        says(stage_output(findings="strip_bom returns text unchanged", scope="one function")),
        says(stage_output(summary="Tidied it.", claims=["The docstring is accurate."])),
        says(stage_output(verdict="accept", findings=[])),
        says(stage_output(summary="Handed off.")),
    ]
    provider = StubProvider(one_item * steps)
    coordinator = local_coordinator(
        load_strict(root),
        repo=source,
        state_dir=tmp_path / "state",
        provider=provider,
        allow_unsandboxed=True,
    )
    return coordinator, provider


def test_a_tied_swarm_picks_the_first_attempt_that_scored_that_well() -> None:
    """Ties are common — a ratio over three gates has four possible values — so the rule has
    to be both deterministic and explainable. "The first one that scored this well" is;
    "the one whose name sorts last" is not, and that is what it did before this test.
    """
    plan = swarm("p", "solve it", attempts=3)
    scores = {"p-attempt-1": 0.5, "p-attempt-2": 0.5, "p-attempt-3": 0.4}

    result = Execution(
        runner=lambda s: Outcome(step=s.name, state=StepState.SUCCEEDED, score=scores[s.name])
    ).run(plan)

    assert result.chosen is not None and result.chosen.step == "p-attempt-1"
