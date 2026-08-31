"""Orchestration: work items, the stage machine, and routing policy.

See ``docs/PRD.md`` §7.4 for requirements.
"""

from software_factory.orchestrator.workitem import (
    DEFAULT_NON_SKIPPABLE,
    DEFAULT_TRANSITIONS,
    TERMINAL,
    Blocker,
    SourceContext,
    StageMachine,
    Transition,
    TransitionRefused,
    WorkClass,
    WorkItem,
    classify_request,
    new_id,
    validate_graph,
)

__all__ = [
    "DEFAULT_NON_SKIPPABLE",
    "DEFAULT_TRANSITIONS",
    "TERMINAL",
    "Blocker",
    "SourceContext",
    "StageMachine",
    "Transition",
    "TransitionRefused",
    "WorkClass",
    "WorkItem",
    "classify_request",
    "new_id",
    "validate_graph",
]
