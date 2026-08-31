"""The Slack adapter (PRD FR-18.2, FR-18.7, FR-18.8, FR-18.9, FR-18.10).

The first shipped integration, so these tests are as much about the *contract* as about
Slack: an adapter that satisfies the protocol structurally, normalises to a stable id,
refuses forged requests, and never lets a factory talk to itself.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from software_factory.intake.adapters import Adapter, Deduplicator, Health, Reply
from software_factory.intake.events import Provider
from software_factory.integrations.slack import (
    MAX_BODY_CHARS,
    SlackAdapter,
    SlackCredentials,
    SlackError,
    SlackSignatureError,
    challenge_for,
    signature_headers,
    verify_signature,
)
from software_factory.providers.base import ProviderError
from software_factory.providers.transport import Response

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
TOKEN = "xoxb-not-a-real-token-for-tests"


class FakeTransport:
    """Records what was sent and answers what the test says. Opens no socket."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers) or [Response(status=200, body={"ok": True})]
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self, url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout_s: float
    ) -> Response:
        self.calls.append({"url": url, "headers": headers, "payload": payload})
        answer = self.answers[0] if len(self.answers) == 1 else self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def adapter(*answers: Any, channels: tuple[str, ...] = (), bot: str = "U0BOT") -> SlackAdapter:
    return SlackAdapter(
        credentials=SlackCredentials(bot_token=TOKEN, signing_secret=SECRET, bot_user_id=bot),
        channels=frozenset(channels),
        transport=FakeTransport(*answers),
    )


def envelope(**event: Any) -> dict[str, Any]:
    """A Slack Events API envelope with sane defaults, overridable per test."""
    inner = {"type": "app_mention", "user": "U0HUMAN", "text": "hello", "channel": "C0CHAN"}
    inner.update(event)
    return {
        "type": "event_callback",
        "event_id": "Ev0PV52K21",
        "team_id": "T0TEAM",
        "event": inner,
    }


# ------------------------------------------------------------------- request signatures


def test_a_correctly_signed_request_verifies() -> None:
    body = b'{"type":"event_callback"}'
    headers = signature_headers(signing_secret=SECRET, body=body)

    verify_signature(
        signing_secret=SECRET,
        timestamp=headers["X-Slack-Request-Timestamp"],
        body=body,
        signature=headers["X-Slack-Signature"],
    )


def test_a_signature_from_another_secret_is_refused() -> None:
    """The whole point. Anyone who learns the webhook URL can POST to it."""
    body = b'{"type":"event_callback"}'
    headers = signature_headers(signing_secret="someone-elses-secret", body=body)

    with pytest.raises(SlackSignatureError, match="does not match"):
        verify_signature(
            signing_secret=SECRET,
            timestamp=headers["X-Slack-Request-Timestamp"],
            body=body,
            signature=headers["X-Slack-Signature"],
        )


def test_a_tampered_body_is_refused() -> None:
    body = b'{"text":"deploy to staging"}'
    headers = signature_headers(signing_secret=SECRET, body=body)

    with pytest.raises(SlackSignatureError):
        verify_signature(
            signing_secret=SECRET,
            timestamp=headers["X-Slack-Request-Timestamp"],
            body=b'{"text":"deploy to production"}',
            signature=headers["X-Slack-Signature"],
        )


def test_verification_is_over_raw_bytes_not_reparsed_json() -> None:
    """Re-serialising a parsed body changes whitespace and key order.

    A receiver that verifies `json.dumps(json.loads(body))` rejects every genuine Slack
    request, and the failure looks exactly like a wrong secret -- so the usual fix is to
    stop verifying.
    """
    body = b'{"type":"event_callback",  "event_id":"Ev1"}'
    headers = signature_headers(signing_secret=SECRET, body=body)
    reserialised = json.dumps(json.loads(body)).encode()

    verify_signature(
        signing_secret=SECRET,
        timestamp=headers["X-Slack-Request-Timestamp"],
        body=body,
        signature=headers["X-Slack-Signature"],
    )
    with pytest.raises(SlackSignatureError):
        verify_signature(
            signing_secret=SECRET,
            timestamp=headers["X-Slack-Request-Timestamp"],
            body=reserialised,
            signature=headers["X-Slack-Signature"],
        )


def test_a_replayed_request_is_refused() -> None:
    body = b"{}"
    old = time.time() - 3600
    headers = signature_headers(signing_secret=SECRET, body=body, at=old)

    with pytest.raises(SlackSignatureError, match="replay window"):
        verify_signature(
            signing_secret=SECRET,
            timestamp=headers["X-Slack-Request-Timestamp"],
            body=body,
            signature=headers["X-Slack-Signature"],
        )


