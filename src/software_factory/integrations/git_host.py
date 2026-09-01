"""A git-host adapter, and the post-merge observation the outcome metrics need.

The second shipped integration, and the one four metrics were waiting on. `sf metrics` and
the dashboard have always reported `changes_merged`, `autonomy` and `cycle_time_to_merge`
as unavailable with the reason "no git-host adapter is configured, so this cannot be
observed; reporting zero here would read as a factory that produces none". That reason was
correct and it was also a standing admission: the project's own outcome measures (O-1, O-5,
and cycle time) could not be computed, so every quality claim was unvalidated by FR-15.14's
own rule.

This closes that. Three things, in order of how much they matter:

**Observation.** FR-15.14 says O-2, O-3 and O-4 need the repository watched *after* merge.
A `pull_request` webhook carries exactly that: whether it merged, when, and how many commits
on the branch came from somebody other than the factory. `observe()` turns one into a
`ChangeObservation`, and `record()` writes it to the ledger as the one entry type those
metrics fold over. Nothing here estimates: a change with no observation is absent from the
numerator *and* the denominator, because a factory that counts its own handoffs as merges is
grading its own homework.

**Intake.** Issues and their labels and comments become factory events, on the same contract
Slack uses.

**Handoff.** `open_change()` opens a pull request. Deliberately a method a *command* calls
rather than something the coordinator does inside a run: opening a change is an outward-
facing act with a credential attached, and a run that can do it on its own initiative is a
run that can publish. The stage machine reaches HANDOFF and stops; a person or an automation
with the token takes it from there.

Two host-specific facts drive the design:

* GitHub signs the **body only** -- `X-Hub-Signature-256: sha256=<hmac>` -- with no
  timestamp in the signed material. So there is no replay *window* to enforce, and replay
  protection rests entirely on event identity. Saying that plainly matters more than the
  five lines of code it saves: a reader who assumes a Slack-style window is here will not
  add the dedupe that is actually load-bearing.
* The delivery GUID is **not** the event id. Redelivering from the UI issues a new GUID for
  the same underlying event, so keying on it makes every manual redelivery a second work
  item -- the failure the Slack adapter avoids by keying on Slack's own `event_id`. Here the
  id is built from what the *event* is: repository, event, action, subject, and the
  subject's own updated-at.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from software_factory.errors import ErrorCode, FactoryError
from software_factory.intake.adapters import Health, HealthReport, Reply
from software_factory.intake.events import FactoryEvent, Origin, Provider, event_identity
from software_factory.memory.records import utc_now
from software_factory.providers.base import ProviderError
from software_factory.providers.transport import (
    RequestTransport,
    Response,
    UrllibTransport,
)

#: The public API. Configurable for an enterprise install, never taken from an event.
GITHUB_API = "https://api.github.com"

#: The signature header and its algorithm prefix.
SIGNATURE_HEADER = "X-Hub-Signature-256"
SIGNATURE_PREFIX = "sha256="

#: Webhook events this adapter turns into factory events. Everything else normalises to
#: `None`, which the contract calls a normal answer -- a busy repository emits far more
#: than any factory asked for.
HANDLED_EVENTS = frozenset({"issues", "issue_comment", "pull_request", "pull_request_review"})

#: Issue actions worth acting on. `opened` and `labeled` are how work arrives; `closed` is
#: how it stops being work, and a factory that keeps working a closed issue is worse than
#: one that never started.
HANDLED_ISSUE_ACTIONS = frozenset({"opened", "labeled", "reopened", "closed"})

#: Body text longer than this is carried truncated, with the truncation recorded. An issue
#: with a 400 KB log pasted into it is a pack with a 400 KB log in it.
MAX_BODY_CHARS = 8_000


class GitHostError(FactoryError):
    """The host refused, or answered something this adapter will not act on."""

    code = ErrorCode.INTEGRATION_NOT_CONFIGURED


class GitHostSignatureError(GitHostError):
    """A delivery did not carry a signature this repository's secret produces."""

    code = ErrorCode.NOT_AUTHORIZED


