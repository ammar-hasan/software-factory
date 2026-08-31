"""Principals, capabilities, separation of duties, and human checkpoints.

The theme: the PRD says "a human" must approve, override, widen and stop, and never said
which human. Every one of those was an unchecked string. These tests are about the gap
between holding an authority and being the right person to exercise it *here*.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from software_factory.identity import (
    PERSON_ONLY,
    ApprovalRequest,
    ApprovalState,
    Capability,
    Checkpoint,
    CheckpointBook,
    CheckpointKind,
    CheckpointStatus,
    Decision,
    Directory,
    Principal,
    PrincipalKind,
    Refused,
    approve,
)
from software_factory.memory.records import utc_now
from software_factory.spec.units import TrustClass

# --------------------------------------------------------------------------- fixtures


def person(
    principal_id: str,
    *caps: Capability,
    groups: tuple[str, ...] = (),
    identities: tuple[str, ...] = (),
    active: bool = True,
) -> Principal:
    return Principal(
        id=principal_id,
        kind=PrincipalKind.PERSON,
        display_name=principal_id,
        groups=frozenset(groups),
        capabilities=frozenset(caps),
        identities=frozenset(identities),
        active=active,
    )


def directory(*principals: Principal) -> Directory:
    return Directory(list(principals))


# ------------------------------------------------------------------------- principals


def test_a_principal_needs_a_stable_id() -> None:
    """The id ends up in the ledger, and an entry whose actor cannot be resolved later has
    lost its meaning."""
    with pytest.raises(ValueError, match="stable id"):
        Principal(id="", kind=PrincipalKind.PERSON)


def test_an_agent_cannot_hold_a_capability_that_records_a_person_decided() -> None:
    """Granting `approve_spec` to an agent does not delegate the checkpoint; it deletes it.

    This is refused at construction rather than honoured, because a factory that can
    configure its way out of its own checkpoints has none.
    """
    with pytest.raises(ValueError, match="approve_spec"):
        Principal(
            id="conductor",
            kind=PrincipalKind.AGENT,
            capabilities=frozenset({Capability.APPROVE_SPEC}),
        )


def test_an_agent_may_hold_capabilities_that_are_not_person_only() -> None:
    agent = Principal(
        id="conductor",
        kind=PrincipalKind.AGENT,
        capabilities=frozenset({Capability.STEER_RUN}),
    )

    assert agent.holds(Capability.STEER_RUN)
    assert Capability.STEER_RUN not in PERSON_ONLY


def test_a_persons_decision_carries_operator_trust_and_an_agents_does_not() -> None:
    """Trust is monotone downward (FR-6.4b): an agent reads untrusted text, so it cannot be
    the reason a claim is trusted."""
    assert person("amaya").trust is TrustClass.OPERATOR
    assert Principal(id="scout", kind=PrincipalKind.AGENT).trust is TrustClass.INTERNAL


# ---------------------------------------------------------------------- identity mapping


def test_a_provider_identity_resolves_to_the_principal_it_was_mapped_to() -> None:
    book = directory(person("amaya", identities=("git-host:amaya-r",)))

    assert book.resolve_identity("git-host", "amaya-r") is not None
    assert book.resolve_identity("git-host", "amaya-r").id == "amaya"


def test_an_unmapped_identity_resolves_to_nothing_rather_than_to_someone() -> None:
    """A normal answer, not an error: anyone may open an issue. It stops being normal when
    a decision is attempted, which `authorise` refuses by name."""
    book = directory(person("amaya", identities=("git-host:amaya-r",)))

    assert book.resolve_identity("chat", "amaya-r") is None
    assert book.resolve_identity("git-host", "someone-else") is None


def test_one_identity_cannot_map_to_two_principals() -> None:
    """An ambiguous identity cannot attribute a decision, so it is refused at load."""
    book = directory(person("amaya", identities=("git-host:shared",)))

    with pytest.raises(ValueError, match="maps to both"):
        book.add(person("bo", identities=("git-host:shared",)))


# --------------------------------------------------------------------------- authorising


def test_an_authorised_principal_gets_a_decision_that_records_everything() -> None:
    """FR-25.4: a decision without attribution is not a decision, so this type cannot
    express one."""
    book = directory(person("amaya", Capability.APPROVE_SPEC))

    decision = book.authorise(
        "amaya",
        Capability.APPROVE_SPEC,
        subject="PAY-1",
        rationale="the acceptance criteria match the issue",
        evidence_shown=("delta-7",),
        channel="git-host",
    )

    assert isinstance(decision, Decision)
    assert decision.principal_id == "amaya"
    assert decision.evidence_shown == ("delta-7",)
    assert decision.channel == "git-host"


def test_an_unknown_principal_cannot_decide() -> None:
    book = directory(person("amaya", Capability.APPROVE_SPEC))

    refused = book.authorise("stranger", Capability.APPROVE_SPEC, subject="PAY-1", rationale="ok")

    assert isinstance(refused, Refused)
    assert refused.code == "identity.unknown_principal"


def test_a_principal_without_the_capability_is_refused_and_told_who_has_it() -> None:
    """A refusal that does not say who *can* decide leaves the work stalled with no next
    step, which is the same outcome as no checkpoint at all."""
    book = directory(person("amaya", Capability.APPROVE_SPEC), person("bo", Capability.ERASE_DATA))

    refused = book.authorise("bo", Capability.APPROVE_SPEC, subject="PAY-1", rationale="ok")

    assert isinstance(refused, Refused)
    assert refused.code == "identity.missing_capability"
    assert "amaya" in refused.remediation


def test_a_capability_nobody_holds_says_so_rather_than_naming_nobody() -> None:
    book = directory(person("bo", Capability.ERASE_DATA))

    refused = book.authorise("bo", Capability.APPROVE_SPEC, subject="PAY-1", rationale="ok")

    assert isinstance(refused, Refused)
    assert "nobody holds" in refused.remediation or "No principal holds" in refused.remediation


def test_an_inactive_principal_cannot_decide() -> None:
    book = directory(person("amaya", Capability.APPROVE_SPEC, active=False))

    refused = book.authorise("amaya", Capability.APPROVE_SPEC, subject="PAY-1", rationale="ok")

    assert isinstance(refused, Refused)
    assert refused.code == "identity.inactive_principal"


def test_a_decision_with_no_stated_reason_is_refused() -> None:
    """The record exists so a later reader can evaluate the decision, and "approved" alone
    tells them nothing."""
    book = directory(person("amaya", Capability.APPROVE_SPEC))

    refused = book.authorise("amaya", Capability.APPROVE_SPEC, subject="PAY-1", rationale="   ")

    assert isinstance(refused, Refused)
    assert refused.code == "identity.no_rationale"


def test_a_refusal_raises_an_actionable_error_when_asked_to() -> None:
    from software_factory.identity import AuthorisationError

    book = directory(person("amaya", Capability.APPROVE_SPEC))
    refused = book.authorise("stranger", Capability.APPROVE_SPEC, subject="x", rationale="ok")

    assert isinstance(refused, Refused)
    with pytest.raises(AuthorisationError, match="not a principal"):
        refused.raise_for()


# ------------------------------------------------------------------ separation of duties


def request(self_referential: bool = False) -> ApprovalRequest:
    return ApprovalRequest(
        subject="scorers/tests-actually-run",
        proposer_id="amaya",
        self_referential=self_referential,
        definition_change=True,
    )


def test_a_proposer_cannot_approve_their_own_change() -> None:
    """FR-25.3, and the reason the module exists: proposing and approving the same change
    is one person's judgement recorded twice."""
    book = directory(person("amaya", Capability.ADOPT_DEFINITION_CHANGE))
    state = ApprovalState(request=request())

    refused = approve(book, state, principal_id="amaya", rationale="looks right")

    assert isinstance(refused, Refused)
    assert refused.code == "duties.self_approval"


