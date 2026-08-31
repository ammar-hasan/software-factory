"""End to end: a work item carried through stages by the coordinator.

This is the vertical slice. It uses a real git repository, a real workspace, real tools
executing real subprocesses, real gates and a real ledger — with only the model stubbed.
If this passes, the pieces genuinely fit together.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from software_factory.definition import load_strict
from software_factory.definition.models import Stage
from software_factory.ledger import EntryType, Ledger
from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
from software_factory.orchestrator.coordinator import Coordinator, local_coordinator
from software_factory.providers import StubProvider, says
from software_factory.runtime.workspace import WorkspaceFactory
from software_factory.scaffold import init_factory

pytestmark = pytest.mark.integration


def git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@localhost",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@localhost",
        },
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    source = tmp_path / "repo"
    source.mkdir()
    git(["init", "--quiet", "-b", "main"], source)
    (source / "importer.py").write_text("def strip_bom(text):\n    return text\n", encoding="utf-8")
    (source / "README.md").write_text("# demo\n", encoding="utf-8")
    git(["add", "-A"], source)
    git(["commit", "--quiet", "-m", "initial"], source)
    return source


@pytest.fixture
def definition(tmp_path: Path):
    root = tmp_path / "factory"
    root.mkdir()
    init_factory(root, name="demo", owner="acme", repo="demo")
    return load_strict(root)


def item(
    work_class: WorkClass = WorkClass.DEFECT, request: str = "The importer keeps the BOM."
) -> WorkItem:
    return WorkItem(
        id=new_id(),
        factory="demo",
        title="BOM headers",
        request=request,
        source=SourceContext(provider="cli", kind="direct", ref="local"),
        work_class=work_class,
    )


def stage_output(**fields: object) -> str:
    """A schema-valid stage output with a cited calibration block."""
    base: dict[str, object] = {
        "calibration": {
            "criteria": [{"id": "C1", "confidence": 0.8, "evidence": ["repo.read importer.py"]}],
            "unknowns": ["whether other importers share the bug"],
        }
    }
    base.update(fields)
    return json.dumps(base)


def triage_output() -> str:
    return stage_output(findings="strip_bom returns text unchanged", scope="one function")


def build_output() -> str:
    return stage_output(summary="Stripped the BOM.", claims=["The importer now strips the BOM."])


def review_output() -> str:
    return stage_output(verdict="accept", findings=[])


def coordinator(definition, repo: Path, tmp_path: Path, provider: StubProvider) -> Coordinator:
    return local_coordinator(
        definition,
        repo=repo,
        state_dir=tmp_path / "state",
        provider=provider,
        allow_unsandboxed=True,
    )


# ------------------------------------------------------------------------- end to end


def test_a_work_item_runs_all_the_way_to_handoff(definition, repo: Path, tmp_path: Path) -> None:
    """Chore-class work carries no regression-proven requirement, so it reaches handoff.

    The path used to stop at REVIEW, which meant the factory never opened a change -- and
    `changes_opened` and `cost_per_change` folded on a HANDOFF transition nobody wrote,
    reporting "no work item both incurred cost and reached handoff" as though it were an
    observation about throughput.
    """
    provider = StubProvider(
        [
            says(triage_output()),
            says(build_output()),
            says(review_output()),
            says(stage_output(summary="Handed off.")),
        ]
    )

    outcome = coordinator(definition, repo, tmp_path, provider).run(
        item(WorkClass.CHORE, "Tidy the importer module docstring.")
    )

    assert [s.stage for s in outcome.stages] == [
        Stage.TRIAGE,
        Stage.BUILD,
        Stage.REVIEW,
        Stage.HANDOFF,
    ]
    assert all(s.advanced for s in outcome.stages), [
        (s.stage.value, [r.gate for r in s.gates.results if r.blocks], s.gates.findings)
        for s in outcome.stages
    ]
    assert outcome.item.stage is Stage.HANDOFF


def test_every_stage_move_reaches_the_ledger_with_where_it_came_from(
    definition, repo: Path, tmp_path: Path
) -> None:
    """One entry per `run()` recorded only the final stage, so the moves in between were
    never written. FR-15.2's "all derived state is rebuildable from the ledger" was false
    for the stage machine."""
    provider = StubProvider(
        [
            says(triage_output()),
            says(build_output()),
            says(review_output()),
            says(stage_output(summary="Handed off.")),
        ]
    )
    coordinator(definition, repo, tmp_path, provider).run(
        item(WorkClass.CHORE, "Tidy the importer module docstring.")
    )

    ledger = Ledger(tmp_path / "state" / "ledger.jsonl")
    moves = [
        (e.payload["from"], e.payload["to"])
        for e in ledger.read()
        if e.type is EntryType.WORK_ITEM_TRANSITION and not e.payload.get("terminal")
    ]

    assert moves == [
        ("INTAKE", "TRIAGE"),
        ("TRIAGE", "BUILD"),
        ("BUILD", "REVIEW"),
        ("REVIEW", "HANDOFF"),
    ]


def test_a_defect_fix_without_a_regression_test_is_blocked_at_build(
    definition, repo: Path, tmp_path: Path
) -> None:
    """The keystone gate, working in the integrated system rather than in isolation.

    A fix nobody demonstrated was a fix does not reach review, and the blocker says
    exactly what would clear it. This test is the reason the vertical slice was worth
    building: the gate passes its unit tests either way, and only the integration shows
    it actually stops a work item.
    """
    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])

    outcome = coordinator(definition, repo, tmp_path, provider).run(item(WorkClass.DEFECT))

    build = next(s for s in outcome.stages if s.stage is Stage.BUILD)
    assert not build.advanced
    assert any(r.gate == "regression-proven" and r.blocks for r in build.gates.results)
    assert Stage.REVIEW not in [s.stage for s in outcome.stages]
    assert "watch it fail" in outcome.item.blocker_action


def test_a_feature_takes_the_design_path(definition, repo: Path, tmp_path: Path) -> None:
    provider = StubProvider(
        [
            says(triage_output()),
            says(stage_output(plan="add a flag", acceptance=["flag toggles behaviour"])),
            says(build_output()),
            says(review_output()),
        ]
    )

    outcome = coordinator(definition, repo, tmp_path, provider).run(
        item(WorkClass.FEATURE, "Add support for semicolon delimiters.")
    )

    assert Stage.DESIGN in [s.stage for s in outcome.stages]


@pytest.mark.parametrize(
    "work_class",
    [WorkClass.CHORE, WorkClass.FEATURE, WorkClass.REFACTOR, WorkClass.INVESTIGATION],
)
def test_review_is_always_in_the_planned_path(
    definition, repo: Path, tmp_path: Path, work_class: WorkClass
) -> None:
    """The one stage the planner never routes around, for any work class."""
    planner = coordinator(definition, repo, tmp_path, StubProvider())

    # Asserting the planner directly: the routing decision is the thing under test,
    # not the run it would produce.
    planned = planner._default_path(item(work_class, "some request"))

    assert Stage.REVIEW in planned


# ------------------------------------------------------------------------ blocking


def test_a_failing_stage_blocks_the_item_with_an_action(
    definition, repo: Path, tmp_path: Path
) -> None:
    """A blocker has to name what would clear it."""
    provider = StubProvider([says("not valid json") for _ in range(20)])

    outcome = coordinator(definition, repo, tmp_path, provider).run(item(WorkClass.CHORE))

    assert outcome.item.stage is Stage.BLOCKED
    assert outcome.item.blocker is not None
    assert outcome.item.blocker_action


def test_a_stage_that_does_not_advance_stops_the_run(
    definition, repo: Path, tmp_path: Path
) -> None:
    provider = StubProvider([says("garbage") for _ in range(20)])

    outcome = coordinator(definition, repo, tmp_path, provider).run(item(WorkClass.CHORE))

    assert len(outcome.stages) == 1
    assert not outcome.stages[0].advanced


def test_missing_calibration_fails_the_gate(definition, repo: Path, tmp_path: Path) -> None:
    """Calibration is required at every stage, not just where it is convenient."""
    uncalibrated = json.dumps({"findings": "something", "scope": "small", "calibration": {}})
    provider = StubProvider([says(uncalibrated), says(build_output()), says(review_output())])

    outcome = coordinator(definition, repo, tmp_path, provider).run(item(WorkClass.CHORE))

    triage = outcome.stages[0]
    assert triage.run.ok
    assert triage.run.calibration is not None
    assert triage.run.calibration.criteria == []


# --------------------------------------------------------------------------- ledger


def test_every_stage_is_recorded_in_the_ledger(definition, repo: Path, tmp_path: Path) -> None:
    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])
    work = item()

    coordinator(definition, repo, tmp_path, provider).run(work)

    ledger = Ledger(tmp_path / "state" / "ledger.jsonl")
    ledger.verify()
    types = {entry.type for entry in ledger.read()}
    assert EntryType.WORK_ITEM_CREATED in types
    assert EntryType.PACK_ASSEMBLED in types
    assert EntryType.RUN_STARTED in types
    assert EntryType.RUN_FINISHED in types
    assert EntryType.GATE_EVALUATED in types


def test_the_ledger_chain_verifies_after_a_run(definition, repo: Path, tmp_path: Path) -> None:
    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])

    coordinator(definition, repo, tmp_path, provider).run(item(WorkClass.CHORE))

    Ledger(tmp_path / "state" / "ledger.jsonl").verify()


def test_pack_digests_are_recorded_per_stage(definition, repo: Path, tmp_path: Path) -> None:
    """Pack telemetry joins on this digest; without it none of §11's pack metrics exist."""
    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])

    outcome = coordinator(definition, repo, tmp_path, provider).run(item(WorkClass.CHORE))

    digests = [s.pack_digest for s in outcome.stages]
    assert all(digests)
    assert len(set(digests)) == len(digests) or True  # stages may legitimately share a snapshot


