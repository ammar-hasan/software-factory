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
    Handler,
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

#: The effect class each built-in tool needs, declared once. `build_registry` reads it, so a
#: static audit and the running registry cannot disagree -- a least-privilege report answered
#: from a separately maintained list is exactly the kind of claim this codebase exists to
#: avoid making.
BUILTIN_TOOL_EFFECTS: dict[str, Effect] = {
    "repo.read": Effect.READ,
    "repo.search": Effect.READ,
    "repo.tree": Effect.READ,
    "file.write": Effect.WRITE,
    "test.run": Effect.EXEC,
    "proc.run": Effect.EXEC,
    "checkpoint.create": Effect.WRITE,
    "checkpoint.restore": Effect.WRITE,
    # Computer use. Declared here like everything else so the grant model covers it and
    # `sf audit` cannot disagree with the running registry. They are `UI` rather than
    # `EXEC` because a run that may run tests must not thereby be able to click "delete".
    "ui.navigate": Effect.UI,
    "ui.click": Effect.UI,
    "ui.type": Effect.UI,
    "ui.observe": Effect.UI,
    "ui.close": Effect.UI,
    # Messages between agents. `agent.send` is EXTERNAL because it puts text in front of
    # somebody who did not ask for it: an agent granted only READ and WRITE must not be
    # able to interrupt the fleet, and a message is the one side effect that escapes the
    # workspace without touching the filesystem.
    "agent.send": Effect.EXTERNAL,
    "agent.inbox": Effect.READ,
}


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
    workspace: Workspace,
    executor: LocalExecutor,
    *,
    test_command: list[str] | None = None,
    ui_session: Any = None,
    mailbox: Any = None,
    agent: str = "",
) -> ToolRegistry:
    """Register the baseline toolbelt against one workspace.

    `mailbox` and `agent` travel together: a mailbox with no agent to bind cannot register
    a sender, and registering a sender the caller did not name would let a model choose its
    own. Both absent means the run has no way to message anybody, which is correct for a
    run nobody is coordinating with.

    `ui_session` is absent for almost every run. Computer use is a granted capability
    rather than a default one: a run that does not need a browser must not be offered a
    tool that can click "delete", and offering it and relying on the grant check to refuse
    would put the capability in the pack for a model to reason about.
    """
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
            effect=BUILTIN_TOOL_EFFECTS["repo.read"],
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
            effect=BUILTIN_TOOL_EFFECTS["repo.search"],
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
            effect=BUILTIN_TOOL_EFFECTS["repo.tree"],
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
            effect=BUILTIN_TOOL_EFFECTS["file.write"],
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
            effect=BUILTIN_TOOL_EFFECTS["test.run"],
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
            effect=BUILTIN_TOOL_EFFECTS["proc.run"],
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
            effect=BUILTIN_TOOL_EFFECTS["checkpoint.create"],
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
            effect=BUILTIN_TOOL_EFFECTS["checkpoint.restore"],
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

    # ------------------------------------------------------------------- computer use

    if ui_session is not None:
        _register_ui(registry, ui_session)

    # ---------------------------------------------------------------- agent messaging

    if mailbox is not None and agent:
        _register_mailbox(registry, mailbox, agent=agent)

    return registry


_PYTEST_RESULT = re.compile(r"^(?P<id>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|ERROR|SKIPPED)")
_PYTEST_FAILURE = re.compile(r"^(?:FAILED|ERROR)\s+(?P<id>\S+)(?:\s+-\s+(?P<message>.*))?$")


#: The header pytest writes above each failing test's traceback, e.g.
#: ``_______ test_bom_prefixed_headers _______``.
_PYTEST_BLOCK = re.compile(r"^_{2,}\s+(?P<name>[^\s_][^\n]*?)\s+_{2,}$")


