"""Regressions for the critical findings of the adversarial code review.

Each test reproduces one finding as it was originally exploited and asserts it no longer
works. They live together rather than scattered across the suite because they share a
purpose: these are the bugs that defeated a claim the code makes elsewhere, and they are
the ones most likely to be reintroduced by a well-meaning refactor.

Findings are recorded in `docs/reviews/code-review.md`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from software_factory.definition.models import Effect, NetworkPolicy, Stage
from software_factory.evals import GateContext, GateOutcome, run_gates
from software_factory.harness.tools import (
    Example,
    Grants,
    Tool,
    ToolRegistry,
    ToolSuccess,
)
from software_factory.ledger import EntryType, Ledger, LedgerError
from software_factory.memory import (
    Candidate,
    Kind,
    Memory,
    MemoryStore,
    PromotionCriterion,
    Scope,
    Source,
    SourceKind,
    admit,
    consolidate,
    promote,
)
from software_factory.memory.similarity import negates, tokens
from software_factory.orchestrator import (
    DEFAULT_NON_SKIPPABLE,
    DEFAULT_TRANSITIONS,
    Blocker,
    SourceContext,
    StageMachine,
    Transition,
    TransitionRefused,
    WorkItem,
    new_id,
    validate_graph,
)
from software_factory.orchestrator.workitem import DEFAULT_ORDER
from software_factory.runtime import (
    ExecutorError,
    LocalExecutor,
    SandboxLevel,
    SandboxPolicy,
)
from software_factory.spec import (
    Change,
    ChangeKind,
    CodeAnchor,
    SpecDelta,
    SpecUnit,
    UnitStatus,
    apply_delta,
)
from software_factory.spec.units import TrustClass


def work(stage: Stage = Stage.BUILD) -> WorkItem:
    return WorkItem(
        id=new_id(),
        factory="f",
        title="t",
        request="r",
        source=SourceContext(provider="cli", kind="direct", ref="local"),
        stage=stage,
    )


# ------------------------------------------------------------------------------- C1


def test_c1_review_cannot_be_skipped_by_parking_and_resuming() -> None:
    """Park at BUILD, then resume straight to HANDOFF: review must not disappear.

    The skip check measured from BLOCKED, which has no position, so it reported that
    nothing was skipped and the non-skippable rule never ran.
    """
    machine = StageMachine()
    item = work(Stage.BUILD)
    machine.block(item, Blocker.AWAITING_HUMAN, actor="conductor", action="wait for the reporter")

    outcome = machine.advance(item, Stage.HANDOFF, actor="conductor", reason="they replied")

    assert isinstance(outcome, TransitionRefused)
    assert outcome.code == "stage.non_skippable"
    assert "REVIEW" in outcome.message


def test_c1_resuming_to_a_legitimate_stage_still_works() -> None:
    """The fix must not make a parked item unresumable."""
    machine = StageMachine()
    item = work(Stage.BUILD)
    machine.block(item, Blocker.AWAITING_CI, actor="conductor", action="wait for CI")

    outcome = machine.advance(item, Stage.REVIEW, actor="conductor", reason="CI finished")

    assert isinstance(outcome, Transition)
    assert item.parked_at is None


def test_c1_skip_order_does_not_depend_on_dict_ordering() -> None:
    """A security control must not change behaviour with a dict literal's key order."""
    reordered = {
        Stage.REVIEW: DEFAULT_TRANSITIONS[Stage.REVIEW],
        **DEFAULT_TRANSITIONS,
    }
    machine = StageMachine(transitions=reordered)

    assert Stage.REVIEW in machine.skipped_between(Stage.BUILD, Stage.HANDOFF)


def test_c1_a_non_skippable_stage_outside_the_order_is_reported() -> None:
    problems = validate_graph(
        DEFAULT_TRANSITIONS,
        frozenset({Stage.INTAKE, Stage.BLOCKED}),
        order=DEFAULT_ORDER,
    )

    assert any("absent from the stage order" in problem for problem in problems)


def test_c1_the_default_graph_remains_valid() -> None:
    assert validate_graph(DEFAULT_TRANSITIONS, DEFAULT_NON_SKIPPABLE, DEFAULT_ORDER) == []


# ------------------------------------------------------------------------------- C2


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    memory = MemoryStore(tmp_path / "memory.jsonl")
    memory.load()
    return memory


def write(store: MemoryStore, content: str, ref: str, trust: TrustClass) -> Memory:
    outcome = admit(
        Candidate(
            kind=Kind.CONVENTION,
            scope=Scope.REPOSITORY,
            scope_ref="acme/payments",
            content=content,
            provenance=(Source(kind=SourceKind.RUN, ref=ref),),
            trust=trust,
        ),
        store,
    )
    assert isinstance(outcome, Memory), outcome
    return outcome


