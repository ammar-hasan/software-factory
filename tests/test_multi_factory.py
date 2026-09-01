"""Several factories in one tree, doing real work at the same time.

`test_workers.py` and `test_audit.py` check the pieces. This checks the shape an
organisation actually has: a handful of factories, each owning its own repositories, sharing
a machine and sometimes a repository, with one operator trying to understand all of them at
once.

Everything here uses real definitions, real git repositories, real ledgers and real gates —
only the model is scripted. What it probes is the class of failure that only appears at
plurality: state one factory writes that another reads, an identifier that was unique per
factory and is not unique per workspace, and a report that averages over factories that
cannot be compared.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from software_factory.definition import load_strict
from software_factory.definition.workspace import load_workspace, scaffold_workspace
from software_factory.ledger import EntryType, Ledger
from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
from software_factory.orchestrator.coordinator import local_coordinator
from software_factory.providers import StubProvider, says
from software_factory.scaffold import init_factory

pytestmark = pytest.mark.integration


def repo(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "importer.py").write_text("def strip(t):\n    return t\n", encoding="utf-8")
    for command in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.test"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "initial"],
    ):
        subprocess.run(["git", *command], cwd=path, check=True, capture_output=True)
    return path


def stage_output(**fields: object) -> str:
    base: dict[str, object] = {
        "calibration": {
            "criteria": [{"id": "C1", "confidence": 0.8, "evidence": ["repo.read x"]}],
            "unknowns": [],
        }
    }
    base.update(fields)
    return json.dumps(base)


def scripted(runs: int = 2) -> StubProvider:
    one = [
        says(stage_output(findings="ok", scope="one function")),
        says(stage_output(summary="done", claims=["it works"])),
        says(stage_output(verdict="accept", findings=[])),
        says(stage_output(summary="handed off")),
    ]
    return StubProvider(one * runs)


def workspace_of(tmp_path: Path, names: list[str], *, shared_repo: str | None = None):
    """A workspace of real factories, each with its own repository unless one is shared."""
    for name in names:
        root = tmp_path / name
        root.mkdir(parents=True)
        init_factory(root, name=name, owner="acme", repo=shared_repo or name)
    scaffold_workspace(tmp_path, name="acme", members=names)
    return load_workspace(tmp_path)


def run_one(tmp_path: Path, factory: str, source: Path, *, title: str = "Tidy the importer"):
    definition = load_strict(tmp_path / factory)
    coordinator = local_coordinator(
        definition,
        repo=source,
        state_dir=tmp_path / factory / ".factory",
        provider=scripted(),
        allow_unsandboxed=True,
    )
    return coordinator.run(
        WorkItem(
            id=new_id(),
            factory=factory,
            title=title,
            request="Tidy the importer module docstring.",
            source=SourceContext(provider="cli", kind="direct", ref="local"),
            work_class=WorkClass.CHORE,
        )
    )


# --------------------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------------------


def test_two_factories_keep_separate_ledgers(tmp_path: Path) -> None:
    """The most basic thing plurality can break, and the one everything else rests on.

    A shared ledger would make every metric in the workspace the sum of all of them, and
    the per-factory numbers an operator reads would each describe the whole fleet.
    """
    workspace_of(tmp_path, ["payments", "search"])
    run_one(tmp_path, "payments", repo(tmp_path, "payments-repo"))
    run_one(tmp_path, "search", repo(tmp_path, "search-repo"))

    payments = list(Ledger(tmp_path / "payments" / ".factory" / "ledger.jsonl").read())
    search = list(Ledger(tmp_path / "search" / ".factory" / "ledger.jsonl").read())

    # Each ledger holds its own factory's work and nothing else. Asserting only that the
    # two work-item ids differ would pass against a single shared ledger holding both.
    payments_items = {e.subject for e in payments if e.type is EntryType.WORK_ITEM_CREATED}
    search_items = {e.subject for e in search if e.type is EntryType.WORK_ITEM_CREATED}

    assert len(payments_items) == 1 and len(search_items) == 1
    assert payments_items.isdisjoint(search_items)
    assert not (payments_items & {e.subject for e in search})
    # And each chain starts at one: a shared writer would leave the second ledger beginning
    # partway through a sequence it did not start.
    assert payments[0].seq == 1 and search[0].seq == 1
    Ledger(tmp_path / "payments" / ".factory" / "ledger.jsonl").verify()
    Ledger(tmp_path / "search" / ".factory" / "ledger.jsonl").verify()


def test_a_message_to_one_factorys_agent_does_not_reach_anothers(tmp_path: Path) -> None:
    """Agents are addressed by name, and every factory has an agent called `builder`.

    A mailbox keyed on the name alone would deliver an operator's note about the payments
    schema to whoever happened to be building the search index.
    """
    from software_factory.orchestrator.mailbox import Mailbox

    workspace_of(tmp_path, ["payments", "search"])
    for name in ("payments", "search"):
        (tmp_path / name / ".factory").mkdir(parents=True, exist_ok=True)

    payments = Mailbox(
        ledger=Ledger(tmp_path / "payments" / ".factory" / "ledger.jsonl"),
        state_dir=tmp_path / "payments" / ".factory",
    )
    search = Mailbox(
        ledger=Ledger(tmp_path / "search" / ".factory" / "ledger.jsonl"),
        state_dir=tmp_path / "search" / ".factory",
    )
    payments.send(sender="operator", recipient="builder", kind="status", body="use the new schema")

    assert [m.body for m in payments.inbox("builder")[0]] == ["use the new schema"]
    assert search.inbox("builder")[0] == []


def test_two_factories_do_not_share_a_worker_lease(tmp_path: Path) -> None:
    """Leases are per state directory. Two factories on one machine, each believing it has
    the worker, is the failure the on-disk lease exists to prevent — and it comes back if
    the lease file is keyed anywhere other than beside the ledger."""
    from software_factory.orchestrator.workers import WorkerPool

    workspace_of(tmp_path, ["payments", "search"])
    declared = [{"name": "w1", "host": "w1.internal", "capacity": 1}]
    payments = WorkerPool.from_dicts(declared, state_dir=tmp_path / "payments" / ".factory")
    search = WorkerPool.from_dicts(declared, state_dir=tmp_path / "search" / ".factory")

    assert payments.place("run-a").placed
    # A separate factory with a separate state directory sees a separate pool. That is
    # correct *and* it is the thing an operator must understand: one worker declared in two
    # factories is two claims on one machine, which is why the audit reports the divergence.
    assert search.place("run-b").placed
    assert len(payments.leases()) == 1
    assert len(search.leases()) == 1


def test_work_item_ids_do_not_collide_across_factories(tmp_path: Path) -> None:
    """An id unique per factory and not per workspace makes the cross-factory audit join
    two unrelated runs and report one."""
    workspace_of(tmp_path, ["payments", "search"])
    first = run_one(tmp_path, "payments", repo(tmp_path, "payments-repo"))
    second = run_one(tmp_path, "search", repo(tmp_path, "search-repo"))

    assert first.item.id != second.item.id


# --------------------------------------------------------------------------------------
# What the operator sees
# --------------------------------------------------------------------------------------


def test_the_audit_sees_every_factory_that_ran(tmp_path: Path) -> None:
    from software_factory.observability.audit import audit

    workspace_of(tmp_path, ["payments", "search", "platform"])
    run_one(tmp_path, "payments", repo(tmp_path, "payments-repo"))
    run_one(tmp_path, "search", repo(tmp_path, "search-repo"))

    result = audit(load_workspace(tmp_path))

    by_name = {f.name: f for f in result.factories}
    assert set(by_name) == {"payments", "search", "platform"}
    assert by_name["payments"].ledger_verifies is True
    assert by_name["platform"].ledger_verifies is None, "a factory that never ran is not broken"


def test_a_shared_repository_is_reported_across_real_factories(tmp_path: Path) -> None:
    """Two factories opening changes on one repository review each other's work as though
    it came from outside — and neither of them can see the other to know that."""
    from software_factory.observability.audit import audit

    workspace = workspace_of(tmp_path, ["payments", "search"], shared_repo="shared")

    findings = audit(workspace).findings

    assert any("claimed by more than one" in f.summary for f in findings)


def test_the_workspace_ledgers_all_verify_after_real_runs(tmp_path: Path) -> None:
    """The hash chain is per factory, and a run in one must not disturb another's.

    Checked after real work rather than on empty ledgers, because the interesting failure is
    a writer that reaches across state directories.
    """
    from software_factory.observability.audit import audit

    workspace_of(tmp_path, ["payments", "search"])
    run_one(tmp_path, "payments", repo(tmp_path, "payments-repo"))
    run_one(tmp_path, "search", repo(tmp_path, "search-repo"))
    run_one(tmp_path, "payments", repo(tmp_path, "payments-repo-2"), title="Second item")

    result = audit(load_workspace(tmp_path))

    assert result.ok
    assert all(f.ledger_verifies for f in result.factories if f.runs)


def test_one_broken_factory_does_not_hide_the_others(tmp_path: Path) -> None:
    """The report has to survive its own worst input: a definition that will not parse
    sitting beside factories that are working normally."""
    from software_factory.observability.audit import audit

    workspace_of(tmp_path, ["payments", "search"])
    run_one(tmp_path, "payments", repo(tmp_path, "payments-repo"))
    (tmp_path / "search" / "factory.yaml").write_text("not: [valid", encoding="utf-8")

    result = audit(load_workspace(tmp_path))

    assert not result.ok
    by_name = {f.name: f for f in result.factories}
    assert by_name["payments"].runs and by_name["payments"].runs > 0
    assert by_name["search"].loaded is False


def test_a_factory_added_later_appears_without_touching_the_others(tmp_path: Path) -> None:
    """Growth is the common case and the one most likely to require a migration nobody
    wrote. A new member must not require the existing ledgers to change at all."""
    import yaml

    from software_factory.observability.audit import audit

    workspace_of(tmp_path, ["payments"])
    run_one(tmp_path, "payments", repo(tmp_path, "payments-repo"))
    before = (tmp_path / "payments" / ".factory" / "ledger.jsonl").read_bytes()

    later = tmp_path / "search"
    later.mkdir()
    init_factory(later, name="search", owner="acme", repo="search")
    document = yaml.safe_load((tmp_path / "workspace.yaml").read_text(encoding="utf-8"))
    document["factories"].append({"path": "search"})
    (tmp_path / "workspace.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    result = audit(load_workspace(tmp_path))

    assert {f.name for f in result.factories} == {"payments", "search"}
    assert (tmp_path / "payments" / ".factory" / "ledger.jsonl").read_bytes() == before