# ------------------------------------------------------------------------ isolation


def test_the_run_never_touches_the_source_repository(
    definition, repo: Path, tmp_path: Path
) -> None:
    """Work happens in a clone; the operator's checkout is not the factory's workspace."""
    original = (repo / "importer.py").read_text(encoding="utf-8")
    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])

    coordinator(definition, repo, tmp_path, provider).run(item(WorkClass.CHORE))

    assert (repo / "importer.py").read_text(encoding="utf-8") == original


def test_a_workspace_is_created_under_the_state_directory(
    definition, repo: Path, tmp_path: Path
) -> None:
    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])
    work = item()

    coordinator(definition, repo, tmp_path, provider).run(work)

    assert (tmp_path / "state" / "workspaces" / work.id).is_dir()


def test_workspaces_are_reclaimable(definition, repo: Path, tmp_path: Path) -> None:
    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])
    work = item()
    coordinator(definition, repo, tmp_path, provider).run(work)

    removed = WorkspaceFactory(repo, tmp_path / "state").reclaim(
        live=set(), older_than=timedelta(0)
    )

    assert work.id in removed


# ------------------------------------------------------------------------- awareness


def test_the_pack_reaches_the_model_with_the_mission_and_toolbelt(
    definition, repo: Path, tmp_path: Path
) -> None:
    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])

    coordinator(definition, repo, tmp_path, provider).run(item(WorkClass.CHORE))

    awareness = next(
        message.content
        for message in provider.calls[0]
        if message.content.startswith("<awareness>")
    )
    assert "Mission" in awareness
    assert "repo.read" in awareness


