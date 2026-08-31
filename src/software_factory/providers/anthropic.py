"""The Anthropic Messages adapter (PRD FR-11.2, PR-10).

Kept separate from the OpenAI-compatible adapter rather than bent into it, because three
differences are structural and a translation layer that pretends otherwise gets each of
them subtly wrong:

1. **System is not a message.** It is a top-level field. A system message pushed into the
   list is either rejected or, worse, accepted as a user turn -- at which point the
   harness's carefully delimited invariants arrive as something the model may argue with.
2. **Tool results are content blocks on a user turn**, not their own role, and consecutive
   results must be merged into one turn. Sending them as separate turns violates the
   alternation rule.
3. **Cached input tokens are reported disjointly.** `input_tokens` *excludes*
   `cache_read_input_tokens` here, and includes them on the OpenAI wire. Our `Usage`
   convention is inclusive (see `Usage.check`), so this adapter must add them. An adapter
   that passes both fields straight through under-reports every cached call's input by
   exactly the cache hit rate -- silently, and in the flattering direction.

That third one is why `Usage.observed` exists and why every adapter goes through it.
"""

from __future__ import annotations

import time
from typing import Any

from software_factory.providers.base import (
    Completion,
    Message,
    Provider,
    ProviderError,
    Role,
    StopReason,
    ToolCall,
    Usage,
)
from software_factory.providers.transport import (
    DEFAULT_TIMEOUT_S,
    Transport,
    UrllibTransport,
)

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"

STOP_REASONS: dict[str, StopReason] = {
    "end_turn": StopReason.COMPLETE,
    "stop_sequence": StopReason.COMPLETE,
    "tool_use": StopReason.TOOL_CALL,
    "max_tokens": StopReason.LENGTH,
    "refusal": StopReason.FILTERED,
    "pause_turn": StopReason.LENGTH,
}
"""Vendor spellings mapped onto our five outcomes.

`pause_turn` maps to `LENGTH` rather than `COMPLETE`: the turn was cut short and the loop
should treat it as an incomplete answer, which is exactly what it does with a length stop.
An unmapped value becomes `ERROR`, never `COMPLETE`.
"""


class AnthropicProvider(Provider):
    """Completions over the Messages API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        name: str = "anthropic",
        transport: Transport | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport or UrllibTransport()
        self._timeout_s = timeout_s
        self._extra_headers = dict(extra_headers or {})

    @property
    def endpoint(self) -> str:
        return f"{self._base_url}/v1/messages"

    def _headers(self) -> dict[str, str]:
        headers = {"anthropic-version": API_VERSION, **self._extra_headers}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> Completion:
        system, turns = _split_system(messages)
        payload: dict[str, Any] = {
            "model": model,
            "messages": turns,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [_encode_tool(t) for t in tools]

        started = time.monotonic()
        response = self._transport.post_json(
            self.endpoint,
            headers=self._headers(),
            payload=payload,
            timeout_s=self._timeout_s,
        )
        elapsed = time.monotonic() - started
        return _decode_completion(response.body, model=model, latency_s=elapsed)

    def available(self) -> tuple[bool, str]:
        """Report a missing credential without making a call.

        Checked before a run rather than discovered during one: an unset environment
        variable is the most common configuration failure, and burning a work item's
        setup to learn it is a bad trade.
        """
        if not self._api_key:
            return False, "no API key configured for the Anthropic provider"
        return True, ""


def _split_system(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Lift system messages out and fold the rest into alternating turns.

    System messages are joined in order with blank lines. The harness composes several
    (invariants, policy, role, awareness) and their order is load-bearing -- §6 of
    HARNESS.md says later sections never silently override earlier ones, which is only
    true if the order survives translation.
    """
    system_parts: list[str] = []
    turns: list[dict[str, Any]] = []
    for message in messages:
        if message.role is Role.SYSTEM:
            if turns:
                # A system message after the conversation has started cannot be lifted
                # without moving it in time, which changes what the model was told when.
                raise ProviderError(
                    "a system message appeared after the conversation began; this API "
                    "takes system content only as a prelude, and hoisting it would "
                    "silently reorder what the model was told",
                    retryable=False,
                )
            system_parts.append(message.content)
            continue
        if message.role is Role.TOOL:
            _append_tool_result(turns, message)
            continue
        turns.append(_encode_turn(message))
    return "\n\n".join(system_parts), turns