def test_a_request_from_the_future_is_refused() -> None:
    """A one-sided window is passed by setting the clock forward."""
    body = b"{}"
    headers = signature_headers(signing_secret=SECRET, body=body, at=time.time() + 3600)

    with pytest.raises(SlackSignatureError, match="replay window"):
        verify_signature(
            signing_secret=SECRET,
            timestamp=headers["X-Slack-Request-Timestamp"],
            body=body,
            signature=headers["X-Slack-Signature"],
        )


def test_verification_without_a_secret_refuses_rather_than_passing() -> None:
    """An unconfigured verifier must not be a verifier that accepts everything."""
    with pytest.raises(SlackSignatureError, match="no signing secret"):
        verify_signature(signing_secret="", timestamp="1", body=b"{}", signature="v0=x")


def test_a_non_numeric_timestamp_is_refused() -> None:
    with pytest.raises(SlackSignatureError, match="not an integer"):
        verify_signature(
            signing_secret=SECRET, timestamp="not-a-time", body=b"{}", signature="v0=x"
        )


def test_the_url_verification_challenge_is_echoed_and_starts_no_work() -> None:
    raw = {"type": "url_verification", "challenge": "3eZbrw1aB"}

    assert challenge_for(raw) == "3eZbrw1aB"
    assert adapter().normalise(raw) is None, "re-saving the app config filed a work item"
    assert challenge_for(envelope()) is None


# ------------------------------------------------------------------------- the loop guard


@pytest.mark.parametrize(
    ("field", "value"),
    [("bot_id", "B0APP"), ("subtype", "bot_message"), ("user", "U0BOT")],
)
def test_the_factory_never_reads_its_own_messages(field: str, value: str) -> None:
    """A factory that replies in a channel it reads will reply to itself forever.

    Three signals because each alone has a hole: `bot_id` is absent when an app posts with
    a user token, `subtype` is absent on `app_mention`, and the id comparison needs an id
    `authenticate()` may not have learned.
    """
    assert adapter().normalise(envelope(**{field: value})) is None


def test_a_human_message_in_the_same_channel_still_arrives() -> None:
    """The guard must not be so wide that it drops the work."""
    event = adapter(channels=("C0CHAN",)).normalise(envelope(type="message", text="fix the csv"))

    assert event is not None
    assert event.author == "U0HUMAN"


# --------------------------------------------------------------------------- normalising


def test_the_event_id_is_slacks_own_so_a_retry_is_one_work_item() -> None:
    """Slack retries anything it did not see a 2xx for, up to three times.

    An id minted from the clock makes a slow first run into three runs, three costs and
    three pull requests.
    """
    raw = envelope()
    first = adapter().normalise(raw)
    time.sleep(0.01)
    retry = adapter().normalise({**raw, "_retry_num": 1})

    assert first is not None and retry is not None
    assert first.id == retry.id == "Ev0PV52K21"

    dedupe = Deduplicator()
    assert not dedupe.seen(first)
    dedupe.record(first)
    assert dedupe.seen(retry), "a redelivery would have started a second work item"


def test_an_envelope_with_no_event_id_is_refused_loudly() -> None:
    """Malformed, not uninteresting. Substituting a timestamp is the bug this prevents."""
    raw = envelope()
    del raw["event_id"]

    with pytest.raises(SlackError, match="redelivery"):
        adapter().normalise(raw)


def test_a_plain_message_outside_the_allow_list_is_ignored() -> None:
    """Reading every message in a workspace is a decision an operator makes on purpose."""
    assert adapter().normalise(envelope(type="message", text="lunch?")) is None


def test_a_mention_is_a_request_wherever_it_happens() -> None:
    """Being @-mentioned is unambiguous; it does not need an allow-list."""
    assert adapter().normalise(envelope(type="app_mention")) is not None


@pytest.mark.parametrize("subtype", ["message_changed", "channel_join", "message_deleted"])
def test_channel_bookkeeping_is_not_a_request(subtype: str) -> None:
    """An edit is not a new request; correcting a typo must not start a second run."""
    assert (
        adapter(channels=("C0CHAN",)).normalise(envelope(type="message", subtype=subtype)) is None
    )


