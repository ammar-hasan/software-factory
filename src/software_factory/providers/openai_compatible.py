"""The OpenAI-compatible chat-completions adapter (PRD FR-11.2, PR-10, NFR-11.3).

One adapter reaches Ollama, llama.cpp's server, vLLM, LM Studio, text-generation-webui,
OpenRouter, Together, Groq, Fireworks and OpenAI itself, because they all serve
`POST {base}/chat/completions` with the same body. That is why this is the workhorse and
the Anthropic adapter is the special case: a local-first factory (PR-2) needs the local
runtimes to be first-class, and the local runtimes chose this shape.

The adapter is deliberately strict about two things a permissive one gets wrong:

*Tool arguments.* They arrive as a JSON **string**. A smaller model produces invalid JSON
there regularly. Returning `{}` on a parse failure is the single most dangerous line an
adapter can contain, because a tool called with no arguments often succeeds at doing
nothing and the run continues believing it acted. FR-11.10 forbids exactly this: the
outcome is typed and recorded, never invented.

*Usage.* Missing usage is reported as zero and flagged, not estimated. An estimated cost
that looks like a measured one makes every budget decision downstream unfalsifiable.
"""

from __future__ import annotations

import json
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

FINISH_REASONS: dict[str, StopReason] = {
    "stop": StopReason.COMPLETE,
    "tool_calls": StopReason.TOOL_CALL,
    # Older and self-hosted servers still emit the pre-2023 spelling.
    "function_call": StopReason.TOOL_CALL,
    "length": StopReason.LENGTH,
    "content_filter": StopReason.FILTERED,
}
"""Vendor spellings mapped onto our five outcomes.

An unmapped value becomes `ERROR` rather than `COMPLETE`. Defaulting an unknown stop
reason to "finished normally" is how a truncated answer is treated as an answer.
"""


class OpenAICompatibleProvider(Provider):
    """Chat completions over the OpenAI wire format.

    `base_url` includes the version prefix the server expects (`http://localhost:11434/v1`
    for Ollama, `https://api.openai.com/v1` for OpenAI), because the prefix is not
    consistent across servers and guessing it produces a 404 that reads like an outage.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        name: str = "openai-compatible",
        transport: Transport | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport or UrllibTransport()
        self._timeout_s = timeout_s
        self._extra_headers = dict(extra_headers or {})

    @property
    def endpoint(self) -> str:
        return f"{self._base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = dict(self._extra_headers)
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
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
        payload: dict[str, Any] = {
            "model": model,
            "messages": [_encode_message(m) for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = [_encode_tool(t) for t in tools]
            payload["tool_choice"] = "auto"

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
        """Ask the server what models it has.

        A reachability probe that does not name a model is the right check: it separates
        "the endpoint is down" from "that model is not pulled", and an operator needs to
        act differently on each.
        """
        try:
            self._transport.post_json(
                f"{self._base_url}/models",
                headers=self._headers(),
                payload={},
                timeout_s=min(self._timeout_s, 10.0),
            )
        except ProviderError as exc:
            # A 405 means the endpoint exists and dislikes POST, which answers the
            # question being asked. Anything else is a real reachability failure.
            if "405" in str(exc):
                return True, ""
            return False, str(exc)
        return True, ""


def _encode_message(message: Message) -> dict[str, Any]:
    encoded: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.tool_calls:
        encoded["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    if message.role is Role.TOOL:
        if not message.tool_call_id:
            raise ProviderError(
                "a tool result must carry the id of the call it answers; without it the "
                "server cannot pair them and silently drops the result",
                retryable=False,
            )
        encoded["tool_call_id"] = message.tool_call_id
    if message.name:
        encoded["name"] = message.name
    return encoded


def _encode_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Wrap a tool declaration in the OpenAI function envelope.

    The harness names the schema `input_schema` (`ToolRegistry` follows the MCP spelling);
    this wire format calls it `parameters`. Accepting both is not politeness -- reading
    only `parameters` would silently send every harness tool with an empty schema, which
    tells the model the tool takes no arguments. It would then call it with none, the call
    would fail validation, and the cause would look like a model problem.

    A tool with no schema under either name is refused rather than defaulted, for the same
    reason: an empty object is a *valid* schema, so defaulting hides the mistake instead of
    surfacing it. An already-wrapped function passes through unchanged, so a definition
    written against a vendor example is not double-wrapped into a tool the model never sees.
    """
    if tool.get("type") == "function" and "function" in tool:
        return tool
    schema = tool.get("parameters", tool.get("input_schema"))
    if schema is None:
        raise ProviderError(
            f"tool {tool.get('name')!r} declares no schema under `parameters` or "
            "`input_schema`; sending it with an empty schema would tell the model it "
            "takes no arguments",
            retryable=False,
        )
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": schema,
        },
    }