def test_another_holder_can_approve() -> None:
    book = directory(
        person("amaya", Capability.ADOPT_DEFINITION_CHANGE),
        person("bo", Capability.ADOPT_DEFINITION_CHANGE),
    )
    state = ApprovalState(request=request())

    updated = approve(book, state, principal_id="bo", rationale="the diff matches the rationale")

    assert isinstance(updated, ApprovalState)
    assert updated.satisfied
    assert updated.outstanding == 0


def test_the_same_principal_cannot_approve_twice() -> None:
    """Two approvals from one person is one approval written down twice, and a count that
    can be inflated that way measures nothing."""
    book = directory(
        person("amaya", Capability.APPROVE_SELF_REFERENTIAL_CHANGE, groups=("maintainers",)),
        person("bo", Capability.APPROVE_SELF_REFERENTIAL_CHANGE, groups=("reviewers",)),
    )
    state = ApprovalState(request=request(self_referential=True))
    first = approve(book, state, principal_id="bo", rationale="independent read")
    assert isinstance(first, ApprovalState)

    refused = approve(book, first, principal_id="bo", rationale="still fine")

    assert isinstance(refused, Refused)
    assert refused.code == "duties.duplicate_approval"


def test_a_self_referential_change_needs_two_approvals() -> None:
    book = directory(
        person("amaya", Capability.APPROVE_SELF_REFERENTIAL_CHANGE, groups=("maintainers",)),
        person("bo", Capability.APPROVE_SELF_REFERENTIAL_CHANGE, groups=("reviewers",)),
        person("cass", Capability.APPROVE_SELF_REFERENTIAL_CHANGE, groups=("security",)),
    )
    state = ApprovalState(request=request(self_referential=True))

    first = approve(book, state, principal_id="bo", rationale="independent read")
    assert isinstance(first, ApprovalState)
    assert not first.satisfied
    assert first.outstanding == 1

    second = approve(book, first, principal_id="cass", rationale="second independent read")
    assert isinstance(second, ApprovalState)
    assert second.satisfied


