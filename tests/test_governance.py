"""Data classification, retention, legal hold, erasure, and ledger segmentation.

The theme is the PRD's own sentence: append-only plus a permanent archive makes compliance
architecturally impossible. These are the mechanisms that make it possible again, and the
tests are mostly about how they compose -- a retention sweep that does not know about holds
is a compliance bug, and an erasure that does not know about retention reports as complete a
job retention was about to redo.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest

from software_factory.governance import (
    DEFAULT_CLASSIFICATION,
    Artifact,
    Classification,
    DataClass,
    HoldReason,
    LegalHold,
    Manifest,
    Retention,
    SegmentError,
    Sensitivity,
    classes_holding,
    classification_for,
    seal,
)
from software_factory.ledger import EntryType, Ledger
from software_factory.memory.records import utc_now

# --------------------------------------------------------------------- classification


def test_every_persisted_class_declares_what_it_can_hold() -> None:
    """A retention policy that does not say what is in the thing being retained is a number
    with no argument behind it."""
    assert set(DEFAULT_CLASSIFICATION) == set(DataClass)
    for rule in DEFAULT_CLASSIFICATION.values():
        assert rule.contains
        assert rule.rationale.strip()


def test_a_class_holding_personal_data_must_be_erasable() -> None:
    """Otherwise the classification is a promise the storage cannot keep."""
    with pytest.raises(ValueError, match="cannot be erased by subject"):
        Classification(
            data_class=DataClass.RECORDING,
            contains=frozenset({Sensitivity.PERSONAL_DATA}),
            retention=timedelta(days=30),
            erasable_by_subject=False,
            rationale="",
        )


def test_a_class_that_can_hold_credentials_must_redact_at_capture() -> None:
    """Retention is not a control for credentials; redaction is (FR-17.3). A credential kept
    for thirty days and then deleted was exposed for thirty days."""
    with pytest.raises(ValueError, match="redaction is"):
        Classification(
            data_class=DataClass.TRANSCRIPT,
            contains=frozenset({Sensitivity.CREDENTIALS}),
            retention=timedelta(days=1),
            erasable_by_subject=True,
            rationale="",
        )


def test_the_ledger_holds_references_and_is_deliberately_not_erasable() -> None:
    """The exception, stated rather than hidden: a ledger holding bodies could not be both
    append-only and erasable, and it is the record of erasure that has to survive."""
    ledger = classification_for(DataClass.LEDGER)

    assert ledger.contains == frozenset({Sensitivity.METADATA})
    assert not ledger.erasable_by_subject
    assert ledger.retention is None


def test_classes_holding_personal_data_are_enumerable() -> None:
    """The question a subject-erasure request starts from."""
    holders = classes_holding(Sensitivity.PERSONAL_DATA)

    assert DataClass.TRANSCRIPT in holders
    assert DataClass.LEDGER not in holders


def test_a_workspace_expires_in_hours_not_days() -> None:
    """It outlives its usefulness the moment its run ends, and holds a granted secret."""
    workspace = classification_for(DataClass.WORKSPACE)

    assert workspace.retention is not None
    assert workspace.retention <= timedelta(hours=12)


# -------------------------------------------------------------------------- retention


def artifact(artifact_id: str, data_class: DataClass, *, age_days: float = 0, **kwargs) -> Artifact:
    base: dict[str, object] = {
        "id": artifact_id,
        "data_class": data_class,
        "created_at": utc_now() - timedelta(days=age_days),
    }
    base.update(kwargs)
    return Artifact(**base)  # type: ignore[arg-type]


def tombstoner() -> Callable[[Artifact], None]:
    """A destructor that does nothing but exist.

    `sweep` and `erase` now require one. Passing this rather than omitting it is the whole
    point of the change: a caller who omits the destructor used to get a report asserting
    deletions that never happened, and several tests here were the ones blessing it.
    """
    return lambda _artifact: None


def test_an_artifact_past_its_retention_expires() -> None:
    """The destructor is observed, not merely supplied.

    This called `sweep` with no destructor at all and asserted `report.acted`: nothing was
    tombstoned, `old.tombstoned` was still False, and the test's name said an artifact
    expired. Passing a no-op destructor would fix the signature and keep the hole, so what
    the destructor *received* is what is asserted.
    """
    retention = Retention()
    old = artifact("t1", DataClass.TRANSCRIPT, age_days=45)
    tombstoned: list[str] = []

    report = retention.sweep([old], tombstone=lambda a: tombstoned.append(a.id))

    assert tombstoned == ["t1"]
    assert report.expired == ["t1"]
    assert report.acted


def test_an_artifact_within_its_retention_is_kept() -> None:
    retention = Retention()

    kept = retention.sweep(
        [artifact("t1", DataClass.TRANSCRIPT, age_days=5)], tombstone=None, dry_run=True
    )
    assert kept.expired == []


def test_a_class_with_no_retention_never_expires_on_age() -> None:
    """Memory has its own lifecycle -- lanes, decay, eviction -- so a blanket age limit
    would fight it."""
    retention = Retention()

    kept = retention.sweep(
        [artifact("m1", DataClass.MEMORY, age_days=3650)], tombstone=None, dry_run=True
    )
    assert kept.expired == []


def test_retention_removes_bodies_and_the_record_survives() -> None:
    """FR-15.10a. A claim on a tombstone renders as "evidence expired" -- never as
    unsupported, and never as satisfied."""
    tombstoned: list[str] = []
    retention = Retention()
    old = artifact("e1", DataClass.EVIDENCE, age_days=400)

    retention.sweep([old], tombstone=lambda a: tombstoned.append(a.id))

    assert tombstoned == ["e1"]


def test_an_already_tombstoned_artifact_is_not_expired_twice() -> None:
    retention = Retention()
    already = artifact("e1", DataClass.EVIDENCE, age_days=400, tombstoned=True)

    report = retention.sweep([already], tombstone=tombstoner())

    assert report.expired == []
    assert report.already_tombstoned == ["e1"]


# ------------------------------------------------------------------------- legal hold


def hold(subjects: tuple[str, ...] = ("amaya",), hold_id: str = "h1") -> LegalHold:
    return LegalHold(
        id=hold_id,
        subjects=frozenset(subjects),
        reason=HoldReason.LITIGATION,
        placed_by="human:counsel",
        note="retain pending the dispute",
    )


def test_a_hold_suspends_retention_for_its_subjects() -> None:
    retention = Retention(holds=[hold()])
    old = artifact("t1", DataClass.TRANSCRIPT, age_days=400, subjects=frozenset({"amaya"}))

    report = retention.sweep([old], tombstone=tombstoner())

    assert report.expired == []
    assert report.held == [("t1", "h1")]


def test_a_hold_is_reported_not_silent() -> None:
    """An operator watching storage grow needs to see *why* it is growing."""
    retention = Retention(holds=[hold()])
    old = artifact("t1", DataClass.TRANSCRIPT, age_days=400, subjects=frozenset({"amaya"}))

    assert retention.sweep([old], tombstone=tombstoner()).as_dict()["held"] == [
        {"artifact": "t1", "hold": "h1"}
    ]


def test_a_hold_covers_artifacts_created_after_it_was_placed() -> None:
    """A hold names subjects, not artifacts. Naming artifacts would require enumerating them
    at hold time, and the point of a hold is catching what nobody has enumerated yet."""
    retention = Retention(holds=[hold()])
    later = artifact("t2", DataClass.TRANSCRIPT, age_days=400, subjects=frozenset({"amaya"}))

    assert retention.sweep([later], tombstone=tombstoner()).held == [("t2", "h1")]


def test_a_hold_does_not_cover_an_unrelated_subject() -> None:
    retention = Retention(holds=[hold(subjects=("bo",))])
    old = artifact("t1", DataClass.TRANSCRIPT, age_days=400, subjects=frozenset({"amaya"}))

    assert retention.sweep([old], tombstone=tombstoner()).expired == ["t1"]


def test_lifting_a_hold_lets_retention_resume() -> None:
    retention = Retention(holds=[hold()])
    old = artifact("t1", DataClass.TRANSCRIPT, age_days=400, subjects=frozenset({"amaya"}))
    assert retention.sweep([old], tombstone=tombstoner()).held

    lifted = retention.lift_hold("h1", by="human:counsel")

    assert lifted is not None
    assert not lifted.active
    assert retention.sweep([old], tombstone=tombstoner()).expired == ["t1"]


def test_a_duplicate_hold_id_is_refused() -> None:
    retention = Retention(holds=[hold()])

    with pytest.raises(ValueError, match="duplicate hold"):
        retention.place_hold(hold())


# ---------------------------------------------------------------------------- erasure


def test_erasure_destroys_what_it_can_and_names_what_it_cannot() -> None:
    """A subject-erasure request whose answer is "probably everything" is not an answer."""
    destroyed: list[str] = []
    retention = Retention()
    artifacts = [
        artifact("t1", DataClass.TRANSCRIPT, subjects=frozenset({"amaya"})),
        artifact("l1", DataClass.LEDGER, subjects=frozenset({"amaya"})),
        artifact("t2", DataClass.TRANSCRIPT, subjects=frozenset({"bo"})),
    ]

    report = retention.erase(
        "amaya", artifacts, requested_by="human:dpo", destroy=lambda a: destroyed.append(a.id)
    )

    assert report.erased == ["t1"]
    assert destroyed == ["t1"]
    assert [a for a, _ in report.unerasable] == ["l1"]
    assert "never bodies" in report.unerasable[0][1]


def test_an_unerasable_class_does_not_make_the_request_incomplete() -> None:
    """The ledger holding references is a stated property of the design, not a failure of
    this particular request."""
    retention = Retention()

    report = retention.erase(
        "amaya",
        [artifact("l1", DataClass.LEDGER, subjects=frozenset({"amaya"}))],
        requested_by="human:dpo",
        destroy=tombstoner(),
    )

    assert report.complete
    assert report.unerasable


def test_a_legal_hold_blocks_erasure_and_the_report_says_so() -> None:
    """The two obligations genuinely conflict, and resolving that silently -- in either
    direction -- is worse than naming it for the person who has to."""
    retention = Retention(holds=[hold()])

    report = retention.erase(
        "amaya",
        [artifact("t1", DataClass.TRANSCRIPT, subjects=frozenset({"amaya"}))],
        requested_by="human:dpo",
        destroy=tombstoner(),
    )

    assert not report.complete
    assert report.blocked_by_hold == [("t1", "h1")]
    assert report.erased == []


def test_an_erasure_report_renders_every_outcome() -> None:
    retention = Retention()

    destroyed: list[str] = []
    body = retention.erase(
        "amaya",
        [artifact("t1", DataClass.TRANSCRIPT, subjects=frozenset({"amaya"}))],
        requested_by="human:dpo",
        destroy=lambda a: destroyed.append(a.id),
    ).as_dict()

    # The receipt is shaped to be handed to a data subject. It asserted `complete: true` and
    # `erased: ["t1"]` for a call with no destructor at all -- a deletion that did not
    # occur, blessed as the specified rendering.
    assert destroyed == ["t1"]
    assert body["subject"] == "amaya"
    assert body["complete"] is True
    assert body["erased"] == ["t1"]


# ----------------------------------------------------------------------- segmentation


def filled_ledger(path: Path, count: int) -> Ledger:
    ledger = Ledger(path)
    for index in range(count):
        ledger.append(EntryType.RUN_STARTED, actor="worker", subject=f"run-{index}")
    return ledger


def test_nothing_is_sealed_until_a_segment_is_complete(tmp_path: Path) -> None:
    """A partial segment would have to be re-sealed as it grew, and a seal that changes is
    not a seal."""
    ledger = filled_ledger(tmp_path / "ledger.jsonl", 9)
    manifest = Manifest(path=tmp_path / "segments.jsonl")

    assert seal(ledger, manifest, size=10) == []
    assert manifest.sealed_through == 0


def test_a_complete_segment_seals_and_records_its_range(tmp_path: Path) -> None:
    ledger = filled_ledger(tmp_path / "ledger.jsonl", 25)
    manifest = Manifest(path=tmp_path / "segments.jsonl")

    sealed = seal(ledger, manifest, size=10)

    assert [s.index for s in sealed] == [0, 1]
    assert (sealed[0].first_seq, sealed[0].last_seq) == (1, 10)
    assert (sealed[1].first_seq, sealed[1].last_seq) == (11, 20)
    assert manifest.sealed_through == 20


def test_segments_chain_across_the_boundary(tmp_path: Path) -> None:
    """The property that makes an archived prefix verifiable without being present:
    verifying segment 1 needs segment 0's digest, not its ten thousand entries."""
    ledger = filled_ledger(tmp_path / "ledger.jsonl", 25)
    manifest = Manifest(path=tmp_path / "segments.jsonl")
    sealed = seal(ledger, manifest, size=10)

    assert sealed[1].prev_segment_digest == sealed[0].digest
    manifest.verify()


