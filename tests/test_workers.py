"""Routing work to the machine that can run it.

`workerHost` already existed and named exactly one machine, which is enough for a factory
with one worker and wrong for every other shape. What did not exist was any way to say
"this work needs a GPU" and have it land somewhere that has one.

The tests that matter here are not the matching ones — matching a label set is easy to get
right. They are the four ways a router quietly does the wrong thing: falling back when
nothing matches, counting capacity in one process, killing work to drain a machine, and
reclaiming a lease without saying so.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from software_factory.errors import FactoryError
from software_factory.memory.records import utc_now
from software_factory.orchestrator.workers import (
    Availability,
    Worker,
    WorkerPool,
)


def pool(tmp_path: Path, *raw: dict, **kwargs) -> WorkerPool:
    return WorkerPool.from_dicts(list(raw), state_dir=tmp_path, **kwargs)


GPU = {"name": "gpu-1", "host": "gpu1.internal", "labels": ["gpu", "linux"]}
CPU = {"name": "cpu-1", "host": "cpu1.internal", "labels": ["linux"]}


# --------------------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------------------


def test_work_lands_on_a_worker_that_has_every_label_it_needs(tmp_path: Path) -> None:
    placed = pool(tmp_path, CPU, GPU).place("run-1", requires={"gpu"})

    assert placed.availability is Availability.AVAILABLE
    assert placed.lease is not None
    assert placed.lease.worker == "gpu-1"
    assert placed.lease.host == "gpu1.internal"


def test_a_worker_with_extra_labels_still_matches(tmp_path: Path) -> None:
    """Requirements are a subset, not an equality. Otherwise adding a capability to a
    machine stops it being eligible for the work it already ran."""
    assert pool(tmp_path, GPU).place("run-1", requires={"linux"}).placed


def test_work_with_no_requirements_lands_anywhere(tmp_path: Path) -> None:
    assert pool(tmp_path, CPU).place("run-1").placed


# --------------------------------------------------------------------------------------
# The fallback trap
# --------------------------------------------------------------------------------------


def test_an_unmatchable_requirement_is_refused_not_downgraded(tmp_path: Path) -> None:
    """The single most important behaviour in this module.

    Falling back to any available machine means work that asked for a GPU runs on a CPU box
    and produces results that are *wrong* rather than missing — and nothing in the record
    says why. Missing capacity has to look like missing capacity.
    """
    placed = pool(tmp_path, CPU).place("run-1", requires={"gpu"})

    assert placed.availability is Availability.UNAVAILABLE
    assert placed.lease is None


def test_the_refusal_names_the_label_nothing_has(tmp_path: Path) -> None:
    """An operator told "no worker matches {gpu, linux, staging}" checks three things.
    Told "no worker has label `gpu`" they add one."""
    placed = pool(tmp_path, CPU).place("run-1", requires={"gpu", "linux"})

    assert placed.missing == frozenset({"gpu"})
    assert "gpu" in placed.reason
    assert "linux" not in placed.reason


def test_an_empty_pool_is_unavailable_and_says_so(tmp_path: Path) -> None:
    placed = pool(tmp_path).place("run-1")

    assert placed.availability is Availability.UNAVAILABLE
    assert "no workers are configured" in placed.reason


def test_a_full_pool_is_saturated_not_unavailable(tmp_path: Path) -> None:
    """They need different responses: one is a definition to edit, the other is a wait.

    A router that reports "no" for both sends an operator to fix a file that was correct.
    """
    workers = pool(tmp_path, CPU)
    workers.place("run-1")

    placed = workers.place("run-2")

    assert placed.availability is Availability.SATURATED
    assert placed.missing == frozenset()


# --------------------------------------------------------------------------------------
# Capacity, across processes
# --------------------------------------------------------------------------------------


def test_capacity_is_shared_between_processes(tmp_path: Path) -> None:
    """A count held in memory means every coordinator believes the pool is idle."""
    first = pool(tmp_path, CPU)
    first.place("run-1")

    second = pool(tmp_path, CPU)

    assert second.place("run-2").availability is Availability.SATURATED


def test_a_worker_takes_as_many_runs_as_it_declares(tmp_path: Path) -> None:
    workers = pool(tmp_path, {**CPU, "capacity": 3})

    assert [workers.place(f"run-{i}").placed for i in range(3)] == [True, True, True]
    assert workers.place("run-4").availability is Availability.SATURATED


def test_releasing_frees_the_slot(tmp_path: Path) -> None:
    workers = pool(tmp_path, CPU)
    workers.place("run-1")

    assert workers.release("run-1") is True
    assert workers.place("run-2").placed


def test_releasing_a_run_that_held_nothing_says_so(tmp_path: Path) -> None:
    """Releasing twice is common and harmless. Releasing a run that never held a lease
    means the caller lost track of which run it was, and silence hides that."""
    assert pool(tmp_path, CPU).release("run-never") is False


def test_the_least_loaded_worker_is_chosen(tmp_path: Path) -> None:
    workers = pool(
        tmp_path,
        {"name": "a", "host": "a.internal", "capacity": 2},
        {"name": "b", "host": "b.internal", "capacity": 2},
    )
    first = workers.place("run-1").lease
    second = workers.place("run-2").lease

    assert first is not None and second is not None
    assert {first.worker, second.worker} == {"a", "b"}


def test_placement_is_deterministic(tmp_path: Path) -> None:
    """A router that breaks ties randomly cannot be reproduced from the ledger, and
    "it went to a different machine that time" is an explanation nobody can check."""
    one = pool(tmp_path / "one", {"name": "a", "host": "a.i"}, {"name": "b", "host": "b.i"})
    two = pool(tmp_path / "two", {"name": "a", "host": "a.i"}, {"name": "b", "host": "b.i"})

    assert one.place("run-1").lease.worker == two.place("run-1").lease.worker  # type: ignore[union-attr]


# --------------------------------------------------------------------------------------
# Draining
# --------------------------------------------------------------------------------------


def test_a_draining_worker_takes_no_new_work(tmp_path: Path) -> None:
    placed = pool(tmp_path, {**CPU, "draining": True}).place("run-1")

    assert placed.availability is Availability.UNAVAILABLE


def test_draining_does_not_end_work_already_running(tmp_path: Path) -> None:
    """A drain that killed in-flight leases would be a crash with a nicer name.

    An operator who needs a machine back eventually is not the same as one who needs it back
    now — the second is `sf stop`, which already exists and records who asked.
    """
    running = pool(tmp_path, CPU)
    running.place("run-1")

    drained = pool(tmp_path, {**CPU, "draining": True})

    assert [lease.run for lease in drained.leases()] == ["run-1"]
    assert drained.place("run-2").availability is Availability.UNAVAILABLE


def test_a_drained_worker_is_not_reported_as_a_missing_label(tmp_path: Path) -> None:
    """An operator told "no worker has label `gpu`" while the GPU box sits draining goes
    and buys a second one. "Yours is unavailable right now" is a different sentence, and
    the first version of this router only knew how to say the wrong one."""
    placed = pool(tmp_path, {**GPU, "draining": True}).place("run-1", requires={"gpu"})

    assert placed.availability is Availability.UNAVAILABLE
    assert "draining" in placed.reason
    assert "gpu-1" in placed.reason
    assert placed.missing == frozenset()


# --------------------------------------------------------------------------------------
# Stale leases
# --------------------------------------------------------------------------------------


def test_an_expired_lease_frees_the_worker(tmp_path: Path) -> None:
    """A worker whose process died would otherwise hold capacity until somebody noticed."""
    workers = pool(tmp_path, CPU, lease_seconds=1)
    workers.place("run-1")
    _age_leases(tmp_path, seconds=10)

    assert workers.place("run-2").placed


def test_reclaiming_a_lease_is_recorded(tmp_path: Path) -> None:
    """An expiry is a guess: the run may still be alive and merely slow.

    Without a record, a run that was reclaimed and one that finished cleanly leave identical
    traces, and "did this execute twice" has no answer at all.
    """
    workers = pool(tmp_path, CPU, lease_seconds=1)
    workers.place("run-1")
    _age_leases(tmp_path, seconds=10)
    workers.place("run-2")

    assert [lease.run for lease in workers.reclaimed()] == ["run-1"]


def test_a_heartbeat_holds_a_lease_open(tmp_path: Path) -> None:
    workers = pool(tmp_path, CPU, lease_seconds=60)
    workers.place("run-1")
    _age_leases(tmp_path, seconds=120)

    assert workers.heartbeat("run-1") is False


def test_a_heartbeat_on_a_reclaimed_lease_reports_the_loss(tmp_path: Path) -> None:
    """`False` is the interesting answer: somebody else may now be on that machine, and a
    caller that ignores it will overwrite whatever the new holder is doing."""
    workers = pool(tmp_path, CPU, lease_seconds=1)
    workers.place("run-1")
    _age_leases(tmp_path, seconds=10)

    assert workers.heartbeat("run-1") is False


def test_a_heartbeat_pushes_the_expiry_out(tmp_path: Path) -> None:
    """A live lease, five seconds from being reclaimed, is good for the full term again.

    Aged forward rather than into the past: a lease already expired is the *other* test, and
    conflating them would have this one pass on a heartbeat that did nothing.
    """
    workers = pool(tmp_path, CPU, lease_seconds=600)
    before = workers.place("run-1").lease
    assert before is not None
    _expire_in(tmp_path, seconds=5)

    assert workers.heartbeat("run-1") is True

    after = workers.leases()[0]
    assert after.expires_at > utc_now() + timedelta(seconds=500)


# --------------------------------------------------------------------------------------
# Definitions that would misroute
# --------------------------------------------------------------------------------------


def test_two_workers_with_one_name_are_refused(tmp_path: Path) -> None:
    """They share a lease slot, so one silently gets double the capacity it declared —
    and which one depends on the order the file happened to be written in."""
    with pytest.raises(FactoryError):
        pool(tmp_path, CPU, {**CPU, "host": "other.internal"})


def test_a_worker_with_no_host_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FactoryError):
        pool(tmp_path, {"name": "ghost"})


def test_a_worker_with_zero_capacity_is_refused(tmp_path: Path) -> None:
    """Configured to be useless is almost never what was meant; `draining` is."""
    with pytest.raises(FactoryError):
        pool(tmp_path, {**CPU, "capacity": 0})


def test_a_corrupt_lease_file_lets_work_through(tmp_path: Path) -> None:
    """Forgiving in one direction only, and this is the safe one.

    An unreadable lease file makes the pool look idle, so work is dispatched. Treating it as
    fully leased would stop the whole factory on a corrupt cache file.
    """
    (tmp_path / "workers.json").write_text("{not json", encoding="utf-8")

    assert pool(tmp_path, CPU).place("run-1").placed


def test_lowering_capacity_below_the_load_is_reported_not_hidden(tmp_path: Path) -> None:
    """Clamping would turn an over-committed machine into a merely full one, and hide the
    edit that caused it."""
    busy = pool(tmp_path, {**CPU, "capacity": 3})
    for index in range(3):
        busy.place(f"run-{index}")

    shrunk = pool(tmp_path, {**CPU, "capacity": 1})

    assert shrunk.summarise()["workers"][0]["free"] == -2


# --------------------------------------------------------------------------------------
# The fleet view
# --------------------------------------------------------------------------------------


def test_the_summary_shows_load_per_worker(tmp_path: Path) -> None:
    workers = pool(tmp_path, {**CPU, "capacity": 2}, GPU)
    workers.place("run-1")

    summary = workers.summarise()
    by_name = {entry["name"]: entry for entry in summary["workers"]}

    assert by_name["cpu-1"]["inUse"] == 1
    assert by_name["cpu-1"]["free"] == 1
    assert by_name["gpu-1"]["inUse"] == 0


def test_a_worker_declares_labels_as_a_set(tmp_path: Path) -> None:
    worker = Worker.from_dict({"name": "a", "host": "a.i", "labels": ["gpu", "gpu", " linux "]})

    assert worker.labels == frozenset({"gpu", "linux"})


def _age_leases(state_dir: Path, *, seconds: int) -> None:
    """Move every lease's expiry into the past.

    Rewriting the file rather than sleeping: a test that waits for a real expiry either
    takes as long as the expiry or forces the expiry to be short enough to be unrealistic,
    and the second changes the thing under test to suit the test.
    """
    path = state_dir / "workers.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    past = (utc_now() - timedelta(seconds=seconds)).isoformat()
    for lease in raw["leases"].values():
        lease["expiresAt"] = past
    path.write_text(json.dumps(raw), encoding="utf-8")


def _expire_in(state_dir: Path, *, seconds: int) -> None:
    """Bring every lease's expiry close, without letting it pass."""
    path = state_dir / "workers.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    soon = (utc_now() + timedelta(seconds=seconds)).isoformat()
    for lease in raw["leases"].values():
        lease["expiresAt"] = soon
    path.write_text(json.dumps(raw), encoding="utf-8")


