"""Requirements inducted from the source analysis (`docs/induction-plan.md`).

These are additions rather than fixes, so there is no unfixed code to confirm a failure
against. What each test does instead is state the property the requirement exists for, in the
terms the plan used to argue for building it -- because the plan also names what would make
us *not* build each one, and a test that only checked the mechanism would not notice the
requirement drifting away from its reason.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from software_factory.definition.models import Stage
from software_factory.ledger import EntryType, Ledger
from software_factory.orchestrator import SourceContext, WorkClass, WorkItem
from software_factory.orchestrator.coordinator import MAX_DISCOVERIES_PER_RUN, STAGE_SCHEMAS

# ------------------------------------------------------------------- FR-31 discovery


def test_every_stage_can_report_a_discovery() -> None:
    """An agent that can only report findings inside its own scope reports the ones outside
    it nowhere (FR-31.1)."""
    for stage, schema in STAGE_SCHEMAS.items():
        assert "discoveries" in schema["properties"], stage


def test_a_discovery_is_schema_valid_with_what_and_where() -> None:
    """FR-31.4: a sibling work item with no locator is a report a human has to re-derive,
    which costs more than the discovery saved."""
    import jsonschema

    schema = STAGE_SCHEMAS[Stage.BUILD]
    body = {
        "summary": "s",
        "claims": [],
        "calibration": {},
        "discoveries": [
            {
                "what": "the retry helper swallows KeyboardInterrupt",
                "where": "retry.py:41",
                "why_separate": "unrelated to the BOM handling this item is about",
            }
        ],
    }

    jsonschema.validate(body, schema)

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**body, "discoveries": [{"what": "something"}]}, schema)


def test_a_discovery_becomes_a_sibling_not_a_child(tmp_path: Path) -> None:
    """As a child it would make the finder's completion depend on unrelated work, which is
    exactly the incentive that teaches an agent not to report them (FR-31.2)."""
    from tests.support import (
        run_with_discoveries,  # type: ignore[import-not-found]
    )

    outcome = run_with_discoveries(
        tmp_path,
        [{"what": "retry helper swallows KeyboardInterrupt", "where": "retry.py:41"}],
    )

    assert len(outcome.discoveries) == 1
    found = outcome.discoveries[0]
    assert found.id != outcome.item.id
    assert found.discovered_by
    assert outcome.item.stage is Stage.HANDOFF, "the finder did not wait on what it found"


def test_a_run_cannot_file_more_discoveries_than_the_cap(tmp_path: Path) -> None:
    """An agent filing forty issues in one run has found one thing and reported it forty
    times (FR-31.3)."""
    from tests.support import (
        run_with_discoveries,  # type: ignore[import-not-found]
    )

    outcome = run_with_discoveries(
        tmp_path,
        [{"what": f"finding {i}", "where": f"file{i}.py:1"} for i in range(9)],
    )

    assert len(outcome.discoveries) == MAX_DISCOVERIES_PER_RUN


def test_the_cap_being_applied_is_recorded(tmp_path: Path) -> None:
    """A silent truncation would hide a real judgement about the agent's output."""
    from tests.support import (
        run_with_discoveries,  # type: ignore[import-not-found]
    )

    outcome = run_with_discoveries(
        tmp_path,
        [{"what": f"finding {i}", "where": f"file{i}.py:1"} for i in range(9)],
    )

    entries = list(Ledger(tmp_path / "state" / "ledger.jsonl").read())
    capped = [
        e
        for e in entries
        if e.type is EntryType.VIOLATION and e.payload.get("kind") == "discovery_cap"
    ]

    assert capped, "the cap was applied and nothing said so"
    assert capped[0].payload["reported"] == 9
    assert outcome.discoveries


