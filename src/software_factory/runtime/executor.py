"""Executors: where a run's commands actually run (PRD FR-8.2, FR-8.5-8.7, FR-12.10).

The executor is the component that *enforces* the blast-radius contract. The prompt
states the contract; this decides what actually happens. That split is the whole security
model: no wording anywhere can widen what a run reaches, because the wording is not what
is consulted.

The local executor works with no privileged daemon, and degrades explicitly: when
OS-level sandboxing is unavailable it says so and requires the operator to opt in, rather
than quietly running unconfined.
"""

from __future__ import annotations

import enum
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from software_factory.definition.models import NetworkPolicy
from software_factory.errors import FactoryError
from software_factory.evals.gates import ViolationClass


class ExecutorError(FactoryError):
    """The executor could not run the command at all."""


class SandboxLevel(enum.StrEnum):
    """How strongly the executor can confine a command."""

    NONE = "none"
    PROCESS = "process"
    """Working directory, environment, and resource limits. No filesystem confinement."""
    NAMESPACE = "namespace"
    """Filesystem and network confinement via an OS sandbox helper."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The structured result of one command. Never prose."""

    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    truncated: bool = False
    violations: tuple[tuple[ViolationClass, str], ...] = ()

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def as_dict(self) -> dict[str, object]:
        return {
            "command": list(self.command),
            "exitCode": self.exit_code,
            "ok": self.ok,
            "durationSeconds": round(self.duration_s, 3),
            "timedOut": self.timed_out,
            "truncated": self.truncated,
            "violations": [{"class": c.value, "detail": d} for c, d in self.violations],
        }


@dataclass(slots=True)
class SandboxPolicy:
    """What a command may touch. Enforced, not requested."""

    workspace: Path
    writable_paths: tuple[Path, ...] = ()
    network: NetworkPolicy = NetworkPolicy.NONE
    network_allowlist: tuple[str, ...] = ()
    env_allowlist: tuple[str, ...] = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
    secrets: dict[str, str] = field(default_factory=dict)
    cpu_seconds: int = 900
    memory_mb: int = 4096
    wall_clock_s: int = 1800
    output_limit_bytes: int = 1_000_000
    #: Paths ordinary toolchains write to constantly. Writes here are `benign`, not
    #: violations -- a zero-tolerance counter over these is a gate that gets switched off
    #: in the first week (PRD FR-12.10).
    tolerated_writes: tuple[str, ...] = (
        "/tmp",
        "/var/tmp",
        "/dev/null",
        "~/.cache",
        "~/.npm",
        "~/.cargo/registry",
    )

    def is_writable(self, path: Path) -> bool:
        candidates = (self.workspace, *self.writable_paths)
        resolved = path.resolve()
        return any(_is_within(resolved, allowed.resolve()) for allowed in candidates)

    def classify_write(self, path: Path) -> ViolationClass | None:
        """``None`` when the write is inside the contract.

        The tolerated-path check resolves first and compares component-wise. A bare
        ``startswith`` on the unresolved string classified ``/tmp/../etc/passwd`` as
        benign, and ``/tmpevil/x`` as inside ``/tmp`` -- so a write outside the contract
        needed one ``..`` to become invisible to the gate.
        """
        if self.is_writable(path):
            return None
        resolved = path.resolve()
        for tolerated in self.tolerated_writes:
            try:
                allowed = Path(tolerated).expanduser().resolve()
            except OSError:  # pragma: no cover - unresolvable tolerated path
                continue
            if resolved == allowed or _is_within(resolved, allowed):
                return ViolationClass.BENIGN
        return ViolationClass.BLOCKED

    def environment(self) -> dict[str, str]:
        """The environment a command sees: an allowlist, plus declared secrets only."""
        env = {name: os.environ[name] for name in self.env_allowlist if name in os.environ}
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        env["HOME"] = str(self.workspace)
        env["PWD"] = str(self.workspace)
        # A run that declares no network should not be able to reach one by accident
        # through a proxy variable inherited from the operator's shell.
        if self.network is NetworkPolicy.NONE:
            for blocked in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
                env.pop(blocked, None)
        env.update(self.secrets)
        return env


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def detect_sandbox_level() -> SandboxLevel:
    """What confinement this machine can actually provide.

    Reported rather than assumed: an operator who thinks they have namespace isolation
    and has process isolation is worse off than one who knows.
    """
    if shutil.which("bwrap"):
        return SandboxLevel.NAMESPACE
    return SandboxLevel.PROCESS