def test_a_self_referential_approver_must_be_outside_the_proposers_group() -> None:
    """The failure mode is capture, not carelessness: a proposal that quietly widens what
    counts as success reads perfectly well to someone who shares its assumptions."""
    book = directory(
        person("amaya", Capability.APPROVE_SELF_REFERENTIAL_CHANGE, groups=("maintainers",)),
        person("bo", Capability.APPROVE_SELF_REFERENTIAL_CHANGE, groups=("maintainers",)),
    )
    state = ApprovalState(request=request(self_referential=True))

    refused = approve(book, state, principal_id="bo", rationale="fine by me")

    assert isinstance(refused, Refused)
    assert refused.code == "duties.same_group"
    assert "maintainers" in refused.message


def test_ungrouped_principals_do_not_conflict() -> None:
    """A factory that has not declared groups has not expressed an opinion about
    independence, and inventing one would block every approval it never asked for."""
    book = directory(
        person("amaya", Capability.APPROVE_SELF_REFERENTIAL_CHANGE),
        person("bo", Capability.APPROVE_SELF_REFERENTIAL_CHANGE),
    )
    state = ApprovalState(request=request(self_referential=True))

    updated = approve(book, state, principal_id="bo", rationale="independent read")

    assert isinstance(updated, ApprovalState)


def test_an_approval_state_renders_its_progress() -> None:
    book = directory(
        person("amaya", Capability.ADOPT_DEFINITION_CHANGE),
        person("bo", Capability.ADOPT_DEFINITION_CHANGE),
    )
    state = ApprovalState(request=request())
    updated = approve(book, state, principal_id="bo", rationale="checked")
    assert isinstance(updated, ApprovalState)

    assert "1/1" in updated.render()
    assert "bo" in updated.render()