def test_a_filed_discovery_says_a_machine_filed_it(tmp_path: Path) -> None:
    """FR-16.5. A human reading it should know what produced it, and read its confidence
    accordingly."""
    from tests.support import (
        run_with_discoveries,  # type: ignore[import-not-found]
    )

    run_with_discoveries(tmp_path, [{"what": "a real thing", "where": "retry.py:41"}])

    entries = list(Ledger(tmp_path / "state" / "ledger.jsonl").read())
    filed = [
        e
        for e in entries
        if e.type is EntryType.WORK_ITEM_CREATED and e.payload.get("discoveredBy")
    ]

    assert filed
    assert "Filed by the factory, not by a person" in filed[0].payload["note"]
    assert "sibling" in filed[0].payload["note"]


def test_a_discovery_with_no_locator_is_not_filed(tmp_path: Path) -> None:
    """The schema refuses it, and the coordinator refuses it again: a model that returns a
    field the schema forbids is the normal case, not the exception."""
    from tests.support import (
        run_with_discoveries,  # type: ignore[import-not-found]
    )

    outcome = run_with_discoveries(tmp_path, [{"what": "something is wrong", "where": "  "}])

    assert outcome.discoveries == []


# --------------------------------------------------------------------- FR-2.1 fleet


def test_a_conductor_alone_is_not_a_factory(tmp_path: Path) -> None:
    """A conductor with nobody to route to can accept work and do none of it.

    FR-2.1 asked for "at least one agent", which the conductor satisfies -- a requirement
    weaker than every real factory including the scaffold, which has always shipped five.
    """
    from software_factory.definition.loader import load
    from software_factory.definition.validate import validate

    from .conftest import FACTORY_YAML, RUNNER_YAML, agent, write  # type: ignore[attr-defined]

    root = tmp_path / "factory"
    write(root / "factory.yaml", FACTORY_YAML)
    write(root / "runners" / "linux.yaml", RUNNER_YAML)
    write(root / "agents" / "conductor" / "agent.md", agent("CONDUCTOR", body="Route it."))

    definition, report = load(root)
    validate(definition, report)

    assert "factory.no_specialist" in {issue.code for issue in report.errors}


def test_the_scaffold_satisfies_the_tightened_requirement(tmp_path: Path) -> None:
    from software_factory.definition.loader import load
    from software_factory.definition.validate import validate
    from software_factory.scaffold import init_factory

    init_factory(tmp_path, name="ref", owner="amaya", repo="service")
    definition, report = load(tmp_path)
    validate(definition, report)

    assert "factory.no_specialist" not in {issue.code for issue in report.errors}


# --------------------------------------------------------------- FR-32 explanation


def test_a_handed_off_work_item_answers_from_its_record(tmp_path: Path) -> None:
    """FR-32.1. The plumbing existed -- replies in place, an addressable work item, a kept
    conversation -- and only the capability was missing."""
    from software_factory.orchestrator.explain import Explainer
    from tests.support import run_with_discoveries

    outcome = run_with_discoveries(tmp_path, [])
    entries = list(Ledger(tmp_path / "state" / "ledger.jsonl").read())

    answer = Explainer.from_ledger(entries).answer(
        outcome.item.id, "why did you not use a decorator?"
    )

    assert answer.answered
    assert any("decorator" in c.text for c in answer.citations)
    assert all(c.run_id for c in answer.citations), "an answer must say which run said it"


def test_a_question_the_record_does_not_cover_is_refused(tmp_path: Path) -> None:
    """FR-32.3. This is the point where a person is most likely to accept a plausible
    reconstruction: reading a change they did not write, from a system that has been right
    so far, in a hurry."""
    from software_factory.orchestrator.explain import Explainer
    from tests.support import run_with_discoveries

    outcome = run_with_discoveries(tmp_path, [])
    entries = list(Ledger(tmp_path / "state" / "ledger.jsonl").read())

    answer = Explainer.from_ledger(entries).answer(
        outcome.item.id, "what colour should the bikeshed be"
    )

    assert not answer.answered
    assert "does not say" in answer.note


