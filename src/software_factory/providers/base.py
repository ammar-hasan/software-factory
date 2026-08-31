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
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    """The calls an assistant turn requested, kept on the message that requested them.

    Not decoration. Both real wire formats reject a tool *result* whose id does not
    appear in the preceding assistant turn, so a transcript that records only the
    assistant's text cannot be sent back to any provider after the first tool call --
    it fails on turn two, in production, with a 400 that reads like a schema problem.

    It is also what makes replay (FR-11.11) meaningful: replaying a tool sequence
    requires knowing which call produced which result, and pairing by position guesses.
    """

    def tokens(self) -> int:
        from software_factory.harness.awareness import estimate_tokens

        return estimate_tokens(self.content)


@dataclass(frozen=True, slots=True)
class Usage:
    """What a completion cost. Recorded per call, rolled up per run (PRD FR-11.12)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    """How many of ``input_tokens`` were served from cache.

    **Inclusive**: ``input_tokens`` counts every input token and this names the cached
    subset of them, so ``cost`` bills ``input_tokens - cached_input_tokens``. The
    docstring used to say only "reported separately", which an adapter author could
    reasonably read as disjoint -- and under that reading every cost figure is
    under-reported by exactly the cache hit rate, silently and in the flattering
    direction. Each adapter asserts the invariant; see ``Usage.check``.
    """

    latency_s: float = 0.0

    def check(self) -> None:
        """Assert the inclusive-cache convention. Called by every adapter."""
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError(
                f"cached_input_tokens ({self.cached_input_tokens}) exceeds input_tokens "
                f"({self.input_tokens}); cached tokens are a subset of the input count, "
                "not a separate total"
            )

    def cost(self, *, per_mtok_in: float, per_mtok_out: float) -> float:
        billable_in = max(0, self.input_tokens - self.cached_input_tokens)
        return (billable_in / 1e6) * per_mtok_in + (self.output_tokens / 1e6) * per_mtok_out

    @classmethod
    def observed(cls, **fields: object) -> Usage:
        """Build a `Usage` from an adapter's report, checking the cache convention.

        Every adapter goes through here rather than calling the constructor, so a provider
        that reports cached tokens disjointly is caught at the boundary instead of
        under-reporting cost by the cache hit rate for the life of the deployment.
        """
        usage = cls(**fields)  # type: ignore[arg-type]
        usage.check()
        return usage

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