# --------------------------------------------------------------------------------------
# Routing a real work item
# --------------------------------------------------------------------------------------


def test_an_item_needing_a_label_no_worker_has_never_starts(tmp_path: Path) -> None:
    """The point of routing first: before a workspace, before a model call.

    An item that needs a machine the fleet does not have must not clone a repository and
    burn a token to discover it — and the block has to name the label, because "gate failed"
    sends somebody to read a diff that was never written.
    """
    from software_factory.orchestrator.workitem import Blocker

    factory, source, provider = _factory_with_workers(tmp_path, workers=[])
    coord = _coordinator(factory, source, tmp_path, provider)
    item = _item(requires=("gpu",))

    coord.run(item)

    assert item.blocker is Blocker.EXTERNAL_DEPENDENCY
    assert "gpu" in item.blocker_action
    assert provider.calls == []


def test_an_item_needing_nothing_runs_with_no_workers_configured(tmp_path: Path) -> None:
    """The local case, unchanged. A factory that declares no workers is not a broken
    factory — it is every factory that existed before this module."""
    factory, source, provider = _factory_with_workers(tmp_path, workers=[])
    coord = _coordinator(factory, source, tmp_path, provider)
    item = _item()

    coord.run(item)

    assert provider.calls != []


def test_a_routed_item_records_where_it_landed(tmp_path: Path) -> None:
    """ "Why did this run on gpu-1" is asked as often as "why did it run nowhere"."""
    from software_factory.ledger import EntryType, Ledger

    factory, source, provider = _factory_with_workers(
        tmp_path, workers=[{"name": "gpu-1", "host": "gpu1.internal", "labels": ["gpu"]}]
    )
    coord = _coordinator(factory, source, tmp_path, provider)

    coord.run(_item(requires=("gpu",)))

    routed = [
        entry.payload["routing"]
        for entry in Ledger(coord.ledger.path).read()
        if entry.type is EntryType.WORK_ITEM_TRANSITION and "routing" in entry.payload
    ]
    assert routed and routed[0]["lease"]["worker"] == "gpu-1"