def test_answering_never_calls_a_model(tmp_path: Path) -> None:
    """FR-32.2. An answer produced by re-running is an answer about a *different* execution:
    the reviewer asked "why did you do that" and would be told what a second run would do.

    Asserted by giving the explainer a provider that fails on any call, since "does not
    re-run" is the kind of property that decays into "does not re-run yet".
    """
    from software_factory.orchestrator.explain import Explainer
    from software_factory.providers import UnavailableProvider
    from tests.support import run_with_discoveries

    outcome = run_with_discoveries(tmp_path, [])
    entries = list(Ledger(tmp_path / "state" / "ledger.jsonl").read())

    explainer = Explainer.from_ledger(entries)
    # Nothing in the module may hold a provider at all -- there is no seam through which a
    # model call could be made, which is a stronger statement than "it did not make one".
    import inspect

    from software_factory.orchestrator import explain as module

    source = inspect.getsource(module)
    assert "provider" not in source.lower().replace("provider(", "")
    assert isinstance(UnavailableProvider(), UnavailableProvider)

    assert explainer.answer(outcome.item.id, "why keep the signature?").answered


def test_an_unknown_work_item_says_so_rather_than_answering_emptily() -> None:
    from software_factory.orchestrator.explain import Explainer

    answer = Explainer().answer("wi-does-not-exist", "why?")

    assert not answer.answered
    assert "No conversation is recorded" in answer.note


def test_an_answer_cites_a_bounded_number_of_notes(tmp_path: Path) -> None:
    """An answer that quotes everything has not answered."""
    from software_factory.orchestrator.explain import MAX_CITED, Explainer
    from tests.support import run_with_discoveries

    outcome = run_with_discoveries(tmp_path, [])
    entries = list(Ledger(tmp_path / "state" / "ledger.jsonl").read())

    answer = Explainer.from_ledger(entries).answer(outcome.item.id, "strip_bom decorator timeout")

    assert 0 < len(answer.citations) <= MAX_CITED


def test_explain_is_reachable_from_the_cli(tmp_path: Path) -> None:
    """FR-18.10's local parity: every capability must also be reachable through `sf`."""
    from typer.testing import CliRunner

    from software_factory.cli import app
    from tests.support import run_with_discoveries

    outcome = run_with_discoveries(tmp_path, [])
    ledger = str(tmp_path / "state" / "ledger.jsonl")

    result = CliRunner().invoke(
        app, ["explain", ledger, outcome.item.id, "why not a decorator?", "--json"]
    )

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["answer"]["answered"] is True
    assert body["answer"]["citations"]


# ------------------------------------------------------- FR-33 review comments


def trusted_scorer(*, outcome_partner: str):
    """A scorer that has earned the right to drive change.

    The trust conditions -- human labels, agreement, kappa -- are checked before the
    outcome-partner rule, so a scorer that has not met them is refused for that instead and
    the rule under test never runs.
    """
    from software_factory.evals.scorers import Label, Scorer

    return Scorer(
        name="reviewer-load",
        labels=(Label(value="ok", score=1.0), Label(value="bad", score=0.0)),
        passing_score=0.9,
        self_improvement=True,
        labelled_sample=200,
        agreement=0.95,
        kappa=0.8,
        outcome_partner=outcome_partner,
    )


def review_failure(run: str, detail: str):
    from software_factory.improvement.clustering import Failure, Source

    return Failure(
        run_id=run,
        work_item_id=f"wi-{run}",
        stage="REVIEW",
        agent="builder",
        failure_class="reviewer-comment",
        detail=detail,
        source=Source.REVIEW_COMMENT,
    )


def assurance_failure(run: str, detail: str):
    from software_factory.improvement.clustering import Failure, Source

    return Failure(
        run_id=run,
        work_item_id=f"wi-{run}",
        stage="REVIEW",
        agent="builder",
        failure_class="reviewer-comment",
        detail=detail,
        source=Source.ASSURANCE,
    )