def test_the_leading_mention_is_stripped_from_the_request() -> None:
    """A work item titled `<@U0BOT> fix the importer` leads with an opaque id."""
    event = adapter().normalise(envelope(text="<@U0BOT> the CSV importer mangles BOM headers"))

    assert event is not None
    assert event.title == "the CSV importer mangles BOM headers"
    assert not event.body.startswith("<@")


def test_a_reply_address_is_the_thread_and_backpressure_counts_the_channel() -> None:
    """Distinct on purpose (FR-26.3).

    `ref` is where a reply goes and is per-thread; `source` is what a rate limit counts, and
    one channel emitting a thousand threads is the failure a per-thread bucket cannot see.
    """
    event = adapter().normalise(envelope(ts="1700000000.000100", thread_ts="1699999999.000100"))

    assert event is not None
    assert event.origin.thread == "1699999999.000100"
    assert event.origin.ref == "C0CHAN/1699999999.000100"
    assert event.origin.source_key == "C0CHAN"


def test_a_message_with_no_thread_replies_into_its_own_thread() -> None:
    event = adapter().normalise(envelope(ts="1700000000.000100"))

    assert event is not None
    assert event.origin.thread == "1700000000.000100"


def test_a_very_long_message_is_carried_truncated_and_says_so() -> None:
    """A pasted 400 KB log becomes the pack an agent reasons over."""
    event = adapter().normalise(envelope(text="x" * (MAX_BODY_CHARS + 5_000)))

    assert event is not None
    assert len(event.body) == MAX_BODY_CHARS
    assert event.attributes["truncated"] is True


def test_mentions_are_an_attribute_so_filters_can_match_them() -> None:
    event = adapter().normalise(envelope(text="<@U0BOT> ping <@U0ONCALL|carla> and <!here>"))

    assert event is not None
    assert event.attributes["mentions"] == ["U0BOT", "U0ONCALL"]


def test_a_permalink_is_left_empty_rather_than_guessed() -> None:
    """A link that 404s teaches a reviewer not to follow the next one."""
    event = adapter().normalise(envelope())
    assert event is not None
    assert event.origin.url == ""

    known = SlackAdapter(
        credentials=SlackCredentials(
            bot_token=TOKEN, signing_secret=SECRET, bot_user_id="U0BOT", team_domain="acme"
        ),
        transport=FakeTransport(),
    )
    linked = known.normalise(envelope(ts="1700000000.000100"))
    assert linked is not None
    assert linked.origin.url == ("https://acme.slack.com/archives/C0CHAN/p1700000000000100")


def test_identity_is_the_user_id_not_the_display_name() -> None:
    """Display names are chosen by their owner and can be changed to another person's."""
    assert adapter().resolve_identity(envelope(user="U0HUMAN")) == "U0HUMAN"


# -------------------------------------------------------------------------------- replies


def test_a_reply_lands_in_the_originating_thread() -> None:
    """A question asked in a thread and answered in the channel is a question nobody sees."""
    slack = adapter(channels=("C0CHAN",))
    event = slack.normalise(envelope(ts="1700000000.000100"))
    assert event is not None

    assert slack.reply(event, Reply(body="on it", kind="status")) is True
    sent = slack.transport.calls[-1]["payload"]
    assert sent["channel"] == "C0CHAN"
    assert sent["thread_ts"] == "1700000000.000100"


def test_slack_refusing_with_http_200_is_a_failed_reply() -> None:
    """The Web API answers 200 with `ok: false`.

    Code that checks the status code alone reports every failure as a success, and FR-18.8's
    "post back to where the work came from" silently becomes "post nowhere".
    """
    slack = adapter(
        Response(status=200, body={"ok": False, "error": "channel_not_found"}),
        channels=("C0CHAN",),
    )
    event = slack.normalise(envelope())
    assert event is not None

    assert slack.reply(event, Reply(body="on it")) is False


def test_a_question_is_marked_so_it_is_not_buried_in_a_status_feed() -> None:
    slack = adapter(channels=("C0CHAN",))
    event = slack.normalise(envelope())
    assert event is not None

    slack.reply(event, Reply(body="Which branch?", kind="question"))
    assert "Question" in slack.transport.calls[-1]["payload"]["text"]


def test_replies_never_unfurl_links() -> None:
    """An unfurled URL is a request this factory made to a host it did not choose."""
    slack = adapter(channels=("C0CHAN",))
    event = slack.normalise(envelope())
    assert event is not None

    slack.reply(event, Reply(body="see http://example.test/x"))
    sent = slack.transport.calls[-1]["payload"]
    assert sent["unfurl_links"] is False
    assert sent["unfurl_media"] is False


