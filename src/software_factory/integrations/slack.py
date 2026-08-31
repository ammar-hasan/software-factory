"""A chat adapter for Slack (PRD FR-18.2, FR-18.7, FR-18.8, FR-18.9, FR-18.10).

The first shipped integration, and it exists to prove the adapter contract is a contract
rather than a description: everything below satisfies `intake.adapters.Adapter`
structurally, imports nothing from the orchestrator, and adds no orchestration code.

Four decisions here are load-bearing, and each of them is a way this goes wrong in
production rather than in a test:

**The signature check raises; it does not return a boolean.** A function that returns
`False` for "this request was not signed by Slack" can be called and its result dropped,
and a receiver that forgets one `if` accepts forged events from anyone who learns the URL.
This codebase's most-repeated finding is a control that existed and was not wired in, so
the control here is not skippable by omission -- you get the verified body or you get an
exception.

**Redelivery is keyed on Slack's `event_id`, never on a timestamp.** Slack retries any
delivery it did not see a 2xx for, up to three times, with `X-Slack-Retry-Num` set. An
adapter that mints an id from `event_ts` or from the receipt time turns each retry into a
second work item -- so a slow first run becomes three runs, three costs, and three pull
requests. An envelope with no `event_id` is malformed rather than uninteresting, and is
refused loudly instead of being given a synthetic one.

**Bot messages are dropped before anything else.** A factory that replies in a channel it
also reads will read its own reply, treat it as a new request, and reply again. This is the
classic chat-integration outage, it costs money for as long as it runs, and the only
reliable place to stop it is at the front of `normalise`.

**A Slack API call that "succeeds" has to be read.** The Web API answers HTTP 200 with
`{"ok": false, "error": "channel_not_found"}`. Code that checks the status code alone
reports every failure as a success -- which for `reply` means FR-18.8's "post back to where
the work came from" silently becomes "post nowhere", and a question nobody received is a
checkpoint that times out for the wrong reason.

Nothing here holds a socket open. Slack's Events API is a webhook, so the receiving side is
whatever the operator already runs; this module verifies, normalises, replies and reports
health, and `sf chat receive` puts a saved envelope through the same path with no network at
all, which is what FR-18.10's local parity asks for.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from software_factory.errors import ErrorCode, FactoryError
from software_factory.intake.adapters import Health, HealthReport, Reply
from software_factory.intake.events import FactoryEvent, Origin, Provider
from software_factory.memory.records import utc_now
from software_factory.providers.base import ProviderError
from software_factory.providers.transport import (
    Response,
    Transport,
    UrllibTransport,
)

#: Slack's Web API. Configurable on the adapter so a test can point somewhere inert, but
#: never defaulted to anything else: a chat adapter whose endpoint comes from an event is a
#: chat adapter that can be told where to send its token.
SLACK_API = "https://slack.com/api"

#: The only signature version Slack has ever issued. Pinned rather than read from the
#: header, because accepting whatever version the *caller* names is how a downgrade works.
SIGNATURE_VERSION = "v0"

#: How far a request's timestamp may be from ours. Slack's own guidance is five minutes,
#: and the window is what stops a captured request being replayed tomorrow.
MAX_CLOCK_SKEW = timedelta(minutes=5)

#: Message text longer than this is carried truncated, with the truncation recorded.
#:
#: Not a display limit -- a pasted 400 KB log becomes the pack an agent reasons over, and
#: an intake path with no ceiling is a spend path with no ceiling. The full text stays in
#: Slack, which is where the person who pasted it will look for it.
MAX_BODY_CHARS = 8_000

#: Message subtypes that are edits, joins, pins and other channel bookkeeping.
#:
#: An edit is not a new request. Accepting `message_changed` means correcting a typo starts
#: a second run on the same sentence, and the two runs disagree about which text is real.
IGNORED_SUBTYPES = frozenset(
    {
        "message_changed",
        "message_deleted",
        "message_replied",
        "channel_join",
        "channel_leave",
        "channel_topic",
        "channel_purpose",
        "channel_name",
        "channel_archive",
        "channel_unarchive",
        "bot_message",
        "thread_broadcast",
        "file_share",
        "tombstone",
    }
)

#: The inner event types this adapter understands. Everything else normalises to `None`,
#: which the contract calls a normal answer: a workspace emits far more than it asks for.
HANDLED_EVENTS = frozenset({"app_mention", "message"})


class SlackError(FactoryError):
    """Slack refused, or answered something this adapter will not act on."""

    code = ErrorCode.INTEGRATION_NOT_CONFIGURED


class SlackSignatureError(SlackError):
    """A request did not carry a signature this workspace's secret produces.

    Its own class because the receiver's response differs: a signature failure is a 401 and
    is never retried, while an unconfigured integration is a 503 that should be.
    """

    code = ErrorCode.NOT_AUTHORIZED


@dataclass(frozen=True)
class SlackCredentials:
    """What the adapter needs, and the two values that must never be printed.

    `__repr__` is written rather than inherited. A dataclass repr containing a bot token
    reaches a log the first time anything formats an exception, and the codebase already
    found this exact class of leak in the container executor, where secret *values* went on
    a command line visible to `ps`.
    """

    bot_token: str
    signing_secret: str
    bot_user_id: str = ""
    """This app's own user id. Filled by `authenticate()` when not supplied, and used to
    drop the factory's own messages. Without it the loop guard rests on `bot_id` alone,
    which a user-token post does not carry."""

    team_domain: str = ""
    """The workspace subdomain, used to build permalinks. Empty by default and left empty
    rather than guessed: a fabricated permalink that 404s is worse than no permalink,
    because a reviewer follows it once and stops trusting the others."""

    def __repr__(self) -> str:
        return (
            f"SlackCredentials(bot_token='***', signing_secret='***', "
            f"bot_user_id={self.bot_user_id!r}, team_domain={self.team_domain!r})"
        )

    def check(self) -> None:
        """Refuse a half-configured adapter at construction rather than at first event.

        An adapter with no signing secret verifies nothing, and one that is only discovered
        to be misconfigured when a real event arrives is discovered by the event.
        """
        if not self.bot_token.strip():
            raise SlackError(
                "a Slack adapter needs a bot token",
                remediation="Set the bot token from the app's OAuth page (it starts `xoxb-`).",
            )
        if not self.signing_secret.strip():
            raise SlackError(
                "a Slack adapter needs a signing secret",
                remediation=(
                    "Copy the signing secret from the Slack app's Basic Information page. "
                    "Without it, request signatures cannot be verified and any caller who "
                    "learns the webhook URL can start work in this factory."
                ),
            )


def verify_signature(
    *,
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    now: datetime | None = None,
    max_skew: timedelta = MAX_CLOCK_SKEW,
) -> None:
    """Verify one Slack request, or raise.

    Raising rather than returning is the point -- see the module docstring. Compared with
    `hmac.compare_digest` so the comparison does not leak the correct prefix through timing,
    and over the **raw body bytes**, because re-serialising parsed JSON changes whitespace
    and key order and produces a different digest for the same request.
    """
    if not signing_secret:
        raise SlackSignatureError(
            "no signing secret is configured, so no request can be verified",
            remediation="Configure the Slack app's signing secret before accepting events.",
        )
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        raise SlackSignatureError(
            f"request timestamp {timestamp!r} is not an integer",
            remediation="Pass the `X-Slack-Request-Timestamp` header value unchanged.",
        ) from None

    reference = (now or utc_now()).timestamp()
    if abs(reference - sent_at) > max_skew.total_seconds():
        # Both directions. A request from the future is as suspicious as an old one, and a
        # one-sided check is passed by setting the clock forward.
        raise SlackSignatureError(
            f"request timestamp is {abs(reference - sent_at):.0f}s away from now, "
            f"outside the {int(max_skew.total_seconds())}s replay window",
            remediation=(
                "Check this host's clock. If it is right, the request is a replay and "
                "should be refused."
            ),
        )

    basestring = b"%s:%s:%s" % (SIGNATURE_VERSION.encode(), timestamp.encode(), body)
    expected = (
        f"{SIGNATURE_VERSION}="
        f"{hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()}"
    )
    if not hmac.compare_digest(expected, signature or ""):
        raise SlackSignatureError(
            "request signature does not match this workspace's signing secret",
            remediation=(
                "Confirm the signing secret matches the Slack app sending these events. "
                "If it does, refuse the request: it was not signed by Slack."
            ),
        )


def challenge_for(raw: dict[str, Any]) -> str | None:
    """The challenge string Slack expects back, or `None` for any other envelope.

    Slack verifies a new Events API URL by posting a `url_verification` envelope and
    requiring its `challenge` echoed. Kept separate from `normalise` because it is not an
    event: it starts no work, and an adapter that returned it as one would file a work item
    every time somebody re-saved the app configuration.
    """
    if raw.get("type") != "url_verification":
        return None
    challenge = raw.get("challenge")
    return challenge if isinstance(challenge, str) and challenge else None


@dataclass(slots=True)
class SlackAdapter:
    """Slack as a `chat` provider.

    `channels` is an allow-list for plain messages and does not restrict mentions: being
    @-mentioned is an unambiguous request to this factory wherever it happens, while
    reading every message in a channel is a decision an operator has to make on purpose.
    An empty list therefore means "mentions only", which is the safe default and the one
    that costs nothing to run.
    """

    credentials: SlackCredentials
    channels: frozenset[str] = frozenset()
    transport: Transport = field(default_factory=UrllibTransport)
    api_base: str = SLACK_API
    timeout_s: float = 30.0

    provider: Provider = field(default=Provider.CHAT, init=False)
    subscribed: frozenset[str] = field(default=frozenset(), init=False)
    _last_error: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.credentials.check()

    # ------------------------------------------------------------------ the six methods

    def authenticate(self) -> bool:
        """Confirm the token works and learn this app's own user id.

        `False` rather than raising, per the contract: Slack being unreachable at startup
        is normal and must not stop the factory booting. The bot user id learned here is
        what the loop guard needs, so a factory that could not authenticate is also a
        factory that must not post -- which `health()` then reports.
        """
        try:
            response = self._call("auth.test", {})
        except (SlackError, ProviderError) as exc:
            self._last_error = str(exc)
            return False
        user_id = str(response.body.get("user_id") or "")
        if user_id and not self.credentials.bot_user_id:
            object.__setattr__(self.credentials, "bot_user_id", user_id)
        self._last_error = ""
        return True

    def subscribe(self, events: Iterable[str]) -> None:
        """Record which events this adapter will act on. Idempotent.

        This makes **no network call**, and saying so matters more than the method does.
        Slack event subscriptions live in the app manifest and are changed by an
        administrator in the app configuration, not at runtime by a client. A `subscribe`
        that quietly did nothing while looking like it registered something would be a
        method whose whole value is a false impression.
        """
        self.subscribed = frozenset(self.subscribed | {str(e) for e in events})

    def normalise(self, raw: dict[str, Any]) -> FactoryEvent | None:
        """Turn a Slack Events API envelope into a factory event, or `None` to ignore it."""
        if raw.get("type") == "url_verification":
            # Not an event. `challenge_for` handles it; returning `None` here keeps a
            # re-saved app configuration from filing a work item.
            return None
        if raw.get("type") != "event_callback":
            return None

        inner = raw.get("event")
        if not isinstance(inner, dict):
            return None
        kind = str(inner.get("type", ""))
        if kind not in HANDLED_EVENTS:
            return None

        if self._is_own_or_bot(inner):
            # First, before anything else can spend on it. See the module docstring.
            return None

        subtype = str(inner.get("subtype") or "")
        if subtype in IGNORED_SUBTYPES:
            return None

        channel = str(inner.get("channel") or "")
        if kind == "message" and channel not in self.channels:
            # Mentions are requests wherever they happen; plain messages are only requests
            # in channels an operator has opted in.
            return None

        event_id = str(raw.get("event_id") or "")
        if not event_id:
            raise SlackError(
                "an event_callback arrived with no event_id, so redelivery cannot be detected",
                remediation=(
                    "Refuse this delivery. Do not substitute a timestamp: Slack retries any "
                    "delivery it did not see a 2xx for, and an id derived from the clock "
                    "makes each retry a second work item."
                ),
            )

        text = self._strip_leading_mention(str(inner.get("text") or ""))
        truncated = len(text) > MAX_BODY_CHARS
        body = text[:MAX_BODY_CHARS]
        ts = str(inner.get("ts") or "")
        thread_ts = str(inner.get("thread_ts") or ts)

        return FactoryEvent(
            id=event_id,
            provider=Provider.CHAT,
            event=f"slack.{kind}",
            origin=Origin(
                provider=Provider.CHAT,
                # A reply address, per-thread: replying to the channel would answer a
                # question asked in a thread somewhere nobody following it will see.
                ref=f"{channel}/{thread_ts}" if thread_ts else channel,
                thread=thread_ts,
                url=self._permalink(channel, ts),
                # Backpressure counts against the channel, not the thread. One channel
                # emitting a thousand threads is the failure FR-26.3 names, and a
                # per-thread bucket cannot see it.
                source=channel,
            ),
            title=_first_line(body),
            body=body,
            author=str(inner.get("user") or ""),
            attributes={
                "channel": channel,
                "channelType": str(inner.get("channel_type") or ""),
                "team": str(raw.get("team_id") or ""),
                "threadTs": thread_ts,
                "ts": ts,
                "eventType": kind,
                "isThreadReply": bool(inner.get("thread_ts")) and inner.get("thread_ts") != ts,
                "mentions": _mentions(str(inner.get("text") or "")),
                "hasFiles": bool(inner.get("files")),
                "truncated": truncated,
                "retryNum": int(raw.get("_retry_num") or 0),
            },
        )

    def resolve_identity(self, raw: dict[str, Any]) -> str:
        """The Slack user id of whoever caused this.

        The id (`U012ABC`), never the display name. Display names are chosen by their owner
        and can be changed to another person's, so a directory keyed on them is a directory
        anyone in the workspace can rewrite.
        """
        inner = raw.get("event")
        if isinstance(inner, dict):
            return str(inner.get("user") or "")
        return str(raw.get("user") or "")

    def reply(self, event: FactoryEvent, reply: Reply) -> bool:
        """Post back into the originating thread (FR-18.8).

        Returns `False` when Slack refused, which it does with HTTP 200 and `ok: false` --
        the reason `_call` reads the body rather than the status.
        """
        channel = str(event.attributes.get("channel") or "")
        thread_ts = str(event.attributes.get("threadTs") or event.origin.thread or "")
        if not channel:
            self._last_error = "the event carries no channel, so there is nowhere to reply"
            return False
        payload: dict[str, Any] = {
            "channel": channel,
            "text": _render(reply),
            # Never let Slack render a link preview for text a model wrote: an unfurled URL
            # is a request this factory made to a host it did not choose.
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts
        try:
            self._call("chat.postMessage", payload)
        except (SlackError, ProviderError) as exc:
            self._last_error = str(exc)
            return False
        self._last_error = ""
        return True

    def health(self) -> HealthReport:
        """Ask Slack whether this token still works.

        Rate limiting is `DEGRADED`, not `UNAVAILABLE`: events still arrive and work
        continues, with the degradation stated. Treating a 429 as down would park every
        affected work item for a condition that clears itself in seconds.
        """
        now = utc_now()
        try:
            self._call("auth.test", {})
        except ProviderError as exc:
            if exc.status == 429:
                return HealthReport(
                    provider=Provider.CHAT,
                    status=Health.DEGRADED,
                    detail=f"Slack is rate limiting this app: {exc}",
                    checked_at=now,
                    retry_after=timedelta(seconds=30),
                )
            return HealthReport(
                provider=Provider.CHAT,
                status=Health.UNAVAILABLE,
                detail=f"Slack is unreachable: {exc}",
                checked_at=now,
            )
        except SlackError as exc:
            return HealthReport(
                provider=Provider.CHAT,
                status=Health.UNAVAILABLE,
                detail=f"Slack refused this token: {exc}",
                checked_at=now,
            )
        if not self.credentials.bot_user_id:
            # Authenticated, but the loop guard is missing its strongest signal. Working,
            # and not fully -- which is exactly what DEGRADED is for.
            return HealthReport(
                provider=Provider.CHAT,
                status=Health.DEGRADED,
                detail=(
                    "authenticated, but this app's own user id is unknown, so the guard "
                    "against replying to its own messages rests on `bot_id` alone"
                ),
                checked_at=now,
            )
        return HealthReport(provider=Provider.CHAT, status=Health.HEALTHY, checked_at=now)

    # ------------------------------------------------------------------------ internals

    def _call(self, method: str, payload: dict[str, Any]) -> Response:
        """One Slack Web API call, with `ok: false` treated as the failure it is."""
        response = self.transport.post_json(
            f"{self.api_base.rstrip('/')}/{method}",
            headers={
                "authorization": f"Bearer {self.credentials.bot_token}",
                "content-type": "application/json; charset=utf-8",
            },
            payload=payload,
            timeout_s=self.timeout_s,
        )
        if not response.body.get("ok"):
            error = str(response.body.get("error") or "unknown_error")
            raise SlackError(
                f"Slack refused {method}: {error}",
                remediation=_REMEDIATIONS.get(
                    error,
                    f"See Slack's documentation for `{method}` and the error `{error}`.",
                ),
            )
        return response

    def _is_own_or_bot(self, inner: dict[str, Any]) -> bool:
        """Three independent signals, because each alone has a hole.

        `bot_id` is absent when an app posts with a user token; `subtype` is absent for
        `app_mention`; and the user id comparison needs an id we may not have learned yet.
        A factory that replies to itself is an outage that bills by the minute, so this is
        checked three ways rather than one.
        """
        if inner.get("bot_id"):
            return True
        if str(inner.get("subtype") or "") == "bot_message":
            return True
        own = self.credentials.bot_user_id
        return bool(own) and str(inner.get("user") or "") == own

    def _strip_leading_mention(self, text: str) -> str:
        """Drop the `<@U…>` that opens every `app_mention`.

        Leaving it in makes the request read as "<@U012ABC> fix the importer", and the
        title on the work item then leads with an opaque id rather than with what was
        asked for.
        """
        stripped = text.lstrip()
        if not stripped.startswith("<@"):
            return text.strip()
        end = stripped.find(">")
        return stripped[end + 1 :].strip() if end != -1 else text.strip()

    def _permalink(self, channel: str, ts: str) -> str:
        """A message permalink, or empty when the workspace domain is unknown.

        Empty rather than guessed. `Origin.url` is followed by a human deciding something,
        and a link that 404s teaches them not to follow the next one.
        """
        if not (self.credentials.team_domain and channel and ts):
            return ""
        return (
            f"https://{self.credentials.team_domain}.slack.com/archives/"
            f"{channel}/p{ts.replace('.', '')}"
        )


#: What an operator should do about the Slack errors that actually happen.
_REMEDIATIONS = {
    "channel_not_found": "Invite the app to the channel, or check the channel id.",
    "not_in_channel": "Invite the app to the channel with `/invite`.",
    "invalid_auth": "The bot token is wrong or was revoked. Reinstall the app.",
    "token_revoked": "The bot token was revoked. Reinstall the app to issue a new one.",
    "account_inactive": "The bot user is deactivated. Re-enable it in the workspace.",
    "missing_scope": "Grant the app the scope Slack names, then reinstall it.",
    "ratelimited": "Slack is rate limiting this app. Retry after the interval it returns.",
    "msg_too_long": "Shorten the reply; Slack refuses messages over 40,000 characters.",
    "is_archived": "The channel is archived. Replies cannot be posted there.",
}

#: How each reply kind is marked, so a question is not buried in a status feed.
#:
#: FR-18.8 carries `kind` for exactly this reason, and every provider that can render the
#: distinction should. A question nobody notices is a checkpoint that times out for the
#: wrong reason.
_PREFIXES = {
    "question": ":raising_hand: *Question from the factory*\n",
    "result": ":white_check_mark: *Result*\n",
    "status": "",
    "blocked": ":no_entry: *Blocked*\n",
}


def _render(reply: Reply) -> str:
    return f"{_PREFIXES.get(reply.kind, '')}{reply.body}"


def _first_line(text: str, *, limit: int = 120) -> str:
    """A title from the first line of a message.

    A chat message has no title field, and a work item with no title is one nobody can
    find on the board. Truncated on a word boundary where one is near the limit.
    """
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if len(line) <= limit:
        return line
    cut = line[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).rstrip() + "…"


def _mentions(text: str) -> list[str]:
    """Every `<@U…>` in the text, as ids.

    An attribute rather than a parsed field, because filters match attributes: "only act
    when the on-call group is mentioned" is a rule an operator should be able to write
    without a schema change.
    """
    found: list[str] = []
    start = 0
    while True:
        open_at = text.find("<@", start)
        if open_at == -1:
            return found
        close_at = text.find(">", open_at)
        if close_at == -1:
            return found
        handle = text[open_at + 2 : close_at].split("|", 1)[0]
        if handle and handle not in found:
            found.append(handle)
        start = close_at + 1


def signature_headers(
    *, signing_secret: str, body: bytes, at: float | None = None
) -> dict[str, str]:
    """Produce the headers Slack would send for `body`.

    Here rather than in the test suite because `sf chat receive --sign` uses it to check a
    receiver end to end without a workspace, and a signing helper that only exists in tests
    is one the shipped path is never exercised against.
    """
    timestamp = str(int(at if at is not None else time.time()))
    basestring = b"%s:%s:%s" % (SIGNATURE_VERSION.encode(), timestamp.encode(), body)
    digest = hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": f"{SIGNATURE_VERSION}={digest}",
    }