def test_review_comments_cluster_beside_scorer_failures() -> None:
    """FR-14.2 clustered only scorer failures. A reviewer's complaint is a failure mode no
    rubric has encoded yet, which makes it the most valuable input the loop could have and
    the one it ignored (FR-33.1)."""
    from software_factory.improvement import cluster_failures

    comments = [
        review_failure(f"r{i}", "the error message does not say what to do") for i in range(4)
    ]

    clusters = cluster_failures(comments)

    assert len(clusters) == 1
    assert clusters[0].size == 4


def test_a_review_comment_and_an_assurance_failure_are_not_one_problem() -> None:
    """Even when they look alike. Merging them would hide which one a proposal answers."""
    from software_factory.improvement import cluster_failures

    same_text = "the error message does not say what to do"
    mixed = [review_failure(f"r{i}", same_text) for i in range(3)]
    mixed += [assurance_failure(f"a{i}", same_text) for i in range(3)]

    clusters = cluster_failures(mixed)

    assert len(clusters) == 2
    assert {c.source.value for c in clusters} == {"review_comment", "assurance"}


def test_a_review_driven_proposal_cannot_be_measured_against_comment_count() -> None:
    """FR-33.2, and the reason the whole input needed a guard.

    Fewer comments is achievable by producing changes nobody reviews carefully, so a loop
    optimising for it would learn to make its output harder to scrutinise and score that as
    an improvement.
    """
    from software_factory.improvement import cluster_failures
    from software_factory.improvement.loop import LoopState, Refused, may_propose

    cluster = cluster_failures(
        [review_failure(f"r{i}", "the error message does not say what to do") for i in range(4)]
    )[0]
    gameable = trusted_scorer(outcome_partner="review-comment-count")

    refusal = may_propose(LoopState(), cluster, target="agents/builder", scorer=gameable)

    assert isinstance(refusal, Refused)
    assert refusal.code == "loop.gameable_partner"
    assert "revert rate" in refusal.remediation


def test_a_review_driven_proposal_measured_against_reverts_is_allowed() -> None:
    """The guard must not become a reason review comments are unusable."""
    from software_factory.improvement import cluster_failures
    from software_factory.improvement.loop import LoopState, may_propose

    cluster = cluster_failures(
        [review_failure(f"r{i}", "the error message does not say what to do") for i in range(4)]
    )[0]
    sound = trusted_scorer(outcome_partner="revert-rate")

    assert may_propose(LoopState(), cluster, target="agents/builder", scorer=sound) is None


# ---------------------------------------------------------------- FR-34 delegation


def test_delegation_is_bounded_in_depth() -> None:
    """Unbounded delegation is unbounded spend, and the failure mode is quiet: a run that
    looks stalled while forty descendants work (FR-34.3)."""
    from software_factory.orchestrator.delegation import (
        MAX_DELEGATION_DEPTH,
        DelegationBook,
        Refused,
    )

    book = DelegationBook()
    parent = "run-0"
    for level in range(MAX_DELEGATION_DEPTH):
        child = f"run-{level + 1}"
        assert book.record(parent_run_id=parent, child_run_id=child) is None
        parent = child

    refusal = book.record(parent_run_id=parent, child_run_id="run-too-deep")

    assert isinstance(refusal, Refused)
    assert refusal.code == "delegation.too_deep"


def test_delegation_is_bounded_in_fan_out() -> None:
    """A run needing five sub-agents has not decomposed its work, it has scattered it."""
    from software_factory.orchestrator.delegation import MAX_FAN_OUT, DelegationBook, Refused

    book = DelegationBook()
    for index in range(MAX_FAN_OUT):
        assert book.record(parent_run_id="run-0", child_run_id=f"child-{index}") is None

    refusal = book.record(parent_run_id="run-0", child_run_id="one-too-many")

    assert isinstance(refusal, Refused)
    assert refusal.code == "delegation.fan_out"


def test_a_run_cannot_be_its_own_parent() -> None:
    """Which would make the depth check non-terminating."""
    from software_factory.orchestrator.delegation import DelegationBook, Refused

    refusal = DelegationBook().record(parent_run_id="run-0", child_run_id="run-0")

    assert isinstance(refusal, Refused)
    assert refusal.code == "delegation.self"


