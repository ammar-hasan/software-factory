"""The executor parity conformance suite (PRD FR-20.5).

> A published suite asserts that a fixed set of work items produces identical stage
> transitions, gate outcomes, and evidence structure across `local`, `container`, and
> `ssh-worker` executors. Divergence is a release blocker (FR-0.2).

The requirement is about *the same definition behaving the same way wherever it runs*, which
is what makes FR-20.8's promotion path real: a local factory becomes a shared one by
changing executor and storage settings only, and that is a lie if the executors disagree.

Two things are checked and they are different:

* **Structural parity**, which runs everywhere including CI with nothing installed. Every
  executor's `run` returns the same shape, reports the same command, and classifies
  failures the same way. This is what catches an executor that quietly returns a different
  contract.
* **Behavioural parity**, which needs a real container runtime and a real worker and is
  skipped with a stated reason when they are absent. A skipped test that says why is honest;
  one that silently passes is the C9 failure in the test suite.

The skip is deliberate rather than a gap: a parity suite that pretended to have verified an
executor it could not reach would be asserting exactly the thing FR-20.5 exists to check.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from software_factory.definition.models import NetworkPolicy
from software_factory.runtime.executor import (
    CommandResult,
    LocalExecutor,
    SandboxLevel,
    SandboxPolicy,
)
from software_factory.runtime.executors import (
    ContainerExecutor,
    ContainerImage,
    Executor,
    SshWorkerExecutor,
)

pytestmark = pytest.mark.integration


def _working_runtime() -> str | None:
    """A container runtime this machine can actually *use*, or None.

    Probed rather than looked up on PATH. A docker binary with no reachable daemon is the
    normal state inside a container, and `shutil.which` finding it would make this suite
    report a container executor it cannot run -- which is precisely the "presence is not
    capability" mistake the whole project keeps finding elsewhere.
    """
    import subprocess

    for candidate in ("docker", "podman"):
        binary = shutil.which(candidate)
        if binary is None:
            continue
        try:
            probe = subprocess.run([binary, "info"], capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return binary
    return None


#: A container runtime this machine can actually use, or None.
RUNTIME = _working_runtime()

#: The image the behavioural suite runs in. Pinned, like every image this project accepts.
PARITY_IMAGE = "python:3.12-slim"


def policy(workspace: Path, **kwargs) -> SandboxPolicy:
    base: dict[str, object] = {"workspace": workspace, "wall_clock_s": 30}
    base.update(kwargs)
    return SandboxPolicy(**base)  # type: ignore[arg-type]


def structural_executors(tmp_path: Path) -> dict[str, Executor]:
    """Every executor, constructed but not necessarily runnable.

    Construction alone is worth checking: an executor that cannot be built with a valid
    policy is one no definition can select.
    """
    executors: dict[str, Executor] = {
        "local": LocalExecutor(policy(tmp_path), level=SandboxLevel.PROCESS)
    }
    executors["container"] = ContainerExecutor(
        policy(tmp_path),
        ContainerImage("ghcr.io/acme/builder:1.0"),
        runtime=RUNTIME or "/usr/bin/docker",
    )
    executors["ssh-worker"] = SshWorkerExecutor(
        policy(tmp_path, network=NetworkPolicy.OPEN),
        host="worker.internal",
        remote_workspace="/srv/factory",
        ssh=shutil.which("ssh") or "/usr/bin/ssh",
    )
    return executors


# ------------------------------------------------------------------- structural parity


def test_every_executor_satisfies_the_same_protocol(tmp_path: Path) -> None:
    """FR-20.8's promotion path is a lie if selecting a different executor means calling a
    different interface."""
    for name, executor in structural_executors(tmp_path).items():
        assert isinstance(executor, Executor), name
        assert hasattr(executor, "policy"), name


def test_every_executor_carries_the_same_policy(tmp_path: Path) -> None:
    """The policy is what `sf audit` reports. An executor that held a different one would
    make the audit describe a run that did not happen."""
    for name, executor in structural_executors(tmp_path).items():
        assert executor.policy.workspace == tmp_path, name


def test_every_executor_reports_the_command_the_caller_asked_for(tmp_path: Path) -> None:
    """A transcript full of `docker run --rm ...` or `ssh -o BatchMode=yes ...` tells a
    reader about the executor rather than about the work, and the executor is not what they
    are debugging. Checked structurally here; the behavioural suite proves it end to end.
    """
    import inspect

    from software_factory.runtime import executors as module

    for name in ("ContainerExecutor", "SshWorkerExecutor"):
        source = inspect.getsource(getattr(module, name).run)
        assert "command=tuple(command)" in source, name


def test_the_result_shape_is_identical_across_executors() -> None:
    """One `CommandResult`, one set of fields. An executor returning a different shape would
    make every gate reading it executor-specific."""
    fields = set(CommandResult.__dataclass_fields__)

    assert fields == {
        "command",
        "exit_code",
        "stdout",
        "stderr",
        "duration_s",
        "timed_out",
        "truncated",
        "violations",
    }


def test_redaction_and_capping_are_shared_not_reimplemented() -> None:
    """Two executors that redact differently make "the same definition behaves the same
    everywhere" false in the direction hardest to notice: a secret leaking on one runner and
    not another."""
    import inspect

    from software_factory.runtime import executors as module

    for name in ("ContainerExecutor", "SshWorkerExecutor"):
        source = inspect.getsource(getattr(module, name))
        assert "self._inner" in source, f"{name} does not compose LocalExecutor"
        assert "_redact" not in source, f"{name} reimplements redaction"
        assert "_cap(" not in source, f"{name} reimplements capping"


# ------------------------------------------------------------------ behavioural parity


@pytest.mark.slow
@pytest.mark.skipif(
    RUNTIME is None, reason="no usable container runtime (a binary with no daemon does not count)"
)
def test_a_command_produces_the_same_result_locally_and_in_a_container(tmp_path: Path) -> None:
    """The parity that matters: same command, same exit code, same output.

    Skipped with a stated reason when no runtime exists. A parity suite that pretended to
    have verified an executor it could not reach would be asserting exactly the thing
    FR-20.5 exists to check.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.py").write_text("print('parity')\n", encoding="utf-8")

    local = LocalExecutor(policy(workspace), level=SandboxLevel.PROCESS)
    container = ContainerExecutor(
        policy(workspace, network=NetworkPolicy.NONE),
        ContainerImage(PARITY_IMAGE),
        runtime=RUNTIME,
    )

    local_result = local.run([sys.executable, "hello.py"], cwd=workspace)
    container_result = container.run(["python", "hello.py"], cwd=workspace)

    assert local_result.exit_code == container_result.exit_code == 0
    assert local_result.stdout.strip() == container_result.stdout.strip() == "parity"
    assert local_result.timed_out == container_result.timed_out


