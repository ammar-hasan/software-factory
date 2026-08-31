"""The memory fabric: admission, promotion, policing, and retrieval.

The tests that matter most here are the refusals. A memory system is easy to make
permissive and hard to make trustworthy, so most of what follows checks that something
did *not* get in, did *not* get promoted, or did *not* reach an agent.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from software_factory.memory import (
    Candidate,
    Corroboration,
    Kind,
    Lane,
    Memory,
    MemoryStore,
    PromotionCriterion,
    PromotionRefused,
    Rejected,
    RejectionReason,
    Scope,
    ScopeBudget,
    Source,
    SourceKind,
    admit,
    blast_radius,
    consolidate,
    demote,
    detect_contradictions,
    enforce_budget,
    expire_and_decay,
    invalidate,
    promote,
    record_use,
    retrieve,
    revalidate_anchors,
)
from software_factory.memory.records import utc_now
from software_factory.memory.retrieval import RetrievalRequest
from software_factory.spec.units import TrustClass, digest_text


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    memory_store = MemoryStore(tmp_path / "memory.jsonl")
    memory_store.load()
    return memory_store


def source(ref: str = "run-1", *, kind: SourceKind = SourceKind.RUN, locator: str = "") -> Source:
    return Source(kind=kind, ref=ref, locator=locator)


def candidate(
    content: str = "The payments importer reads headers as UTF-8 with a BOM.",
    *,
    kind: Kind = Kind.FACT,
    sources: tuple[Source, ...] = (),
    trust: TrustClass = TrustClass.INTERNAL,
    scope_ref: str = "acme/payments",
    parents: tuple[str, ...] = (),
) -> Candidate:
    return Candidate(
        kind=kind,
        scope=Scope.REPOSITORY,
        scope_ref=scope_ref,
        content=content,
        provenance=sources or (source(),),
        trust=trust,
        parents=parents,
    )


def admitted(store: MemoryStore, **kwargs) -> Memory:
    result = admit(candidate(**kwargs), store)
    assert isinstance(result, Memory), result
    return result


# ------------------------------------------------------------------ admission control


def test_a_well_formed_candidate_lands_in_the_candidate_lane(store: MemoryStore) -> None:
    memory = admitted(store)

    assert memory.lane is Lane.CANDIDATE
    assert store.get(memory.id) is not None


def test_an_unsourced_candidate_is_refused(store: MemoryStore) -> None:
    result = admit(
        Candidate(
            kind=Kind.FACT,
            scope=Scope.REPOSITORY,
            scope_ref="acme/payments",
            content="Something true.",
            provenance=(),
        ),
        store,
    )

    assert isinstance(result, Rejected)
    assert result.reason is RejectionReason.UNSOURCED


def test_an_empty_candidate_is_refused(store: MemoryStore) -> None:
    result = admit(candidate("   "), store)

    assert isinstance(result, Rejected)
    assert result.reason is RejectionReason.INCOMPLETE


def test_a_compound_claim_is_refused(store: MemoryStore) -> None:
    """A memory holding two claims cannot be selectively invalidated."""
    result = admit(
        candidate("The importer strips BOMs. The exporter writes CRLF line endings."), store
    )

    assert isinstance(result, Rejected)
    assert result.reason is RejectionReason.COMPOUND_CLAIM


def test_an_overlong_claim_is_refused(store: MemoryStore) -> None:
    result = admit(candidate("word " * 200), store)

    assert isinstance(result, Rejected)
    assert result.reason is RejectionReason.COMPOUND_CLAIM


@pytest.mark.parametrize(
    "secret",
    [
        "the key is sk-abcdefghijklmnopqrstuvwx",
        "use ghp_abcdefghijklmnopqrstuvwxyz0123",
        "AKIAIOSFODNN7EXAMPLE is the access key",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_secret_shaped_content_is_refused(store: MemoryStore, secret: str) -> None:
    """A false positive costs one memory; a false negative writes a secret into prompts."""
    result = admit(candidate(secret), store)

    assert isinstance(result, Rejected)
    assert result.reason is RejectionReason.SECRET_SUSPECTED
    assert "rotate" in result.remediation


def test_an_anchor_without_a_locator_is_refused(store: MemoryStore) -> None:
    result = admit(candidate(kind=Kind.ANCHOR), store)

    assert isinstance(result, Rejected)
    assert result.reason is RejectionReason.INCOMPLETE


def test_a_near_duplicate_is_refused_in_favour_of_merging(store: MemoryStore) -> None:
    admitted(store)

    result = admit(candidate("The payments importer reads headers as UTF-8 with a BOM."), store)

    assert isinstance(result, Rejected)
    assert result.reason is RejectionReason.DUPLICATE


def test_a_claim_contradicting_canon_is_refused(store: MemoryStore) -> None:
    memory = admitted(store, content="The importer must strip a byte-order mark from headers.")
    promote(
        memory,
        store,
        criterion=PromotionCriterion.HUMAN,
        evidence=("maintainer confirmed",),
        actor="human:maintainer",
    )

    result = admit(candidate("The importer must not strip a byte-order mark from headers."), store)

    assert isinstance(result, Rejected)
    assert result.reason is RejectionReason.CONTRADICTION


def test_writing_outside_a_granted_scope_is_refused(store: MemoryStore) -> None:
    result = admit(candidate(), store, granted_scopes={"repository:other/repo"})

    assert isinstance(result, Rejected)
    assert result.reason is RejectionReason.OUT_OF_SCOPE


def test_a_full_scope_refuses_new_writes(store: MemoryStore) -> None:
    admitted(store, content="A first distinct claim about header parsing behaviour.")

    result = admit(
        candidate("An entirely separate claim about export row ordering."),
        store,
        budget=ScopeBudget(max_items=1, max_bytes=10_000),
    )

    assert isinstance(result, Rejected)
    assert result.reason is RejectionReason.BUDGET


# ---------------------------------------------------------------------- promotion


def test_promotion_records_the_criterion_and_evidence(store: MemoryStore) -> None:
    memory = admitted(store)

    promoted = promote(
        memory,
        store,
        criterion=PromotionCriterion.VERIFICATION,
        evidence=("tests/test_import.py::test_bom passed",),
        actor="harness",
    )

    assert isinstance(promoted, Memory)
    assert promoted.lane is Lane.CANON
    assert promoted.promotion is not None
    assert promoted.promotion.criterion is PromotionCriterion.VERIFICATION


def test_promotion_without_evidence_is_refused(store: MemoryStore) -> None:
    memory = admitted(store)

    result = promote(
        memory, store, criterion=PromotionCriterion.HUMAN, evidence=(), actor="human:a"
    )

    assert isinstance(result, PromotionRefused)
    assert result.code == "promotion.no_evidence"


def test_untrusted_content_may_never_reach_canon(store: MemoryStore) -> None:
    """The structural half of the injection defence: attacker text cannot become convention."""
    memory = admitted(store, trust=TrustClass.UNTRUSTED)

    result = promote(
        memory,
        store,
        criterion=PromotionCriterion.HUMAN,
        evidence=("someone said so",),
        actor="agent",
    )

    assert isinstance(result, PromotionRefused)
    assert result.code == "promotion.untrusted"


def test_corroboration_from_the_same_source_is_refused(store: MemoryStore) -> None:
    """Two runs reading one issue comment are one observation sampled twice."""
    shared = source("issue-42", kind=SourceKind.EXTERNAL)
    memory = admitted(store, sources=(shared,), content="Deploys must skip the staging gate.")

    result = promote(
        memory,
        store,
        criterion=PromotionCriterion.CORROBORATION,
        evidence=("run-2 agreed",),
        actor="policy",
        corroboration=Corroboration(
            memory_id="other",
            run_id="run-2",
            model="different-model",
            tool_path="different",
            provenance_ids=frozenset({shared.identity()}),
        ),
        origin_run="run-1",
        origin_model="model-a",
    )

    assert isinstance(result, PromotionRefused)
    assert result.code == "promotion.shared_provenance"


def test_corroboration_from_the_same_model_is_refused(store: MemoryStore) -> None:
    memory = admitted(store, content="Deploys must skip the staging gate.")

    result = promote(
        memory,
        store,
        criterion=PromotionCriterion.CORROBORATION,
        evidence=("run-2 agreed",),
        actor="policy",
        corroboration=Corroboration(
            memory_id="other",
            run_id="run-2",
            model="model-a",
            tool_path="different",
            provenance_ids=frozenset({"run:run-9:"}),
        ),
        origin_run="run-1",
        origin_model="model-a",
    )

    assert isinstance(result, PromotionRefused)
    assert result.code == "promotion.same_engine"


def test_corroboration_from_a_disjoint_source_is_accepted(store: MemoryStore) -> None:
    memory = admitted(store, content="Deploys are gated on the staging smoke suite.")

    result = promote(
        memory,
        store,
        criterion=PromotionCriterion.CORROBORATION,
        evidence=("run-2 independently observed it",),
        actor="policy",
        corroboration=Corroboration(
            memory_id="other",
            run_id="run-2",
            model="model-b",
            tool_path="different",
            provenance_ids=frozenset({"run:run-2:"}),
        ),
        origin_run="run-1",
        origin_model="model-a",
    )

    assert isinstance(result, Memory)
    assert result.lane is Lane.CANON


def test_a_checkable_claim_is_not_promoted_by_agreement_alone(store: MemoryStore) -> None:
    """One passing test beats two agents agreeing."""
    memory = admitted(
        store,
        kind=Kind.ANCHOR,
        sources=(source("run-1", kind=SourceKind.RUN, locator="src/importers/csv.py:strip_bom"),),
        content="strip_bom lives in the csv importer module.",
    )

    result = promote(
        memory,
        store,
        criterion=PromotionCriterion.CORROBORATION,
        evidence=("run-2 agreed",),
        actor="policy",
        corroboration=Corroboration(
            memory_id="other",
            run_id="run-2",
            model="model-b",
            tool_path="different",
            provenance_ids=frozenset({"run:run-2:"}),
        ),
        origin_run="run-1",
        origin_model="model-a",
    )

    assert isinstance(result, PromotionRefused)
    assert result.code == "promotion.verification_available"


def test_an_archived_memory_does_not_re_enter_canon(store: MemoryStore) -> None:
    memory = admitted(store)
    demote(memory, store, reason="superseded")

    result = promote(
        memory,
        store,
        criterion=PromotionCriterion.HUMAN,
        evidence=("changed my mind",),
        actor="human:a",
    )

    assert isinstance(result, PromotionRefused)
    assert result.code == "promotion.wrong_lane"


# ------------------------------------------------------------------------ policing


def test_a_contradiction_quarantines_both_sides(store: MemoryStore) -> None:
    """Recency does not imply correctness; when code is reverted the older claim wins."""
    left = admitted(store, content="Retries are enabled for the payments webhook.")
    right = admitted(store, content="Retries are disabled for the payments webhook.")

    report = detect_contradictions(store)

    assert set(report.quarantined) == {left.id, right.id}
    assert store.get(left.id).quarantined
    assert store.get(right.id).quarantined


def test_the_policy_pass_is_idempotent(store: MemoryStore) -> None:
    """`policing.py` makes this claim for the *whole* pass, and the test ran one quarter
    of it: `revalidate_anchors` violated it outright (M1) and consolidation merged across
    lanes (C2), both invisible to a test that only called `detect_contradictions`.
    """
    from software_factory.memory.policing import run_pass
    from software_factory.spec.units import digest_text

    admitted(store, content="Retries are enabled for the payments webhook.")
    admitted(store, content="Retries are disabled for the payments webhook.")
    admitted(store, content="The importer reads headers as UTF-8 with a byte-order mark.")

    anchored = admitted(store, content="strip_bom lstrips the BOM from the first cell.")
    anchored.kind = Kind.ANCHOR
    anchored.provenance = (
        Source(
            kind=SourceKind.FILE,
            ref="src/importers/csv.py",
            locator="src/importers/csv.py:strip_bom",
            excerpt_digest=digest_text("def strip_bom(text):\n    return text\n"),
        ),
    )
    store.put(anchored, op="anchor", actor="test", reason="fixture")

    # The anchor has drifted, and stays drifted: the world does not change between passes.
    def resolve(_locator: str) -> str:
        return "def strip_bom(text):\n    return text.lstrip('\\ufeff')\n"

    first = run_pass(store, resolve=resolve)
    second = run_pass(store, resolve=resolve)

    assert first.acted, "the fixture did not exercise the pass"
    assert not second.acted, "running the pass twice on an unchanged store acted twice"


def test_expired_memories_are_archived(store: MemoryStore) -> None:
    memory = admitted(store)
    memory.expires_on = utc_now() - timedelta(days=1)
    store.put(memory, op="test", actor="test", reason="force expiry")

    report = expire_and_decay(store)

    assert memory.id in report.expired
    assert store.get(memory.id).lane is Lane.ARCHIVE


def test_an_anchor_whose_target_vanished_is_archived(store: MemoryStore) -> None:
    memory = admitted(
        store,
        kind=Kind.ANCHOR,
        sources=(source(locator="src/importers/csv.py:strip_bom"),),
        content="strip_bom lives in the csv importer module.",
    )

    report = revalidate_anchors(store, resolve=lambda _locator: None)

    assert memory.id in report.stale
    assert store.get(memory.id).lane is Lane.ARCHIVE


def test_an_anchor_whose_target_changed_is_weakened_not_archived(store: MemoryStore) -> None:
    original = "def strip_bom(text):\n    return text\n"
    memory = admitted(
        store,
        kind=Kind.ANCHOR,
        sources=(
            Source(
                kind=SourceKind.FILE,
                ref="src/importers/csv.py",
                locator="src/importers/csv.py:strip_bom",
                excerpt_digest=digest_text(original),
            ),
        ),
        content="strip_bom lives in the csv importer module.",
    )
    before = memory.confidence

    report = revalidate_anchors(store, resolve=lambda _locator: original.replace("text", "value"))

    assert memory.id in report.weakened
    assert store.get(memory.id).lane is Lane.CANDIDATE
    assert store.get(memory.id).confidence < before


def test_consolidation_preserves_the_union_of_provenance(store: MemoryStore) -> None:
    """A merged memory is better-sourced than any of its inputs, never worse."""
    text = "The importer normalises header encodings before parsing."
    first = admitted(store, content=text, sources=(source("run-1"),))
    first.lane = Lane.CANON
    store.put(first, op="test", actor="test", reason="setup")
    second = Memory(
        id=store.new_id(),
        lane=Lane.CANON,
        kind=Kind.FACT,
        scope=Scope.REPOSITORY,
        scope_ref="acme/payments",
        content=text + " ",
        provenance=(source("run-2"),),
    )
    store.put(second, op="test", actor="test", reason="setup")

    report = consolidate(store)

    assert report.merged
    survivor_id = report.merged[0][1]
    survivor = store.get(survivor_id)
    assert {s.ref for s in survivor.provenance} == {"run-1", "run-2"}


def test_quarantined_memories_are_not_merged(store: MemoryStore) -> None:
    text = "The importer normalises header encodings before parsing."
    first = admitted(store, content=text, sources=(source("run-1"),))
    first.quarantined = True
    store.put(first, op="test", actor="test", reason="setup")

    assert consolidate(store).merged == []


# ------------------------------------------------------- poisoning containment


def test_invalidation_archives_descendants_whose_provenance_collapses(
    store: MemoryStore,
) -> None:
    root = admitted(store, content="Deploys are gated on the staging smoke suite.")
    child = admitted(
        store,
        content="Because deploys are gated, hotfixes wait for staging.",
        parents=(root.id,),
    )

    report = invalidate(store, root.id, reason="source retracted")

    assert root.id in report.invalidated
    assert child.id in report.invalidated
    assert store.get(child.id).lane is Lane.ARCHIVE


def test_a_descendant_with_an_independent_parent_is_weakened_not_archived(
    store: MemoryStore,
) -> None:
    """Partial collapse penalises; it does not silently keep full confidence either."""
    root = admitted(store, content="Deploys are gated on the staging smoke suite.")
    other = admitted(store, content="Hotfix branches are cut from the release tag.")
    child = admitted(
        store,
        content="Hotfixes therefore wait for the staging smoke suite to finish.",
        parents=(root.id, other.id),
    )
    before = child.confidence

    report = invalidate(store, root.id, reason="source retracted")

    assert child.id in report.weakened
    assert store.get(child.id).lane is not Lane.ARCHIVE
    assert store.get(child.id).confidence < before


def test_invalidation_cascades_transitively(store: MemoryStore) -> None:
    root = admitted(store, content="Deploys are gated on the staging smoke suite.")
    middle = admitted(store, content="Hotfixes wait for staging to finish.", parents=(root.id,))
    leaf = admitted(
        store,
        content="Therefore the hotfix runbook starts with a staging check.",
        parents=(middle.id,),
    )

    report = invalidate(store, root.id, reason="source retracted")

    assert {root.id, middle.id, leaf.id} <= set(report.invalidated)


def test_blast_radius_reports_what_would_be_affected(store: MemoryStore) -> None:
    root = admitted(store, content="Deploys are gated on the staging smoke suite.")
    child = admitted(store, content="Hotfixes wait for staging.", parents=(root.id,))

    impact = blast_radius(store, root.id)

    assert impact["descendants"] == [child.id]
    assert impact["total"] == 1


# ---------------------------------------------------------------- single-source cap


def test_a_single_source_memory_is_confidence_capped(store: MemoryStore) -> None:
    memory = admitted(store)
    memory.confidence = 0.95

    assert memory.effective_confidence() == pytest.approx(0.6)


def test_a_promoted_memory_is_not_capped(store: MemoryStore) -> None:
    memory = admitted(store)
    memory.confidence = 0.95
    promote(
        memory,
        store,
        criterion=PromotionCriterion.VERIFICATION,
        evidence=("a test passed",),
        actor="harness",
    )

    assert memory.effective_confidence() == pytest.approx(0.95)


# ------------------------------------------------------------------------ retrieval


def canon(store: MemoryStore, content: str, *, sources: tuple[Source, ...] = ()) -> Memory:
    memory = admitted(store, content=content, sources=sources or (source(f"run-{content[:6]}"),))
    promote(
        memory,
        store,
        criterion=PromotionCriterion.HUMAN,
        evidence=("maintainer confirmed",),
        actor="human:maintainer",
    )
    return memory


def request(**kwargs) -> RetrievalRequest:
    base = {
        "query": "importer header parsing",
        "scopes": ((Scope.REPOSITORY, "acme/payments"),),
    }
    base.update(kwargs)
    return RetrievalRequest(**base)


def test_retrieval_returns_canon_by_default(store: MemoryStore) -> None:
    canon(store, "The importer parses headers before rows.")
    admitted(store, content="An unproven claim about importer header ordering.")

    result = retrieve(store, request())

    assert len(result.memories) == 1
    assert result.memories[0].lane == "canon"


def test_candidate_memories_appear_only_on_opt_in_and_are_labelled(store: MemoryStore) -> None:
    admitted(store, content="An unproven claim about importer header ordering.")

    result = retrieve(store, request(include_candidate=True))

    assert len(result.memories) == 1
    assert result.memories[0].unverified
    assert "[unverified]" in result.memories[0].render()


def test_a_disputed_memory_never_reaches_an_agent(store: MemoryStore) -> None:
    """Stage 3 is a drop, not a demotion, in every lane.

    Admission only checks contradiction against canon, so two candidates can coexist
    until the policy pass finds them -- which is exactly the state this tests.
    """
    admitted(store, content="Retries are enabled for the importer webhook.")
    admitted(store, content="Retries are disabled for the importer webhook.")
    detect_contradictions(store)

    result = retrieve(store, request(query="retries importer webhook", include_candidate=True))

    assert result.memories == []
    assert result.dropped_disputed == 2


def test_expired_memories_are_not_returned(store: MemoryStore) -> None:
    memory = canon(store, "The importer parses headers before rows.")
    memory.expires_on = utc_now() - timedelta(days=1)
    store.put(memory, op="test", actor="test", reason="force expiry")

    result = retrieve(store, request())

    assert result.memories == []
    assert result.dropped_expired == 1


def test_one_source_cannot_dominate_a_result(store: MemoryStore) -> None:
    """This is what stops one confident extraction colouring every subsequent run."""
    noisy = source("issue-42", kind=SourceKind.EXTERNAL)
    for index in range(8):
        canon(store, f"Importer header rule number {index} about parsing order.", sources=(noisy,))

    result = retrieve(store, request(limit=10))

    assert len(result.memories) <= 3
    assert result.dropped_diversity > 0


def test_results_are_capped_at_the_requested_limit(store: MemoryStore) -> None:
    for index in range(10):
        canon(
            store,
            f"Importer header rule {index} about parsing order.",
            sources=(source(f"run-{index}"),),
        )

    result = retrieve(store, request(limit=3, diversity_cap=1.0))

    assert len(result.memories) == 3


def test_out_of_scope_memories_are_never_returned(store: MemoryStore) -> None:
    canon(store, "The importer parses headers before rows.")

    result = retrieve(store, request(scopes=((Scope.REPOSITORY, "other/repo"),)))

    assert result.memories == []


def test_retrieval_does_not_write(store: MemoryStore) -> None:
    memory = canon(store, "The importer parses headers before rows.")
    before = store.get(memory.id).use_count

    retrieve(store, request())

    assert store.get(memory.id).use_count == before


def test_being_retrieved_is_not_being_useful(store: MemoryStore) -> None:
    """helped_count moves only for a run that then passed its gates."""
    memory = canon(store, "The importer parses headers before rows.")

    record_use(store, [memory.id], helped=False)
    assert store.get(memory.id).use_count == 1
    assert store.get(memory.id).helped_count == 0

    record_use(store, [memory.id], helped=True)
    assert store.get(memory.id).helped_count == 1


# --------------------------------------------------------------------- budget & store


def test_a_scope_shrinks_under_budget_pressure(store: MemoryStore) -> None:
    for index in range(40):
        admitted(
            store,
            content=f"Importer rule {index} covering a distinct parsing behaviour.",
            sources=(source(f"run-{index}"),),
        )

    report = enforce_budget(store, "repository", "acme/payments", max_items=10, max_bytes=10**9)

    live = [
        m for m in store.in_scope(Scope.REPOSITORY, "acme/payments") if m.lane is not Lane.ARCHIVE
    ]
    assert len(live) <= 10
    assert report.evicted


def test_the_index_rebuilds_from_the_log(tmp_path: Path) -> None:
    """The log is the truth; the index is a cache."""
    path = tmp_path / "memory.jsonl"
    first = MemoryStore(path)
    first.load()
    memory = admit(candidate(), first)
    assert isinstance(memory, Memory)

    second = MemoryStore(path)
    second.load()

    assert second.get(memory.id) is not None
    assert second.get(memory.id).content == memory.content


def test_provenance_tree_answers_why_this_exists(store: MemoryStore) -> None:
    root = admitted(store, content="Deploys are gated on the staging smoke suite.")
    child = admitted(store, content="Hotfixes wait for staging.", parents=(root.id,))

    tree = store.provenance_tree(child.id)

    assert tree["found"] is True
    assert tree["parents"][0]["id"] == root.id


def test_erasure_destroys_content_and_records_that_it_happened(store: MemoryStore) -> None:
    """Deletion has to be possible in an append-only design, and auditable.

    The content assertion is the point and was missing (T3). Both of the other two passed
    under C10, where `erase` appended a tombstone and left the original record -- content,
    provenance and all -- in a file whose whole selling point is that it is greppable. For
    a subject-erasure request that is the difference between compliance and a claim of it.
    """
    memory = admitted(store)
    content = memory.content
    assert content in store.path.read_text(encoding="utf-8")

    store.erase(memory.id, actor="human:dpo", reason="erasure request")

    assert store.get(memory.id) is None
    assert content not in store.path.read_text(encoding="utf-8")
    ops = [m.op for m in store.mutations(memory.id)]
    assert "delete" in ops


def test_a_corrupt_log_line_names_its_line_number(tmp_path: Path) -> None:
    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path)
    store.load()
    admit(candidate(), store)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    with pytest.raises(Exception, match=r"memory\.jsonl:2"):
        MemoryStore(path).load()