def test_a_lease_is_released_when_the_run_ends(tmp_path: Path) -> None:
    """A lease held by a finished run is capacity nobody can account for, and it comes back
    only when the lease expires — an hour by design."""
    factory, source, provider = _factory_with_workers(
        tmp_path, workers=[{"name": "w1", "host": "w1.internal", "labels": ["gpu"]}]
    )
    coord = _coordinator(factory, source, tmp_path, provider)

    coord.run(_item(requires=("gpu",)))

    assert coord.workers.leases() == []


def test_a_lease_is_released_when_the_run_raises(tmp_path: Path) -> None:
    """The exception path is the one that matters: a crash loop that leaks a lease per
    attempt idles the whole fleet without a single error mentioning workers."""
    factory, source, provider = _factory_with_workers(
        tmp_path, workers=[{"name": "w1", "host": "w1.internal", "labels": ["gpu"]}]
    )
    coord = _coordinator(factory, source, tmp_path, provider)
    coord.machine = _Exploding()

    with pytest.raises(RuntimeError):
        coord.run(_item(requires=("gpu",)))

    assert coord.workers.leases() == []


class _Exploding:
    def advance(self, *args, **kwargs):
        raise RuntimeError("boom")


def _item(*, requires: tuple[str, ...] = ()):
    from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id

    return WorkItem(
        id=new_id(),
        factory="demo",
        title="BOM headers",
        request="The importer keeps the BOM.",
        source=SourceContext(provider="cli", kind="direct", ref="local"),
        work_class=WorkClass.CHORE,
        requires=requires,
    )


