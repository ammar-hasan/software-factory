"""Regressions from the second adversarial review (docs/reviews/review-2-*.md).

Every test here was confirmed failing against the code as reviewed, for the reason its
name gives. That order is not ceremony: `regression-proven` is the gate this repository
imposes on every defect fix it processes, and a project that exempts itself from its own
keystone gate has not built the gate, it has described one.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from software_factory.definition.models import Stage
from software_factory.governance import Artifact, DataClass, Retention
from software_factory.identity import (
    Capability,
    Decision,
    Directory,
    Principal,
    PrincipalKind,
    Refused,
)
from software_factory.memory.records import utc_now
from software_factory.orchestrator import StageMachine
from software_factory.orchestrator.workitem import SourceContext, TransitionRefused, WorkItem


def person(pid: str, *caps: Capability, group: str = "eng") -> Principal:
    return Principal(
        id=pid,
        kind=PrincipalKind.PERSON,
        capabilities=frozenset(caps),
        groups=frozenset({group}),
    )


def item(wid: str = "wi-1", stage: Stage = Stage.TRIAGE) -> WorkItem:
    return WorkItem(
        id=wid,
        factory="f",
        title="t",
        request="r",
        source=SourceContext(provider="cli", kind="direct", ref="local"),
        stage=stage,
    )


# ------------------------------------------------------------------------------- I1


def test_i1_an_approval_with_no_subject_authorises_nothing() -> None:
    """An empty subject was accepted as "any subject".

    `DEFAULT_NON_SKIPPABLE` is `{REVIEW}` because the conductor reads attacker-controllable
    text, so unbounded routing authority is an injection primitive. One empty-subject
    decision -- the shape a hurried operator produces for a "blanket approval" -- restored
    that authority permanently, over every work item, with no expiry.
    """
    machine = StageMachine()
    wildcard = Decision(
        principal_id="amaya",
        capability=Capability.SKIP_STAGE,
        subject="",
        rationale="blanket approval for the sprint",
    )

    result = machine.advance(
        item(), Stage.HANDOFF, actor="conductor", reason="looks done", approval=wildcard
    )

    assert isinstance(result, TransitionRefused)
    assert result.code == "stage.non_skippable"
    assert "not 'wi-1'" in result.remediation


def test_i1_authorise_refuses_to_mint_a_subjectless_decision() -> None:
    """The wildcard was reachable through the supported API, not only by construction."""
    directory = Directory([person("amaya", Capability.SKIP_STAGE)])

    result = directory.authorise(
        "amaya", Capability.SKIP_STAGE, subject="", rationale="blanket approval"
    )

    assert isinstance(result, Refused)
    assert result.code == "identity.no_subject"


def test_i1_an_approval_for_one_work_item_still_authorises_that_one() -> None:
    """The fix must not break the case the check exists for."""
    machine = StageMachine()
    approval = Decision(
        principal_id="amaya",
        capability=Capability.SKIP_STAGE,
        subject="wi-1",
        rationale="the change is a comment typo; review adds nothing",
    )

    result = machine.advance(
        item("wi-1"), Stage.HANDOFF, actor="conductor", reason="typo", approval=approval
    )

    assert not isinstance(result, TransitionRefused)


# ------------------------------------------------------------------------------- I3


def test_i3_a_sweep_with_no_destructor_does_not_report_that_it_acted() -> None:
    """`acted: True` from a sweep that deleted nothing.

    The report is shaped to be shown to an auditor. A positive assertion of deletion that
    nothing established is worse than no report at all.
    """
    old = Artifact(
        id="t1",
        data_class=DataClass.TRANSCRIPT,
        created_at=utc_now() - timedelta(days=400),
    )

    with pytest.raises(TypeError):
        Retention().sweep([old])  # type: ignore[call-arg]


def test_i3_a_dry_run_sweep_reports_that_it_acted_on_nothing() -> None:
    """Audit-only is a legitimate mode; reporting it as deletion is not."""
    old = Artifact(
        id="t1",
        data_class=DataClass.TRANSCRIPT,
        created_at=utc_now() - timedelta(days=400),
    )

    report = Retention().sweep([old], tombstone=None, dry_run=True)

    assert report.expired == ["t1"]
    assert report.acted is False
    assert report.examined == 1
    assert report.as_dict()["dryRun"] is True


def test_i3_an_erasure_receipt_records_what_it_examined() -> None:
    """ "Complete" over a list nobody enumerated is not an answer.

    `Retention` never enumerates anything; it reports over whatever list it was handed. The
    receipt now says how many artifacts that was, so a reader can tell a real sweep from an
    erasure run against an empty list.
    """
    destroyed: list[str] = []
    artifact = Artifact(
        id="t1",
        data_class=DataClass.TRANSCRIPT,
        created_at=utc_now(),
        subjects=frozenset({"amaya"}),
    )

    report = Retention().erase(
        "amaya", [artifact], requested_by="human:dpo", destroy=lambda a: destroyed.append(a.id)
    )

    assert destroyed == ["t1"]
    assert report.examined == 1
    assert report.as_dict()["examined"] == 1


def test_i3_an_erasure_over_an_empty_list_is_not_complete() -> None:
    report = Retention().erase("amaya", [], requested_by="human:dpo", destroy=lambda _a: None)

    assert report.complete is False
    assert report.examined == 0


def test_i3_an_already_tombstoned_body_is_not_destroyed_twice() -> None:
    """`sweep` skipped them and `erase` did not, so a destroyed body was destroyed again."""
    destroyed: list[str] = []
    gone = Artifact(
        id="t1",
        data_class=DataClass.TRANSCRIPT,
        created_at=utc_now(),
        subjects=frozenset({"amaya"}),
        tombstoned=True,
    )

    report = Retention().erase(
        "amaya", [gone], requested_by="human:dpo", destroy=lambda a: destroyed.append(a.id)
    )

    assert destroyed == []
    assert "t1" not in report.erased


# ------------------------------------------------------------------------------- I4


def ledger_with(tmp_path, count: int):
    from software_factory.ledger import EntryType, Ledger

    ledger = Ledger(tmp_path / "ledger.jsonl")
    for i in range(count):
        ledger.append(EntryType.RUN_STARTED, actor="worker", subject=f"run-{i}")
    return ledger


def rewrite_entry(path, line_index: int, **fields: str) -> None:
    """Edit one ledger line in place, as a tamperer would."""
    import json

    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[line_index])
    record.update(fields)
    lines[line_index] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_i4_sealing_refuses_a_ledger_that_does_not_verify(tmp_path) -> None:
    """Sealing a tampered range laundered it.

    The segment records the tampered hash as the sealed truth, so any later comparison is
    against the forgery. Sealing a broken chain is worse than neutral.
    """
    from software_factory.governance import Manifest, SegmentError, seal
    from software_factory.ledger import Ledger

    ledger_with(tmp_path, 12)
    rewrite_entry(tmp_path / "ledger.jsonl", 2, subject="run-2-REWRITTEN")

    with pytest.raises(SegmentError, match="does not verify"):
        seal(Ledger(tmp_path / "ledger.jsonl"), Manifest(path=tmp_path / "segments.jsonl"), size=10)


def test_i4_verify_against_the_ledger_catches_a_rewritten_sealed_entry(tmp_path) -> None:
    """The manifest verified a range that had been rewritten underneath it.

    `verify()` only walks the manifest's own chain, so a sealed entry could be edited in
    the ledger and `sf govern verify` still printed `ok` -- the exact claim the module
    docstring makes, never performed.
    """
    from software_factory.governance import Manifest, SegmentError, seal
    from software_factory.ledger import Ledger

    ledger = ledger_with(tmp_path, 25)
    manifest = Manifest(path=tmp_path / "segments.jsonl")
    seal(ledger, manifest, size=10)
    manifest.verify()  # chain-only still passes; that is the point

    rewrite_entry(tmp_path / "ledger.jsonl", 4, subject="run-4-REWRITTEN")

    with pytest.raises(SegmentError, match="does not match the sealed"):
        manifest.verify_against(Ledger(tmp_path / "ledger.jsonl"))


def test_i4_the_last_segment_is_anchored_in_the_ledger(tmp_path) -> None:
    """Nothing chained to the final segment, so any field in it could be rewritten.

    Sealing now appends an entry carrying the new segment's digest, so the manifest and the
    ledger each commit to the other and neither can be rewritten alone.
    """
    from software_factory.governance import Manifest, SegmentError, seal
    from software_factory.ledger import EntryType, Ledger

    ledger = ledger_with(tmp_path, 25)
    manifest = Manifest(path=tmp_path / "segments.jsonl")
    seal(ledger, manifest, size=10)

    anchors = [e for e in ledger.read() if e.type is EntryType.SEGMENT_SEALED]
    assert [a.subject for a in anchors] == ["segment-0", "segment-1"]

    last = manifest.segments[-1]
    manifest.segments[-1] = type(last)(
        index=last.index,
        first_seq=last.first_seq,
        last_seq=last.last_seq,
        last_hash="f" * 64,
        prev_segment_digest=last.prev_segment_digest,
        entry_count=last.entry_count,
        sealed_at=last.sealed_at,
    )

    with pytest.raises(SegmentError, match="anchor"):
        manifest.verify_against(Ledger(tmp_path / "ledger.jsonl"))


def test_i4_verify_against_reports_what_it_could_not_check(tmp_path) -> None:
    """An archived prefix is the case this exists for, and it must say so rather than
    quietly verifying less than the reader thinks."""
    from software_factory.governance import Manifest, seal
    from software_factory.ledger import Ledger

    ledger = ledger_with(tmp_path, 25)
    manifest = Manifest(path=tmp_path / "segments.jsonl")
    seal(ledger, manifest, size=10)

    report = manifest.verify_against(Ledger(tmp_path / "ledger.jsonl"))

    assert report.checked_against_entries == [0, 1]
    assert report.chain_only == []


# ------------------------------------------------------------------------- O1, O2


def gate_entry(ledger, *, item: str, gate: str, outcome: str, stage: str):
    from software_factory.ledger import EntryType

    return ledger.append(
        EntryType.GATE_EVALUATED,
        actor="assurance",
        subject=item,
        payload={"gate": gate, "outcome": outcome, "stage": stage},
    )


def test_o2_a_gate_that_failed_at_a_later_stage_is_not_hidden(tmp_path) -> None:
    """A failure at REVIEW was reported as a 100% pass rate.

    De-duplication was keyed on `(work item, gate)` and ignored the stage the coordinator
    already writes into the payload. The same gate legitimately runs at several stages, so
    the later evaluations were being discarded as repeats of the first -- and the first is
    the one most likely to have passed.
    """
    from software_factory.ledger import Ledger
    from software_factory.observability.metrics import compute

    ledger = Ledger(tmp_path / "ledger.jsonl")
    gate_entry(ledger, item="wi-1", gate="secret-clean", outcome="pass", stage="BUILD")
    gate_entry(ledger, item="wi-1", gate="secret-clean", outcome="fail", stage="REVIEW")

    report = compute(ledger.read())
    rate = report.measure("gate_pass_rate")

    assert rate.sample == 2
    assert rate.value == 0.5


def test_o2_a_retry_of_the_same_gate_at_the_same_stage_is_still_one_attempt(tmp_path) -> None:
    """The property the de-duplication exists for must survive the fix.

    A gate that passes on the fourth try has still failed, and counting every attempt would
    let a factory improve the number by retrying more.
    """
    from software_factory.ledger import Ledger
    from software_factory.observability.metrics import compute

    ledger = Ledger(tmp_path / "ledger.jsonl")
    gate_entry(ledger, item="wi-1", gate="tests-pass", outcome="fail", stage="BUILD")
    gate_entry(ledger, item="wi-1", gate="tests-pass", outcome="pass", stage="BUILD")

    rate = compute(ledger.read()).measure("gate_pass_rate")

    assert rate.sample == 1
    assert rate.value == 0.0


def test_o1_changes_opened_is_not_an_available_zero(tmp_path) -> None:
    """`changes_opened: 0 changes` sat as an established value beneath three metrics that
    correctly said "reporting zero here would read as a factory that produces none".

    Opening a change is a git-host act. Without that adapter the count is unobservable, and
    an unobservable quantity reported as zero is the worst of both: it reads as measured.
    """
    from software_factory.ledger import Ledger
    from software_factory.observability.metrics import Availability, compute

    ledger = Ledger(tmp_path / "ledger.jsonl")

    measure = compute(ledger.read()).measure("changes_opened")

    assert measure.availability is Availability.UNAVAILABLE
    assert measure.value is None


def test_o1_changes_opened_is_reported_when_the_adapter_is_present(tmp_path) -> None:
    from software_factory.ledger import EntryType, Ledger
    from software_factory.observability.metrics import Availability, compute

    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        EntryType.WORK_ITEM_TRANSITION,
        actor="coordinator",
        subject="wi-1",
        payload={"to": "HANDOFF", "from": "VERIFY"},
    )

    measure = compute(ledger.read(), integrations=frozenset({"git-host"})).measure("changes_opened")

    assert measure.availability is Availability.AVAILABLE
    assert measure.value == 1.0


# ------------------------------------------------------------------------------- N1


def trigger_definition(tmp_path, *, filter_body: str):
    """A valid factory whose one automation carries `filter_body` on its trigger."""
    from software_factory.definition.loader import load_strict
    from software_factory.scaffold import init_factory

    init_factory(tmp_path, name="ref", owner="amaya", repo="service")
    path = next((tmp_path / "automations").glob("*/automation.md"))
    text = path.read_text(encoding="utf-8")
    marker = "    filter:\n"
    assert marker in text, text
    head, _, tail = text.partition(marker)
    rest = tail.split("\n")
    # Drop the existing filter block, keeping everything from the next unindented key.
    keep = [line for line in rest if not line.startswith("      ")]
    path.write_text(head + filter_body + "\n".join(keep), encoding="utf-8")
    return load_strict(tmp_path)


def test_n1_author_trust_is_not_a_filter_predicate(tmp_path) -> None:
    """`authorTrust: any` disabled the author check *and* became a required attribute.

    The automation went inert for real traffic and live for anything carrying an attribute
    literally named `authorTrust` -- which is where adapters put provider-supplied facts and
    where `sf intake --attribute` writes directly. The one control meaning "this automation
    accepts strangers" handed the decision of whether it fires to whoever produces the event.
    """
    from software_factory.errors import DefinitionError

    with pytest.raises(DefinitionError, match="authorTrust"):
        trigger_definition(tmp_path, filter_body="    filter:\n      authorTrust: any\n")


def test_n1_the_declared_opt_out_does_not_leak_into_the_filter(tmp_path) -> None:
    from software_factory.intake.loading import automations_from

    definition = trigger_definition(tmp_path, filter_body="    authorTrust: any\n")
    automations = automations_from(definition)

    assert [a.require_known_author for a in automations] == [False]
    assert all("authorTrust" not in a.filter for a in automations)


def test_n1_the_default_still_requires_a_known_author(tmp_path) -> None:
    """FR-18.6: restrictive by default. Permissiveness is chosen, never inherited."""
    from software_factory.intake.loading import automations_from

    definition = trigger_definition(tmp_path, filter_body="    filter:\n      label: bug\n")

    assert all(a.require_known_author for a in automations_from(definition))


# ------------------------------------------------------------------------------- N2


def factory_event(event_id: str = "e1"):
    from software_factory.intake import FactoryEvent, Origin, Provider

    return FactoryEvent(
        id=event_id,
        provider=Provider.GIT_HOST,
        event="issue.opened",
        origin=Origin(provider=Provider.GIT_HOST, ref="acme/svc#1"),
        title="a bug",
        author="amaya",
    )


class SickAdapter:
    """An adapter that is down, so intake refuses with `provider_unavailable`."""

    provider = None

    def __init__(self, provider) -> None:
        self.provider = provider

    def name(self) -> str:
        return "sick"

    def health(self):
        from software_factory.intake.adapters import Health, HealthReport

        return HealthReport(status=Health.UNAVAILABLE, detail="the host is down")

    def normalise(self, raw):  # pragma: no cover - not reached
        raise NotImplementedError

    def verify(self, raw):  # pragma: no cover - not reached
        return True

    def reply(self, origin, body):  # pragma: no cover - not reached
        return None

    def capabilities(self):
        return frozenset()


def test_n2_a_refused_event_can_be_retried(tmp_path) -> None:
    """The deduplicator recorded the event before anything could refuse it.

    Both `provider_unavailable` and the backpressure refusals tell the caller to retry, and
    the retry then returned `intake.redelivered` forever. FR-18.9 promises work parks rather
    than being dropped; it was dropped.
    """
    from software_factory.intake import Provider, Refused
    from software_factory.intake.adapters import Registry
    from software_factory.intake.pipeline import Pipeline

    registry = Registry()
    registry.register(SickAdapter(Provider.GIT_HOST))
    registry.check()
    pipeline = Pipeline(registry=registry)
    event = factory_event()

    first = pipeline.receive(event)
    assert isinstance(first[0], Refused)
    assert first[0].code == "intake.provider_unavailable"

    second = pipeline.receive(event)
    assert isinstance(second[0], Refused)
    assert second[0].code == "intake.provider_unavailable", "the retry was swallowed as a duplicate"


def test_n2_an_accepted_event_is_still_deduplicated() -> None:
    """The property the deduplicator exists for must survive the fix."""
    from software_factory.intake import Provider, Refused
    from software_factory.intake.pipeline import Automation, Pipeline

    pipeline = Pipeline(
        automations=[
            Automation(
                name="triage",
                agent="conductor",
                prompt="triage it",
                provider=Provider.GIT_HOST,
                event="issue.opened",
                require_known_author=False,
            )
        ]
    )
    event = factory_event()

    pipeline.receive(event)
    again = pipeline.receive(event)

    assert isinstance(again[0], Refused)
    assert again[0].code == "intake.redelivered"


# ------------------------------------------------------------------------------- N3


def failure(run_id: str, detail: str, *, gate: str = "tests-pass", stage: str = "BUILD"):
    from software_factory.improvement.clustering import Failure

    return Failure(
        run_id=run_id,
        work_item_id=f"wi-{run_id}",
        agent="builder",
        gate=gate,
        stage=stage,
        detail=detail,
    )


def test_n3_a_cluster_key_does_not_depend_on_the_order_failures_were_read(tmp_path) -> None:
    """The anti-thrash key was positional, so the same failures got a different signature.

    `cluster_failures` suffixed split groups by enumeration order, and that order is the
    order the ledger happened to be read in. FR-14.6's property -- a rejected proposal must
    not return without new evidence -- was defeated by nothing more adversarial than reading
    the ledger the other way round.
    """
    from software_factory.improvement.clustering import cluster_failures

    encoding = [failure(f"e{i}", "unicode decode error in the csv importer") for i in range(3)]
    timeouts = [failure(f"t{i}", "connection timed out talking to the registry") for i in range(4)]

    forwards = cluster_failures(encoding + timeouts)
    backwards = cluster_failures(timeouts + encoding)

    def keyed(clusters):
        return {c.signature: sorted(f.run_id for f in c.failures) for c in clusters}

    assert keyed(forwards) == keyed(backwards)


def test_n3_a_detail_free_failure_does_not_relabel_the_other_clusters() -> None:
    """One extra failure with an empty detail re-keyed both clusters, discarding every
    standing rejection for them."""
    from software_factory.improvement.clustering import cluster_failures

    encoding = [failure(f"e{i}", "unicode decode error in the csv importer") for i in range(3)]
    timeouts = [failure(f"t{i}", "connection timed out talking to the registry") for i in range(4)]

    before = {
        c.signature
        for c in cluster_failures(encoding + timeouts)
        if any(f.run_id.startswith("e") for f in c.failures)
    }
    after = {
        c.signature
        for c in cluster_failures([*encoding, *timeouts, failure("b0", "")])
        if any(f.run_id.startswith("e") for f in c.failures)
    }

    assert before == after


def test_n3_two_failures_from_one_run_are_analysed_separately() -> None:
    """`_split` keyed its text analysis by `run_id`, so two failures from the same run
    collided and a cluster could hold members with nothing in common."""
    from software_factory.improvement.clustering import cluster_failures

    same_run = [
        failure("r1", "unicode decode error in the csv importer", gate="tests-pass"),
        failure("r1", "connection timed out talking to the registry", gate="tests-pass"),
    ]
    more = [failure(f"e{i}", "unicode decode error in the csv importer") for i in range(3)]

    clusters = cluster_failures(same_run + more)

    for cluster in clusters:
        details = {f.detail for f in cluster.failures}
        assert len(details) == 1, details


def test_n3_reopening_a_rejected_proposal_needs_authority() -> None:
    """`settle()` rewrote status to anything the caller passed, with no capability check
    and no evidence, so a REJECTED record could simply be moved back to OPEN and vanish
    from `rejected_signatures()`."""
    from software_factory.improvement.loop import (
        LoopState,
        ProposalStatus,
        settle,
    )

    state = LoopState()

    with pytest.raises(TypeError):
        settle(state, "p1", ProposalStatus.OPEN)  # type: ignore[call-arg]


# ------------------------------------------------------------------------------- I2


def test_i2_the_spend_cap_stops_a_halted_factory_starting_work(tmp_path) -> None:
    """The spend cap was a report. Nothing consulted it.

    `CapState.accepts_new_work` and `continues_running_work` -- the entire behavioural half
    -- were referenced only by tests, so a factory past its cap carried on spending.
    """
    from datetime import timedelta

    from software_factory.economics import Cause, Charge, SpendCap
    from software_factory.economics.spend import Ledgerless

    cap = SpendCap(scope="f", limit_units=10.0, period=timedelta(days=1))
    report = Ledgerless(cap).report(
        [
            Charge(
                units=13.0, work_item_id="wi-0", agent="builder", stage="BUILD", cause=Cause.PRIMARY
            )
        ]
    )

    assert not report.state.accepts_new_work
    assert not report.state.continues_running_work


def test_i2_charges_are_folded_by_the_economics_module_not_the_cli(tmp_path) -> None:
    """The fold lived in the CLI, so the coordinator could not consult the same number.

    A cap the operator sees and a cap the factory enforces have to be the same
    computation, or "within budget" means two different things depending on who asks.
    """
    from software_factory.economics.spend import charges_from
    from software_factory.ledger import EntryType, Ledger

    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        EntryType.MODEL_CALLED,
        actor="builder",
        subject="run-1",
        payload={"costUnits": 4.0, "workItem": "wi-1", "agent": "builder", "stage": "BUILD"},
    )

    charges = charges_from(ledger.read())

    assert [c.units for c in charges] == [4.0]
    assert charges[0].work_item_id == "wi-1"


def test_i2_an_unavailable_adapter_is_actually_checked(tmp_path) -> None:
    """`Registry.check()` was never called, so `accepts()` always returned True and the
    provider-unavailable branch of intake was unreachable."""
    from software_factory.intake import Provider, Refused
    from software_factory.intake.adapters import Registry
    from software_factory.intake.pipeline import Pipeline

    registry = Registry()
    registry.register(SickAdapter(Provider.GIT_HOST))
    pipeline = Pipeline(registry=registry)

    outcomes = pipeline.receive(factory_event())

    assert isinstance(outcomes[0], Refused)
    assert outcomes[0].code == "intake.provider_unavailable"
    assert outcomes[0].parks_work


def test_i2_a_checkpoint_is_opened_when_a_block_needs_a_person(tmp_path) -> None:
    """`CheckpointBook` had no caller, so a factory enforced zero human checkpoints.

    `checkpoints.py` even told users to run `sf checkpoints`, a command that did not exist.
    """
    from software_factory.identity import (
        Capability,
        CheckpointKind,
        Directory,
        Principal,
        PrincipalKind,
    )
    from software_factory.identity.checkpoints import Checkpoint, CheckpointBook

    book = CheckpointBook(
        directory=Directory(
            [
                Principal(
                    id="amaya",
                    kind=PrincipalKind.PERSON,
                    capabilities=frozenset({Capability.ANSWER_QUESTION}),
                )
            ]
        )
    )

    opened = book.open(
        Checkpoint(
            id="cp-1",
            kind=CheckpointKind.QUESTION,
            work_item_id="wi-1",
            question="which importer?",
            asked_by="conductor",
        )
    )

    assert book.routable_to(opened.id) == ["amaya"]