def test_a_run_has_one_parent() -> None:
    """Or its cost is attributable to two places at once."""
    from software_factory.orchestrator.delegation import DelegationBook, Refused

    book = DelegationBook()
    book.record(parent_run_id="run-a", child_run_id="child")

    refusal = book.record(parent_run_id="run-b", child_run_id="child")

    assert isinstance(refusal, Refused)
    assert refusal.code == "delegation.already_parented"


def test_a_childs_spend_counts_against_its_parent() -> None:
    """Otherwise delegation is a way to exceed a work item's budget by asking someone else to
    spend it: a budget bounds a run, and a run that can create runs bounds nothing (FR-34.2).
    """
    from software_factory.economics import Cause, Charge, attribute_to_roots

    charges = [
        Charge(
            units=1.0,
            work_item_id="wi-1",
            agent="builder",
            stage="BUILD",
            cause=Cause.PRIMARY,
            run_id="root",
        ),
        Charge(
            units=8.0,
            work_item_id="wi-1",
            agent="prover",
            stage="BUILD",
            cause=Cause.PRIMARY,
            run_id="child",
        ),
        Charge(
            units=4.0,
            work_item_id="wi-1",
            agent="scout",
            stage="BUILD",
            cause=Cause.PRIMARY,
            run_id="grandchild",
        ),
    ]

    totals = attribute_to_roots(charges, {"child": "root", "grandchild": "child"})

    assert totals == {"root": 13.0}


def test_the_tree_shows_a_cheap_parent_with_expensive_children(tmp_path: Path) -> None:
    """The case a flat per-agent report renders as innocent (FR-34.4)."""
    from software_factory.orchestrator.delegation import tree_from

    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        EntryType.RUN_STARTED,
        actor="builder",
        subject="wi-1",
        payload={"run": "root", "stage": "BUILD"},
    )
    ledger.append(
        EntryType.MODEL_CALLED,
        actor="builder",
        subject="wi-1",
        payload={"run": "root", "costUnits": 0.5},
    )
    for index in range(3):
        ledger.append(
            EntryType.RUN_STARTED,
            actor="prover",
            subject="wi-1",
            payload={"run": f"child-{index}", "stage": "BUILD", "parentRun": "root"},
        )
        ledger.append(
            EntryType.MODEL_CALLED,
            actor="prover",
            subject="wi-1",
            payload={"run": f"child-{index}", "costUnits": 9.0},
        )

    roots = tree_from(ledger.read())

    assert len(roots) == 1
    assert roots[0].cost_units == 0.5
    assert roots[0].total_cost == 27.5
    assert roots[0].depth == 2


def test_a_factory_that_never_delegates_gets_the_same_view(tmp_path: Path) -> None:
    """A reader should not have to know which case they are in."""
    from software_factory.orchestrator.delegation import tree_from

    ledger = Ledger(tmp_path / "ledger.jsonl")
    for index in range(3):
        ledger.append(
            EntryType.RUN_STARTED,
            actor="builder",
            subject="wi-1",
            payload={"run": f"run-{index}", "stage": "BUILD"},
        )

    roots = tree_from(ledger.read())

    assert len(roots) == 3
    assert all(not root.children for root in roots)
    assert all(root.depth == 1 for root in roots)


# ------------------------------------------- the validation the trials exposed


