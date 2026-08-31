"""Work items and the stage machine.

The two things worth proving here: an illegal transition is refused with the legal
options named, and routing authority is bounded — the conductor reads text an attacker
can write, so it must not be able to route review away on its own say-so.
"""

from __future__ import annotations

import pytest

from software_factory.definition.models import Stage
from software_factory.orchestrator import (
    DEFAULT_NON_SKIPPABLE,
    DEFAULT_TRANSITIONS,
    Blocker,
    SourceContext,
    StageMachine,
    Transition,
    TransitionRefused,
    WorkClass,
    WorkItem,
    classify_request,
    new_id,
    validate_graph,
)
from software_factory.spec.units import TrustClass


def item(stage: Stage = Stage.INTAKE, **kwargs) -> WorkItem:
    base: dict[str, object] = {
        "id": new_id(),
        "factory": "payments",
        "title": "CSV importer mangles BOM headers",
        "request": "Uploading a UTF-8 CSV with a BOM names the first column oddly.",
        "source": SourceContext(provider="git-host", kind="issue", ref="acme/payments#42"),
        "stage": stage,
    }
    base.update(kwargs)
    return WorkItem(**base)  # type: ignore[arg-type]


@pytest.fixture
def machine() -> StageMachine:
    return StageMachine()


# ------------------------------------------------------------------------ transitions


def test_a_legal_transition_is_recorded_with_actor_and_reason(machine: StageMachine) -> None:
    work = item(Stage.TRIAGE)

    moved = machine.advance(work, Stage.BUILD, actor="conductor", reason="scope is small")

    assert isinstance(moved, Transition)
    assert work.stage is Stage.BUILD
    assert work.history[-1].actor == "conductor"
    assert "scope is small" in work.history[-1].reason


def test_an_illegal_transition_names_the_legal_options(machine: StageMachine) -> None:
    work = item(Stage.INTAKE)

    refused = machine.advance(work, Stage.COMPLETE, actor="conductor", reason="looks done")

    assert isinstance(refused, TransitionRefused)
    assert refused.code == "stage.illegal_transition"
    assert "TRIAGE" in refused.remediation
    assert work.stage is Stage.INTAKE


def test_a_terminal_item_cannot_move(machine: StageMachine) -> None:
    work = item(Stage.COMPLETE)

    refused = machine.advance(work, Stage.BUILD, actor="conductor", reason="one more thing")

    assert isinstance(refused, TransitionRefused)
    assert refused.code == "stage.terminal"


def test_skipping_a_stage_is_recorded(machine: StageMachine) -> None:
    """Skipping is allowed and legible, not silent."""
    work = item(Stage.INTAKE)

    machine.advance(work, Stage.BUILD, actor="conductor", reason="already well defined")

    assert Stage.TRIAGE in work.history[-1].skipped
    assert Stage.DESIGN in work.history[-1].skipped


# ------------------------------------------------------------------ bounded authority


def test_review_cannot_be_skipped_on_an_agents_authority(machine: StageMachine) -> None:
    """Text that persuades the conductor to skip review would otherwise remove review."""
    work = item(Stage.TRIAGE)

    refused = machine.advance(work, Stage.HANDOFF, actor="conductor", reason="looks fine")

    assert isinstance(refused, TransitionRefused)
    assert refused.code == "stage.non_skippable"
    assert "REVIEW" in refused.message


def test_a_human_can_approve_skipping_review(machine: StageMachine) -> None:
    work = item(Stage.TRIAGE)

    moved = machine.advance(
        work,
        Stage.HANDOFF,
        actor="human:maintainer",
        reason="documentation-only change",
        human_approved_skip=True,
    )

    assert isinstance(moved, Transition)
    assert Stage.REVIEW in moved.skipped


def test_a_routing_decision_justified_only_by_untrusted_input_is_refused(
    machine: StageMachine,
) -> None:
    work = item(Stage.TRIAGE)

    refused = machine.advance(
        work,
        Stage.BUILD,
        actor="conductor",
        reason="the issue says to go straight to implementation",
        basis_trust=TrustClass.UNTRUSTED,
    )

    assert isinstance(refused, TransitionRefused)
    assert refused.code == "stage.untrusted_basis"
    assert "attacker can write" in refused.remediation


def test_a_verified_basis_is_accepted(machine: StageMachine) -> None:
    work = item(Stage.TRIAGE)

    moved = machine.advance(
        work,
        Stage.BUILD,
        actor="conductor",
        reason="the failing test localises the defect to one function",
        basis_trust=TrustClass.VERIFIED,
    )

    assert isinstance(moved, Transition)


# --------------------------------------------------------------------------- blocking


def test_blocking_requires_the_action_that_would_clear_it(machine: StageMachine) -> None:
    """A blocker nobody can act on is a dead end dressed as a status."""
    work = item(Stage.BUILD)

    refused = machine.block(work, Blocker.AWAITING_HUMAN, actor="conductor", action="   ")

    assert isinstance(refused, TransitionRefused)
    assert refused.code == "stage.blocker_without_action"