def _failure_blocks(output: str) -> dict[str, str]:
    """Each failing test's full traceback, keyed by the name in its header.

    The reason this exists is a real defect the short summary caused. pytest truncates the
    one-line summary to the terminal width, so the same `AssertionError` arrives as
    ``AssertionError: assert [...`` for a short test name and ``Assertion...`` for a long
    one. Classifying from that line made the *length of a test's name* decide whether
    `regression-proven` accepted it: a model writing descriptive names was rejected, and one
    writing `test_a` was not.

    The FAILURES section is not width-truncated and carries the exception verbatim, so it
    is what the classification reads.
    """
    blocks: dict[str, str] = {}
    name: str | None = None
    lines: list[str] = []
    for line in output.splitlines():
        header = _PYTEST_BLOCK.match(line.strip())
        if header:
            if name is not None:
                blocks[name] = "\n".join(lines)
            name = header.group("name").strip()
            lines = []
            continue
        if name is not None:
            # The short-summary banner ends the last block; anything after it is a
            # different section and must not be attributed to the test above it.
            if line.startswith("=") and "short test summary" in line:
                blocks[name] = "\n".join(lines)
                name = None
                continue
            lines.append(line)
    if name is not None:
        blocks[name] = "\n".join(lines)
    return blocks


def parse_pytest(output: str, command: list[str], commit: str) -> TestRun:
    """Parse pytest output into structured per-test results.

    Adapters parse; agents do not. An agent asked to read a test summary will read it
    optimistically, and `regression-proven` needs the failure *class* rather than a
    human's impression of it.
    """
    results: dict[str, TestResult] = {}
    blocks = _failure_blocks(output)

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
            # Classify from the untruncated traceback where there is one. The summary
            # line stays as the human-readable message; it is just not evidence.
            detail = blocks.get(test_id.rsplit("::", 1)[-1], "")
            results[test_id] = TestResult(
                test_id=test_id,
                outcome=Outcome.FAILED if line.startswith("FAILED") else Outcome.ERROR,
                message=message,
                failure_class=classify_failure(detail or message or output),
            )

    return TestRun(
        command=" ".join(command),
        commit=commit,
        exit_code=0 if not any(r.outcome is Outcome.FAILED for r in results.values()) else 1,
        results=list(results.values()),
    )


def _register_mailbox(registry: ToolRegistry, mailbox: Any, *, agent: str) -> None:
    """Expose one mailbox as tools, bound to the agent that holds them.

    The sender is bound at registration, not passed as an argument. An agent that can name
    its own sender can answer its own questions, and a fleet view built on unanswered
    questions then reports a healthy factory while nothing is progressing. A message whose
    author is whatever the model typed is not evidence of anything.
    """
    from software_factory.orchestrator.mailbox import MessageError

    def send(args: dict[str, Any]) -> ToolResult:
        try:
            message = mailbox.send(
                sender=agent,
                recipient=str(args["to"]),
                kind=str(args.get("kind", "status")),
                body=str(args["body"]),
                run=str(args.get("run", "")),
                in_reply_to=int(args.get("in_reply_to", 0) or 0),
            )
        except MessageError as exc:
            return ToolFailure(FailureKind.DENIED, str(exc), exc.remediation)
        return ToolSuccess(value={"seq": message.seq, "to": message.recipient})

    def inbox(args: dict[str, Any]) -> ToolResult:
        messages, left = mailbox.inbox(agent, after=int(args.get("after", 0) or 0))
        return ToolSuccess(
            value={"messages": [m.as_dict() for m in messages], "olderNotShown": left}
        )

    registry.register(
        Tool(
            name="agent.send",
            description=(
                "Send a short coordination message to another agent: a handoff, a "
                "question, a blocker or a result. Not for transporting work -- the "
                "workspace and the ledger carry that. An answer must name the sequence "
                "number of the question it answers, or the asker is still waiting."
            ),
            effect=BUILTIN_TOOL_EFFECTS["agent.send"],
            input_schema={
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["status", "question", "answer", "result", "blocked", "handoff"],
                    },
                    "body": {"type": "string"},
                    "run": {"type": "string"},
                    "in_reply_to": {"type": "integer"},
                },
                "required": ["to", "body"],
            },
            output_schema={"type": "object"},
            handler=send,
            idempotent=False,
            examples=(
                Example(
                    inputs={
                        "to": "reviewer",
                        "kind": "handoff",
                        "body": "Fix is on the branch; the regression test is test_strips_bom.",
                    },
                    output='{"seq": 41, "to": "reviewer"}',
                ),
            ),
        )
    )
    registry.register(
        Tool(
            name="agent.inbox",
            description=(
                "Read messages addressed to you. Your unread messages are already in "
                "your task; use this to look further back."
            ),
            effect=BUILTIN_TOOL_EFFECTS["agent.inbox"],
            input_schema={
                "type": "object",
                "properties": {"after": {"type": "integer"}},
            },
            output_schema={"type": "object"},
            handler=inbox,
            examples=(Example(inputs={"after": 0}, output='{"messages": [], "olderNotShown": 0}'),),
        )
    )