# -------------------------------------------------------------------------- checkpoints


def checkpoint(kind: CheckpointKind = CheckpointKind.SPEC_APPROVAL, **kwargs) -> Checkpoint:
    base: dict[str, object] = {
        "id": "cp-1",
        "kind": kind,
        "work_item_id": "wi-1",
        "question": "Does this spec match the issue?",
        "asked_by": "architect",
        "origin": "git-host:acme/payments#42",
        "evidence": ("delta-7",),
    }
    base.update(kwargs)
    return Checkpoint(**base)  # type: ignore[arg-type]


def test_a_checkpoint_routes_to_the_principals_who_can_clear_it() -> None:
    """FR-16.3. You cannot route a question to "a human"; you route it to the holders of
    the capability that answers it."""
    book = CheckpointBook(
        directory=directory(
            person("amaya", Capability.APPROVE_SPEC),
            person("bo", Capability.ANSWER_QUESTION),
        )
    )
    book.open(checkpoint())

    assert book.routable_to("cp-1") == ["amaya"]


def test_a_checkpoint_nobody_could_clear_is_refused_at_the_point_of_asking() -> None:
    """Better to fail where the question is asked than to park a work item on a question
    that had no possible answer."""
    book = CheckpointBook(directory=directory(person("bo", Capability.ANSWER_QUESTION)))

    with pytest.raises(ValueError, match="could never be cleared"):
        book.open(checkpoint())


def test_answering_a_checkpoint_records_who_and_what_they_saw() -> None:
    book = CheckpointBook(directory=directory(person("amaya", Capability.APPROVE_SPEC)))
    book.open(checkpoint())

    decision = book.resolve("cp-1", principal_id="amaya", answer="matches; approved", channel="cli")

    assert isinstance(decision, Decision)
    assert decision.evidence_shown == ("delta-7",)
    assert book.checkpoints["cp-1"].status is CheckpointStatus.RESOLVED


def test_a_principal_without_the_capability_cannot_answer() -> None:
    book = CheckpointBook(
        directory=directory(
            person("amaya", Capability.APPROVE_SPEC),
            person("bo", Capability.ANSWER_QUESTION),
        )
    )
    book.open(checkpoint())

    refused = book.resolve("cp-1", principal_id="bo", answer="looks fine")

    assert isinstance(refused, Refused)
    assert refused.code == "identity.missing_capability"
    assert book.checkpoints["cp-1"].status is not CheckpointStatus.RESOLVED


def test_a_checkpoint_is_answered_once() -> None:
    book = CheckpointBook(
        directory=directory(
            person("amaya", Capability.APPROVE_SPEC),
            person("bo", Capability.APPROVE_SPEC),
        )
    )
    book.open(checkpoint())
    book.resolve("cp-1", principal_id="amaya", answer="approved")

    refused = book.resolve("cp-1", principal_id="bo", answer="also approved")

    assert isinstance(refused, Refused)
    assert refused.code == "checkpoint.already_resolved"


