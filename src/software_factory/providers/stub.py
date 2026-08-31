"""A scripted provider for tests and replay (PRD NFR-5.2, FR-11.11).

The entire factory has to be testable without a model, and recorded runs have to replay
deterministically with model calls stubbed. Both needs are the same object: a provider
that returns a fixed script.

It is strict on purpose. Running past the end of its script raises rather than returning
something plausible, because a test that silently gets an empty completion passes for the
wrong reason.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from software_factory.providers.base import (
    Completion,
    Message,
    Provider,
    ProviderError,
    StopReason,
    ToolCall,
    Usage,
)


class StubProvider(Provider):
    """Returns scripted completions in order, recording what it was asked."""

    name = "stub"

    def __init__(self, script: Iterable[Completion] | None = None) -> None:
        self._script = list(script or [])
        self._index = 0
        self.calls: list[list[Message]] = []
        self.models: list[str] = []

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,  # noqa: ARG002 - interface conformance
        max_tokens: int = 4096,  # noqa: ARG002 - interface conformance
        temperature: float = 0.0,  # noqa: ARG002 - interface conformance
    ) -> Completion:
        self.calls.append(list(messages))
        self.models.append(model)
        if self._index >= len(self._script):
            raise ProviderError(
                f"stub script exhausted after {self._index} completion(s); the loop asked "
                "for one more than the test scripted"
            )
        completion = self._script[self._index]
        self._index += 1
        return completion

    @property
    def remaining(self) -> int:
        return len(self._script) - self._index

    @property
    def exhausted(self) -> bool:
        return self.remaining == 0


def says(text: str, *, tokens_in: int = 100, tokens_out: int = 50) -> Completion:
    """A completion that finishes with text."""
    return Completion(
        text=text,
        stop_reason=StopReason.COMPLETE,
        usage=Usage.observed(input_tokens=tokens_in, output_tokens=tokens_out),
        model="stub",
    )


def calls(name: str, arguments: dict[str, Any], *, call_id: str = "call-1") -> Completion:
    """A completion that requests one tool call."""
    return Completion(
        text="",
        stop_reason=StopReason.TOOL_CALL,
        tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments),),
        usage=Usage.observed(input_tokens=100, output_tokens=20),
        model="stub",
    )


def fails(reason: str) -> Completion:
    """A completion that reports a provider-side failure without raising.

    Distinct from raising :class:`ProviderError`: this is the provider answering with an
    error, which the loop must classify rather than treat as output.
    """
    return Completion(
        text="",
        stop_reason=StopReason.ERROR,
        error=reason,
        model="stub",
        usage=Usage(),
    )


class UnavailableProvider(Provider):
    """A provider that is configured but cannot serve. Used to test degradation.

    `retryable` defaults to True because the original use was an unreachable endpoint,
    which may well come back. A *misconfiguration* must pass False: an unset API key does
    not become set by trying again, and retrying it spends the run's retry budget and its
    wall clock before reporting a cause that was knowable at startup.
    """

    name = "unavailable"

    def __init__(self, reason: str = "endpoint unreachable", *, retryable: bool = True) -> None:
        self.reason = reason
        self.retryable = retryable

    def complete(
        self,
        messages: list[Message],  # noqa: ARG002 - interface conformance
        **kwargs: Any,  # noqa: ARG002 - interface conformance
    ) -> Completion:
        raise ProviderError(self.reason, retryable=self.retryable)

    def available(self) -> tuple[bool, str]:
        return False, self.reason