def _encode_turn(message: Message) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    for call in message.tool_calls:
        blocks.append(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
        )
    if not blocks:
        # An empty content list is rejected. A turn with nothing in it is a harness bug,
        # and saying so here is more useful than a 400 from the far end.
        raise ProviderError(
            f"a {message.role.value} turn had neither text nor tool calls",
            retryable=False,
        )
    return {"role": message.role.value, "content": blocks}


def _append_tool_result(turns: list[dict[str, Any]], message: Message) -> None:
    """Add a tool result, merging into the previous user turn when there is one.

    Consecutive results must share a turn. Emitting one turn each breaks alternation, and
    the error the API returns for that names the roles rather than the cause.
    """
    if not message.tool_call_id:
        raise ProviderError(
            "a tool result must carry the id of the call it answers",
            retryable=False,
        )
    block = {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id,
        "content": message.content,
    }
    if turns and turns[-1]["role"] == Role.USER.value and _is_tool_result_turn(turns[-1]):
        turns[-1]["content"].append(block)
        return
    turns.append({"role": Role.USER.value, "content": [block]})


def _is_tool_result_turn(turn: dict[str, Any]) -> bool:
    content = turn.get("content")
    return isinstance(content, list) and all(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    )


def _encode_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Accept the harness spelling (`input_schema`) or the OpenAI one (`parameters`).

    Refused rather than defaulted when neither is present, for the reason given in the
    OpenAI adapter: an empty schema is a valid schema, so a default hides the mistake.
    """
    if "function" in tool and tool.get("type") == "function":
        function = tool["function"]
        return {
            "name": function["name"],
            "description": function.get("description", ""),
            "input_schema": function.get("parameters", {"type": "object", "properties": {}}),
        }
    schema = tool.get("input_schema", tool.get("parameters"))
    if schema is None:
        raise ProviderError(
            f"tool {tool.get('name')!r} declares no schema under `input_schema` or `parameters`",
            retryable=False,
        )
    return {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "input_schema": schema,
    }


def _decode_completion(body: dict[str, Any], *, model: str, latency_s: float) -> Completion:
    content = body.get("content")
    if not isinstance(content, list):
        raise ProviderError(
            f"response had no content blocks; keys were {sorted(body)}",
            retryable=False,
        )
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            texts.append(str(block.get("text", "")))
        elif kind == "tool_use":
            arguments = block.get("input")
            if not isinstance(arguments, dict):
                # Arrives already decoded on this API, so a non-object here means the
                # response is malformed rather than that the model wrote bad JSON.
                raise ProviderError(
                    f"tool_use block for {block.get('name')!r} had input of type "
                    f"{type(arguments).__name__}, expected an object",
                    retryable=True,
                )
            calls.append(
                ToolCall(
                    id=str(block.get("id", "")),
                    name=str(block.get("name", "")),
                    arguments=arguments,
                )
            )

    raw_reason = body.get("stop_reason") or ""
    stop_reason = STOP_REASONS.get(str(raw_reason), StopReason.ERROR)
    if calls and stop_reason is not StopReason.TOOL_CALL:
        stop_reason = StopReason.TOOL_CALL
    error = None
    if stop_reason is StopReason.ERROR:
        error = f"unrecognised stop_reason {raw_reason!r}"

    return Completion(
        text="".join(texts),
        stop_reason=stop_reason,
        tool_calls=tuple(calls),
        usage=_decode_usage(body.get("usage"), latency_s=latency_s),
        model=str(body.get("model") or model),
        error=error,
    )


def _decode_usage(raw: Any, *, latency_s: float) -> Usage:
    """Convert disjoint cache accounting into our inclusive convention.

    `input_tokens` here excludes cache reads, so the total input is the sum of three
    fields. `cache_creation_input_tokens` counts tokens *written* to the cache: they were
    processed at full price on this call, so they belong in the input total and **not** in
    the cached subset. Folding them into `cached_input_tokens` would make a cache write
    look free, which is the opposite of true -- writes cost more than an uncached call.
    """
    if not isinstance(raw, dict):
        return Usage(latency_s=latency_s)
    fresh = int(raw.get("input_tokens") or 0)
    cache_read = int(raw.get("cache_read_input_tokens") or 0)
    cache_write = int(raw.get("cache_creation_input_tokens") or 0)
    return Usage.observed(
        input_tokens=fresh + cache_read + cache_write,
        output_tokens=int(raw.get("output_tokens") or 0),
        cached_input_tokens=cache_read,
        latency_s=latency_s,
    )
