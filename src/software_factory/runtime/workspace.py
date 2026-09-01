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
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from software_factory.errors import FactoryError

#: Git's empty-tree object. Diffing against it turns "what is in this commit" into a
#: numstat, whose ``-\t-\t`` prefix is git's own answer for "this file is binary".
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


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
        # `git show <commit>:<path>` emits the file's raw bytes, so strict decoding raised
        # UnicodeDecodeError -- not WorkspaceError -- straight past the `check=False` a
        # caller passed specifically to handle failure gracefully. One binary asset in a
        # repository was enough to crash `regression-proven`, the keystone gate, whose
        # two-checkout comparison is built on `file_at`.
        errors="replace",
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
        """A file's content at another commit, for two-checkout gates like regression-proven.

        ``None`` for a path that does not exist there *and* for one git reports as binary.
        A mangled string full of replacement characters would compare unequal to itself
        across two checkouts and read as a change that never happened.
        """
        probe = _git(
            ["diff", "--numstat", EMPTY_TREE, commit, "--", relative], cwd=self.root, check=False
        )
        if probe.returncode == 0 and probe.stdout.startswith("-\t-\t"):
            return None
        result = _git(["show", f"{commit}:{relative}"], cwd=self.root, check=False)
        return result.stdout if result.returncode == 0 else None


#: Where a run's tooling writes its own state. Inside the workspace because the sandbox
#: confines writes there, and excluded from git because none of it is the change.
HOME_DIR = ".sf-home"

#: Build artifacts no run authors and every run produces. Excluded so a repository that
#: does not happen to ignore them does not report a cache file as part of the change.
#: Deliberately short: anything ambiguous belongs in the repository's own `.gitignore`,
#: where a person decided it, rather than here where the factory decided it for them.
IGNORED_ARTIFACTS = ("__pycache__/", "*.py[cod]", ".pytest_cache/", ".ruff_cache/", ".mypy_cache/")


class WorkspaceFactory:
    """Creates isolated workspaces from a source repository."""

    def __init__(self, source: Path, state_dir: Path) -> None:
        self.source = Path(source).resolve()
        self.state_dir = Path(state_dir).resolve()

    def create(
        self, *, run_id: str | None = None, ref: str = "HEAD", replace: bool = False
    ) -> Workspace:
        """Clone the source into a fresh directory at ``ref``.

        A local clone rather than a worktree: worktrees share the object store and a
        `git clean` in one can surprise another, and isolation is the point.

        ``run_id`` is caller-supplied, so reusing one used to delete the previous
        workspace -- its uncommitted work and its checkpoint refs, which are the undo the
        courage clause promises -- without a word. Destroying a workspace is now something
        a caller asks for with ``replace=True``, never something it stumbles into.
        """
        if not (self.source / ".git").exists():
            raise WorkspaceError(
                f"{self.source} is not a git repository",
                remediation="Point at a repository, or run `git init` there first.",
            )

        run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        root = self.state_dir / "workspaces" / run_id
        if root.exists():
            if not replace:
                raise WorkspaceError(
                    f"a workspace for run {run_id!r} already exists at {root}",
                    remediation=(
                        "Use a different run id, reclaim the old workspace first, or pass "
                        "replace=True to discard it deliberately."
                    ),
                )
            shutil.rmtree(root)
        root.parent.mkdir(parents=True, exist_ok=True)

        _git(
            ["clone", "--no-hardlinks", "--quiet", str(self.source), str(root)],
            cwd=self.state_dir.parent,
        )
        _git(["checkout", "--quiet", "--detach", ref], cwd=root)
        base = _git(["rev-parse", "HEAD"], cwd=root).stdout.strip()

        # A place for the run's tooling to put its own droppings. The sandbox confines
        # writes to the workspace, so `HOME` has to live inside it -- and with `HOME` set to
        # the repository root, one `pip install` put forty cache files into `changed_paths`.
        # That surface is not cosmetic: it is what the blast-radius contract is checked
        # against, what the review pack describes, and what a change would commit. A real
        # trial reached handoff having "changed" `.rustup/settings.toml`.
        #
        # Excluded through `.git/info/exclude` rather than a `.gitignore`, because a
        # `.gitignore` is a file in the user's repository and writing one would make the
        # factory's own scaffolding show up in the diff it is trying to keep clean.
        (root / HOME_DIR).mkdir(exist_ok=True)
        exclude = root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write("\n# written by the factory: not part of the repository\n")
            handle.write(f"/{HOME_DIR}/\n")
            for pattern in IGNORED_ARTIFACTS:
                handle.write(f"{pattern}\n")

        return Workspace(root=root, run_id=run_id, base_commit=base)

    def destroy(self, workspace: Workspace) -> None:
        """Reclaim a workspace. Called by garbage collection as well as at run end."""
        if workspace.root.exists():
            shutil.rmtree(workspace.root, ignore_errors=True)

    def reclaim(
        self,
        *,
        live: set[str],
        older_than: timedelta = timedelta(hours=6),
        now: float | None = None,
    ) -> list[str]:
        """Remove workspaces for runs that are no longer live (PRD FR-28.6).

        Without this, a factory fills the disk within days of normal operation, and a full
        disk during a chained ledger append is this design's worst corruption mode.

        Two conditions, both required, and neither with a default that means "everything".
        ``live`` was previously an optional ``keep`` set, so ``reclaim()`` written without
        arguments -- or one whose set came back empty because the orchestrator happened to
        be restarting -- deleted every in-flight run's uncommitted work and its checkpoint
        refs with it, reporting nothing, because ``ignore_errors=True`` swallowed the lot.
        ``live`` is now required, and age is the second condition: a workspace younger than
        ``older_than`` is left alone even when it is absent from ``live``, because a run
        that started moments ago is the one most likely to be missing from a stale list.

        Removal failures are returned as part of the answer rather than silenced: a
        reclaim that could not reclaim is something an operator watching disk needs told.
        """
        base = self.state_dir / "workspaces"
        if not base.is_dir():
            return []
        cutoff = (now if now is not None else time.time()) - older_than.total_seconds()
        removed = []
        for path in sorted(base.iterdir()):
            if not path.is_dir() or path.name in live:
                continue
            if path.stat().st_mtime > cutoff:
                continue
            try:
                shutil.rmtree(path)
            except OSError as exc:
                raise WorkspaceError(
                    f"could not reclaim workspace {path.name}: {exc}",
                    remediation=("Check for a process still holding files open in it, then retry."),
                ) from exc
            removed.append(path.name)
        return sorted(removed)