def _decode_completion(body: dict[str, Any], *, model: str, latency_s: float) -> Completion:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError(
            f"response had no choices; keys were {sorted(body)}",
            retryable=False,
        )
    choice = choices[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    if not isinstance(text, str):
        # Some servers return content as a list of parts. Join the text ones rather than
        # stringifying the structure into the transcript.
        text = "".join(
            part.get("text", "")
            for part in text
            if isinstance(part, dict) and part.get("type") == "text"
        )

    raw_reason = choice.get("finish_reason") or ""
    tool_calls = _decode_tool_calls(message.get("tool_calls"))
    stop_reason = FINISH_REASONS.get(raw_reason, StopReason.ERROR)
    if tool_calls and stop_reason is not StopReason.TOOL_CALL:
        # Several self-hosted servers emit `finish_reason: stop` alongside tool calls.
        # Believing the label over the payload would drop the calls on the floor.
        stop_reason = StopReason.TOOL_CALL

    error = None
    if stop_reason is StopReason.ERROR:
        error = f"unrecognised finish_reason {raw_reason!r}"

    return Completion(
        text=text,
        stop_reason=stop_reason,
        tool_calls=tool_calls,
        usage=_decode_usage(body.get("usage"), latency_s=latency_s),
        model=body.get("model") or model,
        error=error,
    )


def _decode_tool_calls(raw: Any) -> tuple[ToolCall, ...]:
    if not isinstance(raw, list):
        return ()
    calls: list[ToolCall] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        function = item.get("function") or {}
        name = function.get("name") or item.get("name") or ""
        arguments = function.get("arguments", item.get("arguments", {}))
        calls.append(
            ToolCall(
                id=str(item.get("id") or f"call_{index}"),
                name=str(name),
                arguments=_parse_arguments(arguments, name=str(name)),
            )
        )
    return tuple(calls)


def _parse_arguments(arguments: Any, *, name: str) -> dict[str, Any]:
    """Decode a tool call's arguments, refusing to invent an empty one.

    A model that emits malformed JSON here has failed, and the failure has to reach the
    loop as a failure. `{}` is a valid argument object for several real tools, so
    substituting it converts a parse error into a successful call that does nothing --
    the invented result FR-11.10 exists to forbid.
    """
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        raise ProviderError(
            f"tool call {name!r} had arguments of type {type(arguments).__name__}, "
            "expected a JSON object or a JSON string",
            retryable=False,
        )
    if not arguments.strip():
        # An empty string is the wire's way of saying "no arguments", which is different
        # from unparseable, and every server that takes no-argument tools emits it.
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            f"tool call {name!r} had unparseable arguments ({exc}); "
            f"the model emitted: {arguments[:200]!r}",
            retryable=True,
        ) from exc
    if not isinstance(parsed, dict):
        raise ProviderError(
            f"tool call {name!r} arguments decoded to {type(parsed).__name__}, expected an object",
            retryable=True,
        )
    return parsed


def _decode_usage(raw: Any, *, latency_s: float) -> Usage:
    """Read reported usage, treating absent as zero rather than estimating it.

    `cached_tokens` is nested under `prompt_tokens_details` and is *already included* in
    `prompt_tokens` on this wire format, which matches our inclusive convention -- so it
    is passed through unchanged. The Anthropic adapter has to convert; see the comment
    there for why the difference matters.
    """
    if not isinstance(raw, dict):
        return Usage(latency_s=latency_s)
    details = raw.get("prompt_tokens_details")
    cached = 0
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens") or 0)
    return Usage.observed(
        input_tokens=int(raw.get("prompt_tokens") or 0),
        output_tokens=int(raw.get("completion_tokens") or 0),
        cached_input_tokens=cached,
        latency_s=latency_s,
    )