def test_altering_a_non_final_segment_breaks_every_later_digest(tmp_path: Path) -> None:
    """Exactly as altering an earlier entry changes every later entry's hash.

    The final segment is the case the chain cannot cover, and it is checked by `test_i4_the_last_segment_is_anchored_in_the_ledger` -- not here. This name used to promise "every later digest", which a reader takes as the guarantee.
    """
    ledger = filled_ledger(tmp_path / "ledger.jsonl", 25)
    manifest = Manifest(path=tmp_path / "segments.jsonl")
    seal(ledger, manifest, size=10)

    first = manifest.segments[0]
    manifest.segments[0] = type(first)(
        index=first.index,
        first_seq=first.first_seq,
        last_seq=first.last_seq,
        last_hash="0" * 64,  # the tamper
        prev_segment_digest=first.prev_segment_digest,
        entry_count=first.entry_count,
        sealed_at=first.sealed_at,
    )

    with pytest.raises(SegmentError, match="chains to"):
        manifest.verify()


def test_a_manifest_round_trips(tmp_path: Path) -> None:
    ledger = filled_ledger(tmp_path / "ledger.jsonl", 25)
    manifest = Manifest(path=tmp_path / "segments.jsonl")
    seal(ledger, manifest, size=10)

    reloaded = Manifest.load(tmp_path / "segments.jsonl")

    assert [s.digest for s in reloaded.segments] == [s.digest for s in manifest.segments]
    reloaded.verify()


def test_sealing_continues_where_the_last_seal_stopped(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = filled_ledger(path, 12)
    manifest = Manifest(path=tmp_path / "segments.jsonl")
    seal(ledger, manifest, size=10)

    for index in range(12, 25):
        ledger.append(EntryType.RUN_STARTED, actor="worker", subject=f"run-{index}")
    more = seal(ledger, manifest, size=10)

    assert [s.first_seq for s in more] == [11]
    manifest.verify()


def test_an_empty_manifest_verifies_and_chains_to_genesis(tmp_path: Path) -> None:
    from software_factory.ledger.entry import GENESIS

    manifest = Manifest(path=tmp_path / "segments.jsonl")

    manifest.verify()
    assert manifest.tip_digest == GENESIS