class ChangeState(enum.StrEnum):
    """What has happened to a change.

    `CLOSED` is distinct from `MERGED` on purpose. A change that was closed unmerged is a
    change the factory produced and nobody took, and folding it into "not merged yet" is how
    a rejection rate hides.
    """

    OPENED = "opened"
    MERGED = "merged"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ChangeObservation:
    """What the repository says happened to one change, after handoff.

    ``human_commits`` is the field the autonomy metric (O-5) rests on, and it is counted
    rather than inferred: commits on the branch whose author is not the factory's own
    identity. A factory that cannot tell whose commits these are must report autonomy as
    unavailable rather than as 100%, which is why this is `None` when unknown and never 0.
    """

    change: str
    """The change's stable reference, e.g. ``acme/payments#128``."""

    state: ChangeState
    work_item: str = ""
    opened_at: datetime | None = None
    merged_at: datetime | None = None
    human_commits: int | None = None
    author: str = ""
    url: str = ""

    @property
    def cycle_time(self) -> timedelta | None:
        if self.opened_at is None or self.merged_at is None:
            return None
        return self.merged_at - self.opened_at

    def as_payload(self) -> dict[str, Any]:
        return {
            "change": self.change,
            "state": self.state.value,
            "workItem": self.work_item,
            "openedAt": None if self.opened_at is None else self.opened_at.isoformat(),
            "mergedAt": None if self.merged_at is None else self.merged_at.isoformat(),
            "humanCommits": self.human_commits,
            "author": self.author,
            "url": self.url,
        }


@dataclass(frozen=True)
class GitHostCredentials:
    """What the adapter needs, and the two values that must never be printed."""

    token: str = ""
    webhook_secret: str = ""
    factory_login: str = ""
    """The account the factory pushes as. Used to tell a factory commit from a human one --
    without it, autonomy is not computable and is reported unavailable rather than guessed.
    """

    def __repr__(self) -> str:
        return (
            f"GitHostCredentials(token='***', webhook_secret='***', "
            f"factory_login={self.factory_login!r})"
        )

    def require_token(self) -> None:
        if not self.token.strip():
            raise GitHostError(
                "reaching the git host needs a token",
                remediation=(
                    "Set SF_GIT_HOST_TOKEN to a token with the scopes this repository "
                    "needs. Never pass it as a command-line flag: `ps` shows it to every "
                    "process on the host."
                ),
            )

    def require_secret(self) -> None:
        if not self.webhook_secret.strip():
            raise GitHostSignatureError(
                "verifying a webhook needs the repository's webhook secret",
                remediation=(
                    "Set SF_GIT_HOST_WEBHOOK_SECRET to the secret configured on the "
                    "webhook. Without it any caller who learns the URL can start work."
                ),
            )


def verify_signature(*, webhook_secret: str, body: bytes, signature: str) -> None:
    """Verify one delivery, or raise.

    Raising rather than returning a boolean, for the same reason as everywhere else here: a
    result that can be dropped is a control that can be forgotten.

    **There is no replay window**, because the host signs the body alone with no timestamp
    in the signed material. Nothing can be enforced that is not signed, and a window
    computed from an unsigned header is a window the sender chooses. Replay protection here
    is event identity and the deduplicator, not a clock.
    """
    if not webhook_secret:
        raise GitHostSignatureError(
            "no webhook secret is configured, so no delivery can be verified",
            remediation="Configure the repository's webhook secret before accepting events.",
        )
    digest = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(f"{SIGNATURE_PREFIX}{digest}", signature or ""):
        raise GitHostSignatureError(
            "delivery signature does not match this repository's webhook secret",
            remediation=(
                "Confirm the secret matches the webhook sending these deliveries. If it "
                "does, refuse the request: it was not signed by the host."
            ),
        )


def signature_header(*, webhook_secret: str, body: bytes) -> dict[str, str]:
    """The header the host would send for `body`, so a receiver can be exercised locally."""
    if not webhook_secret:
        raise GitHostSignatureError(
            "cannot sign a delivery without a webhook secret",
            remediation="Set SF_GIT_HOST_WEBHOOK_SECRET before signing anything.",
        )
    digest = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return {SIGNATURE_HEADER: f"{SIGNATURE_PREFIX}{digest}"}


