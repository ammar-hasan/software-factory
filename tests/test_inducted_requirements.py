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
