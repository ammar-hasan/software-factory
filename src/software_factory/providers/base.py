"""The model provider interface (PRD FR-11.1, FR-11.2, NFR-5.2).

Every model interaction goes through this, which is what makes the entire factory
testable without a model. A provider is deliberately small: given messages, tools, and a
budget, return a completion. Everything else -- retries, escalation, budgets, tool
dispatch -- belongs to the harness, so a new provider is an afternoon rather than a
project.

Inference credentials live only at this boundary and are never injected into an execution
workspace (PRD FR-17.1).
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class Role(enum.StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class StopReason(enum.StrEnum):
    """Why generation stopped. Every value is actionable by the turn loop."""

    COMPLETE = "complete"
    TOOL_CALL = "tool_call"
    LENGTH = "length"
    FILTERED = "filtered"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str
    tool_call_id: str | None = None
    name: str | None = None

    def tokens(self) -> int:
        from software_factory.harness.awareness import estimate_tokens

        return estimate_tokens(self.content)


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Usage:
    """What a completion cost. Recorded per call, rolled up per run (PRD FR-11.12)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    """Reported separately, because folding cached tokens into the input count makes cost
    trends incomparable across a caching change (completeness review)."""

    latency_s: float = 0.0

    def cost(self, *, per_mtok_in: float, per_mtok_out: float) -> float:
        billable_in = max(0, self.input_tokens - self.cached_input_tokens)
        return (billable_in / 1e6) * per_mtok_in + (self.output_tokens / 1e6) * per_mtok_out

    def merged(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            latency_s=self.latency_s + other.latency_s,
        )


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    stop_reason: StopReason
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    error: str | None = None

    @property
    def wants_tools(self) -> bool:
        return self.stop_reason is StopReason.TOOL_CALL and bool(self.tool_calls)


class ProviderError(Exception):
    """A provider could not serve a request. Typed so the loop can decide what to do."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class Provider(ABC):
    """A source of completions."""

    name: str = "provider"

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        """Return one completion, or raise :class:`ProviderError`."""

    def available(self) -> tuple[bool, str]:
        """Whether this provider can serve requests right now, and why not if it cannot.

        Reported rather than discovered mid-run: a factory should be able to tell an
        operator that its configured endpoint is unreachable before it burns a budget
        finding out.
        """
        return True, ""
