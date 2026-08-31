"""Deterministic pack section builders.

Eight of the ten pack sections are built without a model, which is what makes a pack the
same for a small model as for a large one. These tests check that each builder produces
cited items, scopes itself to the change surface, and degrades with a stated reason
rather than failing the pack.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from software_factory.definition.models import AgentRole, SkillStatus, Stage
from software_factory.harness.awareness import Origin
from software_factory.harness.sections import (
    conventions_builder,
    hazards_builder,
    open_questions_builder,
    precedent_builder,
    skills_builder,
    spec_slice_builder,
    terrain_builder,
)
from software_factory.ledger import EntryType, Ledger
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
    detect_contradictions,
    promote,
)
from software_factory.skills import SkillMetrics, SkillRecord, SkillRegistry
from software_factory.spec import CodeAnchor, SpecStore, SpecUnit, UnitStatus


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
    root = tmp_path / "repo"
    root.mkdir()
    git(["init", "--quiet", "-b", "main"], root)
    (root / "importer.py").write_text("def strip_bom(text):\n    return text\n", encoding="utf-8")
    (root / "reports.py").write_text(
        "import importer\n\n\ndef render():\n    ...\n", encoding="utf-8"
    )
    (root / "unrelated.py").write_text("x = 1\n", encoding="utf-8")
    git(["add", "-A"], root)
    git(["commit", "--quiet", "-m", "initial"], root)
    return root


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.jsonl")


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    memory = MemoryStore(tmp_path / "memory.jsonl")
    memory.load()
    return memory


# --------------------------------------------------------------------------- terrain


def test_terrain_marks_the_change_surface(repo: Path) -> None:
    items, degradation = terrain_builder(repo, {"importer.py"})()

    assert degradation is None
    assert any("importer.py — in the change surface" in item.content for item in items)


def test_terrain_finds_files_that_import_the_surface(repo: Path) -> None:
    """The neighbours are what is likely to break, which is the question that matters."""
    items, _ = terrain_builder(repo, {"importer.py"})()

    assert any("reports.py — imports the change surface" in item.content for item in items)


def test_terrain_degrades_when_the_workspace_is_missing(tmp_path: Path) -> None:
    items, degradation = terrain_builder(tmp_path / "gone", set())()

    assert items == []
    assert degradation == "workspace unavailable"


def test_terrain_items_are_all_cited(repo: Path) -> None:
    items, _ = terrain_builder(repo, {"importer.py"})()

    assert all(item.citation.ref for item in items)


# ------------------------------------------------------------------------- precedent


def test_precedent_reports_nothing_when_there_is_no_history(ledger: Ledger) -> None:
    items, degradation = precedent_builder(ledger, {"importer.py"})()

    assert items == []
    assert degradation == "no prior runs recorded yet"


def test_precedent_ranks_work_on_the_same_files_first(ledger: Ledger) -> None:
    ledger.append(
        EntryType.RUN_FINISHED,
        actor="builder",
        subject="wi-elsewhere",
        payload={"stage": "BUILD", "status": "completed", "paths": ["unrelated.py"]},
    )
    ledger.append(
        EntryType.RUN_FINISHED,
        actor="builder",
        subject="wi-same",
        payload={"stage": "BUILD", "status": "gate_failed", "paths": ["importer.py"]},
    )

    items, degradation = precedent_builder(ledger, {"importer.py"})()

    assert degradation is None
    assert "wi-same" in items[0].content
    assert "touched importer.py" in items[0].content


def test_precedent_says_when_nothing_touched_this_surface(ledger: Ledger) -> None:
    ledger.append(
        EntryType.RUN_FINISHED,
        actor="builder",
        subject="wi-elsewhere",
        payload={"stage": "BUILD", "status": "completed", "paths": ["unrelated.py"]},
    )

    _items, degradation = precedent_builder(ledger, {"importer.py"})()

    assert degradation == "no prior work recorded on this change surface"


def test_precedent_carries_the_failure_reason(ledger: Ledger) -> None:
    """What was tried and why it failed is the whole point of the section."""
    ledger.append(
        EntryType.RUN_FINISHED,
        actor="builder",
        subject="wi-1",
        payload={
            "stage": "BUILD",
            "status": "gate_failed",
            "reason": "regression-proven: no new test",
            "paths": ["importer.py"],
        },
    )

    items, _ = precedent_builder(ledger, {"importer.py"})()

    assert "regression-proven" in items[0].content


# --------------------------------------------------------------------------- hazards


def test_hazards_surface_reverted_commits(repo: Path, ledger: Ledger) -> None:
    (repo / "importer.py").write_text("broken\n", encoding="utf-8")
    git(["commit", "--quiet", "-am", 'Revert "tidy the importer"'], repo)

    items, _ = hazards_builder(repo, ledger, {"importer.py"})()

    assert any("reverted:" in item.content for item in items)


def test_hazards_report_repeatedly_failing_gates(repo: Path, ledger: Ledger) -> None:
    for _ in range(3):
        ledger.append(
            EntryType.GATE_EVALUATED,
            actor="builder",
            subject="wi-1",
            payload={"gate": "regression-proven", "outcome": "fail"},
        )

    items, _ = hazards_builder(repo, ledger, set())()

    assert any("regression-proven has failed 3 time(s)" in item.content for item in items)


def test_hazards_degrade_outside_a_repository(tmp_path: Path, ledger: Ledger) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    _items, degradation = hazards_builder(plain, ledger, set())()

    assert degradation == "version history unavailable"


def test_hazards_say_so_when_there_are_none(repo: Path, ledger: Ledger) -> None:
    _items, degradation = hazards_builder(repo, ledger, set())()

    assert degradation == "no hazards found for this surface"


# ----------------------------------------------------------------------- conventions


def canon(store: MemoryStore, content: str, ref: str = "run-1") -> Memory:
    outcome = admit(
        Candidate(
            kind=Kind.CONVENTION,
            scope=Scope.REPOSITORY,
            scope_ref="acme/payments",
            content=content,
            provenance=(Source(kind=SourceKind.RUN, ref=ref),),
        ),
        store,
    )
    assert isinstance(outcome, Memory), outcome
    promote(
        outcome,
        store,
        criterion=PromotionCriterion.HUMAN,
        evidence=("maintainer confirmed",),
        actor="human:maintainer",
    )
    return outcome


def test_conventions_return_canon_memories_with_citations(store: MemoryStore) -> None:
    canon(store, "Importer changes always update the encoding fixtures.")

    items, _ = conventions_builder(
        store,
        scope_ref="acme/payments",
        query="importer encoding fixtures",
        surface={"importer.py"},
    )()

    assert items
    assert items[0].citation.ref.startswith("mem_")
    assert items[0].origin is Origin.MODEL_GENERATED


def test_conventions_say_so_when_the_repository_has_none(store: MemoryStore) -> None:
    items, degradation = conventions_builder(
        store, scope_ref="acme/payments", query="anything", surface=set()
    )()

    assert items == []
    assert "no established conventions" in (degradation or "")


def test_a_claim_contradicting_canon_never_gets_written(store: MemoryStore) -> None:
    """Admission refuses it, so there is nothing for retrieval to withhold later."""
    canon(store, "Retries are enabled for the importer webhook.", ref="run-a")

    outcome = admit(
        Candidate(
            kind=Kind.CONVENTION,
            scope=Scope.REPOSITORY,
            scope_ref="acme/payments",
            content="Retries are disabled for the importer webhook.",
            provenance=(Source(kind=SourceKind.RUN, ref="run-b"),),
        ),
        store,
    )

    assert not isinstance(outcome, Memory)
    assert len(store.all()) == 1


def test_disputed_memories_are_withheld_from_the_pack(store: MemoryStore) -> None:
    """A dispute between two unproven claims withholds both, and the pack says so.

    This is how a dispute actually arises: admission only checks against canon, so two
    candidates can coexist until the policy pass finds them.
    """
    for content, ref in (
        ("Retries are enabled for the importer webhook.", "run-a"),
        ("Retries are disabled for the importer webhook.", "run-b"),
    ):
        admit(
            Candidate(
                kind=Kind.CONVENTION,
                scope=Scope.REPOSITORY,
                scope_ref="acme/payments",
                content=content,
                provenance=(Source(kind=SourceKind.RUN, ref=ref),),
            ),
            store,
        )
    detect_contradictions(store)

    items, degradation = conventions_builder(
        store,
        scope_ref="acme/payments",
        query="retries importer webhook",
        surface=set(),
        include_candidate=True,
    )()

    assert items == []
    assert degradation is not None


# ---------------------------------------------------------------------------- skills


def registry() -> SkillRegistry:
    return SkillRegistry(
        [
            SkillRecord(
                name="repo-validation",
                description=(
                    "Use before finishing a code change to run the repository lint and tests. "
                    "Not for documentation-only changes."
                ),
                body="Run them.",
                status=SkillStatus.ACTIVE,
                roles=(AgentRole.BUILDER,),
                stages=(Stage.BUILD,),
                metrics=SkillMetrics(loaded=10, helped=9),
            ),
            SkillRecord(
                name="review-security",
                description=(
                    "Use when reviewing authentication changes to apply the security "
                    "checklist. Not for unrelated refactors."
                ),
                body="Check it.",
                status=SkillStatus.ACTIVE,
                roles=(AgentRole.CRITIC,),
                stages=(Stage.REVIEW,),
            ),
        ]
    )


def test_skills_are_offered_only_for_the_matching_role_and_stage() -> None:
    items, _ = skills_builder(
        registry(),
        role=AgentRole.BUILDER,
        stage=Stage.BUILD,
        surface=set(),
        task="run the repository validation",
    )()

    assert [item.citation.ref for item in items] == ["skill:repo-validation"]


def test_no_applicable_skills_is_stated_not_silent() -> None:
    items, degradation = skills_builder(
        registry(),
        role=AgentRole.PROVER,
        stage=Stage.VERIFY,
        surface=set(),
        task="anything",
    )()

    assert items == []
    assert degradation == "no skills apply to this role and stage"


# ------------------------------------------------------------------------ spec slice


def test_spec_slice_points_at_induction_when_there_is_no_spec() -> None:
    items, degradation = spec_slice_builder(SpecStore(), {"importer.py"})()

    assert items == []
    assert "sf spec induct" in (degradation or "")


def test_spec_slice_returns_units_governing_the_surface() -> None:
    spec = SpecStore()
    spec.add(
        SpecUnit(
            id="PAY-1",
            title="BOM handling",
            status=UnitStatus.ACTIVE,
            intent="The importer strips a byte-order mark from CSV headers.",
            implements=(CodeAnchor(path="importer.py", symbol="strip_bom"),),
        )
    )

    items, degradation = spec_slice_builder(spec, {"importer.py"})()

    assert degradation is None
    assert items[0].citation.ref == "PAY-1"
    assert items[0].origin is Origin.HUMAN_AUTHORED


def test_spec_slice_says_when_nothing_governs_the_surface() -> None:
    spec = SpecStore()
    spec.add(
        SpecUnit(
            id="PAY-2",
            title="Reports",
            status=UnitStatus.ACTIVE,
            intent="Reports render totals in the account currency.",
            implements=(CodeAnchor(path="reports.py"),),
        )
    )

    _items, degradation = spec_slice_builder(spec, {"importer.py"})()

    assert degradation == "no spec units govern this change surface"


# -------------------------------------------------------------------- open questions


def test_open_questions_carry_forward_from_earlier_stages(ledger: Ledger) -> None:
    """An agent should know what it is not expected to resolve alone."""
    ledger.append(
        EntryType.RUN_FINISHED,
        actor="scout",
        subject="wi-1",
        payload={"stage": "TRIAGE", "unknowns": ["whether other importers share the bug"]},
    )

    items, _ = open_questions_builder(ledger, "wi-1")()

    assert items
    assert "other importers" in items[0].content


def test_open_questions_are_scoped_to_the_work_item(ledger: Ledger) -> None:
    ledger.append(
        EntryType.RUN_FINISHED,
        actor="scout",
        subject="wi-other",
        payload={"stage": "TRIAGE", "unknowns": ["something else entirely"]},
    )

    items, _ = open_questions_builder(ledger, "wi-1")()

    assert items == []