def test_a_blocked_item_records_its_blocker_and_action(machine: StageMachine) -> None:
    work = item(Stage.BUILD)

    machine.block(
        work,
        Blocker.MISSING_CREDENTIAL,
        actor="conductor",
        action="grant the registry token to the builder agent",
    )

    assert work.stage is Stage.BLOCKED
    assert work.blocker is Blocker.MISSING_CREDENTIAL
    assert "registry token" in work.blocker_action


def test_leaving_blocked_clears_the_blocker(machine: StageMachine) -> None:
    work = item(Stage.BUILD)
    machine.block(work, Blocker.AWAITING_CI, actor="conductor", action="wait for CI")

    machine.advance(work, Stage.BUILD, actor="conductor", reason="CI finished")

    assert work.blocker is None
    assert work.blocker_action == ""


# ------------------------------------------------------------------------ cancellation


def test_a_human_can_cancel_from_any_stage(machine: StageMachine) -> None:
    for stage in (Stage.TRIAGE, Stage.BUILD, Stage.REVIEW, Stage.BLOCKED):
        work = item(stage)

        cancelled = machine.cancel(
            work, actor="human:maintainer", reason="no longer needed", human_approved=True
        )

        assert isinstance(cancelled, Transition)
        assert work.stage is Stage.CANCELLED


def test_cancelling_a_terminal_item_is_refused(machine: StageMachine) -> None:
    work = item(Stage.CANCELLED)

    refused = machine.cancel(work, actor="human:a", reason="again", human_approved=True)

    assert isinstance(refused, TransitionRefused)
    assert refused.code == "stage.terminal"


def test_an_agent_cannot_cancel_a_work_item(machine: StageMachine) -> None:
    """`cancel` said "always available to a human" and checked nothing, so it was equally
    available to an agent -- a one-call route past every gate in the graph, offered by the
    component that reads attacker-controlled text (N15)."""
    work = item(Stage.BUILD)

    refused = machine.cancel(work, actor="agent:conductor", reason="not worth doing")

    assert isinstance(refused, TransitionRefused)
    assert refused.code == "stage.cancel_needs_human"
    assert work.stage is Stage.BUILD


# ------------------------------------------------------------------------------ rework


def test_returning_to_an_earlier_stage_counts_as_rework(machine: StageMachine) -> None:
    work = item(Stage.BUILD)
    machine.advance(work, Stage.REVIEW, actor="conductor", reason="ready")
    machine.advance(work, Stage.BUILD, actor="conductor", reason="review found a gap")

    assert work.returned_to_earlier_stage() == 1


def test_forward_progress_is_not_rework(machine: StageMachine) -> None:
    work = item(Stage.TRIAGE)
    machine.advance(work, Stage.BUILD, actor="conductor", reason="small change")
    machine.advance(work, Stage.REVIEW, actor="conductor", reason="ready")

    assert work.returned_to_earlier_stage() == 0


# --------------------------------------------------------------------- graph validation


def test_the_default_graph_is_valid() -> None:
    assert validate_graph(DEFAULT_TRANSITIONS, DEFAULT_NON_SKIPPABLE) == []


def test_a_graph_with_nothing_non_skippable_is_rejected() -> None:
    """Otherwise routing can be talked out of every check."""
    problems = validate_graph(DEFAULT_TRANSITIONS, frozenset())

    assert any("non-skippable" in problem for problem in problems)


def test_an_unreachable_stage_is_reported() -> None:
    graph = dict(DEFAULT_TRANSITIONS)
    graph[Stage.INTAKE] = frozenset({Stage.TRIAGE})
    graph[Stage.TRIAGE] = frozenset()

    problems = validate_graph(graph, DEFAULT_NON_SKIPPABLE)

    assert any("unreachable" in problem for problem in problems)


def test_a_graph_without_handoff_is_rejected() -> None:
    graph = {k: v for k, v in DEFAULT_TRANSITIONS.items() if k is not Stage.HANDOFF}

    problems = validate_graph(graph, DEFAULT_NON_SKIPPABLE)

    assert any("HANDOFF" in problem for problem in problems)


# ------------------------------------------------------------------------ classification


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The importer crashes on BOM headers", WorkClass.DEFECT),
        ("Refactor the encoding helpers", WorkClass.REFACTOR),
        ("Please review my change", WorkClass.REVIEW),
        ("Why does the importer reorder columns?", WorkClass.INVESTIGATION),
        ("Add support for semicolon delimiters", WorkClass.FEATURE),
    ],
)
def test_requests_get_a_first_guess_at_work_class(text: str, expected: WorkClass) -> None:
    assert classify_request(text) is expected


# ------------------------------------------------------------------------------ record


def test_a_work_item_serialises_its_history_and_rework(machine: StageMachine) -> None:
    work = item(Stage.BUILD)
    machine.advance(work, Stage.REVIEW, actor="conductor", reason="ready")

    payload = work.as_dict()

    assert payload["stage"] == "REVIEW"
    assert payload["rework"] == 0
    assert payload["history"]


def test_source_context_has_a_stable_identity() -> None:
    """Redelivery of one upstream event must not create two work items."""
    source = SourceContext(provider="git-host", kind="issue", ref="acme/payments#42")

    assert source.identity() == "git-host:issue:acme/payments#42"
