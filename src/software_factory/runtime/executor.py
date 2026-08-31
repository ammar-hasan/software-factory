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
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from software_factory.definition.models import NetworkPolicy
from software_factory.errors import FactoryError
from software_factory.evals.gates import ViolationClass

#: How long to wait for a SIGKILLed process group to release the output pipes. Anything
#: still holding them after this could not be signalled, so its output is unreachable.
_REAP_GRACE_S = 5.0


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
        env.update(self.secrets)
        # A run that declares no network must not reach one by accident through a proxy
        # variable. This runs *after* the secrets are merged, which is the only position
        # where it does anything: `env_allowlist` does not carry the proxy names, so
        # stripping before the merge only ever removed variables that were never there --
        # dead code that read as a control. The live cases are a declared secret named
        # `HTTPS_PROXY` and an operator who added a proxy name to `env_allowlist`.
        if self.network is NetworkPolicy.NONE:
            for blocked in (
                "http_proxy",
                "https_proxy",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "all_proxy",
                "ALL_PROXY",
                "no_proxy",
                "NO_PROXY",
            ):
                env.pop(blocked, None)
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
        deadline = timeout_s or self.policy.wall_clock_s
        started = time.monotonic()
        try:
            # Popen rather than subprocess.run because `run`'s timeout path calls
            # Popen.kill(), which signals the direct child alone. The child is a session
            # leader, so a test runner's workers, a build daemon or a language server it
            # spawned all outlive the timeout -- holding the workspace open while
            # WorkspaceFactory.destroy races them with rmtree. Reaping needs killpg, and
            # killpg needs the handle.
            #
            # start_new_session replaces the old preexec_fn=os.setsid. It does the same
            # thing, but subprocess implements it between fork and exec in async-signal-safe
            # code; a Python-level preexec_fn can deadlock there, and an orchestrator
            # running agents concurrently is exactly the threaded caller that provokes it.
            process = subprocess.Popen(
                wrapped,
                cwd=workdir,
                env=self.policy.environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=os.name == "posix",
            )
        except FileNotFoundError as missing:
            return CommandResult(
                command=tuple(command),
                exit_code=127,
                stdout="",
                stderr=f"command not found: {command[0]} ({missing})",
                duration_s=time.monotonic() - started,
            )

        timed_out = False
        out: str | None
        err: str | None
        try:
            out, err = process.communicate(timeout=deadline)
        except subprocess.TimeoutExpired:
            timed_out = True
            out, err = self._reap(process)

        duration = time.monotonic() - started
        stdout, out_truncated = self._cap(self._redact(_decode(out)))
        stderr, err_truncated = self._cap(self._redact(_decode(err)))
        if timed_out:
            return CommandResult(
                command=tuple(command),
                exit_code=124,
                stdout=stdout,
                stderr=stderr or f"timed out after {duration:.0f}s",
                duration_s=duration,
                timed_out=True,
                truncated=out_truncated or err_truncated,
            )
        return CommandResult(
            command=tuple(command),
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_s=duration,
            truncated=out_truncated or err_truncated,
        )

    @staticmethod
    def _reap(process: subprocess.Popen[str]) -> tuple[str | None, str | None]:
        """Kill a timed-out command and everything it spawned, then collect what it wrote.

        The partial output is frequently the useful part of a timeout, so this returns it
        rather than discarding it -- but only after the group is gone, because a survivor
        still holding the pipe would keep `communicate` blocking forever.
        """
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                # Already reaped, or the group is not ours to signal. Fall through to the
                # direct kill: a best-effort escalation must not raise over the timeout it
                # is reporting.
                process.kill()
        else:
            process.kill()
        try:
            # Bounded: the group has had SIGKILL, so anything still holding the pipe is a
            # process we could not signal. Give up on its output rather than hang the run.
            return process.communicate(timeout=_REAP_GRACE_S)
        except subprocess.TimeoutExpired:
            return None, None

    def _wrap(self, command: list[str]) -> list[str]:
        """Wrap a command in the strongest available confinement."""
        limited = self._limited(command)
        if self.level is not SandboxLevel.NAMESPACE:
            return limited

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
        return [*args, "--", *limited]

    def _limited(self, command: list[str]) -> list[str]:
        """Resource ceilings, enforced by the OS rather than requested politely.

        Innermost on purpose. These were set from a `preexec_fn`, which put them on the
        *sandbox helper*: bwrap's own address space was charged against the run's memory
        ceiling, and the program actually being confined got whatever was left. Placing the
        shim inside `--` bounds the target and nothing else.

        `ulimit` with neither -H nor -S sets both the soft and the hard limit, matching what
        `setrlimit((n, n))` did; `exec` replaces the shell so no extra process survives.
        """
        if os.name != "posix":
            return command
        limits = (
            f"ulimit -t {self.policy.cpu_seconds}; "
            f"ulimit -v {self.policy.memory_mb * 1024}; "
            "ulimit -c 0; "
            'exec "$@"'
        )
        return ["/bin/sh", "-c", limits, "sh", *command]

    def _redact(self, text: str) -> str:
        """Strip declared secret values from output before anyone sees it.

        Applied at capture, not at read: a transcript, an evidence bundle and a log all
        read from here, and redacting at each of them would eventually miss one.
        """
        return redact(text, self.policy.secrets)

    def _cap(self, text: str) -> tuple[str, bool]:
        """Truncate output, and say so. Silent truncation is a defect (HARNESS.md T-6).

        Measured in UTF-8 bytes, which is what `output_limit_bytes` says and what the
        elision notice reports. It compared `len(text)` -- characters -- so output in a
        non-Latin script ran to two to four times the declared limit while the notice
        called the difference "bytes".

        The cut points are found by decoding back from a byte slice with `errors="ignore"`,
        so a multi-byte character is never split in half.
        """
        encoded = text.encode("utf-8")
        limit = self.policy.output_limit_bytes
        if len(encoded) <= limit:
            return text, False
        head_bytes = limit // 2
        tail_bytes = limit - head_bytes
        head = encoded[:head_bytes].decode("utf-8", errors="ignore")
        tail = encoded[-tail_bytes:].decode("utf-8", errors="ignore")
        return (
            f"{head}\n\n[... {len(encoded) - limit} bytes elided ...]\n\n{tail}",
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
