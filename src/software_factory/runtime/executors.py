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
import subprocess
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
        # The tag lives in the last path component, and only there. A registry host may
        # carry a port -- `registry.local:5000/app` -- so splitting the whole reference on
        # the last colon reads that port as a tag. And a reference with no colon at all
        # used to yield the *image name* as its tag: `ubuntu` was accepted as pinned, which
        # is `ubuntu:latest` to every runtime and the exact case this class exists to
        # refuse. The old check caught `ghcr.io/acme/builder` only because that shape puts
        # a slash in the would-be tag -- an accident of the example, not the property.
        last = self.reference.rpartition("/")[2]
        name, separator, tag = last.partition(":")
        if not separator or not tag or tag == "latest" or not name:
            raise ValueError(
                f"{self.reference!r} is not pinned; use a digest (image@sha256:...) or at "
                "least an explicit version tag. `latest` is a different image on different "
                "days, which makes a failed replay indistinguishable from a real defect"
            )

    @property
    def pinned_by_digest(self) -> bool:
        return "@sha256:" in self.reference


def _guard_cwd(policy: SandboxPolicy, cwd: Path | None) -> None:
    """Refuse a working directory outside the run's writable paths.

    The local executor has enforced this all along; the other two did not, so the same call
    was an `ExecutorError` on one and a normal run on another. FR-20.5's parity requirement
    is exactly the claim that cannot survive that, and a gate or tool relying on the refusal
    would behave differently depending on where the run happened to execute.
    """
    if cwd is None:
        return
    if not policy.is_writable(cwd):
        raise ExecutorError(
            f"{cwd} is outside the run's writable paths",
            remediation=(
                "Run inside the workspace, or declare the path in `writablePaths`. A "
                "working directory outside the contract is outside the blast radius the "
                "run was reviewed against."
            ),
        )


def _remote_path(policy: SandboxPolicy, remote_workspace: str, cwd: Path | None) -> str:
    """Translate a local workspace path onto the worker.

    A *local* absolute path was being sent as a *remote* working directory, and nothing
    mapped one onto the other. On a worker whose checkout lives somewhere else that is a
    `cd` into a directory that does not exist, reported as the command failing.
    """
    if cwd is None:
        return remote_workspace
    try:
        relative = cwd.resolve().relative_to(policy.workspace.resolve())
    except ValueError:
        return remote_workspace
    return remote_workspace if str(relative) == "." else f"{remote_workspace}/{relative}"


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
        probe_runtime: bool = True,
    ) -> None:
        self.policy = policy
        self.image = image
        self.user = user
        # An explicit runtime is validated the same way, except when the caller is a test
        # asserting on the argv this builds rather than running anything. `probe_runtime`
        # is how that is said out loud: a test that had to reach through a private name to
        # avoid a daemon probe would have made the probe untestable.
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
        if runtime is not None and probe_runtime and not _daemon_reachable(runtime):
            raise ExecutorError(
                f"{runtime} is present but its daemon is not reachable",
                remediation=(
                    "Start the container daemon, or set `executor: local` and accept the "
                    "weaker isolation deliberately. Constructing anyway would report the "
                    "run's own command as having failed, which hides that the isolation "
                    "the factory declared was never applied."
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
        # Guarded here, against the value being guarded. The inner executor checks the cwd
        # it is *given*, and it is given the workspace -- so the caller's cwd went straight
        # to `--workdir` unchecked, and the same call was an error locally and a normal run
        # in a container. Parity is the claim; this is where it was false.
        _guard_cwd(self.policy, cwd)
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
        if policy.secrets:
            # OpenSSH forwards no environment by default, this executor sets no `SendEnv`
            # or `SetEnv`, and it prefixes no assignment to the remote command -- so a
            # declared secret simply did not exist on the worker. A command reading it got
            # an empty variable and failed with an authentication error attributed to the
            # credential rather than to the executor.
            #
            # Refusing rather than forwarding, because the alternatives are worse: `env
            # NAME=value` on the remote command line reproduces the `ps` exposure this
            # module fixed for containers, and a temporary file over the connection is a
            # secret written to a disk the factory does not control or clean up.
            raise ExecutorError(
                f"the ssh worker cannot carry {len(policy.secrets)} declared secret(s) to {host!r}",
                remediation=(
                    "Configure the worker's own environment with these values, and remove "
                    "them from the run's `secrets`. This executor does not confine the "
                    "worker and cannot deliver a secret to it without writing the value "
                    "somewhere the factory does not control."
                ),
            )
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
        self._inner = LocalExecutor(
            policy,
            level=SandboxLevel.NONE,
            allow_unsandboxed=True,
            describes_itself_as="ssh worker",
        )

    @property
    def sandbox_level(self) -> SandboxLevel:
        """Always ``NONE``. The worker may be well confined; this executor cannot tell, and
        reporting confinement it has not verified is exactly the failure C9 was."""
        return SandboxLevel.NONE

    def run(
        self, command: list[str], *, timeout_s: int | None = None, cwd: Path | None = None
    ) -> CommandResult:
        _guard_cwd(self.policy, cwd)
        remote_cwd = _remote_path(self.policy, self.remote_workspace, cwd)
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


DAEMON_PROBE_TIMEOUT_S = 10.0


def _detect_runtime() -> str | None:
    """The first container runtime with a *reachable daemon*, or None.

    Presence is not capability. `shutil.which` alone returns a binary whose daemon may be
    unreachable -- normal inside a container -- and this executor then constructed happily
    and reported the *caller's* command as having failed, because `run` rewrites
    `command=tuple(command)` before returning. A gate reading that result sees `echo hello`
    exiting 1, not "there is no container runtime": a run that never executed anywhere is
    indistinguishable from a run whose command failed, and nothing says the isolation the
    factory declared was never applied.

    The project already knew the right check and had it in `tests/test_parity.py`, whose
    own docstring names the alternative as "precisely the 'presence is not capability'
    mistake the whole project keeps finding elsewhere" -- while the code under test made
    exactly that mistake.
    """
    for candidate in ("docker", "podman"):
        found = shutil.which(candidate)
        if found and _daemon_reachable(found):
            return found
    return None


def _daemon_reachable(runtime: str) -> bool:
    """Whether this runtime can actually run anything right now."""
    try:
        probe = subprocess.run(
            [runtime, "info"],
            capture_output=True,
            timeout=DAEMON_PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _shell_quote(part: str) -> str:
    """POSIX single-quoting. Used for the remote side of an ssh invocation."""
    return "'" + part.replace("'", "'\"'\"'") + "'"
