"""Tests for the real provider adapters.

Every test here runs without a network. That is not a convenience: the offline job
(`scripts/run_offline_tests.py`) runs this whole suite with connection and name
resolution denied, and a provider layer that can only be tested against a live endpoint
is one that is tested rarely and therefore wrong.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from software_factory.providers.anthropic import AnthropicProvider
from software_factory.providers.base import (
    Message,
    ProviderError,
    Role,
    StopReason,
    ToolCall,
    Usage,
)
from software_factory.providers.openai_compatible import OpenAICompatibleProvider
from software_factory.providers.registry import (
    UnknownProviderError,
    endpoint_for,
    known_providers,
    resolve,
    spec_for,
)
from software_factory.providers.transport import (
    RETRYABLE_STATUS,
    Response,
    RetryingTransport,
    UrllibTransport,
    redact_headers,
)


class FakeTransport:
    """Records requests and replays canned responses. The only test double needed."""

    def __init__(self, *responses: dict[str, Any] | ProviderError) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_s: float,
    ) -> Response:
        self.requests.append(
            {"url": url, "headers": headers, "payload": payload, "timeout_s": timeout_s}
        )
        if not self._responses:
            raise AssertionError(f"unexpected extra request to {url}")
        item = self._responses.pop(0)
        if isinstance(item, ProviderError):
            raise item
        return Response(status=200, body=item)


def openai_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "m",
        "choices": [{"finish_reason": "stop", "message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------- OpenAI wire


def test_openai_round_trip() -> None:
    transport = FakeTransport(openai_body())
    provider = OpenAICompatibleProvider(base_url="http://x/v1", transport=transport)
    completion = provider.complete([Message(Role.USER, "hi")], model="m")

    assert completion.text == "hello"
    assert completion.stop_reason is StopReason.COMPLETE
    assert completion.usage.input_tokens == 10
    assert transport.requests[0]["url"] == "http://x/v1/chat/completions"


def test_openai_sends_the_key_as_a_bearer_token() -> None:
    transport = FakeTransport(openai_body())
    provider = OpenAICompatibleProvider(base_url="http://x/v1", api_key="k", transport=transport)
    provider.complete([Message(Role.USER, "hi")], model="m")
    assert transport.requests[0]["headers"]["authorization"] == "Bearer k"


def test_openai_tool_call_decodes_string_arguments() -> None:
    body = openai_body(
        choices=[
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {"name": "read", "arguments": '{"path": "a.py"}'},
                        }
                    ],
                },
            }
        ]
    )
    provider = OpenAICompatibleProvider(base_url="http://x/v1", transport=FakeTransport(body))
    completion = provider.complete([Message(Role.USER, "hi")], model="m")

    assert completion.wants_tools
    assert completion.tool_calls[0].arguments == {"path": "a.py"}


def test_openai_unparseable_tool_arguments_are_an_error_not_an_empty_dict() -> None:
    """The single most dangerous line an adapter can contain (FR-11.10).

    `{}` is a valid argument object for real tools, so substituting it on a parse failure
    turns a malformed call into a successful call that does nothing -- and the run carries
    on believing it acted.
    """
    body = openai_body(
        choices=[
            {
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [{"id": "c1", "function": {"name": "read", "arguments": "{pa"}}]
                },
            }
        ]
    )
    provider = OpenAICompatibleProvider(base_url="http://x/v1", transport=FakeTransport(body))
    with pytest.raises(ProviderError, match="unparseable arguments"):
        provider.complete([Message(Role.USER, "hi")], model="m")


def test_openai_empty_argument_string_means_no_arguments() -> None:
    body = openai_body(
        choices=[
            {
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [{"id": "c1", "function": {"name": "now", "arguments": ""}}]
                },
            }
        ]
    )
    provider = OpenAICompatibleProvider(base_url="http://x/v1", transport=FakeTransport(body))
    completion = provider.complete([Message(Role.USER, "hi")], model="m")
    assert completion.tool_calls[0].arguments == {}


def test_openai_tool_calls_win_over_a_contradicting_finish_reason() -> None:
    """Several self-hosted servers say `stop` while returning tool calls."""
    body = openai_body(
        choices=[
            {
                "finish_reason": "stop",
                "message": {
                    "tool_calls": [{"id": "c1", "function": {"name": "read", "arguments": "{}"}}]
                },
            }
        ]
    )
    provider = OpenAICompatibleProvider(base_url="http://x/v1", transport=FakeTransport(body))
    completion = provider.complete([Message(Role.USER, "hi")], model="m")
    assert completion.stop_reason is StopReason.TOOL_CALL


def test_openai_unknown_finish_reason_is_an_error_not_a_completion() -> None:
    body = openai_body(choices=[{"finish_reason": "banana", "message": {"content": "partial"}}])
    provider = OpenAICompatibleProvider(base_url="http://x/v1", transport=FakeTransport(body))
    completion = provider.complete([Message(Role.USER, "hi")], model="m")
    assert completion.stop_reason is StopReason.ERROR
    assert completion.error is not None and "banana" in completion.error


def test_openai_cached_tokens_are_already_inclusive() -> None:
    body = openai_body(
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 80},
        }
    )
    provider = OpenAICompatibleProvider(base_url="http://x/v1", transport=FakeTransport(body))
    usage = provider.complete([Message(Role.USER, "hi")], model="m").usage
    assert usage.input_tokens == 100
    assert usage.cached_input_tokens == 80


def test_openai_absent_usage_is_zero_not_estimated() -> None:
    body = openai_body(usage=None)
    provider = OpenAICompatibleProvider(base_url="http://x/v1", transport=FakeTransport(body))
    usage = provider.complete([Message(Role.USER, "hi")], model="m").usage
    assert usage.input_tokens == 0 and usage.output_tokens == 0


def test_openai_tool_schema_reads_the_harness_spelling() -> None:
    """Regression: the harness emits `input_schema`; this wire wants `parameters`.

    Reading only `parameters` sent every harness tool with an empty schema, which tells
    the model the tool takes no arguments. It would then call it with none, and the
    failure would look like a model problem rather than a translation one.
    """
    transport = FakeTransport(openai_body())
    provider = OpenAICompatibleProvider(base_url="http://x/v1", transport=transport)
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    provider.complete(
        [Message(Role.USER, "hi")],
        model="m",
        tools=[{"name": "read", "description": "d", "input_schema": schema}],
    )
    sent = transport.requests[0]["payload"]["tools"][0]
    assert sent["function"]["parameters"] == schema


def test_openai_tool_without_a_schema_is_refused_not_defaulted() -> None:
    provider = OpenAICompatibleProvider(
        base_url="http://x/v1", transport=FakeTransport(openai_body())
    )
    with pytest.raises(ProviderError, match="declares no schema"):
        provider.complete([Message(Role.USER, "hi")], model="m", tools=[{"name": "read"}])


def test_openai_already_wrapped_tool_is_not_double_wrapped() -> None:
    transport = FakeTransport(openai_body())
    provider = OpenAICompatibleProvider(base_url="http://x/v1", transport=transport)
    wrapped = {"type": "function", "function": {"name": "read", "parameters": {}}}
    provider.complete([Message(Role.USER, "hi")], model="m", tools=[wrapped])
    assert transport.requests[0]["payload"]["tools"][0] == wrapped


def test_openai_assistant_turn_carries_its_tool_calls() -> None:
    """Regression: a tool result whose id is not in the preceding assistant turn is a 400.

    The loop used to append only the assistant's text, so every run that called a tool
    failed on turn two against any real provider.
    """
    transport = FakeTransport(openai_body())
    provider = OpenAICompatibleProvider(base_url="http://x/v1", transport=transport)
    provider.complete(
        [
            Message(Role.USER, "hi"),
            Message(
                Role.ASSISTANT,
                "",
                tool_calls=(ToolCall(id="c1", name="read", arguments={"path": "a"}),),
            ),
            Message(Role.TOOL, "contents", tool_call_id="c1"),
        ],
        model="m",
    )
    sent = transport.requests[0]["payload"]["messages"]
    assert sent[1]["tool_calls"][0]["id"] == "c1"
    assert json.loads(sent[1]["tool_calls"][0]["function"]["arguments"]) == {"path": "a"}
    assert sent[2]["tool_call_id"] == "c1"


def test_openai_tool_result_without_an_id_is_refused() -> None:
    provider = OpenAICompatibleProvider(
        base_url="http://x/v1", transport=FakeTransport(openai_body())
    )
    with pytest.raises(ProviderError, match="id of the call"):
        provider.complete([Message(Role.TOOL, "result")], model="m")


def test_openai_availability_probes_the_model_list() -> None:
    transport = FakeTransport({"data": []})
    provider = OpenAICompatibleProvider(base_url="http://x/v1", transport=transport)
    assert provider.available() == (True, "")
    assert transport.requests[0]["url"] == "http://x/v1/models"


def test_openai_availability_reports_why_not() -> None:
    provider = OpenAICompatibleProvider(
        base_url="http://x/v1",
        transport=FakeTransport(ProviderError("cannot reach http://x/v1/models: refused")),
    )
    ok, reason = provider.available()
    assert not ok and "refused" in reason


# ------------------------------------------------------------------ Anthropic wire


def anthropic_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "m",
        "content": [{"type": "text", "text": "hello"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 3},
    }
    body.update(overrides)
    return body


def test_anthropic_lifts_system_messages_to_the_top_level_in_order() -> None:
    """Order is load-bearing: later sections never silently override earlier ones."""
    transport = FakeTransport(anthropic_body())
    provider = AnthropicProvider(api_key="k", transport=transport)
    provider.complete(
        [
            Message(Role.SYSTEM, "first"),
            Message(Role.SYSTEM, "second"),
            Message(Role.USER, "hi"),
        ],
        model="m",
    )
    payload = transport.requests[0]["payload"]
    assert payload["system"] == "first\n\nsecond"
    assert [t["role"] for t in payload["messages"]] == ["user"]


def test_anthropic_refuses_a_late_system_message() -> None:
    provider = AnthropicProvider(api_key="k", transport=FakeTransport(anthropic_body()))
    with pytest.raises(ProviderError, match="after the conversation began"):
        provider.complete([Message(Role.USER, "hi"), Message(Role.SYSTEM, "sneaky")], model="m")


def test_anthropic_merges_consecutive_tool_results_into_one_turn() -> None:
    """Separate turns break alternation, and the API error names roles, not the cause."""
    transport = FakeTransport(anthropic_body())
    provider = AnthropicProvider(api_key="k", transport=transport)
    provider.complete(
        [
            Message(Role.USER, "hi"),
            Message(
                Role.ASSISTANT,
                "",
                tool_calls=(
                    ToolCall(id="c1", name="read", arguments={}),
                    ToolCall(id="c2", name="grep", arguments={}),
                ),
            ),
            Message(Role.TOOL, "one", tool_call_id="c1"),
            Message(Role.TOOL, "two", tool_call_id="c2"),
        ],
        model="m",
    )
    turns = transport.requests[0]["payload"]["messages"]
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert len(turns[2]["content"]) == 2
    assert [b["tool_use_id"] for b in turns[2]["content"]] == ["c1", "c2"]


def test_anthropic_assistant_tool_calls_become_tool_use_blocks() -> None:
    transport = FakeTransport(anthropic_body())
    provider = AnthropicProvider(api_key="k", transport=transport)
    provider.complete(
        [
            Message(Role.USER, "hi"),
            Message(
                Role.ASSISTANT,
                "thinking",
                tool_calls=(ToolCall(id="c1", name="read", arguments={"p": 1}),),
            ),
        ],
        model="m",
    )
    blocks = transport.requests[0]["payload"]["messages"][1]["content"]
    assert blocks[0] == {"type": "text", "text": "thinking"}
    assert blocks[1] == {"type": "tool_use", "id": "c1", "name": "read", "input": {"p": 1}}


def test_anthropic_empty_turn_is_refused() -> None:
    provider = AnthropicProvider(api_key="k", transport=FakeTransport(anthropic_body()))
    with pytest.raises(ProviderError, match="neither text nor tool calls"):
        provider.complete([Message(Role.USER, "")], model="m")


def test_anthropic_cache_reads_are_added_to_the_input_total() -> None:
    """The convention is inverted on this wire, and passing it through under-reports cost.

    `input_tokens` excludes cache reads here and includes them on the OpenAI wire. An
    adapter that forwards both fields unchanged under-reports every cached call's input by
    exactly the cache hit rate -- silently, and in the flattering direction.
    """
    body = anthropic_body(
        usage={
            "input_tokens": 20,
            "output_tokens": 5,
            "cache_read_input_tokens": 80,
        }
    )
    provider = AnthropicProvider(api_key="k", transport=FakeTransport(body))
    usage = provider.complete([Message(Role.USER, "hi")], model="m").usage
    assert usage.input_tokens == 100
    assert usage.cached_input_tokens == 80


def test_anthropic_cache_writes_are_input_but_not_cached() -> None:
    """A cache write costs more than an uncached call; counting it as cached bills zero."""
    body = anthropic_body(
        usage={
            "input_tokens": 20,
            "output_tokens": 5,
            "cache_creation_input_tokens": 60,
        }
    )
    provider = AnthropicProvider(api_key="k", transport=FakeTransport(body))
    usage = provider.complete([Message(Role.USER, "hi")], model="m").usage
    assert usage.input_tokens == 80
    assert usage.cached_input_tokens == 0
    assert usage.cost(per_mtok_in=1_000_000, per_mtok_out=0) == pytest.approx(80.0)


def test_anthropic_stop_reasons_map_to_our_five() -> None:
    for wire, expected in [
        ("end_turn", StopReason.COMPLETE),
        ("max_tokens", StopReason.LENGTH),
        ("refusal", StopReason.FILTERED),
        ("pause_turn", StopReason.LENGTH),
        ("something_new", StopReason.ERROR),
    ]:
        provider = AnthropicProvider(
            api_key="k", transport=FakeTransport(anthropic_body(stop_reason=wire))
        )
        completion = provider.complete([Message(Role.USER, "hi")], model="m")
        assert completion.stop_reason is expected, wire


def test_anthropic_without_a_key_reports_unavailable_before_any_call() -> None:
    provider = AnthropicProvider(transport=FakeTransport())
    ok, reason = provider.available()
    assert not ok and "API key" in reason


def test_anthropic_tool_use_with_non_object_input_is_an_error() -> None:
    body = anthropic_body(
        content=[{"type": "tool_use", "id": "c1", "name": "read", "input": "oops"}],
        stop_reason="tool_use",
    )
    provider = AnthropicProvider(api_key="k", transport=FakeTransport(body))
    with pytest.raises(ProviderError, match="expected an object"):
        provider.complete([Message(Role.USER, "hi")], model="m")


# ----------------------------------------------------------------------- transport


def test_redaction_keeps_names_and_drops_values() -> None:
    redacted = redact_headers(
        {"authorization": "Bearer sk-real", "content-type": "application/json"}
    )
    assert "sk-real" not in json.dumps(redacted)
    assert "authorization" in redacted
    assert redacted["content-type"] == "application/json"


def test_retrying_transport_backs_off_and_gives_up() -> None:
    slept: list[float] = []
    inner = FakeTransport(
        ProviderError("503", retryable=True),
        ProviderError("503", retryable=True),
        ProviderError("503", retryable=True),
    )
    transport = RetryingTransport(inner, attempts=3, base_delay_s=0.5, sleep=slept.append)
    with pytest.raises(ProviderError, match="after 3 attempts"):
        transport.post_json("http://x", headers={}, payload={}, timeout_s=1)
    assert slept == [0.5, 1.0]


def test_retrying_transport_does_not_retry_a_permanent_failure() -> None:
    slept: list[float] = []
    inner = FakeTransport(ProviderError("401 unauthorized", retryable=False))
    transport = RetryingTransport(inner, attempts=3, sleep=slept.append)
    with pytest.raises(ProviderError, match="401"):
        transport.post_json("http://x", headers={}, payload={}, timeout_s=1)
    assert slept == []
    assert len(inner.requests) == 1


def test_retrying_transport_returns_the_first_success() -> None:
    inner = FakeTransport(ProviderError("503", retryable=True), {"ok": True})
    transport = RetryingTransport(inner, attempts=3, sleep=lambda _: None)
    assert transport.post_json("http://x", headers={}, payload={}, timeout_s=1).body == {"ok": True}


def test_a_bad_credential_status_is_never_retryable() -> None:
    assert 401 not in RETRYABLE_STATUS
    assert 403 not in RETRYABLE_STATUS
    assert 404 not in RETRYABLE_STATUS
    assert 429 in RETRYABLE_STATUS


def test_non_http_schemes_are_refused_without_opening_anything() -> None:
    """A `file:` URL in a config is a mistake or an attempt to read the local disk."""
    with pytest.raises(ProviderError, match="must be http or https"):
        UrllibTransport().post_json("file:///etc/passwd", headers={}, payload={}, timeout_s=1)


# ------------------------------------------------------------------------ registry


def test_local_resolves_with_no_account_at_all() -> None:
    """PR-2: the reference path must not need a credential."""
    resolution = resolve("local", env={})
    assert resolution.usable
    assert resolution.spec.local
    assert not resolution.spec.requires_key


def test_a_missing_key_is_reported_by_name() -> None:
    resolution = resolve("openai", env={})
    assert not resolution.usable
    assert "OPENAI_API_KEY" in resolution.reason


def test_a_missing_key_does_not_produce_a_retryable_failure() -> None:
    """Retrying an unset environment variable spends the budget to learn nothing."""
    resolution = resolve("openai", env={})
    with pytest.raises(ProviderError) as caught:
        resolution.provider.complete([], model="m")
    assert caught.value.retryable is False


def test_resolve_is_total_so_one_bad_tier_does_not_hide_the_rest() -> None:
    resolution = resolve("openai-compatible", env={})
    assert not resolution.usable
    assert "baseUrl" in resolution.reason


def test_unknown_provider_names_what_is_known() -> None:
    with pytest.raises(UnknownProviderError) as caught:
        spec_for("gpt5-turbo-max")
    assert "anthropic" in str(caught.value)
    assert caught.value.retryable is False


def test_ollama_is_an_alias_for_local() -> None:
    assert spec_for("ollama").name == "local"


def test_every_spec_that_requires_a_key_names_the_variable() -> None:
    """A provider that needs a key and does not say which one cannot be diagnosed."""
    for name in known_providers():
        spec = spec_for(name)
        if spec.requires_key:
            assert spec.api_key_env, name


def test_local_defaults_use_the_loopback_address_not_a_name() -> None:
    """`localhost` resolves through DNS, which the offline guard blocks.

    A default spelled `localhost` turns 'no model running' into 'name resolution failed'
    and sends the reader looking at the wrong subsystem.
    """
    for name in known_providers():
        spec = spec_for(name)
        if spec.local and spec.base_url:
            assert "localhost" not in spec.base_url, name


def test_endpoint_for_marks_local_providers_as_local() -> None:
    endpoint = endpoint_for("local")
    assert endpoint is not None and endpoint.local


def test_endpoint_for_an_overridden_local_url_is_not_local() -> None:
    """Pointing `local` at another host is a real destination, and must report as one."""
    endpoint = endpoint_for("local", base_url="http://gpu-box.internal:11434/v1")
    assert endpoint is not None and not endpoint.local


def test_endpoint_for_an_unknown_provider_is_none_not_an_exception() -> None:
    """A typo in one tier must not stop the egress report printing the others."""
    assert endpoint_for("nonesuch") is None


def test_the_stub_provider_has_no_endpoint() -> None:
    assert endpoint_for("stub") is None


def test_usage_rejects_disjoint_cache_accounting() -> None:
    with pytest.raises(ValueError, match="exceeds input_tokens"):
        Usage.observed(input_tokens=10, cached_input_tokens=80)