# --------------------------------------------------------------------------------- health


def test_a_working_adapter_is_healthy() -> None:
    assert adapter().health().status is Health.HEALTHY


def test_rate_limiting_is_degraded_not_unavailable() -> None:
    """Events still arrive and work continues. Parking every item for a condition that
    clears in seconds is worse than saying it is degraded."""
    report = adapter(ProviderError("slack.com returned 429", retryable=True, status=429)).health()

    assert report.status is Health.DEGRADED
    assert report.retry_after is not None


def test_an_unreachable_slack_is_unavailable_with_a_reason() -> None:
    """FR-18.9's "park with the reason" is not satisfiable by a status with no reason."""
    report = adapter(ProviderError("cannot reach slack.com: timed out", retryable=True)).health()

    assert report.status is Health.UNAVAILABLE
    assert report.detail
    assert report.provider is Provider.CHAT


def test_a_revoked_token_is_unavailable() -> None:
    report = adapter(Response(status=200, body={"ok": False, "error": "token_revoked"})).health()

    assert report.status is Health.UNAVAILABLE
    assert "token_revoked" in report.detail


def test_not_knowing_our_own_user_id_is_degraded() -> None:
    """Authenticated, but the loop guard is missing its strongest signal."""
    slack = adapter(bot="")

    report = slack.health()

    assert report.status is Health.DEGRADED
    assert "own user id" in report.detail


def test_authenticate_learns_this_apps_user_id() -> None:
    slack = adapter(Response(status=200, body={"ok": True, "user_id": "U0LEARNED"}), bot="")

    assert slack.authenticate() is True
    assert slack.credentials.bot_user_id == "U0LEARNED"


def test_authenticate_returns_false_rather_than_stopping_the_factory_booting() -> None:
    assert adapter(ProviderError("cannot reach slack.com", retryable=True)).authenticate() is False


# ------------------------------------------------------------------------------- secrets


def test_credentials_never_print_their_secrets() -> None:
    """A dataclass repr containing a bot token reaches a log the first time anything
    formats an exception."""
    text = repr(SlackCredentials(bot_token=TOKEN, signing_secret=SECRET))

    assert TOKEN not in text
    assert SECRET not in text


def test_a_slack_refusal_does_not_quote_the_token() -> None:
    slack = adapter(Response(status=200, body={"ok": False, "error": "invalid_auth"}))

    assert TOKEN not in str(slack.health().detail)


def test_signing_without_a_secret_is_refused() -> None:
    """An empty key produces a perfectly valid HMAC that nothing else can reproduce.

    A signer that shrugs at a missing secret emits signatures no verifier accepts, and the
    usual response to that is to stop verifying.
    """
    with pytest.raises(SlackSignatureError, match="without a signing secret"):
        signature_headers(signing_secret="", body=b"{}")


def test_reaching_slack_without_a_token_is_refused() -> None:
    """Checked where it is used, not at construction: replaying a saved delivery offline
    needs no workspace credential, and demanding one there makes the local path need the
    network it exists to avoid."""
    slack = SlackAdapter(
        credentials=SlackCredentials(bot_token="", signing_secret=SECRET),
        transport=FakeTransport(),
    )

    assert slack.authenticate() is False
    assert slack.health().status is Health.UNAVAILABLE


def test_normalising_a_saved_delivery_needs_no_credentials_at_all() -> None:
    """FR-18.10's local parity. This is the offline path, and it must stay offline."""
    slack = SlackAdapter(
        credentials=SlackCredentials(bot_token="", signing_secret=""),
        transport=FakeTransport(),
    )

    event = slack.normalise(envelope())

    assert event is not None
    assert slack.transport.calls == [], "the offline path reached the network"


def test_the_token_travels_in_a_header_not_a_url() -> None:
    slack = adapter()
    slack.health()

    call = slack.transport.calls[-1]
    assert TOKEN not in call["url"]
    assert call["headers"]["authorization"] == f"Bearer {TOKEN}"


# ------------------------------------------------------------------------- the contract


def test_the_adapter_satisfies_the_protocol_without_importing_it() -> None:
    """FR-18.2: adding an integration must not touch orchestration code, which only holds
    if the orchestrator's whole view of a provider is the protocol."""
    assert isinstance(adapter(), Adapter)
    assert adapter().provider is Provider.CHAT


