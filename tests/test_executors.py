"""The container and ssh-worker executors.

The theme is a single rule: an executor either provides what it claims or refuses at
construction. A factory declaring `executor: container` and getting a silent fallback to
local has lost the isolation it declared, and `sf audit` would report a control that does not
exist -- which is finding C9 in a new place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from software_factory.definition.models import NetworkPolicy
from software_factory.runtime.executor import ExecutorError, SandboxLevel, SandboxPolicy
from software_factory.runtime.executors import (
    CloudExecutorUnavailableError,
    ContainerExecutor,
    ContainerImage,
    Executor,
    SshWorkerExecutor,
    cloud_executor,
)


def policy(workspace: Path, **kwargs) -> SandboxPolicy:
    base: dict[str, object] = {"workspace": workspace, "wall_clock_s": 20}
    base.update(kwargs)
    return SandboxPolicy(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------------ image pinning


def test_an_unpinned_image_is_refused() -> None:
    """`latest` is a different image on different days, so a run that reproduces today and
    not tomorrow has no bug to find."""
    with pytest.raises(ValueError, match="not pinned"):
        ContainerImage("ghcr.io/acme/builder:latest")

    with pytest.raises(ValueError, match="not pinned"):
        ContainerImage("ghcr.io/acme/builder")


def test_a_version_tag_is_accepted_and_a_digest_is_preferred() -> None:
    assert not ContainerImage("ghcr.io/acme/builder:1.4.2").pinned_by_digest
    assert ContainerImage("ghcr.io/acme/builder@sha256:" + "a" * 64).pinned_by_digest


# --------------------------------------------------------------------------- container


def test_a_container_executor_refuses_without_a_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falling back to local would mean a factory that declares container isolation and runs
    with none, reporting the isolation it declared."""
    from software_factory.runtime import executors as module

    monkeypatch.setattr(module, "_detect_runtime", lambda: None)

    with pytest.raises(ExecutorError, match="no container runtime"):
        ContainerExecutor(policy(tmp_path), ContainerImage("ghcr.io/acme/builder:1.0"))


def test_a_container_executor_finds_a_runtime_when_one_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from software_factory.runtime import executors as module

    monkeypatch.setattr(module, "_detect_runtime", lambda: "/usr/bin/podman")

    executor = ContainerExecutor(policy(tmp_path), ContainerImage("ghcr.io/acme/builder:1.0"))

    assert executor.runtime == "/usr/bin/podman"


def test_a_container_executor_refuses_a_network_allowlist(tmp_path: Path) -> None:
    """A container runtime alone does not do per-host filtering either, and mapping
    `allowlist` to an open network would be C9's lie in a new place."""
    with pytest.raises(ExecutorError, match="per-host network allowlist"):
        ContainerExecutor(
            policy(tmp_path, network=NetworkPolicy.ALLOWLIST),
            ContainerImage("ghcr.io/acme/builder:1.0"),
            runtime="/usr/bin/docker",
            probe_runtime=False,
        )


def test_the_container_invocation_drops_capabilities_and_privileges(tmp_path: Path) -> None:
    executor = ContainerExecutor(
        policy(tmp_path),
        ContainerImage("ghcr.io/acme/builder:1.0"),
        runtime="/usr/bin/docker",
        probe_runtime=False,
    )

    wrapped = executor._wrap(["pytest", "-q"], cwd=None)

    assert "--cap-drop" in wrapped and "ALL" in wrapped
    assert "no-new-privileges" in wrapped
    assert wrapped[-2:] == ["pytest", "-q"]


def test_no_network_maps_to_the_runtimes_own_enforcement(tmp_path: Path) -> None:
    """The one place a container genuinely does better than the local executor."""
    executor = ContainerExecutor(
        policy(tmp_path, network=NetworkPolicy.NONE),
        ContainerImage("ghcr.io/acme/builder:1.0"),
        runtime="/usr/bin/docker",
        probe_runtime=False,
    )

    wrapped = executor._wrap(["pytest"], cwd=None)

    assert "--network" in wrapped
    assert wrapped[wrapped.index("--network") + 1] == "none"


