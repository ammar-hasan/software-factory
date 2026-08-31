"""Container and remote executors, behind the interface `LocalExecutor` already has.

PRD FR-8.2 lists four places a run's commands can execute: local, container, ssh worker, and
cloud. The local one is the reference implementation (PR-2), and these are the others -- but
the point of writing them is not that a factory needs four. It is that a factory declaring
`executor: container` and getting a silent fallback to local has lost the isolation it
declared, and `sf audit` would report a control that does not exist. That is the C9 failure
(a network allowlist reported and unenforced), and the fix is the same shape: refuse rather
than degrade.

So each executor here either provides what it claims or raises at construction with a
remediation. The one thing none of them does is quietly do something else.

What is *shared* rather than reimplemented matters as much: capping, redaction, the timeout
that kills a process group, and the violation classification all live in `LocalExecutor` and
are reused by composition. An executor that redacted differently from the local one would
make "the same definition behaves the same everywhere" false in the direction hardest to
notice -- a secret leaking on one runner and not another.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from software_factory.definition.models import NetworkPolicy
from software_factory.errors import FactoryError
from software_factory.runtime.executor import (
    CommandResult,
    ExecutorError,
    LocalExecutor,
    SandboxLevel,
    SandboxPolicy,
)


@runtime_checkable
class Executor(Protocol):
    """What the harness needs from anything that runs commands.

    One method. The tools, the gates and the orchestrator are written against this, so
    swapping where a run executes is a definition change rather than a code change --
    which is FR-8.2's actual requirement.
    """

    policy: SandboxPolicy

    def run(
        self, command: list[str], *, timeout_s: int | None = None, cwd: Path | None = None
    ) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class ContainerImage:
    """A pinned image. Unpinned is refused, not warned about.

    `latest` is a different image on different days, so a run that reproduces today and not
    tomorrow has no bug to find -- and FR-24's replay integrity is a claim about being able
    to re-run the same thing, which an unpinned tag makes false.
    """

    reference: str

    def __post_init__(self) -> None:
        if "@sha256:" in self.reference:
            return
        tag = self.reference.rpartition(":")[2]
        if not tag or tag == "latest" or "/" in tag:
            raise ValueError(
                f"{self.reference!r} is not pinned; use a digest (image@sha256:...) or at "
                "least an explicit version tag. `latest` is a different image on different "
                "days, which makes a failed replay indistinguishable from a real defect"
            )

    @property
    def pinned_by_digest(self) -> bool:
        return "@sha256:" in self.reference


class ContainerExecutor:
    """Runs commands inside a container (FR-8.2).

    Composes `LocalExecutor` rather than reimplementing it: the container runtime is invoked
    *by* a local command, so capping, redaction, the process-group kill and the violation
    classification are all inherited rather than written twice. Two executors that redact
    differently would make "the same definition behaves the same everywhere" false in the
    direction hardest to notice.

    Network policy is where a container genuinely does better than the local executor. The
    local one refuses `allowlist` outright (finding C9) because it cannot enforce per-host
    egress; here `none` maps to `--network none`, which the runtime enforces. `allowlist` is
    still refused, because mapping it to an open network would be the same lie in a new
    place -- a container runtime alone does not do per-host filtering either.
    """

    def __init__(
        self,
        policy: SandboxPolicy,
        image: ContainerImage,
        *,
        runtime: str | None = None,
        user: str = "1000:1000",
    ) -> None:
        self.policy = policy
        self.image = image
        self.user = user
        self.runtime = runtime or _detect_runtime()
        if self.runtime is None:
            raise ExecutorError(
                "no container runtime is available",
                remediation=(
                    "Install docker or podman, or set `executor: local` and accept the "
                    "weaker isolation deliberately. A factory that declares `container` and "
                    "silently runs locally has lost the isolation it declared."
                ),
            )
        if policy.network is NetworkPolicy.ALLOWLIST:
            raise ExecutorError(
                "a container runtime cannot enforce a per-host network allowlist by itself",
                remediation=(
                    "Set `network: none` to deny egress, or `network: open` to accept "
                    "unrestricted egress deliberately. Per-host filtering needs an egress "
                    "proxy, and reporting an unenforced allowlist as a control is worse "
                    "than not offering it."
                ),
            )
        # The inner executor runs `docker`/`podman` itself, so it is unsandboxed on purpose:
        # the container is the sandbox, and wrapping the runtime invocation in bwrap would
        # confine the client rather than the workload.
        self._inner = LocalExecutor(policy, level=SandboxLevel.NONE, allow_unsandboxed=True)

    def run(
        self, command: list[str], *, timeout_s: int | None = None, cwd: Path | None = None
    ) -> CommandResult:
        wrapped = self._wrap(command, cwd=cwd)
        result = self._inner.run(wrapped, timeout_s=timeout_s, cwd=self.policy.workspace)
        # Report the command the caller asked for, not the runtime invocation. A transcript
        # full of `docker run --rm ...` tells a reader about the executor rather than about
        # the work, and the executor is not what they are debugging.
        return CommandResult(
            command=tuple(command),
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_s=result.duration_s,
            timed_out=result.timed_out,
            truncated=result.truncated,
            violations=result.violations,
        )

    def _wrap(self, command: list[str], *, cwd: Path | None) -> list[str]:
        workspace = str(self.policy.workspace)
        args = [
            self.runtime or "docker",
            "run",
            "--rm",
            "--user",
            self.user,
            # No new privileges: a setuid binary inside the image must not be a way out of
            # the confinement the container is providing.
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--memory",
            f"{self.policy.memory_mb}m",
            "--pids-limit",
            "512",
            "--volume",
            f"{workspace}:{workspace}",
            "--workdir",
            str(cwd or self.policy.workspace),
        ]
        if self.policy.network is NetworkPolicy.NONE:
            args += ["--network", "none"]
        for name in sorted(self.policy.environment()):
            # `--env NAME` without a value: docker reads the value from its own environment,
            # which the inner executor already sets from the same policy. The form with the
            # value -- `--env NAME=secret` -- puts it in the host's process list, where
            # anyone who can run `ps` reads it and redaction at capture never reaches.
            #
            # The comment saying so was here before the code did. That is the failure worth
            # naming: a stated control is not a control, and the test named for this
            # property asserted the leak.
            args += ["--env", name]
        for writable in self.policy.writable_paths:
            if Path(writable) != self.policy.workspace:
                args += ["--volume", f"{writable}:{writable}"]
        return [*args, self.image.reference, *command]


class SshWorkerExecutor:
    """Runs commands on a remote worker over SSH (FR-8.2).

    The honest framing, stated here because it is easy to assume otherwise: this is *not* an
    isolation boundary the factory controls. The worker's confinement is whatever the
    operator configured on that machine, and this executor cannot verify it. What it
    provides is a different machine -- for capacity, for an architecture the operator does
    not have locally, or to keep a repository's checkout off a laptop.

    So `sf audit` must not report ssh-worker as isolation, and `sandbox_level` says
    ``NONE``: an operator who thinks they have namespace isolation and has an SSH session is
    worse off than one who knows.
    """

    def __init__(
        self,
        policy: SandboxPolicy,
        *,
        host: str,
        remote_workspace: str,
        ssh: str | None = None,
        options: tuple[str, ...] = (
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
        ),
    ) -> None:
        self.policy = policy
        self.host = host
        self.remote_workspace = remote_workspace
        self.options = options
        self.ssh = ssh or shutil.which("ssh") or ""
        if not self.ssh:
            raise ExecutorError(
                "no ssh client is available",
                remediation="Install an ssh client, or use a different executor.",
            )
        if not host.strip():
            raise ExecutorError(
                "no worker host was configured",
                remediation="Set `workerHost` on the agent or in the factory defaults.",
            )
        if policy.network is NetworkPolicy.NONE:
            raise ExecutorError(
                "an ssh worker cannot enforce `network: none`",
                remediation=(
                    "The worker's egress is whatever that machine allows, and this executor "
                    "cannot restrict it. Use the container executor for a run that must not "
                    "reach the network, or declare `network: open` and mean it."
                ),
            )
        self._inner = LocalExecutor(policy, level=SandboxLevel.NONE, allow_unsandboxed=True)

    @property
    def sandbox_level(self) -> SandboxLevel:
        """Always ``NONE``. The worker may be well confined; this executor cannot tell, and
        reporting confinement it has not verified is exactly the failure C9 was."""
        return SandboxLevel.NONE

    def run(
        self, command: list[str], *, timeout_s: int | None = None, cwd: Path | None = None
    ) -> CommandResult:
        remote_cwd = str(cwd) if cwd else self.remote_workspace
        # `--` then the argument vector: ssh joins its arguments into a shell command on the
        # remote side, so a path containing a space would otherwise become two arguments.
        # Quoting each part is the difference between running one command and running
        # whatever the parts happen to spell.
        remote = " ".join(_shell_quote(part) for part in command)
        wrapped = [
            self.ssh,
            *self.options,
            self.host,
            "--",
            f"cd {_shell_quote(remote_cwd)} && {remote}",
        ]
        result = self._inner.run(wrapped, timeout_s=timeout_s, cwd=self.policy.workspace)
        return CommandResult(
            command=tuple(command),
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_s=result.duration_s,
            timed_out=result.timed_out,
            truncated=result.truncated,
            violations=result.violations,
        )


class CloudExecutorUnavailableError(FactoryError):
    """The cloud executor is declared and not implemented in this build.

    A distinct error rather than a generic one, because the remediation is different: this
    is not a misconfiguration to fix but a capability this build does not have, and telling
    an operator to check their configuration would send them looking for a mistake that is
    not there.
    """


def cloud_executor(
    policy: SandboxPolicy,  # noqa: ARG001 - the signature is the contract it refuses under
    *,
    environment_id: str,
) -> Executor:
    """Refuse clearly rather than fall back.

    FR-8.2 lists a cloud executor and this build does not provide one. The alternative --
    quietly running locally -- would mean a factory declaring `executor: cloud` runs on the
    operator's laptop with the operator's credentials and their `sf audit` reporting
    something else entirely. Refusing costs an operator a configuration change; falling back
    costs them a guarantee they believed they had.
    """
    raise CloudExecutorUnavailableError(
        f"the cloud executor (environment {environment_id!r}) is not available in this build",
        remediation=(
            "Use `local`, `container`, or `ssh-worker`. This build refuses rather than "
            "falling back to local, because a factory that declares `cloud` and silently "
            "runs on your laptop has lost every guarantee it declared."
        ),
    )


def _detect_runtime() -> str | None:
    for candidate in ("docker", "podman"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _shell_quote(part: str) -> str:
    """POSIX single-quoting. Used for the remote side of an ssh invocation."""
    return "'" + part.replace("'", "'\"'\"'") + "'"