def test_an_unanswered_checkpoint_reminds_and_then_parks(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-16.4. A checkpoint with no deadline is a run that quietly costs money until
    somebody notices."""
    book = CheckpointBook(directory=directory(person("amaya", Capability.APPROVE_SPEC)))
    opened = utc_now()
    book.open(
        checkpoint(
            opened_at=opened,
            reminder_after=timedelta(hours=4),
            deadline_after=timedelta(hours=48),
        )
    )

    assert book.sweep(now=opened + timedelta(hours=1)) == []
    assert book.sweep(now=opened + timedelta(hours=5)) == [("cp-1", CheckpointStatus.REMINDED)]
    assert book.sweep(now=opened + timedelta(hours=49)) == [("cp-1", CheckpointStatus.PARKED)]


def test_the_due_state_is_computed_not_stored() -> None:
    """A process that was not running when the deadline passed must not miss it."""
    opened = utc_now() - timedelta(days=7)
    stale = checkpoint(opened_at=opened)

    assert stale.status is CheckpointStatus.OPEN
    assert stale.due_state() is CheckpointStatus.PARKED


def test_a_parked_checkpoint_still_accepts_an_answer() -> None:
    """Parking is about not holding a run open. The question stays worth answering."""
    book = CheckpointBook(directory=directory(person("amaya", Capability.APPROVE_SPEC)))
    book.open(checkpoint(opened_at=utc_now() - timedelta(days=7)))
    book.sweep()
    assert book.checkpoints["cp-1"].status is CheckpointStatus.PARKED

    decision = book.resolve("cp-1", principal_id="amaya", answer="late, but approved")

    assert isinstance(decision, Decision)


def test_open_checkpoints_are_listed_for_a_work_item() -> None:
    book = CheckpointBook(
        directory=directory(
            person("amaya", Capability.APPROVE_SPEC, Capability.ANSWER_QUESTION),
        )
    )
    book.open(checkpoint())
    book.open(checkpoint(kind=CheckpointKind.QUESTION, id="cp-2"))
    book.open(checkpoint(kind=CheckpointKind.QUESTION, id="cp-3", work_item_id="wi-2"))

    assert [c.id for c in book.open_for("wi-1")] == ["cp-1", "cp-2"]


# ------------------------------------------------------------------ loading and linting


def test_a_directory_is_built_from_the_definition(tmp_path) -> None:
    """Authority is configuration for the same reason everything else is: a repository
    review is the only place a capability grant is seen by someone other than its author."""
    from software_factory.definition.loader import load_strict
    from software_factory.identity.loading import directory_from
    from software_factory.scaffold import init_factory

    init_factory(tmp_path, name="reference", owner="amaya", repo="service")
    definition = load_strict(tmp_path)

    book = directory_from(definition)
    owner = book.get("amaya")

    assert owner is not None
    assert owner.holds(Capability.APPROVE_SPEC)
    assert book.resolve_identity("git-host", "amaya") is owner


def test_the_scaffolded_conductor_is_an_agent_with_no_person_only_capability(tmp_path) -> None:
    from software_factory.definition.loader import load_strict
    from software_factory.identity.loading import directory_from
    from software_factory.scaffold import init_factory

    init_factory(tmp_path, name="reference", owner="amaya", repo="service")
    book = directory_from(load_strict(tmp_path))

    conductor = book.get("conductor")
    assert conductor is not None
    assert conductor.kind is PrincipalKind.AGENT
    assert not (conductor.capabilities & PERSON_ONLY)


def test_the_scaffold_can_satisfy_separation_of_duties(tmp_path) -> None:
    """Two holders in two groups. A scaffold that lints clean with one would teach that one
    is enough, and FR-25.3 exists precisely because it is not."""
    from software_factory.definition.loader import load_strict
    from software_factory.identity.loading import directory_from
    from software_factory.scaffold import init_factory

    init_factory(tmp_path, name="reference", owner="amaya", repo="service")
    book = directory_from(load_strict(tmp_path))

    holders = book.holders(Capability.APPROVE_SELF_REFERENTIAL_CHANGE)
    assert len(holders) >= 2
    assert len({frozenset(h.groups) for h in holders}) >= 2


def test_a_capability_granted_to_an_agent_fails_validation(tmp_path) -> None:
    """A factory that can configure its way out of its own checkpoints has none."""
    from software_factory.definition.loader import load
    from software_factory.definition.validate import validate
    from software_factory.scaffold import init_factory

    init_factory(tmp_path, name="reference", owner="amaya", repo="service")
    (tmp_path / "principals" / "conductor.yaml").write_text(
        "id: conductor\nkind: agent\ncapabilities:\n  - approve_spec\n", encoding="utf-8"
    )

    definition, report = load(tmp_path)
    validate(definition, report)

    codes = {issue.code for issue in report.errors}
    assert "principal.capability_needs_person" in codes


def test_an_unknown_capability_name_fails_validation(tmp_path) -> None:
    from software_factory.definition.loader import load
    from software_factory.definition.validate import validate
    from software_factory.scaffold import init_factory

    init_factory(tmp_path, name="reference", owner="amaya", repo="service")
    (tmp_path / "principals" / "amaya.yaml").write_text(
        "id: amaya\nkind: person\ncapabilities:\n  - be_generally_in_charge\n", encoding="utf-8"
    )

    definition, report = load(tmp_path)
    validate(definition, report)

    assert "principal.unknown_capability" in {issue.code for issue in report.errors}


def test_a_bare_provider_handle_is_refused(tmp_path) -> None:
    """The same name on two providers is two people, and guessing otherwise turns an intake
    channel into an authorisation channel."""
    from software_factory.definition.models import PrincipalDefinition

    with pytest.raises(ValueError, match="provider:handle"):
        PrincipalDefinition.model_validate({"id": "amaya", "identities": ["amaya"]})


# ----------------------------------------------------- the stage machine's use of these


def test_skipping_review_needs_a_decision_not_a_boolean() -> None:
    """`human_approved_skip=True` was a claim any caller could make about a human who may
    not exist. A `Decision` names who, under which capability, against what evidence."""
    from software_factory.definition.models import Stage
    from software_factory.orchestrator import Blocker, StageMachine, TransitionRefused
    from software_factory.orchestrator.workitem import SourceContext, WorkItem

    machine = StageMachine()
    work = WorkItem(
        id="wi-1",
        factory="payments",
        title="Fix BOM handling",
        request="CSV headers get a stray character.",
        source=SourceContext(provider="cli", kind="direct", ref="local"),
        # TRIAGE -> HANDOFF is a legal edge that skips REVIEW, so the skip rule is what
        # refuses it rather than the transition table.
        stage=Stage.TRIAGE,
    )

    refused = machine.advance(work, Stage.HANDOFF, actor="conductor", reason="looks done")
    assert isinstance(refused, TransitionRefused)
    assert refused.code == "stage.non_skippable"
    assert (
        Blocker.AWAITING_HUMAN.value == "awaiting_human"
    )  # the blocker enum is what a caller records next

    wrong = Decision(
        principal_id="amaya",
        capability=Capability.ANSWER_QUESTION,
        subject="wi-1",
        rationale="sure",
    )
    still_refused = machine.advance(
        work, Stage.HANDOFF, actor="conductor", reason="looks done", approval=wrong
    )
    assert isinstance(still_refused, TransitionRefused)
    assert "does not authorise" in still_refused.remediation


def test_an_approval_naming_another_work_item_does_not_authorise_this_one() -> None:
    """An approval is for one decision, not a token to reuse.

    A *mismatched* subject only. The empty subject -- which the machine read as a wildcard for every work item -- is covered by `test_i1_an_approval_with_no_subject_authorises_nothing`.
    """
    from software_factory.definition.models import Stage
    from software_factory.orchestrator import StageMachine, TransitionRefused
    from software_factory.orchestrator.workitem import SourceContext, WorkItem

    machine = StageMachine()
    work = WorkItem(
        id="wi-2",
        factory="payments",
        title="Fix BOM handling",
        request="CSV headers get a stray character.",
        source=SourceContext(provider="cli", kind="direct", ref="local"),
        stage=Stage.TRIAGE,
    )
    elsewhere = Decision(
        principal_id="amaya",
        capability=Capability.SKIP_STAGE,
        subject="wi-1",
        rationale="approved for the other item",
    )

    refused = machine.advance(
        work, Stage.HANDOFF, actor="conductor", reason="looks done", approval=elsewhere
    )

    assert isinstance(refused, TransitionRefused)
    assert "not a token to reuse" in refused.remediation