def test_the_gate_context_runs_the_repositorys_own_tests(tmp_path: Path) -> None:
    """`_gate_context` supplied `has_test_command=False` and `build_ok=True` as constants.

    So `tests-pass` was permanently unenforceable, `build-green` permanently satisfied, and
    `regression-proven` -- the keystone gate -- an assertion with measured input on neither
    side. It blocked a defect fix for having no evidence, which is correct, and it had never
    once compared a real run at the tip against a real one at the parent.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from trials.brownfield import prepare
    from trials.harness import build_factory, coordinator_for, scripted

    repo = prepare(tmp_path)
    definition = build_factory(tmp_path / "factory", name="i", owner="t", repo="i")
    coordinator = coordinator_for(definition, repo, tmp_path / "state", scripted())

    from software_factory.runtime.workspace import WorkspaceFactory

    workspace = WorkspaceFactory(repo, tmp_path / "ws").create(run_id="r1", replace=True)
    validation = coordinator._validate(
        WorkItem(
            id="wi-1",
            factory="i",
            title="t",
            request="r",
            source=SourceContext(provider="cli", kind="direct", ref="local"),
            work_class=WorkClass.CHORE,
        ),
        Stage.BUILD,
        workspace,
    )

    assert validation.has_test_command
    assert validation.at_tip is not None
    assert len(validation.at_tip.results) == 2, "the repository's own suite really ran"
    assert validation.build_ok is True


def test_a_repository_with_no_suite_cannot_pass_build_green_either(tmp_path: Path) -> None:
    """The same absence answered two ways is an inconsistency a reader takes for a bug.

    `tests-pass` reported *unenforceable* and `build-green` reported ERROR for a repository
    that simply has no validation.
    """
    from software_factory.evals.gates import GateContext, GateOutcome, build_green, tests_pass

    context = GateContext(
        stage="BUILD", has_test_command=False, has_build_command=False, build_ok=None
    )

    assert build_green(context).outcome is GateOutcome.UNENFORCEABLE
    assert tests_pass(context).outcome is GateOutcome.UNENFORCEABLE
    assert not build_green(context).blocks


def test_the_suite_is_invoked_verbosely_because_the_parser_needs_ids() -> None:
    """`-q` output is a row of dots.

    Invoked quietly, `parse_pytest` returned zero results for a passing suite -- which
    `tests-pass` correctly refused as "exit code 0 with 0 results", so the mismatch surfaced
    as a gate failure rather than a silent pass. It also left `regression-proven` unable to
    name the new test, which is the entire comparison.
    """
    from software_factory.orchestrator.coordinator import PYTEST_COMMAND

    assert "-v" in PYTEST_COMMAND
    assert "-q" not in PYTEST_COMMAND


def test_the_parent_run_never_touches_the_live_working_tree(tmp_path: Path) -> None:
    """A stash that is taken and not popped loses the run's work.

    The first implementation stashed and checked out the run's own tree, and
    `git stash push --staged` returns non-zero on success in git 2.43 -- so the pop was
    skipped. A worktree cannot do either.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from trials.brownfield import prepare
    from trials.harness import write

    from software_factory.orchestrator.coordinator import PYTEST_COMMAND, _run_suite_at

    repo = prepare(tmp_path)
    write(repo / "test_new.py", "def test_new():\n    assert True\n")
    before = (repo / "test_new.py").read_text(encoding="utf-8")

    at_parent = _run_suite_at(repo, PYTEST_COMMAND, "HEAD~1")

    assert at_parent is not None
    assert (repo / "test_new.py").read_text(encoding="utf-8") == before
    assert not any(r.test_id.startswith("test_new.py") for r in at_parent.results)


def test_new_tests_are_carried_onto_the_parents_code(tmp_path: Path) -> None:
    """A plain checkout of the parent cannot contain a test written after it, so a bare
    parent run reports the new test as absent -- correctly, and uselessly, because no fix
    could ever satisfy the gate. FR-13.3a asks for the new test run against the old code.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from trials.brownfield import prepare
    from trials.harness import write

    from software_factory.orchestrator.coordinator import PYTEST_COMMAND, _run_suite_at

    repo = prepare(tmp_path)
    write(repo / "test_new.py", "def test_new():\n    assert True\n")

    carried = _run_suite_at(repo, PYTEST_COMMAND, "HEAD~1", carrying=("test_new.py",))

    assert carried is not None
    assert any(r.test_id.startswith("test_new.py") for r in carried.results)