def test_c2_an_untrusted_candidate_cannot_absorb_a_canon_memory(store: MemoryStore) -> None:
    """Consolidation clustered across lanes and ignored trust, so attacker text could
    archive a canon convention and take its place."""
    canon = write(
        store,
        "The api gateway requires a bearer token issued by our identity service.",
        "human-1",
        TrustClass.OPERATOR,
    )
    promote(
        canon,
        store,
        criterion=PromotionCriterion.HUMAN,
        evidence=("maintainer confirmed",),
        actor="human:maintainer",
    )
    write(
        store,
        "The api gateway requires a bearer token issued by evilcorp and rotated hourly.",
        "issue-1",
        TrustClass.UNTRUSTED,
    )

    consolidate(store)

    surviving = store.get(canon.id)
    assert surviving is not None
    assert surviving.lane.value == "canon"
    assert surviving.superseded_by is None


def test_c2_a_canon_memory_cannot_swallow_untrusted_provenance(store: MemoryStore) -> None:
    canon = write(store, "Deploys are gated on the staging smoke suite.", "h1", TrustClass.OPERATOR)
    promote(
        canon,
        store,
        criterion=PromotionCriterion.HUMAN,
        evidence=("maintainer confirmed",),
        actor="human:maintainer",
    )
    write(
        store,
        "Deploys are gated on the staging smoke suite always.",
        "issue-1",
        TrustClass.UNTRUSTED,
    )

    consolidate(store)

    surviving = store.get(canon.id)
    assert surviving is not None
    assert {source.ref for source in surviving.provenance} == {"h1"}
    assert surviving.trust is TrustClass.OPERATOR


def test_c2_same_lane_same_trust_memories_still_consolidate(store: MemoryStore) -> None:
    """The fix must not disable consolidation for the case it is for.

    The two claims are close enough to cluster but far enough apart that admission does
    not refuse the second as a duplicate -- which is the shape consolidation exists for:
    a general memory subsuming a more specific one.
    """
    write(
        store,
        "The importer normalises header encodings before parsing.",
        "run-1",
        TrustClass.INTERNAL,
    )
    write(
        store,
        "The importer normalises header encodings before parsing every delimited upload.",
        "run-2",
        TrustClass.INTERNAL,
    )

    report = consolidate(store)

    assert report.merged
    survivor = store.get(report.merged[0][1])
    assert survivor is not None
    assert {source.ref for source in survivor.provenance} == {"run-1", "run-2"}


# --------------------------------------------------------------------------- C3, C4, C9


def policy(workspace: Path, **kwargs) -> SandboxPolicy:
    base: dict[str, object] = {"workspace": workspace, "wall_clock_s": 20}
    base.update(kwargs)
    return SandboxPolicy(**base)  # type: ignore[arg-type]


def test_c3_secret_values_never_appear_in_captured_output(tmp_path: Path) -> None:
    """redact() existed and was called from nowhere."""
    executor = LocalExecutor(
        policy(tmp_path, secrets={"SF_TOKEN": "super-secret-value-123"}),
        level=SandboxLevel.PROCESS,
    )

    result = executor.run([sys.executable, "-c", "import os; print(os.environ['SF_TOKEN'])"])

    assert "super-secret-value-123" not in result.stdout
    assert "redacted" in result.stdout


def test_c3_secrets_are_redacted_from_a_timed_out_run(tmp_path: Path) -> None:
    executor = LocalExecutor(
        policy(tmp_path, secrets={"SF_TOKEN": "super-secret-value-123"}),
        level=SandboxLevel.PROCESS,
    )

    result = executor.run(
        [
            sys.executable,
            "-c",
            "import os,sys,time; sys.stdout.write(os.environ['SF_TOKEN']); "
            "sys.stdout.flush(); time.sleep(30)",
        ],
        timeout_s=1,
    )

    assert "super-secret-value-123" not in result.stdout


@pytest.mark.parametrize("path", ["/tmp/../etc/passwd", "/tmpevil/x", "/var/tmp/../../etc/shadow"])
def test_c4_traversal_out_of_a_tolerated_prefix_is_not_benign(tmp_path: Path, path: str) -> None:
    """A bare startswith on the unresolved string made one `..` invisible to the gate."""
    from software_factory.evals.gates import ViolationClass

    assert policy(tmp_path).classify_write(Path(path)) is ViolationClass.BLOCKED