@dataclass(slots=True)
class GitHostAdapter:
    """A GitHub-compatible host as a `git-host` provider."""

    credentials: GitHostCredentials = field(default_factory=GitHostCredentials)
    repositories: frozenset[str] = frozenset()
    """Repositories this factory acts on, as ``owner/name``. Empty means all of them, which
    is only safe for a webhook scoped to one repository -- so `sf` always passes the
    factory's declared repositories rather than relying on that."""

    transport: RequestTransport = field(default_factory=UrllibTransport)
    api_base: str = GITHUB_API
    timeout_s: float = 30.0

    provider: Provider = field(default=Provider.GIT_HOST, init=False)
    subscribed: frozenset[str] = field(default=frozenset(), init=False)

    # ------------------------------------------------------------------ the six methods

    def authenticate(self) -> bool:
        try:
            self._call("GET", "/user", {})
        except (GitHostError, ProviderError):
            return False
        return True

    def subscribe(self, events: Iterable[str]) -> None:
        """Record which events this adapter acts on. Idempotent, and no network call.

        Webhook subscriptions are repository settings changed by somebody with admin on the
        repository, not by a client at runtime. A `subscribe` that looked like it registered
        something would be a method whose whole value is a false impression.
        """
        self.subscribed = frozenset(self.subscribed | {str(e) for e in events})

    def normalise(self, raw: dict[str, Any]) -> FactoryEvent | None:
        """Turn one webhook delivery into a factory event, or `None` to ignore it.

        ``raw`` carries the host's event name under ``_event`` -- it arrives in the
        `X-GitHub-Event` header rather than the body, and an adapter that guessed it from
        the payload shape would confuse an `issues` delivery with an `issue_comment` one.
        """
        kind = str(raw.get("_event") or "")
        if kind not in HANDLED_EVENTS:
            return None

        repository = str((raw.get("repository") or {}).get("full_name") or "")
        if self.repositories and repository not in self.repositories:
            return None

        action = str(raw.get("action") or "")
        sender = str((raw.get("sender") or {}).get("login") or "")
        if sender and sender == self.credentials.factory_login:
            # The factory's own comment on its own change is not a new request. Without
            # this, posting a status update re-triggers intake on the item it is about.
            return None

        subject, title, body, ref, url = self._subject_of(kind, raw)
        if subject is None:
            return None
        if kind == "issues" and action not in HANDLED_ISSUE_ACTIONS:
            return None

        truncated = len(body) > MAX_BODY_CHARS
        return FactoryEvent(
            id=event_identity(
                Provider.GIT_HOST,
                repository,
                kind,
                action,
                str(subject),
                # The subject's own updated-at, so a *changed* issue is a different event
                # and an unchanged redelivery is the same one. The delivery GUID is not
                # used: redelivering from the UI issues a new GUID for the same event.
                str(self._updated_at(kind, raw)),
            ),
            provider=Provider.GIT_HOST,
            event=f"git.{kind}.{action}" if action else f"git.{kind}",
            origin=Origin(
                provider=Provider.GIT_HOST,
                ref=ref,
                thread=ref,
                url=url,
                # Backpressure counts the repository. One repository emitting a thousand
                # issues is the failure FR-26.3 names, and a per-issue bucket cannot see it.
                source=repository or ref,
            ),
            title=title[:200],
            body=body[:MAX_BODY_CHARS],
            author=str(((raw.get(_SUBJECT_KEY[kind]) or {}).get("user") or {}).get("login") or ""),
            attributes={
                "repository": repository,
                "event": kind,
                "action": action,
                "number": subject,
                "labels": _labels(kind, raw),
                "draft": bool((raw.get("pull_request") or {}).get("draft")),
                "merged": bool((raw.get("pull_request") or {}).get("merged")),
                "sender": sender,
                "truncated": truncated,
            },
        )

    def resolve_identity(self, raw: dict[str, Any]) -> str:
        """The host login of whoever caused this."""
        return str((raw.get("sender") or {}).get("login") or "")

    def reply(self, event: FactoryEvent, reply: Reply) -> bool:
        """Comment on the issue or change the work came from (FR-18.8)."""
        repository = str(event.attributes.get("repository") or "")
        number = event.attributes.get("number")
        if not repository or not number:
            return False
        try:
            self._call(
                "POST",
                f"/repos/{repository}/issues/{number}/comments",
                {"body": _render(reply)},
            )
        except (GitHostError, ProviderError):
            return False
        return True

    def health(self) -> HealthReport:
        now = utc_now()
        try:
            self._call("GET", "/rate_limit", {})
        except ProviderError as exc:
            if exc.status == 429 or exc.status == 403:
                return HealthReport(
                    provider=Provider.GIT_HOST,
                    status=Health.DEGRADED,
                    detail=f"the host is rate limiting this token: {exc}",
                    checked_at=now,
                    retry_after=timedelta(seconds=60),
                )
            return HealthReport(
                provider=Provider.GIT_HOST,
                status=Health.UNAVAILABLE,
                detail=f"the git host is unreachable: {exc}",
                checked_at=now,
            )
        except GitHostError as exc:
            return HealthReport(
                provider=Provider.GIT_HOST,
                status=Health.UNAVAILABLE,
                detail=f"the git host refused this token: {exc}",
                checked_at=now,
            )
        if not self.credentials.factory_login:
            return HealthReport(
                provider=Provider.GIT_HOST,
                status=Health.DEGRADED,
                detail=(
                    "reachable, but the factory's own login is not configured, so a "
                    "factory commit cannot be told from a human one and autonomy (O-5) "
                    "stays unavailable"
                ),
                checked_at=now,
            )
        return HealthReport(provider=Provider.GIT_HOST, status=Health.HEALTHY, checked_at=now)

    # ------------------------------------------------------- observation, and opening one

    def observe(self, raw: dict[str, Any]) -> ChangeObservation | None:
        """What a `pull_request` delivery says happened to a change.

        Returns `None` for anything that is not a change's lifecycle, including
        `synchronize` -- a push to the branch is not a state change, and recording one as an
        observation would count a change once per push.
        """
        if str(raw.get("_event") or "") != "pull_request":
            return None
        action = str(raw.get("action") or "")
        pull = raw.get("pull_request") or {}
        if not isinstance(pull, dict) or not pull:
            return None

        repository = str((raw.get("repository") or {}).get("full_name") or "")
        number = pull.get("number")
        if not repository or number is None:
            return None

        if action == "opened":
            state = ChangeState.OPENED
        elif action == "closed":
            state = ChangeState.MERGED if pull.get("merged") else ChangeState.CLOSED
        else:
            return None

        return ChangeObservation(
            change=f"{repository}#{number}",
            state=state,
            work_item=_work_item_from(pull),
            opened_at=_parse_time(pull.get("created_at")),
            merged_at=_parse_time(pull.get("merged_at")),
            human_commits=self._human_commits(raw, pull),
            author=str((pull.get("user") or {}).get("login") or ""),
            url=str(pull.get("html_url") or ""),
        )

    def open_change(
        self,
        *,
        repository: str,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        draft: bool = False,
    ) -> str:
        """Open a pull request and return its reference.

        A method a *command* calls, never something a run does on its own initiative.
        Opening a change is outward-facing and carries a credential, and a run that can
        publish is a run whose blast radius includes everyone watching the repository.
        """
        response = self._call(
            "POST",
            f"/repos/{repository}/pulls",
            {"title": title, "body": body, "head": head, "base": base, "draft": draft},
        )
        number = response.body.get("number")
        if number is None:
            raise GitHostError(
                f"the host accepted the pull request for {repository} but returned no number",
                remediation="Check the repository in the host's UI; the change may exist.",
            )
        return f"{repository}#{number}"

    # ------------------------------------------------------------------------ internals

    def _human_commits(self, raw: dict[str, Any], pull: dict[str, Any]) -> int | None:
        """Commits on the branch not authored by the factory, or `None` when unknowable.

        `None` rather than 0 is the whole point. Autonomy (O-5) is the share of merged
        changes with *zero* human commits, so a factory that cannot tell whose commits these
        are and reports 0 reports perfect autonomy for every change it has ever produced.
        """
        if not self.credentials.factory_login:
            return None
        commits = raw.get("_commits")
        if not isinstance(commits, list):
            # The webhook body does not carry the commit list; a caller that fetched it
            # passes it in. Absent means unknown, and unknown must not read as zero.
            return None
        del pull
        return sum(
            1
            for commit in commits
            if str(((commit or {}).get("author") or {}).get("login") or "")
            != self.credentials.factory_login
        )

    def _subject_of(self, kind: str, raw: dict[str, Any]) -> tuple[int | None, str, str, str, str]:
        """(number, title, body, reply ref, url) for the thing this delivery is about."""
        repository = str((raw.get("repository") or {}).get("full_name") or "")
        holder = raw.get(_SUBJECT_KEY[kind]) or {}
        if not isinstance(holder, dict):
            return None, "", "", "", ""
        number = holder.get("number")
        if kind in ("issue_comment", "pull_request_review"):
            # The comment or review is the body; the issue or change it hangs on is the
            # subject, because that is where a reply belongs.
            container = raw.get("issue") or raw.get("pull_request") or {}
            number = container.get("number", number)
        if number is None:
            return None, "", "", "", ""
        text = str(raw.get("comment", {}).get("body") or holder.get("body") or "")
        return (
            int(number),
            str(holder.get("title") or text.strip().splitlines()[0] if text.strip() else ""),
            text,
            f"{repository}#{number}",
            str(holder.get("html_url") or ""),
        )

    def _updated_at(self, kind: str, raw: dict[str, Any]) -> str:
        holder = raw.get(_SUBJECT_KEY[kind]) or {}
        if not isinstance(holder, dict):
            return ""
        return str(raw.get("comment", {}).get("updated_at") or holder.get("updated_at") or "")

    def _call(self, method: str, path: str, payload: dict[str, Any]) -> Response:
        self.credentials.require_token()
        response = self.transport.request(
            method,
            f"{self.api_base.rstrip('/')}{path}",
            headers={
                "authorization": f"Bearer {self.credentials.token}",
                "accept": "application/vnd.github+json",
            },
            # A GET carries no body. Sending one is how a read turns into something a
            # host may treat as a write.
            payload=payload if method != "GET" else None,
            timeout_s=self.timeout_s,
        )
        if response.status >= 400:
            raise GitHostError(
                f"the host refused {method} {path}: {response.body.get('message') or response.status}",
                remediation="Check the token's scopes and that it can reach this repository.",
            )
        return response