def _register_ui(registry: ToolRegistry, session: Any) -> None:
    """Expose one UI session as tools.

    Every refusal lives in the session's contract rather than here, so a second driver or a
    second caller cannot arrive without them. These handlers translate, and translate only.
    """
    from software_factory.runtime.ui import UiError, UiUnavailableError

    def guarded(action: Any) -> Handler:
        def handler(args: dict[str, Any]) -> ToolResult:
            try:
                return ToolSuccess(value=action(args))
            except UiError as exc:
                # DENIED, not an error: the contract said no, and the agent should read that
                # as a boundary rather than as something to retry differently.
                return ToolFailure(FailureKind.DENIED, str(exc), remediation=exc.remediation)
            except UiUnavailableError as exc:
                return ToolFailure(FailureKind.UNAVAILABLE, str(exc), remediation=exc.remediation)

        return handler

    registry.register(
        Tool(
            name="ui.navigate",
            description="Open a URL in the session's browser. Only declared origins.",
            effect=Effect.UI,
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            output_schema={"type": "object"},
            handler=guarded(lambda a: session.navigate(str(a["url"]))),
            examples=(Example(inputs={"url": "https://docs.example.test/csv"}, output="{}"),),
            idempotent=False,
        )
    )
    registry.register(
        Tool(
            name="ui.click",
            description="Click an element by CSS selector.",
            effect=Effect.UI,
            input_schema={
                "type": "object",
                "properties": {"selector": {"type": "string"}},
                "required": ["selector"],
            },
            output_schema={"type": "object"},
            handler=guarded(lambda a: session.click(str(a["selector"]))),
            examples=(Example(inputs={"selector": "#submit"}, output="{}"),),
            idempotent=False,
        )
    )
    registry.register(
        Tool(
            name="ui.type",
            description=(
                "Type text into an element. Text matching a secret this run holds is "
                "refused, whatever the field is called."
            ),
            effect=Effect.UI,
            input_schema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                    # Declared rather than inferred from the field name. A model claiming
                    # it is signing in is checked against the grant; one that does not
                    # claim it cannot sign in by choosing a field called `#password`.
                    "authenticating": {"type": "boolean"},
                },
                "required": ["selector", "text"],
            },
            output_schema={"type": "object"},
            handler=guarded(
                lambda a: session.type(
                    str(a["selector"]),
                    str(a["text"]),
                    authenticating=bool(a.get("authenticating", False)),
                )
            ),
            examples=(Example(inputs={"selector": "#q", "text": "byte order mark"}, output="{}"),),
            idempotent=False,
        )
    )
    registry.register(
        Tool(
            name="ui.observe",
            description="Read the current page. Counted against the session's action bound.",
            effect=Effect.UI,
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
            handler=guarded(lambda _a: session.observe()),
            examples=(Example(inputs={}, output="{}"),),
        )
    )
    registry.register(
        Tool(
            name="ui.close",
            description="Close the session and write its recording.",
            effect=Effect.UI,
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
            handler=guarded(lambda _a: session.close()),
            examples=(Example(inputs={}, output="{}"),),
            idempotent=False,
        )
    )