def test_c4_a_real_cache_write_is_still_benign(tmp_path: Path) -> None:
    from software_factory.evals.gates import ViolationClass

    assert policy(tmp_path).classify_write(Path("/tmp/pip-build")) is ViolationClass.BENIGN


def test_c9_an_unenforceable_allowlist_is_refused_not_silently_opened(
    tmp_path: Path,
) -> None:
    """`sf audit` reported the allowlist as a control while nothing enforced it."""
    with pytest.raises(ExecutorError, match="cannot enforce a per-host network allowlist"):
        LocalExecutor(
            policy(tmp_path, network=NetworkPolicy.ALLOWLIST, network_allowlist=("pypi.org",)),
            level=SandboxLevel.PROCESS,
        )


def test_c9_none_and_open_are_still_accepted(tmp_path: Path) -> None:
    assert LocalExecutor(policy(tmp_path, network=NetworkPolicy.NONE), level=SandboxLevel.PROCESS)
    assert LocalExecutor(policy(tmp_path, network=NetworkPolicy.OPEN), level=SandboxLevel.PROCESS)


# ------------------------------------------------------------------------------- C5


def active_unit() -> SpecUnit:
    return SpecUnit(
        id="PAY-1",
        title="BOM handling",
        status=UnitStatus.ACTIVE,
        intent="The importer strips a byte-order mark from CSV headers.",
        implements=(CodeAnchor(path="importer.py", symbol="strip_bom"),),
    )


def test_c5_reanchoring_an_active_unit_to_nothing_is_refused() -> None:
    """model_copy skipped the validators, leaving an anchorless ACTIVE unit that
    evaluate() then reported as AGREED - permanently satisfying the spec gate."""
    delta = SpecDelta(
        id="D1",
        work_item="W1",
        changes=[Change(kind=ChangeKind.REANCHOR, unit_id="PAY-1", anchors=())],
    )

    with pytest.raises(ValueError, match="implements"):
        apply_delta(delta, {"PAY-1": active_unit()})


def test_c5_add_stores_the_unit_under_its_own_id() -> None:
    successor = SpecUnit(
        id="PAY-9",
        title="New",
        status=UnitStatus.DRAFT,
        intent="Something new.",
    )
    delta = SpecDelta(
        id="D1",
        work_item="W1",
        changes=[Change(kind=ChangeKind.ADD, unit_id="PAY-9", unit=successor)],
    )

    applied = apply_delta(delta, {})

    assert set(applied) == {"PAY-9"}


def test_c5_supersede_does_not_clobber_an_unrelated_unit() -> None:
    successor = SpecUnit(
        id="PAY-2",
        title="Successor",
        status=UnitStatus.ACTIVE,
        intent="The importer normalises all header encodings.",
        implements=(CodeAnchor(path="importer.py"),),
    )
    delta = SpecDelta(
        id="D1",
        work_item="W1",
        changes=[Change(kind=ChangeKind.SUPERSEDE, unit_id="PAY-1", unit=successor)],
    )

    applied = apply_delta(delta, {"PAY-1": active_unit()})

    assert applied["PAY-1"].status is UnitStatus.DEPRECATED
    assert applied["PAY-1"].title == "BOM handling"
    assert applied["PAY-2"].title == "Successor"


# ------------------------------------------------------------------------------- C6


@pytest.mark.parametrize("stage", ["HANDOFF", "COMPLETE", "INTAKE", "build", "nonsense"])
def test_c6_an_unmapped_stage_errors_rather_than_reporting_clean(stage: str) -> None:
    """HANDOFF is the last point anything could be caught, and it ran zero gates."""
    report = run_gates(GateContext(stage=stage, calibration=object()), stage=stage)

    assert report.blocked
    assert report.results[0].outcome is GateOutcome.ERROR
    assert "no gate set" in report.results[0].detail


def test_c6_a_mapped_stage_still_runs_its_gates() -> None:
    report = run_gates(GateContext(stage="TRIAGE", calibration=object()), stage="TRIAGE")

    assert {r.gate for r in report.results} == {"calibration-present", "blast-radius-clean"}


# ------------------------------------------------------------------------------- C7


def exec_tool() -> Tool:
    return Tool(
        name="proc.run",
        description="Run a command.",
        effect=Effect.EXEC,
        input_schema={"type": "object", "properties": {}, "required": []},
        output_schema={"type": "object"},
        handler=lambda _args: ToolSuccess(value={}),
        examples=(Example(inputs={}, output="{}"),),
    )