class LocalExecutor:
    """Runs commands on this machine, under a policy.

    Deliberately not a container runtime: local is the reference implementation
    (ADR-0002), and requiring a daemon to run one command would break that.
    """

    def __init__(
        self,
        policy: SandboxPolicy,
        *,
        allow_unsandboxed: bool = False,
        level: SandboxLevel | None = None,
    ) -> None:
        self.policy = policy
        self.level = level or detect_sandbox_level()
        if self.level is SandboxLevel.NONE and not allow_unsandboxed:
            raise ExecutorError(
                "no sandboxing is available on this machine",
                remediation=(
                    "Install a sandbox helper, use the container executor, or pass "
                    "--allow-unsandboxed to run without confinement and accept the risk."
                ),
            )
        if policy.network is NetworkPolicy.ALLOWLIST:
            # Per-host egress filtering is not implemented by this executor. Refusing is
            # the only honest option: silently treating an allowlist as open egress while
            # `sf audit` reports it as a control is worse than not offering it, because
            # the operator would be reading a guarantee that does not exist.
            raise ExecutorError(
                "the local executor cannot enforce a per-host network allowlist",
                remediation=(
                    "Set `network: none` to deny egress, or `network: open` to accept "
                    "unrestricted egress deliberately. Per-host filtering needs the "
                    "container executor."
                ),
            )

    def run(
        self, command: list[str], *, timeout_s: int | None = None, cwd: Path | None = None
    ) -> CommandResult:
        """Execute one command and return its structured result.

        A timeout is a recorded outcome, never an exception that loses the partial
        output: the output up to the timeout is frequently the useful part.
        """
        if not command:
            raise ExecutorError("empty command", remediation="Pass the program and its arguments.")

        workdir = cwd or self.policy.workspace
        if not self.policy.is_writable(workdir):
            raise ExecutorError(
                f"{workdir} is outside the run's writable paths",
                remediation="Run inside the workspace, or declare the path as writable.",
            )

        wrapped = self._wrap(command)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                wrapped,
                cwd=workdir,
                env=self.policy.environment(),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_s or self.policy.wall_clock_s,
                preexec_fn=self._limits if os.name == "posix" else None,
            )
            duration = time.monotonic() - started
            stdout, out_truncated = self._cap(self._redact(completed.stdout))
            stderr, err_truncated = self._cap(self._redact(completed.stderr))
            return CommandResult(
                command=tuple(command),
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_s=duration,
                truncated=out_truncated or err_truncated,
            )
        except subprocess.TimeoutExpired as expired:
            duration = time.monotonic() - started
            stdout, _ = self._cap(self._redact(_decode(expired.stdout)))
            stderr, _ = self._cap(self._redact(_decode(expired.stderr)))
            return CommandResult(
                command=tuple(command),
                exit_code=124,
                stdout=stdout,
                stderr=stderr or f"timed out after {duration:.0f}s",
                duration_s=duration,
                timed_out=True,
            )
        except FileNotFoundError as missing:
            return CommandResult(
                command=tuple(command),
                exit_code=127,
                stdout="",
                stderr=f"command not found: {command[0]} ({missing})",
                duration_s=time.monotonic() - started,
            )

    def _wrap(self, command: list[str]) -> list[str]:
        """Wrap a command in the strongest available confinement."""
        if self.level is not SandboxLevel.NAMESPACE:
            return command

        args = [
            "bwrap",
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--ro-bind-try",
            "/etc/ssl",
            "/etc/ssl",
            "--ro-bind-try",
            "/etc/resolv.conf",
            "/etc/resolv.conf",
            "--bind",
            str(self.policy.workspace),
            str(self.policy.workspace),
            "--tmpfs",
            "/tmp",
            "--chdir",
            str(self.policy.workspace),
        ]
        for writable in self.policy.writable_paths:
            args += ["--bind", str(writable), str(writable)]
        if self.policy.network is NetworkPolicy.NONE:
            args.append("--unshare-net")
        return [*args, "--", *command]

    def _limits(self) -> None:  # pragma: no cover - runs in the child process
        """Resource ceilings, enforced by the OS rather than requested politely."""
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (self.policy.cpu_seconds, self.policy.cpu_seconds))
        limit = self.policy.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.setsid()

    def _redact(self, text: str) -> str:
        """Strip declared secret values from output before anyone sees it.

        Applied at capture, not at read: a transcript, an evidence bundle and a log all
        read from here, and redacting at each of them would eventually miss one.
        """
        return redact(text, self.policy.secrets)

    def _cap(self, text: str) -> tuple[str, bool]:
        """Truncate output, and say so. Silent truncation is a defect (HARNESS.md T-6)."""
        if len(text) <= self.policy.output_limit_bytes:
            return text, False
        head = self.policy.output_limit_bytes // 2
        tail = self.policy.output_limit_bytes - head
        return (
            f"{text[:head]}\n\n[... {len(text) - self.policy.output_limit_bytes} bytes elided "
            f"...]\n\n{text[-tail:]}",
            True,
        )


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def redact(text: str, secrets: dict[str, str]) -> str:
    """Replace known secret values wherever they appear (PRD FR-17.3).

    A backstop, not a control: the design does not depend on it. Longest values are
    replaced first so a secret containing another secret does not leave a fragment behind.
    """
    for name, value in sorted(secrets.items(), key=lambda pair: -len(pair[1])):
        if value and len(value) >= 8:
            text = text.replace(value, f"<{name}:redacted>")
    return text
