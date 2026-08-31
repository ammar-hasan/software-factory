"""Execution: isolated workspaces, checkpoints, and the executors that enforce policy.

See ``docs/PRD.md`` §7.8 and §7.12. The executor is the component that *enforces* the
blast-radius contract; the prompt only states it.
"""

from software_factory.runtime.executor import (
    CommandResult,
    ExecutorError,
    LocalExecutor,
    SandboxLevel,
    SandboxPolicy,
    detect_sandbox_level,
    redact,
)
from software_factory.runtime.workspace import (
    Checkpoint,
    Workspace,
    WorkspaceError,
    WorkspaceFactory,
)

__all__ = [
    "Checkpoint",
    "CommandResult",
    "ExecutorError",
    "LocalExecutor",
    "SandboxLevel",
    "SandboxPolicy",
    "Workspace",
    "WorkspaceError",
    "WorkspaceFactory",
    "detect_sandbox_level",
    "redact",
]