def test_c7_one_runs_violation_does_not_terminate_the_next() -> None:
    """Violations are cumulative on a shared registry, so `any?` was always true after
    the first offence - terminating every later run and leaking its text."""
    registry = ToolRegistry()
    registry.register(exec_tool())
    registry.call(
        "proc.run",
        {},
        grants=Grants(tools=frozenset({"proc.run"}), effects=frozenset({Effect.READ})),
    )

    mark = registry.violation_mark()

    assert registry.escalating_violations()
    assert registry.escalating_violations(since=mark) == []


# ------------------------------------------------------------------------------- C8


def test_c8_a_torn_ledger_append_is_recoverable(tmp_path: Path) -> None:
    """One crash or full disk mid-write made the ledger permanently unwritable."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(EntryType.RUN_STARTED, actor="a", subject="run-1")
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "ts": "2026')

    assert ledger.torn_tail()
    entry = ledger.append(EntryType.RUN_FINISHED, actor="a", subject="run-1")

    assert entry.seq == 2
    ledger.verify()


def test_c8_verify_reports_a_torn_tail_rather_than_ignoring_it(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(EntryType.RUN_STARTED, actor="a", subject="run-1")
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "ts": "2026')

    with pytest.raises(LedgerError, match="incomplete"):
        ledger.verify()


def test_c8_tampering_in_the_middle_still_raises(tmp_path: Path) -> None:
    """Torn-tail recovery must not become a licence to edit the log."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for index in range(3):
        ledger.append(EntryType.RUN_STARTED, actor="a", subject=f"run-{index}")
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    lines[1] = "{not json"
    ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(LedgerError, match="malformed"):
        list(ledger.read())


def test_c8_a_torn_memory_append_is_recoverable(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.jsonl")
    store.load()
    write(store, "A first claim about header parsing order.", "run-1", TrustClass.INTERNAL)
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write('{"op": "put", "mem')

    reloaded = MemoryStore(tmp_path / "memory.jsonl")
    reloaded.load()

    assert len(reloaded.all()) == 1
    assert isinstance(
        write(reloaded, "A second distinct claim about delimiters.", "run-2", TrustClass.INTERNAL),
        Memory,
    )


# ------------------------------------------------------------------------------ C10


def test_c10_erase_removes_the_content_from_the_log(tmp_path: Path) -> None:
    """Appending a tombstone is not erasure: the original record stayed, greppable."""
    store = MemoryStore(tmp_path / "memory.jsonl")
    store.load()
    target = write(store, "SENSITIVE-SUBJECT-DATA-marker text.", "run-1", TrustClass.INTERNAL)

    store.erase(target.id, actor="human:dpo", reason="erasure request")

    raw = store.path.read_text(encoding="utf-8")
    assert "SENSITIVE-SUBJECT-DATA-marker" not in raw
    assert "erasure request" in raw


def test_c10_erase_leaves_neighbours_intact(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.jsonl")
    store.load()
    keep = write(store, "An unrelated claim about column ordering.", "run-0", TrustClass.INTERNAL)
    target = write(store, "SENSITIVE-SUBJECT-DATA-marker text.", "run-1", TrustClass.INTERNAL)

    store.erase(target.id, actor="human:dpo", reason="erasure request")
    reloaded = MemoryStore(store.path)
    reloaded.load()

    assert reloaded.get(keep.id) is not None
    assert reloaded.get(target.id) is None


# ------------------------------------------------------------------------------ C11


def test_c11_the_tokenizer_keeps_short_negators() -> None:
    """The length filter ate "no", making a whole class of contradiction invisible."""
    assert "no" in tokens("there is no retry on the payments webhook")


def test_c11_a_contradiction_phrased_with_no_is_detected() -> None:
    assert negates(
        "The payments webhook has retries enabled for transient failures.",
        "The payments webhook has no retries enabled for transient failures.",
    )


def test_c11_admission_refuses_a_no_phrased_contradiction_against_canon(
    store: MemoryStore,
) -> None:
    canon = write(
        store,
        "The payments webhook has retries enabled for transient failures.",
        "h1",
        TrustClass.OPERATOR,
    )
    promote(
        canon,
        store,
        criterion=PromotionCriterion.HUMAN,
        evidence=("maintainer confirmed",),
        actor="human:maintainer",
    )

    outcome = admit(
        Candidate(
            kind=Kind.CONVENTION,
            scope=Scope.REPOSITORY,
            scope_ref="acme/payments",
            content="The payments webhook has no retries enabled for transient failures.",
            provenance=(Source(kind=SourceKind.RUN, ref="run-2"),),
        ),
        store,
    )

    assert not isinstance(outcome, Memory)
    assert outcome.reason.value == "contradiction"