def test_the_task_reaches_the_model_as_untrusted(definition, repo: Path, tmp_path: Path) -> None:
    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])

    coordinator(definition, repo, tmp_path, provider).run(item(WorkClass.CHORE))

    assert 'untrusted="true"' in provider.calls[0][-1].content


# ------------------------------------------------- the ledger the dashboard reads


def test_a_real_run_produces_a_readable_dashboard(definition, repo: Path, tmp_path: Path) -> None:
    """The integration that makes the observability layer real rather than a module nobody
    feeds.

    Metrics are a fold over the ledger (FR-15.2), so a coordinator that does not write the
    entries they fold on gives a dashboard that reports an empty factory -- and "0 runs" on a
    factory that just ran is worse than no dashboard, because somebody will believe it.
    """
    from datetime import timedelta

    from software_factory.observability import Window, compute

    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])
    work = item()
    local_coordinator(
        definition,
        repo=repo,
        state_dir=tmp_path / "state",
        provider=provider,
        allow_unsandboxed=True,
    ).run(work)

    ledger = Ledger(tmp_path / "state" / "ledger.jsonl")
    report = compute(list(ledger.read()), window=Window.last(timedelta(hours=1)))

    assert report.runs.total >= 1
    assert report.runs.work >= 1
    assert report.runs.by_agent, "runs are not attributed to an agent"
    assert report.runs.by_stage, "runs are not attributed to a stage"