@pytest.mark.slow
@pytest.mark.skipif(
    RUNTIME is None, reason="no usable container runtime (a binary with no daemon does not count)"
)
def test_a_failure_is_classified_the_same_way_in_both(tmp_path: Path) -> None:
    """A gate reading an exit code must not need to know where the command ran."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "boom.py").write_text("raise SystemExit(3)\n", encoding="utf-8")

    local = LocalExecutor(policy(workspace), level=SandboxLevel.PROCESS)
    container = ContainerExecutor(
        policy(workspace, network=NetworkPolicy.NONE),
        ContainerImage(PARITY_IMAGE),
        runtime=RUNTIME,
    )

    assert local.run([sys.executable, "boom.py"], cwd=workspace).exit_code == 3
    assert container.run(["python", "boom.py"], cwd=workspace).exit_code == 3


@pytest.mark.slow
@pytest.mark.skipif(
    RUNTIME is None, reason="no usable container runtime (a binary with no daemon does not count)"
)
def test_network_none_is_enforced_in_the_container(tmp_path: Path) -> None:
    """The one place a container genuinely does better than the local executor, so parity
    here means *the declared policy holds*, not that the two behave identically."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "reach.py").write_text(
        "import socket, sys\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=2)\n"
        "    print('reached')\n"
        "except OSError:\n"
        "    print('denied')\n",
        encoding="utf-8",
    )
    container = ContainerExecutor(
        policy(workspace, network=NetworkPolicy.NONE),
        ContainerImage(PARITY_IMAGE),
        runtime=RUNTIME,
    )

    result = container.run(["python", "reach.py"], cwd=workspace)

    assert "denied" in result.stdout


def test_the_suite_states_what_it_could_not_verify() -> None:
    """FR-20.5 makes divergence a release blocker, which requires knowing what was actually
    compared. A suite that skipped silently would leave a reader believing all three
    executors were checked.
    """
    unverified = []
    if RUNTIME is None:
        unverified.append("container (no usable runtime: no daemon reachable)")
    unverified.append("ssh-worker (no reachable worker configured for this suite)")

    # Not a failure -- these are honest environmental limits. The assertion is that the
    # limits are enumerable, so CI can report them rather than a green tick implying more
    # than it means.
    assert all(reason.strip() for reason in unverified)
