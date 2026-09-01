"""The provider layer over a real socket (PRD FR-11.2).

Every other provider test injects a transport. That is the right shape for testing decode
logic, and it means `UrllibTransport` -- the one object in the package that opens a socket --
was exercised by nothing. URL construction, header assembly, HTTP status handling, error-body
decoding and the retry schedule were all only ever tested against a stand-in that could not
disagree with a real server.

So these run the real providers, through the real transport, over loopback TCP, against a
server that answers in the hosts' genuine wire formats -- including the awkward shapes:
tool calls, a 429 with a body, a 500 whose body carries the reason, a malformed body, and
Anthropic's disjoint cache-token accounting.

This is not a substitute for running against a live endpoint. It is the strongest claim
that can be made without one: the bytes on the wire are the bytes these providers build,
and the objects they return are built from bytes a real server would send.

The server binds `127.0.0.1:0`, which the offline job's guard permits precisely because the
process bound it itself.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from software_factory.providers.anthropic import AnthropicProvider
from software_factory.providers.base import Message, ProviderError, Role, StopReason
from software_factory.providers.openai_compatible import OpenAICompatibleProvider
from software_factory.providers.transport import RetryingTransport, UrllibTransport

# --------------------------------------------------------------------------- the server


class Recorder:
    """What the server was sent, and what it should answer."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.answers: list[tuple[int, Any]] = []

    def next_answer(self) -> tuple[int, Any]:
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def serve(recorder: Recorder):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            recorder.requests.append(
                {
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": json.loads(raw) if raw else None,
                    "raw": raw,
                }
            )
            status, payload = recorder.next_answer()
            body = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: Any) -> None:
            """Silent: a test that prints a line per request buries the failure."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def wire():
    recorder = Recorder()
    server = serve(recorder)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield recorder, base
    finally:
        server.shutdown()
        server.server_close()


def openai_answer(**over: Any) -> dict[str, Any]:
    body = {
        "id": "chatcmpl-1",
        "model": "qwen2.5-coder",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "the BOM is stripped"},
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 18, "total_tokens": 138},
    }
    body.update(over)
    return body


# ------------------------------------------------------------------- the OpenAI wire


def test_a_completion_crosses_a_real_socket(wire) -> None:
    recorder, base = wire
    recorder.answers = [(200, openai_answer())]

    completion = OpenAICompatibleProvider(base_url=f"{base}/v1", api_key="sk-test").complete(
        [Message(role=Role.USER, content="what happened to the BOM?")], model="qwen2.5-coder"
    )

    assert completion.text == "the BOM is stripped"
    assert completion.stop_reason is StopReason.COMPLETE
    assert completion.usage.input_tokens == 120
    assert completion.usage.output_tokens == 18
    assert completion.usage.latency_s > 0, "latency was never measured over a real call"


def test_the_request_reaches_the_path_and_carries_the_key(wire) -> None:
    """URL construction and header assembly were only ever checked against a stand-in."""
    recorder, base = wire
    recorder.answers = [(200, openai_answer())]

    OpenAICompatibleProvider(base_url=f"{base}/v1", api_key="sk-test").complete(
        [Message(role=Role.USER, content="hello")], model="qwen2.5-coder"
    )

    sent = recorder.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["headers"]["authorization"] == "Bearer sk-test"
    assert sent["headers"]["content-type"].startswith("application/json")
    assert sent["body"]["model"] == "qwen2.5-coder"


def test_no_authorization_header_is_sent_when_there_is_no_key(wire) -> None:
    """A local server that rejects an empty bearer is a real and confusing failure."""
    recorder, base = wire
    recorder.answers = [(200, openai_answer())]

    OpenAICompatibleProvider(base_url=f"{base}/v1").complete(
        [Message(role=Role.USER, content="hello")], model="local"
    )

    assert "authorization" not in recorder.requests[0]["headers"]


def test_a_tool_call_round_trips_over_the_wire(wire) -> None:
    """The shape a tool-calling run depends on, in the format a real server sends it."""
    recorder, base = wire
    recorder.answers = [
        (
            200,
            openai_answer(
                choices=[
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path": "importer.py"}',
                                    },
                                }
                            ],
                        },
                    }
                ]
            ),
        )
    ]

    completion = OpenAICompatibleProvider(base_url=f"{base}/v1").complete(
        [Message(role=Role.USER, content="read it")],
        model="local",
        tools=[
            {
                "name": "read_file",
                "description": "read a file",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        ],
    )

    assert completion.wants_tools
    assert completion.tool_calls[0].name == "read_file"
    assert completion.tool_calls[0].arguments == {"path": "importer.py"}

    # And the tool went out in the wire format the host expects, with a real schema rather
    # than the empty one that tells a model the tool takes no arguments.
    sent_tool = recorder.requests[0]["body"]["tools"][0]
    assert sent_tool["function"]["name"] == "read_file"
    assert sent_tool["function"]["parameters"]["properties"] == {"path": {"type": "string"}}


def test_an_assistant_turn_carries_its_tool_calls_back(wire) -> None:
    """Turn two of every tool-calling run. Dropping these 400s against any real host."""
    from software_factory.providers.base import ToolCall

    recorder, base = wire
    recorder.answers = [(200, openai_answer())]

    OpenAICompatibleProvider(base_url=f"{base}/v1").complete(
        [
            Message(role=Role.USER, content="read it"),
            Message(
                role=Role.ASSISTANT,
                content="",
                tool_calls=(ToolCall(id="call_1", name="read_file", arguments={"path": "x.py"}),),
            ),
            Message(role=Role.TOOL, content="def f(): ...", tool_call_id="call_1"),
        ],
        model="local",
    )

    turns = recorder.requests[0]["body"]["messages"]
    assistant = next(t for t in turns if t["role"] == "assistant")
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"path": "x.py"}


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(429, True), (500, True), (503, True), (400, False), (401, False), (404, False)],
)
def test_http_failures_are_typed_from_a_real_response(wire, status: int, retryable: bool) -> None:
    """Which failures the loop retries is decided here, and it was decided against a fake."""
    recorder, base = wire
    recorder.answers = [(status, {"error": {"message": "upstream said no"}})]

    with pytest.raises(ProviderError) as caught:
        OpenAICompatibleProvider(base_url=f"{base}/v1").complete(
            [Message(role=Role.USER, content="hi")], model="local"
        )

    assert caught.value.retryable is retryable
    assert caught.value.status == status
    assert "upstream said no" in str(caught.value), "the body's reason was discarded"


def test_a_malformed_body_does_not_become_an_empty_completion(wire) -> None:
    """A server answering 200 with HTML -- a proxy error page -- is a real thing.

    Turning it into a completion with empty text would put a silent no-op in the run.
    """
    recorder, base = wire
    recorder.answers = [(200, "<html>gateway timeout</html>")]

    with pytest.raises(ProviderError):
        OpenAICompatibleProvider(base_url=f"{base}/v1").complete(
            [Message(role=Role.USER, content="hi")], model="local"
        )


def test_retries_happen_over_the_wire_and_then_succeed(wire) -> None:
    recorder, base = wire
    recorder.answers = [(503, {"error": "warming up"}), (200, openai_answer())]
    slept: list[float] = []

    provider = OpenAICompatibleProvider(
        base_url=f"{base}/v1",
        transport=RetryingTransport(UrllibTransport(), attempts=3, sleep=slept.append),
    )
    completion = provider.complete([Message(role=Role.USER, content="hi")], model="local")

    assert completion.text == "the BOM is stripped"
    assert len(recorder.requests) == 2
    assert slept, "the retry did not back off"


def test_a_non_retryable_failure_is_not_retried(wire) -> None:
    """Retrying a 401 three times is three ways to be told the key is wrong."""
    recorder, base = wire
    recorder.answers = [(401, {"error": "bad key"})]

    with pytest.raises(ProviderError):
        OpenAICompatibleProvider(
            base_url=f"{base}/v1",
            transport=RetryingTransport(UrllibTransport(), attempts=3, sleep=lambda _: None),
        ).complete([Message(role=Role.USER, content="hi")], model="local")

    assert len(recorder.requests) == 1


# ---------------------------------------------------------------- the Anthropic wire


def test_the_anthropic_wire_puts_system_at_the_top_level(wire) -> None:
    recorder, base = wire
    recorder.answers = [
        (
            200,
            {
                "id": "msg_1",
                "model": "claude",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "done"}],
                "usage": {"input_tokens": 100, "output_tokens": 12},
            },
        )
    ]

    completion = AnthropicProvider(api_key="sk-ant", base_url=base).complete(
        [
            Message(role=Role.SYSTEM, content="you are careful"),
            Message(role=Role.USER, content="fix it"),
        ],
        model="claude",
    )

    sent = recorder.requests[0]["body"]
    assert sent["system"] == "you are careful"
    assert [t["role"] for t in sent["messages"]] == ["user"]
    assert recorder.requests[0]["headers"]["x-api-key"] == "sk-ant"
    assert completion.text == "done"


def test_anthropic_cache_tokens_are_added_because_they_are_reported_disjointly(wire) -> None:
    """The accounting difference that silently under-reports cost by the cache hit rate.

    On this wire `input_tokens` excludes cache reads and writes; on the OpenAI wire it
    includes them. Forwarding the number unchanged makes every cached run look cheaper than
    it was, in the flattering direction, with nothing to notice it.
    """
    recorder, base = wire
    recorder.answers = [
        (
            200,
            {
                "id": "msg_1",
                "model": "claude",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 900,
                    "cache_creation_input_tokens": 90,
                },
            },
        )
    ]

    usage = (
        AnthropicProvider(api_key="k", base_url=base)
        .complete([Message(role=Role.USER, content="hi")], model="claude")
        .usage
    )

    assert usage.input_tokens == 1000, "cache tokens were dropped from the total"
    assert usage.cached_input_tokens == 900
    usage.check()


def test_anthropic_tool_results_merge_into_one_user_turn(wire) -> None:
    """Structural, not cosmetic: this host refuses consecutive tool-result turns."""
    recorder, base = wire
    recorder.answers = [
        (
            200,
            {
                "id": "m",
                "model": "claude",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )
    ]

    AnthropicProvider(api_key="k", base_url=base).complete(
        [
            Message(role=Role.USER, content="read both"),
            Message(role=Role.TOOL, content="a", tool_call_id="c1"),
            Message(role=Role.TOOL, content="b", tool_call_id="c2"),
        ],
        model="claude",
    )

    turns = recorder.requests[0]["body"]["messages"]
    carrying = [
        t
        for t in turns
        if isinstance(t["content"], list)
        and any(b.get("type") == "tool_result" for b in t["content"])
    ]
    assert len(carrying) == 1, "two tool results went out as two turns"
    assert len(carrying[0]["content"]) == 2


def test_an_anthropic_failure_is_typed_from_its_own_body(wire) -> None:
    recorder, base = wire
    recorder.answers = [(429, {"error": {"type": "rate_limit_error", "message": "slow down"}})]

    with pytest.raises(ProviderError) as caught:
        AnthropicProvider(api_key="k", base_url=base).complete(
            [Message(role=Role.USER, content="hi")], model="claude"
        )

    assert caught.value.retryable is True
    assert caught.value.status == 429
    assert "slow down" in str(caught.value)


# ------------------------------------------------------------------------ the guard


def test_the_transport_refuses_a_non_http_scheme() -> None:
    """A `file:` URL in a config file is either a mistake or an attempt to read local disk."""
    with pytest.raises(ProviderError, match="must be http or https"):
        UrllibTransport().post_json("file:///etc/passwd", headers={}, payload={}, timeout_s=1.0)


# ------------------------------------------------------- the whole factory, over TCP


STAGE_OUTPUTS = [
    {
        "findings": "strip_bom returns its input unchanged",
        "scope": "one function in importer.py",
    },
    {
        "summary": "Stripped the BOM before parsing headers.",
        "claims": ["The importer now strips the BOM."],
    },
    {"verdict": "accept", "findings": []},
    {"summary": "Handed off.", "branch": "factory/bom-headers"},
]

CARRIED = {
    "calibration": {"confidence": 0.8, "evidence": ["importer.py:3"], "unknowns": []},
    "decisions": ["kept the public signature"],
    "attempted": [],
    "constraints": [],
    "artifacts": [],
}


def test_a_whole_work_item_reaches_handoff_over_a_real_socket(wire, tmp_path) -> None:
    """The strongest claim this repository can make without a live model.

    Every other end-to-end run here drives a `StubProvider` -- an object handed to the
    coordinator, which never encodes a request, never opens a socket, and never decodes a
    response. So "the factory works" was a claim about the harness only: the wire format,
    the transport, the HTTP status handling and the response decoding were all outside every
    end-to-end path.

    This drives the same coordinator through `OpenAICompatibleProvider` and `UrllibTransport`
    against a server on loopback answering in the OpenAI wire format. What remains untested
    without a live endpoint is model *quality* -- whether a real model produces good stage
    output -- which is a far smaller and far more honest gap than "the plumbing has never
    run".
    """
    import subprocess

    from software_factory.definition import load_strict
    from software_factory.definition.models import Stage
    from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
    from software_factory.orchestrator.coordinator import local_coordinator
    from software_factory.scaffold import init_factory

    recorder, base = wire
    recorder.answers = [
        (
            200,
            openai_answer(
                choices=[
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({**CARRIED, **output}),
                        },
                    }
                ]
            ),
        )
        for output in STAGE_OUTPUTS
    ]

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "importer.py").write_text("def strip_bom(text):\n    return text\n", encoding="utf-8")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "PATH": __import__("os").environ.get("PATH", ""),
        "HOME": str(tmp_path),
    }
    for args in (("init", "--quiet", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "x")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    factory = tmp_path / "factory"
    init_factory(factory, name="wire", owner="acme", repo="importer")

    item = WorkItem(
        id=new_id(),
        factory="wire",
        title="CSV importer mangles BOM headers",
        request="The BOM is carried into the first header.",
        source=SourceContext(provider="cli", kind="test", ref="wire"),
        work_class=WorkClass.CHORE,
    )

    local_coordinator(
        load_strict(factory),
        repo=repo,
        state_dir=tmp_path / "state",
        provider=OpenAICompatibleProvider(base_url=f"{base}/v1", name="wire-test"),
        allow_unsandboxed=True,
    ).run(item)

    assert item.stage is Stage.HANDOFF, f"blocked at {item.stage}: {item.blocker_action}"
    assert len(recorder.requests) == len(STAGE_OUTPUTS), "a stage did not reach the endpoint"
    # Every request really went over HTTP with a real body, not through an injected object.
    assert all(r["body"]["model"] for r in recorder.requests)
    assert all(r["path"] == "/v1/chat/completions" for r in recorder.requests)


def test_a_provider_failure_is_reported_as_one_not_as_a_gate_failure(wire, tmp_path) -> None:
    """Found by the first live run against a real endpoint, and it is a diagnosis bug.

    The host answered 400 "model does not exist". The run ended `provider_failed`, the
    calibration gate then failed because there was no output to calibrate, and the work item
    was blocked with "Emit the calibration block required by the stage's output schema" --
    advice that sends somebody to rewrite a prompt when the model id in their ladder is
    wrong.

    The blocker was already correct (`external_dependency`); only the action disagreed with
    it, and the action is the sentence an operator acts on.
    """
    import subprocess

    from software_factory.definition import load_strict
    from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
    from software_factory.orchestrator.coordinator import local_coordinator
    from software_factory.orchestrator.workitem import Blocker
    from software_factory.scaffold import init_factory

    recorder, base = wire
    recorder.answers = [(400, {"error": {"message": "modelCode: does not exist"}})]

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "importer.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "PATH": __import__("os").environ.get("PATH", ""),
        "HOME": str(tmp_path),
    }
    for args in (("init", "--quiet", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "x")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    factory = tmp_path / "factory"
    init_factory(factory, name="wire", owner="acme", repo="importer")
    item = WorkItem(
        id=new_id(),
        factory="wire",
        title="anything",
        request="anything",
        source=SourceContext(provider="cli", kind="test", ref="wire"),
        work_class=WorkClass.CHORE,
    )

    local_coordinator(
        load_strict(factory),
        repo=repo,
        state_dir=tmp_path / "state",
        provider=OpenAICompatibleProvider(base_url=f"{base}/v1", name="wire-test"),
        allow_unsandboxed=True,
    ).run(item)

    assert item.blocker is Blocker.EXTERNAL_DEPENDENCY
    assert "does not exist" in (item.blocker_action or ""), item.blocker_action
    assert "calibration" not in (item.blocker_action or "").lower(), (
        "a provider failure was reported as a calibration problem"
    )


def test_a_run_records_the_commit_its_work_sits_on(wire, tmp_path) -> None:
    """`WorkItem.base_commit` was declared and written by nothing.

    So `_gate_context` fell through to a hard-coded `HEAD~1` on every run, which is the
    fallback that broke the keystone gate below.
    """
    import subprocess

    from software_factory.definition import load_strict
    from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
    from software_factory.orchestrator.coordinator import local_coordinator
    from software_factory.scaffold import init_factory

    recorder, base = wire
    recorder.answers = [
        (
            200,
            openai_answer(
                choices=[
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({**CARRIED, **output}),
                        },
                    }
                ]
            ),
        )
        for output in STAGE_OUTPUTS
    ]

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "importer.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "PATH": __import__("os").environ.get("PATH", ""),
        "HOME": str(tmp_path),
    }
    for args in (("init", "--quiet", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "one")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    factory = tmp_path / "factory"
    init_factory(factory, name="wire", owner="acme", repo="importer")
    item = WorkItem(
        id=new_id(),
        factory="wire",
        title="t",
        request="r",
        source=SourceContext(provider="cli", kind="test", ref="wire"),
        work_class=WorkClass.CHORE,
    )

    local_coordinator(
        load_strict(factory),
        repo=repo,
        state_dir=tmp_path / "state",
        provider=OpenAICompatibleProvider(base_url=f"{base}/v1", name="wire-test"),
        allow_unsandboxed=True,
    ).run(item)

    assert item.base_commit, "base_commit is written by nothing"
    assert len(item.base_commit) == 40, "a commit id, not a symbolic ref"


def test_the_parent_of_uncommitted_work_is_head_not_the_commit_before_it(tmp_path) -> None:
    """The second half of the same bug, and the half that broke young repositories.

    A run leaves its changes *uncommitted*, so the parent of the work is `HEAD`. The old
    expression asked for `HEAD~1` — one commit too far back against real history, and
    unresolvable in a repository with a single commit, which is what every new project and
    everything `sf init` leaves behind has.
    """
    import subprocess

    from software_factory.orchestrator.coordinator import _parent_commit

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "PATH": __import__("os").environ.get("PATH", ""),
        "HOME": str(tmp_path),
    }
    for args in (("init", "--quiet", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "one")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    class FakeWorkspace:
        root = repo

    class Blank:
        base_commit = ""

    assert _parent_commit(FakeWorkspace(), Blank()) == head

    class Recorded:
        base_commit = head

    assert _parent_commit(FakeWorkspace(), Recorded()) == head

    class Unresolvable:
        base_commit = "0" * 40

    assert _parent_commit(FakeWorkspace(), Unresolvable()) is None, (
        "an unresolvable commit must be None, not a guess"
    )


def test_the_keystone_gate_is_unenforceable_when_the_parent_is_unknown() -> None:
    """A gate that cannot look must say so rather than report a failure.

    Reporting this as FAIL is what told a real model — which had fixed the defect and
    written two tests — to "write the test first, and watch it fail before you fix
    anything". That is the keystone gate refusing correct work with advice the author had
    already followed, and an empty `new_test_ids` cannot distinguish the two cases on its
    own.
    """
    from software_factory.evals.gates import GateContext, GateOutcome, regression_proven

    cannot_look = regression_proven(
        GateContext(stage="VERIFY", work_class="defect", parent_resolved=False)
    )
    assert cannot_look.outcome is GateOutcome.ERROR
    assert "could not be resolved" in cannot_look.detail

    wrote_none = regression_proven(
        GateContext(stage="VERIFY", work_class="defect", parent_resolved=True)
    )
    assert wrote_none.outcome is GateOutcome.FAIL, "a genuine absence must still fail"


def test_input_and_output_tokens_are_recorded_apart(wire, tmp_path) -> None:
    """The ledger recorded `input + output` under the key `inputTokens`.

    So every reader saw a total labelled as one of its halves, and the other half was
    recorded nowhere. Output tokens are the expensive side on essentially every hosted
    provider — often three to five times the input price — so a cost figure nobody can
    decompose is one nobody can check. Found by reading a real run's ledger and seeing
    output tokens come back empty against a provider that had plainly produced some.
    """
    import subprocess

    from software_factory.definition import load_strict
    from software_factory.ledger import EntryType, Ledger
    from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
    from software_factory.orchestrator.coordinator import local_coordinator
    from software_factory.scaffold import init_factory

    recorder, base = wire
    recorder.answers = [
        (
            200,
            openai_answer(
                usage={"prompt_tokens": 700, "completion_tokens": 200, "total_tokens": 900},
                choices=[
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({**CARRIED, **output}),
                        },
                    }
                ],
            ),
        )
        for output in STAGE_OUTPUTS
    ]

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "PATH": __import__("os").environ.get("PATH", ""),
        "HOME": str(tmp_path),
    }
    for args in (("init", "--quiet", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "x")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    factory = tmp_path / "factory"
    init_factory(factory, name="wire", owner="acme", repo="a")
    item = WorkItem(
        id=new_id(),
        factory="wire",
        title="t",
        request="r",
        source=SourceContext(provider="cli", kind="test", ref="wire"),
        work_class=WorkClass.CHORE,
    )
    state = tmp_path / "state"

    local_coordinator(
        load_strict(factory),
        repo=repo,
        state_dir=state,
        provider=OpenAICompatibleProvider(base_url=f"{base}/v1", name="wire-test"),
        allow_unsandboxed=True,
    ).run(item)

    calls = [e for e in Ledger(state / "ledger.jsonl").read() if e.type is EntryType.MODEL_CALLED]
    assert calls, "no model call was recorded"
    first = calls[0].payload
    assert first["inputTokens"] == 700, "the total is still being recorded as the input half"
    assert first["outputTokens"] == 200
    assert first["totalTokens"] == 900
