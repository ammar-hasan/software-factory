"""Workspaces, checkpoints, and the local executor.

These tests touch the filesystem and run real subprocesses, because that is precisely
what they are checking: a mocked executor would prove nothing about enforcement.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from software_factory.definition.models import NetworkPolicy
from software_factory.evals.gates import ViolationClass
from software_factory.runtime import (
    ExecutorError,
    LocalExecutor,
    SandboxLevel,
    SandboxPolicy,
    WorkspaceError,
    WorkspaceFactory,
    redact,
)

pytestmark = pytest.mark.integration


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
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    git(["init", "--quiet", "-b", "main"], repo)
    (repo / "importer.py").write_text("def strip_bom(text):\n    return text\n", encoding="utf-8")
    git(["add", "-A"], repo)
    git(["commit", "--quiet", "-m", "initial"], repo)
    return repo


@pytest.fixture
def factory(tmp_path: Path, source_repo: Path) -> WorkspaceFactory:
    return WorkspaceFactory(source_repo, tmp_path / "state")


# ------------------------------------------------------------------------- workspaces


def test_a_workspace_is_an_isolated_checkout(factory: WorkspaceFactory) -> None:
    """Concurrent runs on one repository must not interfere."""
    first = factory.create(run_id="run-1")
    second = factory.create(run_id="run-2")

    (first.root / "importer.py").write_text("changed by run 1\n", encoding="utf-8")

    assert first.root != second.root
    assert "changed by run 1" not in (second.root / "importer.py").read_text(encoding="utf-8")


def test_creating_a_workspace_from_a_non_repository_is_actionable(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()

    with pytest.raises(WorkspaceError, match="not a git repository"):
        WorkspaceFactory(not_a_repo, tmp_path / "state").create()


def test_restoring_a_checkpoint_returns_the_tree_exactly(factory: WorkspaceFactory) -> None:
    workspace = factory.create()
    original = (workspace.root / "importer.py").read_text(encoding="utf-8")
    point = workspace.checkpoint("before")

    (workspace.root / "importer.py").write_text("a bold rewrite\n", encoding="utf-8")
    workspace.restore(point)

    assert (workspace.root / "importer.py").read_text(encoding="utf-8") == original


def test_restore_removes_files_created_since_the_checkpoint(factory: WorkspaceFactory) -> None:
    """A restore that leaves debris is not a restore; the debris changes later runs."""
    workspace = factory.create()
    point = workspace.checkpoint("before")

    (workspace.root / "scratch.txt").write_text("speculative work\n", encoding="utf-8")
    workspace.restore(point)

    assert not (workspace.root / "scratch.txt").exists()


def test_speculation_can_be_tried_and_discarded(factory: WorkspaceFactory) -> None:
    """The whole point of cheap undo: a bold approach costs a rollback, not an incident."""
    workspace = factory.create()
    safe = workspace.checkpoint("c0")

    (workspace.root / "importer.py").write_text("approach A\n", encoding="utf-8")
    workspace.checkpoint("tried A")
    (workspace.root / "importer.py").write_text("approach B\n", encoding="utf-8")
    workspace.restore(safe)

    assert "def strip_bom" in (workspace.root / "importer.py").read_text(encoding="utf-8")


def test_changed_paths_reports_the_change_surface(factory: WorkspaceFactory) -> None:
    workspace = factory.create()
    (workspace.root / "importer.py").write_text("changed\n", encoding="utf-8")
    (workspace.root / "new.py").write_text("added\n", encoding="utf-8")

    assert workspace.changed_paths() == {"importer.py", "new.py"}


def test_diff_renders_the_change_as_a_patch(factory: WorkspaceFactory) -> None:
    workspace = factory.create()
    (workspace.root / "importer.py").write_text("changed\n", encoding="utf-8")

    patch = workspace.diff()

    assert "importer.py" in patch
    assert "+changed" in patch


def test_a_file_can_be_read_at_the_parent_commit(factory: WorkspaceFactory) -> None:
    """Two-checkout gates like regression-proven depend on this."""
    workspace = factory.create()
    (workspace.root / "importer.py").write_text("changed\n", encoding="utf-8")

    at_base = workspace.file_at(workspace.base_commit, "importer.py")

    assert at_base is not None
    assert "def strip_bom" in at_base


def test_reclaim_removes_workspaces_for_finished_runs(factory: WorkspaceFactory) -> None:
    """Without this a factory fills the disk within days of normal operation."""
    factory.create(run_id="finished")
    running = factory.create(run_id="live")

    removed = factory.reclaim(live={"live"}, older_than=timedelta(0))

    assert removed == ["finished"]
    assert running.root.exists()


def test_reclaim_spares_a_workspace_younger_than_the_age_bound(
    factory: WorkspaceFactory,
) -> None:
    """A run that started moments ago is the one most likely to be missing from a `live`
    set gathered while the orchestrator was restarting."""
    just_started = factory.create(run_id="fresh")

    assert factory.reclaim(live=set()) == []
    assert just_started.root.exists()


# --------------------------------------------------------------------------- executor


def policy(workspace: Path, **kwargs) -> SandboxPolicy:
    base: dict[str, object] = {"workspace": workspace, "wall_clock_s": 20}
    base.update(kwargs)
    return SandboxPolicy(**base)  # type: ignore[arg-type]


def executor(workspace: Path, **kwargs) -> LocalExecutor:
    return LocalExecutor(policy(workspace, **kwargs), level=SandboxLevel.PROCESS)


def test_a_command_returns_a_structured_result(tmp_path: Path) -> None:
    result = executor(tmp_path).run([sys.executable, "-c", "print('hello')"])

    assert result.ok
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert result.duration_s >= 0


def test_a_failing_command_is_a_result_not_an_exception(tmp_path: Path) -> None:
    result = executor(tmp_path).run([sys.executable, "-c", "raise SystemExit(3)"])

    assert not result.ok
    assert result.exit_code == 3


def test_a_missing_program_is_reported_not_raised(tmp_path: Path) -> None:
    result = executor(tmp_path).run(["definitely-not-a-real-program-xyz"])

    assert result.exit_code == 127
    assert "not found" in result.stderr


def test_a_timeout_is_a_recorded_outcome_that_keeps_partial_output(tmp_path: Path) -> None:
    """Output up to the timeout is frequently the useful part."""
    result = executor(tmp_path).run(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=1
    )

    assert result.timed_out
    assert result.exit_code == 124


def test_an_empty_command_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ExecutorError, match="empty command"):
        executor(tmp_path).run([])


def test_running_outside_the_writable_set_is_refused(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    with pytest.raises(ExecutorError, match="outside the run's writable paths"):
        executor(workspace).run([sys.executable, "-c", "pass"], cwd=elsewhere)


def test_output_over_the_limit_is_truncated_and_says_so(tmp_path: Path) -> None:
    """Silent truncation is a defect."""
    result = executor(tmp_path, output_limit_bytes=500).run(
        [sys.executable, "-c", "print('x' * 5000)"]
    )

    assert result.truncated
    assert "elided" in result.stdout


def test_the_environment_is_an_allowlist(tmp_path: Path) -> None:
    os.environ["SF_TEST_LEAKED"] = "should-not-appear"
    try:
        result = executor(tmp_path).run(
            [sys.executable, "-c", "import os; print(os.environ.get('SF_TEST_LEAKED', 'absent'))"]
        )
    finally:
        os.environ.pop("SF_TEST_LEAKED", None)

    assert "absent" in result.stdout


def test_declared_secrets_reach_the_command_but_not_the_captured_output(
    tmp_path: Path,
) -> None:
    """Both halves matter: the command can use it, and nobody reading output can see it."""
    result = executor(tmp_path, secrets={"SF_TOKEN": "value-1234567890"}).run(
        [sys.executable, "-c", "import os; print(os.environ.get('SF_TOKEN', 'absent'))"]
    )

    assert "absent" not in result.stdout
    assert "value-1234567890" not in result.stdout
    assert "<SF_TOKEN:redacted>" in result.stdout


def test_an_inherited_proxy_variable_never_reaches_any_run(tmp_path: Path) -> None:
    """The allowlist is what keeps an inherited proxy out, in every network mode.

    This test used to be named for the proxy-stripping branch and passed without it: the
    allowlist does not carry proxy names, so `environment()` never copied one from
    `os.environ` in the first place (T4). Testing the allowlist is worth doing -- it is
    just a different property from the one the name claimed.
    """
    os.environ["https_proxy"] = "http://proxy.invalid:8080"
    try:
        for network in (NetworkPolicy.NONE, NetworkPolicy.OPEN):
            env = policy(tmp_path, network=network).environment()
            assert "https_proxy" not in env
            assert "HTTPS_PROXY" not in env
    finally:
        os.environ.pop("https_proxy", None)


def test_a_declared_secret_cannot_smuggle_a_proxy_into_a_no_network_run(
    tmp_path: Path,
) -> None:
    """The live case for the stripping branch, and the one it did not cover.

    Secrets are merged into the environment, so a secret named `HTTPS_PROXY` walked
    straight past a strip that ran before the merge -- turning `network: none` into
    unrestricted egress through a name an operator chose.
    """
    env = policy(
        tmp_path,
        network=NetworkPolicy.NONE,
        secrets={"HTTPS_PROXY": "http://proxy.invalid:8080", "SF_TOKEN": "keep-me"},
    ).environment()

    assert "HTTPS_PROXY" not in env
    assert env["SF_TOKEN"] == "keep-me"


def test_an_open_network_run_keeps_a_declared_proxy(tmp_path: Path) -> None:
    """The strip is conditioned on the declaration, not applied unconditionally: a run that
    declares open egress may legitimately need the proxy to reach it."""
    env = policy(
        tmp_path,
        network=NetworkPolicy.OPEN,
        secrets={"HTTPS_PROXY": "http://proxy.internal:8080"},
    ).environment()

    assert env["HTTPS_PROXY"] == "http://proxy.internal:8080"


# --------------------------------------------------------------- violation classes


def test_a_write_inside_the_workspace_is_not_a_violation(tmp_path: Path) -> None:
    assert policy(tmp_path).classify_write(tmp_path / "file.py") is None


def test_a_cache_write_is_benign_not_a_violation(tmp_path: Path) -> None:
    """Ordinary toolchains write caches constantly; treating that as an attack is noise."""
    assert policy(tmp_path).classify_write(Path("/tmp/pip-build")) is ViolationClass.BENIGN


def test_a_write_outside_the_contract_is_blocked(tmp_path: Path) -> None:
    assert policy(tmp_path).classify_write(Path("/etc/passwd")) is ViolationClass.BLOCKED


def test_a_declared_extra_writable_path_is_allowed(tmp_path: Path) -> None:
    extra = tmp_path / "artifacts"
    extra.mkdir()

    assert policy(tmp_path, writable_paths=(extra,)).classify_write(extra / "out.json") is None


# ------------------------------------------------------------------------- redaction


def test_known_secret_values_are_redacted() -> None:
    text = "the token is value-1234567890 and it appears twice: value-1234567890"

    assert "value-1234567890" not in redact(text, {"SF_TOKEN": "value-1234567890"})


def test_a_secret_containing_another_is_replaced_longest_first() -> None:
    """Otherwise the shorter replacement leaves a fragment of the longer one behind."""
    text = "prefix-secret-suffix"

    result = redact(text, {"SHORT": "prefix-secret", "LONG": "prefix-secret-suffix"})

    assert "prefix-secret" not in result


def test_very_short_values_are_not_redacted() -> None:
    """Redacting a short common string would destroy the surrounding output."""
    assert redact("the value is on", {"FLAG": "on"}) == "the value is on"


def test_sandbox_detection_reports_what_is_available() -> None:
    from software_factory.runtime import detect_sandbox_level

    assert detect_sandbox_level() in set(SandboxLevel)


def test_running_without_a_sandbox_requires_opting_in(tmp_path: Path) -> None:
    """Never silently unconfined: the operator has to say so."""
    with pytest.raises(ExecutorError, match="no sandboxing is available"):
        LocalExecutor(policy(tmp_path), level=SandboxLevel.NONE)

    assert LocalExecutor(policy(tmp_path), level=SandboxLevel.NONE, allow_unsandboxed=True)


def test_a_runs_tooling_does_not_pollute_the_change_surface(tmp_path: Path) -> None:
    """`changed_paths` is what the blast-radius contract is checked against.

    The sandbox confines writes to the workspace, so `HOME` has to live inside it — and with
    `HOME` set to the repository root, one `pip install` put forty cache files into the
    change surface. A real product trial reached handoff having "changed"
    `.rustup/settings.toml` and thirty-eight pip cache entries, which is a diff nobody can
    review and a blast radius computed over files nothing wrote.
    """
    import subprocess

    from software_factory.runtime.executor import LocalExecutor, SandboxPolicy
    from software_factory.runtime.workspace import HOME_DIR, WorkspaceFactory

    source = tmp_path / "repo"
    source.mkdir()
    (source / "a.py").write_text("x = 1\n", encoding="utf-8")
    for command in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.test"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "initial"],
    ):
        subprocess.run(["git", *command], cwd=source, check=True, capture_output=True)

    workspace = WorkspaceFactory(source=source, state_dir=tmp_path / "state").create()
    policy = SandboxPolicy(workspace=workspace.root)
    executor = LocalExecutor(policy, allow_unsandboxed=True)

    # Exactly what a run's tooling does: write into $HOME, and leave a bytecode cache.
    executor.run(["sh", "-c", 'mkdir -p "$HOME/.cache/pip" && echo x > "$HOME/.cache/pip/f"'])
    (workspace.root / "__pycache__").mkdir(exist_ok=True)
    (workspace.root / "__pycache__" / "a.cpython-311.pyc").write_bytes(b"\x00")

    changed = workspace.changed_paths()

    assert changed == set(), sorted(changed)
    assert (workspace.root / HOME_DIR / ".cache" / "pip" / "f").exists(), (
        "the write went somewhere other than the workspace home"
    )


def test_a_real_change_is_still_seen(tmp_path: Path) -> None:
    """The exclusion must not be so wide that it hides the work.

    A filter that quietly swallowed a source file would turn every run into a no-op that
    passed its gates, which is worse than the pollution it was written to stop.
    """
    import subprocess

    from software_factory.runtime.workspace import WorkspaceFactory

    source = tmp_path / "repo"
    source.mkdir()
    (source / "a.py").write_text("x = 1\n", encoding="utf-8")
    for command in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.test"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "initial"],
    ):
        subprocess.run(["git", *command], cwd=source, check=True, capture_output=True)

    workspace = WorkspaceFactory(source=source, state_dir=tmp_path / "state").create()
    (workspace.root / "a.py").write_text("x = 2\n", encoding="utf-8")
    (workspace.root / "b.py").write_text("y = 3\n", encoding="utf-8")

    assert workspace.changed_paths() == {"a.py", "b.py"}
