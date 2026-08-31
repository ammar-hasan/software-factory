"""Workspaces, checkpoints, and the local executor.

These tests touch the filesystem and run real subprocesses, because that is precisely
what they are checking: a mocked executor would prove nothing about enforcement.
"""

from __future__ import annotations

import os
import subprocess
import sys
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
    live = factory.create(run_id="live")

    removed = factory.reclaim(keep={"live"})

    assert removed == ["finished"]
    assert live.root.exists()


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


def test_declared_secrets_reach_the_command(tmp_path: Path) -> None:
    result = executor(tmp_path, secrets={"SF_TOKEN": "value-123"}).run(
        [sys.executable, "-c", "import os; print(os.environ.get('SF_TOKEN', 'absent'))"]
    )

    assert "value-123" in result.stdout


def test_proxy_variables_are_stripped_when_no_network_is_granted(tmp_path: Path) -> None:
    """A run declaring no network must not reach one through an inherited proxy var."""
    os.environ["https_proxy"] = "http://proxy.invalid:8080"
    try:
        env = policy(tmp_path, network=NetworkPolicy.NONE).environment()
    finally:
        os.environ.pop("https_proxy", None)

    assert "https_proxy" not in env
    assert "HTTPS_PROXY" not in env


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
