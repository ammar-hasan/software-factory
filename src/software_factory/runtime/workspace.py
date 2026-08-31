"""Isolated run workspaces and checkpoints (PRD FR-8.4, FR-12.2).

Every run gets its own checkout, never a shared mutable directory, so concurrent runs on
one repository cannot interfere. Inside it, checkpoints make undo cheap — which is the
precondition for the boldness the harness asks agents for. An agent that cannot cheaply
undo will pick the timid approach every time, so this module exists to make rollback a
normal move rather than an incident.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from software_factory.errors import FactoryError


class WorkspaceError(FactoryError):
    """A workspace could not be created, checkpointed, or restored."""


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A point a workspace can be returned to exactly."""

    id: str
    label: str
    tree_digest: str

    def render(self) -> str:
        return f"{self.id} ({self.label})"


def _git(args: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git with the environment pinned so results do not depend on the operator.

    Author identity, hooks, and config discovery are all sources of behaviour that
    differs between machines. A workspace that behaves differently on a laptop and a
    runner is a workspace whose test results mean nothing.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(cwd),
            "GIT_AUTHOR_NAME": "software-factory",
            "GIT_AUTHOR_EMAIL": "factory@localhost",
            "GIT_COMMITTER_NAME": "software-factory",
            "GIT_COMMITTER_EMAIL": "factory@localhost",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    if check and result.returncode != 0:
        raise WorkspaceError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}",
            remediation="Check that the path is a git repository and the working tree is readable.",
        )
    return result


@dataclass(slots=True)
class Workspace:
    """One run's isolated working copy.

    Checkpoints are git stashes-as-commits on a detached scratch ref rather than
    filesystem copies: they are cheap on large repositories, and restoring one is exact
    rather than best-effort.
    """

    root: Path
    run_id: str
    base_commit: str
    checkpoints: list[Checkpoint] = field(default_factory=list)

    @property
    def head(self) -> str:
        return _git(["rev-parse", "HEAD"], cwd=self.root).stdout.strip()

    def checkpoint(self, label: str) -> Checkpoint:
        """Record the exact current state, including uncommitted work."""
        _git(["add", "-A"], cwd=self.root)
        tree = _git(["write-tree"], cwd=self.root).stdout.strip()
        parent = self.head
        commit = _git(
            ["commit-tree", tree, "-p", parent, "-m", f"checkpoint: {label}"], cwd=self.root
        ).stdout.strip()
        point = Checkpoint(id=commit[:12], label=label, tree_digest=tree)
        _git(["update-ref", f"refs/factory/checkpoints/{point.id}", commit], cwd=self.root)
        self.checkpoints.append(point)
        return point

    def restore(self, checkpoint: Checkpoint) -> None:
        """Return the working tree to a checkpoint, exactly.

        Untracked files are removed as well as tracked ones: a "restore" that leaves
        debris behind is not a restore, and the debris is exactly what makes a later run
        behave differently from a fresh one.
        """
        _git(["read-tree", checkpoint.tree_digest], cwd=self.root)
        _git(["checkout-index", "-a", "-f"], cwd=self.root)
        _git(["clean", "-fdx", "--exclude=.factory"], cwd=self.root)

    def diff(self, *, against: str | None = None) -> str:
        """The change this run has made, as a patch."""
        _git(["add", "-A"], cwd=self.root)
        return _git(["diff", "--cached", against or self.base_commit], cwd=self.root).stdout

    def changed_paths(self) -> set[str]:
        """Files this run has touched. The change surface every pack is built around."""
        _git(["add", "-A"], cwd=self.root)
        output = _git(["diff", "--cached", "--name-only", self.base_commit], cwd=self.root).stdout
        return {line.strip() for line in output.splitlines() if line.strip()}

    def read(self, relative: str) -> str | None:
        path = self.root / relative
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return None

    def file_at(self, commit: str, relative: str) -> str | None:
        """A file's content at another commit, for two-checkout gates like regression-proven."""
        result = _git(["show", f"{commit}:{relative}"], cwd=self.root, check=False)
        return result.stdout if result.returncode == 0 else None


class WorkspaceFactory:
    """Creates isolated workspaces from a source repository."""

    def __init__(self, source: Path, state_dir: Path) -> None:
        self.source = Path(source).resolve()
        self.state_dir = Path(state_dir).resolve()

    def create(self, *, run_id: str | None = None, ref: str = "HEAD") -> Workspace:
        """Clone the source into a fresh directory at ``ref``.

        A local clone rather than a worktree: worktrees share the object store and a
        `git clean` in one can surprise another, and isolation is the point.
        """
        if not (self.source / ".git").exists():
            raise WorkspaceError(
                f"{self.source} is not a git repository",
                remediation="Point at a repository, or run `git init` there first.",
            )

        run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        root = self.state_dir / "workspaces" / run_id
        if root.exists():
            shutil.rmtree(root)
        root.parent.mkdir(parents=True, exist_ok=True)

        _git(
            ["clone", "--no-hardlinks", "--quiet", str(self.source), str(root)],
            cwd=self.state_dir.parent,
        )
        _git(["checkout", "--quiet", "--detach", ref], cwd=root)
        base = _git(["rev-parse", "HEAD"], cwd=root).stdout.strip()
        return Workspace(root=root, run_id=run_id, base_commit=base)

    def destroy(self, workspace: Workspace) -> None:
        """Reclaim a workspace. Called by garbage collection as well as at run end."""
        if workspace.root.exists():
            shutil.rmtree(workspace.root, ignore_errors=True)

    def reclaim(self, *, keep: set[str] | None = None) -> list[str]:
        """Remove workspaces for runs that are no longer live (PRD FR-28.6).

        Without this, a factory fills the disk within days of normal operation, and a
        full disk during a chained ledger append is this design's worst corruption mode.
        """
        keep = keep or set()
        base = self.state_dir / "workspaces"
        if not base.is_dir():
            return []
        removed = []
        for path in base.iterdir():
            if path.is_dir() and path.name not in keep:
                shutil.rmtree(path, ignore_errors=True)
                removed.append(path.name)
        return sorted(removed)