def test_subscribe_is_idempotent_and_makes_no_network_call() -> None:
    """Slack subscriptions live in the app manifest. A `subscribe` that looked like it
    registered something while doing nothing would be a method whose value is a false
    impression."""
    slack = adapter()
    slack.subscribe(["app_mention"])
    slack.subscribe(["app_mention", "message"])

    assert slack.subscribed == frozenset({"app_mention", "message"})
    assert slack.transport.calls == []


# ------------------------------------------------------------------ the CLI, end to end
#
# An adapter reachable from no command is the failure this codebase keeps finding in
# itself. These check the wiring, not the adapter.


@pytest.fixture
def factory(tmp_path):
    from software_factory.scaffold import init_factory

    init_factory(tmp_path / "f", name="payments", owner="acme", repo="payments-service")
    return tmp_path / "f"


def run_cli(argv: list[str], env: dict[str, str] | None = None):
    from typer.testing import CliRunner

    from software_factory.cli import app

    return CliRunner().invoke(app, argv, env=env or {})


def test_sf_chat_receive_puts_a_real_envelope_through_intake(factory, tmp_path) -> None:
    """FR-18.10: every capability reachable through an integration is reachable through `sf`.

    A fully local factory loses convenience and nothing else -- and this path opens no
    socket, so it works with the network denied.
    """
    saved = tmp_path / "event.json"
    saved.write_text(json.dumps(envelope(text="<@U0BOT> the importer mangles BOM headers")))

    result = run_cli(["chat", "receive", str(saved), "--root", str(factory), "--json"])

    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["event"]["id"] == "Ev0PV52K21"
    assert body["event"]["title"] == "the importer mangles BOM headers"
    assert body["event"]["backpressureSource"] == "C0CHAN"


def test_sf_chat_receive_echoes_the_url_verification_challenge(factory, tmp_path) -> None:
    saved = tmp_path / "event.json"
    saved.write_text(json.dumps({"type": "url_verification", "challenge": "3eZbrw1aB"}))

    result = run_cli(["chat", "receive", str(saved), "--root", str(factory), "--json"])

    body = json.loads(result.stdout)
    assert body["challenge"] == "3eZbrw1aB"
    assert body["startedWork"] is False


def test_sf_chat_receive_ignores_the_factorys_own_message(factory, tmp_path) -> None:
    saved = tmp_path / "event.json"
    saved.write_text(json.dumps(envelope(bot_id="B0APP")))

    result = run_cli(["chat", "receive", str(saved), "--root", str(factory), "--json"])

    assert json.loads(result.stdout)["ignored"] is True


def test_sf_chat_sign_and_verify_agree(tmp_path) -> None:
    """The signing helper and the verifier are the two halves of one control.

    Shipping them separately is how a receiver ends up verifying against a basestring
    nobody produces.
    """
    body = tmp_path / "body.json"
    body.write_bytes(json.dumps(envelope()).encode())
    env = {"SF_SLACK_SIGNING_SECRET": SECRET}

    signed = run_cli(["chat", "sign", str(body), "--json"], env=env)
    headers = json.loads(signed.stdout)["headers"]

    verified = run_cli(
        [
            "chat",
            "verify",
            str(body),
            "--timestamp",
            headers["X-Slack-Request-Timestamp"],
            "--signature",
            headers["X-Slack-Signature"],
            "--json",
        ],
        env=env,
    )

    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.stdout)["verified"] is True


def test_sf_chat_verify_refuses_a_forged_delivery(tmp_path) -> None:
    body = tmp_path / "body.json"
    body.write_bytes(b'{"type":"event_callback"}')
    forged = signature_headers(signing_secret="someone-elses-secret", body=body.read_bytes())

    result = run_cli(
        [
            "chat",
            "verify",
            str(body),
            "--timestamp",
            forged["X-Slack-Request-Timestamp"],
            "--signature",
            forged["X-Slack-Signature"],
        ],
        env={"SF_SLACK_SIGNING_SECRET": SECRET},
    )

    assert result.exit_code != 0


def test_no_command_takes_a_secret_as_a_flag() -> None:
    """A token passed as `--token xoxb-...` is visible to every process through `ps`.

    This codebase already found that exact leak in the container executor, with a test
    asserting it as the requirement. Once is enough.
    """
    from typer.testing import CliRunner

    from software_factory.cli import app

    runner = CliRunner()
    for argv in (["chat", "verify", "--help"], ["chat", "health", "--help"], ["chat", "--help"]):
        output = runner.invoke(app, argv).output
        assert "--token" not in output, argv
        assert "--secret" not in output, argv