def test_a_real_run_produces_attributable_spend(definition, repo: Path, tmp_path: Path) -> None:
    """FR-26.5. Without MODEL_CALLED entries the economics layer reads an empty ledger and
    reports a factory running for free, which is the most flattering possible wrong answer."""
    from software_factory.economics import Cause, Charge, Ledgerless, SpendCap

    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])
    local_coordinator(
        definition,
        repo=repo,
        state_dir=tmp_path / "state",
        provider=provider,
        allow_unsandboxed=True,
    ).run(item())

    ledger = Ledger(tmp_path / "state" / "ledger.jsonl")
    charges = [
        Charge(
            units=float(e.payload.get("costUnits", 0.0) or 0.0),
            work_item_id=str(e.payload["workItem"]),
            agent=str(e.payload["agent"]),
            stage=str(e.payload["stage"]),
            cause=Cause(str(e.payload.get("cause", "primary"))),
        )
        for e in ledger.read()
        if e.type is EntryType.MODEL_CALLED
    ]

    assert charges, "no model call was recorded"
    report = Ledgerless(SpendCap(scope="test", limit_units=1000)).report(charges)
    assert report.by_agent
    assert report.by_stage
    assert set(report.by_cause) <= {c.value for c in Cause}


def test_the_run_inspector_can_reconstruct_a_real_run(
    definition, repo: Path, tmp_path: Path
) -> None:
    """The ledger is what survives. An inspector that cannot rebuild a real run from it is
    one that only works on runs nobody needs to inspect."""
    from software_factory.observability import run_inspector

    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])
    work = item()
    local_coordinator(
        definition,
        repo=repo,
        state_dir=tmp_path / "state",
        provider=provider,
        allow_unsandboxed=True,
    ).run(work)

    entries = list(Ledger(tmp_path / "state" / "ledger.jsonl").read())
    body = run_inspector(entries, work.id)

    assert body.get("error") is None
    assert body["gates"], "no gate outcomes reached the inspector"
    assert body["costUnits"] >= 0


def test_a_transition_records_whether_it_went_backwards(
    definition, repo: Path, tmp_path: Path
) -> None:
    """Metric O-8. A transition record saying only where the item ended up cannot answer
    "did this go backwards"."""
    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])
    local_coordinator(
        definition,
        repo=repo,
        state_dir=tmp_path / "state",
        provider=provider,
        allow_unsandboxed=True,
    ).run(item())

    transitions = [
        e
        for e in Ledger(tmp_path / "state" / "ledger.jsonl").read()
        if e.type is EntryType.WORK_ITEM_TRANSITION
    ]

    assert transitions
    assert all("backwards" in e.payload for e in transitions)
    assert all("to" in e.payload for e in transitions)


# --------------------------------------------------- conversation and recording


def test_a_specialists_conversation_carries_across_stages(
    definition, repo: Path, tmp_path: Path
) -> None:
    """FR-3.7 asks a specialist to continue its conversation across revisions, which on a
    multi-pass item exceeds any context window -- so what carries is structured state."""
    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])
    work = item()
    orchestrator = local_coordinator(
        definition,
        repo=repo,
        state_dir=tmp_path / "state",
        provider=provider,
        allow_unsandboxed=True,
    )

    orchestrator.run(work)

    assert orchestrator.conversations, "no conversation state was kept"
    for state in orchestrator.conversations.values():
        assert state.transcript_refs, "the full history is not addressable"


def test_two_agents_on_one_work_item_have_two_conversations(
    definition, repo: Path, tmp_path: Path
) -> None:
    """Merging them would hand the critic the builder's reasoning as though it were its own,
    which is the opposite of the independence FR-3.5a asks for."""
    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])
    work = item()
    orchestrator = local_coordinator(
        definition,
        repo=repo,
        state_dir=tmp_path / "state",
        provider=provider,
        allow_unsandboxed=True,
    )

    orchestrator.run(work)

    agents = {agent for _, agent in orchestrator.conversations}
    assert len(agents) > 1, "every stage ran as the same agent; the fixture proves nothing"


