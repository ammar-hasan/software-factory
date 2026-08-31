"""Workspace-backed tool implementations (PRD FR-10.3-10.6).

Every tool here is deterministic and returns structured data. That is the concrete form
of "compute the computable" (PR-6): an agent should never be asked to guess where a
symbol is defined, nor to parse human-oriented test output, when a tool can answer
exactly.

Registered against a workspace and an executor, so the same declarations work under any
executor without the tools knowing which one they are on.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from software_factory.definition.models import Effect
from software_factory.evals.results import (
    Outcome,
    TestResult,
    TestRun,
    classify_failure,
)
from software_factory.harness.tools import (
    CostClass,
    Example,
    FailureKind,
    Tool,
    ToolFailure,
    ToolRegistry,
    ToolResult,
    ToolSuccess,
)
from software_factory.runtime.executor import LocalExecutor
from software_factory.runtime.workspace import Workspace

MAX_SEARCH_HITS = 50
MAX_READ_BYTES = 200_000

#: Files a search should never surface: they are large, generated, or not source.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".factory"}


def _relative(workspace: Workspace, path: Path) -> str:
    return str(path.relative_to(workspace.root))


def _walk(workspace: Workspace) -> list[Path]:
    return sorted(
        path
        for path in workspace.root.rglob("*")
        if path.is_file()
        and not any(part in SKIP_DIRS for part in path.relative_to(workspace.root).parts)
    )


def _safe_path(workspace: Workspace, relative: str) -> Path | None:
    """Resolve a path inside the workspace, or ``None`` if it escapes.

    Path traversal is refused here rather than relied on the sandbox to catch: a tool
    that can read `../../etc/passwd` has widened an agent's reach without any grant
    change, which is exactly what the grant model is supposed to make impossible.
    """
    candidate = (workspace.root / relative).resolve()
    try:
        candidate.relative_to(workspace.root.resolve())
    except ValueError:
        return None
    return candidate


def build_registry(
    workspace: Workspace, executor: LocalExecutor, *, test_command: list[str] | None = None
) -> ToolRegistry:
    """Register the baseline toolbelt against one workspace."""
    registry = ToolRegistry()

    # ------------------------------------------------------------------ read tools

    def repo_read(args: dict[str, Any]) -> ToolResult:
        path = _safe_path(workspace, str(args["path"]))
        if path is None:
            return ToolFailure(
                FailureKind.DENIED,
                "path escapes the workspace",
                "Read a path inside the repository.",
            )
        if not path.is_file():
            return ToolFailure(
                FailureKind.NOT_FOUND,
                f"{args['path']} is not a file",
                "Use repo.search to find the file you meant.",
            )
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            return ToolFailure(
                FailureKind.INVALID_INPUT,
                f"{args['path']} is not readable as UTF-8 text ({exc})",
                "This looks like a binary file; read something else.",
            )

        start = int(args.get("start_line", 1))
        end = int(args.get("end_line", 0)) or None
        lines = text.splitlines()
        selected = lines[max(0, start - 1) : end]
        body = "\n".join(selected)
        truncated = len(body) > MAX_READ_BYTES
        return ToolSuccess(
            value={
                "path": str(args["path"]),
                "start_line": start,
                "end_line": end or len(lines),
                "total_lines": len(lines),
                "content": body[:MAX_READ_BYTES],
            },
            truncated=truncated,
        )

    registry.register(
        Tool(
            name="repo.read",
            description="Read a file, optionally a line range. Returns content with line bounds.",
            effect=Effect.READ,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
            output_schema={"type": "object"},
            handler=repo_read,
            cost_class=CostClass.FREE,
            examples=(
                Example(
                    inputs={"path": "importer.py", "start_line": 1, "end_line": 20},
                    output='{"path": "importer.py", "total_lines": 42, "content": "..."}',
                ),
            ),
        )
    )

    def repo_search(args: dict[str, Any]) -> ToolResult:
        try:
            pattern = re.compile(str(args["pattern"]))
        except re.error as exc:
            return ToolFailure(
                FailureKind.INVALID_INPUT,
                f"not a valid regular expression: {exc}",
                "Escape any regex metacharacters you meant literally.",
            )
        glob = str(args.get("glob", ""))
        hits: list[dict[str, Any]] = []
        for path in _walk(workspace):
            relative = _relative(workspace, path)
            if glob and not Path(relative).match(glob):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    hits.append({"path": relative, "line": number, "text": line.strip()[:200]})
                    if len(hits) >= MAX_SEARCH_HITS:
                        return ToolSuccess(value={"hits": hits}, truncated=True)
        return ToolSuccess(value={"hits": hits})

    registry.register(
        Tool(
            name="repo.search",
            description="Search the repository by regular expression. Returns located hits.",
            effect=Effect.READ,
            input_schema={
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "glob": {"type": "string"}},
                "required": ["pattern"],
            },
            output_schema={"type": "object"},
            handler=repo_search,
            cost_class=CostClass.CHEAP,
            examples=(
                Example(
                    inputs={"pattern": "def strip_bom", "glob": "*.py"},
                    output='{"hits": [{"path": "importer.py", "line": 1, "text": "def strip_bom(text):"}]}',
                ),
            ),
        )
    )

    def repo_tree(args: dict[str, Any]) -> ToolResult:
        paths = [_relative(workspace, path) for path in _walk(workspace)]
        prefix = str(args.get("prefix", ""))
        if prefix:
            paths = [p for p in paths if p.startswith(prefix)]
        return ToolSuccess(value={"files": paths[:500], "total": len(paths)})

    registry.register(
        Tool(
            name="repo.tree",
            description="List the repository's files, optionally under a prefix.",
            effect=Effect.READ,
            input_schema={
                "type": "object",
                "properties": {"prefix": {"type": "string"}},
                "required": [],
            },
            output_schema={"type": "object"},
            handler=repo_tree,
            cost_class=CostClass.FREE,
            examples=(
                Example(
                    inputs={"prefix": "src/"}, output='{"files": ["src/importer.py"], "total": 1}'
                ),
            ),
        )
    )

    # ----------------------------------------------------------------- write tools

    def file_write(args: dict[str, Any]) -> ToolResult:
        path = _safe_path(workspace, str(args["path"]))
        if path is None:
            return ToolFailure(
                FailureKind.DENIED,
                "path escapes the workspace",
                "Write inside the repository.",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(args["content"]), encoding="utf-8")
        return ToolSuccess(value={"path": str(args["path"]), "bytes": len(str(args["content"]))})

    registry.register(
        Tool(
            name="file.write",
            description="Write a file in the workspace, creating parent directories.",
            effect=Effect.WRITE,
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            output_schema={"type": "object"},
            handler=file_write,
            idempotent=False,
            examples=(
                Example(
                    inputs={"path": "importer.py", "content": "def strip_bom(t): ..."},
                    output='{"path": "importer.py", "bytes": 24}',
                ),
            ),
        )
    )

    # ------------------------------------------------------------------ exec tools

    def test_run(args: dict[str, Any]) -> ToolResult:
        command = test_command or ["python", "-m", "pytest", "-q"]
        selector = args.get("selector")
        if selector:
            command = [*command, str(selector)]
        result = executor.run(command)
        run = parse_pytest(result.stdout + "\n" + result.stderr, command, workspace.head)
        run.exit_code = result.exit_code
        run.truncated = result.truncated
        return ToolSuccess(value=run.as_dict(), truncated=result.truncated)

    registry.register(
        Tool(
            name="test.run",
            description=(
                "Run the repository's tests, optionally filtered. Returns per-test outcomes "
                "and failure classes, not a summary."
            ),
            effect=Effect.EXEC,
            input_schema={
                "type": "object",
                "properties": {"selector": {"type": "string"}},
                "required": [],
            },
            output_schema={"type": "object"},
            handler=test_run,
            cost_class=CostClass.MODERATE,
            idempotent=False,
            timeout_ms=600_000,
            examples=(
                Example(
                    inputs={"selector": "-k bom"},
                    output='{"passed": false, "failed": 1, "results": [{"id": "...", "failureClass": "assertion"}]}',
                ),
            ),
        )
    )

    def proc_run(args: dict[str, Any]) -> ToolResult:
        command = args.get("command")
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            return ToolFailure(
                FailureKind.INVALID_INPUT,
                "`command` must be a list of strings",
                'Pass it as ["python", "-m", "pytest"], not as one shell string.',
            )
        result = executor.run(command)
        return ToolSuccess(
            value={
                "exitCode": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "durationSeconds": round(result.duration_s, 3),
                "timedOut": result.timed_out,
            },
            truncated=result.truncated,
        )

    registry.register(
        Tool(
            name="proc.run",
            description="Run a command in the workspace under the run's policy.",
            effect=Effect.EXEC,
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "array", "items": {"type": "string"}}},
                "required": ["command"],
            },
            output_schema={"type": "object"},
            handler=proc_run,
            cost_class=CostClass.MODERATE,
            idempotent=False,
            timeout_ms=600_000,
            examples=(
                Example(
                    inputs={"command": ["python", "-c", "print(1)"]},
                    output='{"exitCode": 0, "stdout": "1\\n"}',
                ),
            ),
        )
    )

    # ------------------------------------------------------------- checkpoint tools

    def checkpoint_create(args: dict[str, Any]) -> ToolResult:
        point = workspace.checkpoint(str(args.get("label", "manual")))
        return ToolSuccess(value={"id": point.id, "label": point.label})

    registry.register(
        Tool(
            name="checkpoint.create",
            description=(
                "Record the workspace exactly as it is, so you can return to it. Free, and "
                "using it is a normal move."
            ),
            effect=Effect.WRITE,
            input_schema={
                "type": "object",
                "properties": {"label": {"type": "string"}},
                "required": [],
            },
            output_schema={"type": "object"},
            handler=checkpoint_create,
            idempotent=False,
            examples=(
                Example(inputs={"label": "before trying the rewrite"}, output='{"id": "a1b2c3"}'),
            ),
        )
    )

    def checkpoint_restore(args: dict[str, Any]) -> ToolResult:
        target = str(args["id"])
        for point in workspace.checkpoints:
            if point.id == target:
                workspace.restore(point)
                return ToolSuccess(value={"restored": point.id, "label": point.label})
        return ToolFailure(
            FailureKind.NOT_FOUND,
            f"no checkpoint {target!r} in this run",
            "Use the id returned by checkpoint.create.",
        )

    registry.register(
        Tool(
            name="checkpoint.restore",
            description=(
                "Return the workspace exactly to a checkpoint. Costs nothing and counts "
                "against no quality measure."
            ),
            effect=Effect.WRITE,
            input_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            output_schema={"type": "object"},
            handler=checkpoint_restore,
            idempotent=False,
            examples=(Example(inputs={"id": "a1b2c3"}, output='{"restored": "a1b2c3"}'),),
        )
    )

    return registry


_PYTEST_RESULT = re.compile(r"^(?P<id>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|ERROR|SKIPPED)")
_PYTEST_FAILURE = re.compile(r"^(?:FAILED|ERROR)\s+(?P<id>\S+)(?:\s+-\s+(?P<message>.*))?$")


def parse_pytest(output: str, command: list[str], commit: str) -> TestRun:
    """Parse pytest output into structured per-test results.

    Adapters parse; agents do not. An agent asked to read a test summary will read it
    optimistically, and `regression-proven` needs the failure *class* rather than a
    human's impression of it.
    """
    results: dict[str, TestResult] = {}

    for line in output.splitlines():
        verbose = _PYTEST_RESULT.match(line.strip())
        if verbose:
            test_id = verbose.group("id")
            results[test_id] = TestResult(
                test_id=test_id, outcome=Outcome(verbose.group("outcome").lower())
            )
            continue

        failure = _PYTEST_FAILURE.match(line.strip())
        if failure:
            test_id = failure.group("id")
            message = failure.group("message") or ""
            results[test_id] = TestResult(
                test_id=test_id,
                outcome=Outcome.FAILED if line.startswith("FAILED") else Outcome.ERROR,
                message=message,
                failure_class=classify_failure(message or output),
            )

    return TestRun(
        command=" ".join(command),
        commit=commit,
        exit_code=0 if not any(r.outcome is Outcome.FAILED for r in results.values()) else 1,
        results=list(results.values()),
    )