#: Which payload key holds the thing a delivery is about, per event.
_SUBJECT_KEY = {
    "issues": "issue",
    "issue_comment": "comment",
    "pull_request": "pull_request",
    "pull_request_review": "review",
}


def _labels(kind: str, raw: dict[str, Any]) -> list[str]:
    holder = raw.get("issue") or raw.get("pull_request") or {}
    del kind
    labels = holder.get("labels") if isinstance(holder, dict) else None
    if not isinstance(labels, list):
        return []
    return [str(label.get("name") or "") for label in labels if isinstance(label, dict)]


def _work_item_from(pull: dict[str, Any]) -> str:
    """The work item a change belongs to, from the branch name the factory pushed.

    Read from the branch rather than from the body, because a body is edited by people and
    a branch name is not. Returns empty when the branch does not carry one, which keeps a
    human-authored change out of the factory's own numerators.
    """
    ref = str(((pull.get("head") or {}).get("ref")) or "")
    prefix = "factory/"
    if not ref.startswith(prefix):
        return ""
    return ref[len(prefix) :]


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


_PREFIXES = {
    "question": "**Question from the factory**\n\n",
    "result": "**Result**\n\n",
    "status": "",
    "blocked": "**Blocked**\n\n",
}


def _render(reply: Reply) -> str:
    return f"{_PREFIXES.get(reply.kind, '')}{reply.body}"