def test_secrets_are_passed_by_environment_not_on_the_command_line(tmp_path: Path) -> None:
    """A secret on a command line is visible in the host's process list to anyone who can
    run `ps`, and redaction at capture does not reach that.

    This test used to assert `SF_TOKEN=` appeared in the argv -- the leak itself, written
    down as the requirement, under a name saying the opposite. `--env NAME` without a value
    is the form that reads the value from the docker client's own environment, which the
    inner executor already sets.
    """
    secret = "value-1234567890"
    executor = ContainerExecutor(
        policy(tmp_path, secrets={"SF_TOKEN": secret}),
        ContainerImage("ghcr.io/acme/builder:1.0"),
        runtime="/usr/bin/docker",
        probe_runtime=False,
    )

    wrapped = executor._wrap(["pytest"], cwd=None)

    passed = [wrapped[i + 1] for i, part in enumerate(wrapped) if part == "--env"]
    assert "SF_TOKEN" in passed
    assert not any(secret in part for part in wrapped), wrapped
    # The value has to reach the container by some route, and the environment is it.
    assert executor.policy.environment()["SF_TOKEN"] == secret


# ------------------------------------------------------------------------- ssh worker


def test_an_ssh_worker_reports_no_sandboxing(tmp_path: Path) -> None:
    """The worker's confinement is whatever the operator configured there, and this executor
    cannot verify it. An operator who thinks they have namespace isolation and has an SSH
    session is worse off than one who knows."""
    executor = SshWorkerExecutor(
        policy(tmp_path, network=NetworkPolicy.OPEN),
        host="worker.internal",
        remote_workspace="/srv/factory",
        ssh="/usr/bin/ssh",
    )

    assert executor.sandbox_level is SandboxLevel.NONE


def test_an_ssh_worker_refuses_to_claim_it_can_deny_the_network(tmp_path: Path) -> None:
    with pytest.raises(ExecutorError, match="cannot enforce `network: none`"):
        SshWorkerExecutor(
            policy(tmp_path, network=NetworkPolicy.NONE),
            host="worker.internal",
            remote_workspace="/srv/factory",
            ssh="/usr/bin/ssh",
        )


def test_an_ssh_worker_needs_a_host(tmp_path: Path) -> None:
    with pytest.raises(ExecutorError, match="no worker host"):
        SshWorkerExecutor(
            policy(tmp_path, network=NetworkPolicy.OPEN),
            host="   ",
            remote_workspace="/srv/factory",
            ssh="/usr/bin/ssh",
        )


def test_remote_arguments_are_quoted(tmp_path: Path) -> None:
    """ssh joins its arguments into a shell command on the remote side, so a path with a
    space would otherwise become two arguments -- and a path with a semicolon would become
    two commands."""
    from software_factory.runtime.executors import _shell_quote

    quoted = _shell_quote("a b; rm -rf /")

    assert quoted == "'a b; rm -rf /'"
    assert _shell_quote("it's") == "'it'\"'\"'s'"


def test_batch_mode_and_host_key_checking_are_on_by_default(tmp_path: Path) -> None:
    """A prompt on a runner hangs the run, and disabled host-key checking makes the
    connection trust whoever answers."""
    executor = SshWorkerExecutor(
        policy(tmp_path, network=NetworkPolicy.OPEN),
        host="worker.internal",
        remote_workspace="/srv/factory",
        ssh="/usr/bin/ssh",
    )

    assert "BatchMode=yes" in executor.options
    assert "StrictHostKeyChecking=yes" in executor.options


# ------------------------------------------------------------------------------- cloud


def test_the_cloud_executor_refuses_rather_than_falling_back(tmp_path: Path) -> None:
    """A factory that declares `cloud` and silently runs on the operator's laptop with the
    operator's credentials has lost every guarantee it declared."""
    with pytest.raises(CloudExecutorUnavailableError, match="not available in this build"):
        cloud_executor(policy(tmp_path), environment_id="env-1")


def test_the_cloud_refusal_names_what_to_use_instead(tmp_path: Path) -> None:
    with pytest.raises(CloudExecutorUnavailableError) as caught:
        cloud_executor(policy(tmp_path), environment_id="env-1")

    assert "container" in caught.value.remediation


# ---------------------------------------------------------------------------- protocol


def test_every_executor_satisfies_one_protocol(tmp_path: Path) -> None:
    """Swapping where a run executes must be a definition change, not a code change."""
    from software_factory.runtime.executor import LocalExecutor

    local = LocalExecutor(policy(tmp_path), level=SandboxLevel.PROCESS)
    container = ContainerExecutor(
        policy(tmp_path),
        ContainerImage("ghcr.io/acme/builder:1.0"),
        runtime="/usr/bin/docker",
        probe_runtime=False,
    )
    remote = SshWorkerExecutor(
        policy(tmp_path, network=NetworkPolicy.OPEN),
        host="worker.internal",
        remote_workspace="/srv/factory",
        ssh="/usr/bin/ssh",
    )

    for executor in (local, container, remote):
        assert isinstance(executor, Executor)