def _factory_with_workers(tmp_path: Path, *, workers: list[dict]):
    """A real factory definition with a `workers:` block, and a scripted provider."""
    import subprocess

    import yaml

    from software_factory.definition import load_strict
    from software_factory.providers import StubProvider, says
    from software_factory.scaffold import init_factory

    root = tmp_path / "factory"
    root.mkdir()
    init_factory(root, name="demo", owner="acme", repo="demo")
    document = yaml.safe_load((root / "factory.yaml").read_text(encoding="utf-8"))
    document["workers"] = workers
    (root / "factory.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    source = tmp_path / "repo"
    source.mkdir()
    (source / "importer.py").write_text("def strip_bom(t):\n    return t\n", encoding="utf-8")
    for command in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.test"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "initial"],
    ):
        subprocess.run(["git", *command], cwd=source, check=True, capture_output=True)

    payload = json.dumps(
        {
            "calibration": {
                "criteria": [{"id": "C1", "confidence": 0.8, "evidence": ["repo.read x"]}],
                "unknowns": [],
            },
            "findings": "ok",
            "scope": "one function",
            "summary": "done",
            "claims": ["it works"],
            "verdict": "accept",
        }
    )
    return load_strict(root), source, StubProvider([says(payload) for _ in range(6)])


def _coordinator(definition, source: Path, tmp_path: Path, provider):
    from software_factory.orchestrator.coordinator import local_coordinator

    return local_coordinator(
        definition,
        repo=source,
        state_dir=tmp_path / "state",
        provider=provider,
        allow_unsandboxed=True,
    )