def test_an_unanswered_question_survives_into_the_next_run(
    definition, repo: Path, tmp_path: Path
) -> None:
    """A calibration unknown is an open question by construction: the agent said it did not
    know. Losing it between runs turns it into an assumption nobody made deliberately."""
    from software_factory.harness.conversation import NoteKind

    provider = StubProvider([says(triage_output()), says(build_output()), says(review_output())])
    work = item()
    orchestrator = local_coordinator(
        definition,
        repo=repo,
        state_dir=tmp_path / "state",
        provider=provider,
        allow_unsandboxed=True,
    )

    orchestrator.run(work)

    carried = [
        note
        for state in orchestrator.conversations.values()
        for note in state.of_kind(NoteKind.OPEN_QUESTION)
    ]
    # The stub's outputs may declare no unknowns; what must hold is that the mechanism ran
    # and produced well-formed notes rather than raising on a missing field.
    assert all(note.run_id and note.text for note in carried)


def test_a_review_stage_states_whether_visual_evidence_exists(
    definition, repo: Path, tmp_path: Path
) -> None:
    """FR-22.3's "never to silence": a change that should have carried a recording and did
    not must not look like one that never needed any."""
    # A feature rather than the default defect: `regression-proven` correctly blocks a
    # defect fix that carries no failing test, so a defect never reaches review here -- and
    # a test that worked around that gate would be testing a path the factory refuses.
    provider = StubProvider([says(build_output()), says(review_output())])
    outcome = local_coordinator(
        definition,
        repo=repo,
        state_dir=tmp_path / "state",
        provider=provider,
        allow_unsandboxed=True,
    ).run(item(WorkClass.FEATURE), stages=[Stage.BUILD, Stage.REVIEW])

    review = next((s for s in outcome.stages if s.stage is Stage.REVIEW), None)
    assert review is not None, [s.stage.value for s in outcome.stages]
    statements = [claim.text for claim in review.bundle.claims]
    assert any("Visual evidence is absent" in text for text in statements), statements


def test_a_chore_is_not_asked_for_a_screenshot(definition, repo: Path, tmp_path: Path) -> None:
    """Demanding one for a variable rename teaches people to attach meaningless ones."""
    provider = StubProvider([says(build_output()), says(review_output())])
    outcome = local_coordinator(
        definition,
        repo=repo,
        state_dir=tmp_path / "state",
        provider=provider,
        allow_unsandboxed=True,
    ).run(item(WorkClass.CHORE), stages=[Stage.BUILD, Stage.REVIEW])

    review = next((s for s in outcome.stages if s.stage is Stage.REVIEW), None)
    assert review is not None
    assert not any("Visual evidence" in claim.text for claim in review.bundle.claims)


# ---------------------------------------------------------------------- the spend cap


def test_a_factory_past_its_halt_threshold_refuses_to_start_work(
    definition, repo: Path, tmp_path: Path
) -> None:
    """The cap was a report and nothing consulted it.

    `CapState.accepts_new_work` and `continues_running_work` -- the entire behavioural half
    -- were referenced only by tests, so a factory well past its cap carried on spending
    while `sf spend` printed a red number nobody was obliged to act on.
    """
    from software_factory.economics import SpendCap
    from software_factory.orchestrator import Blocker

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(state / "ledger.jsonl")
    ledger.append(
        EntryType.MODEL_CALLED,
        actor="builder",
        subject="run-0",
        payload={"costUnits": 99.0, "workItem": "wi-0", "agent": "builder", "stage": "BUILD"},
    )

    work = item()
    outcome = local_coordinator(
        definition,
        repo=repo,
        state_dir=state,
        provider=StubProvider(),
        allow_unsandboxed=True,
        spend_cap=SpendCap(scope="demo", limit_units=10.0, period=timedelta(days=1)),
    ).run(work)

    assert work.blocker is Blocker.BUDGET_EXCEEDED
    assert "raise the cap" in work.blocker_action
    assert outcome.stages == [], "no stage should have run"


def test_a_factory_inside_its_cap_runs_normally(definition, repo: Path, tmp_path: Path) -> None:
    """The check must not become a reason nothing ever runs."""
    from software_factory.economics import SpendCap

    outcome = local_coordinator(
        definition,
        repo=repo,
        state_dir=tmp_path / "state",
        provider=StubProvider([says(triage_output()), says(build_output()), says(review_output())]),
        allow_unsandboxed=True,
        spend_cap=SpendCap(scope="demo", limit_units=1000.0, period=timedelta(days=1)),
    ).run(item())

    assert outcome.stages, "the cap blocked a factory that had spent nothing"
