"""The normalised factory event, and the filter language that selects one (PRD FR-18.2-18.7).

Everything that can start work -- a git-host issue, a chat mention, a tracker state change,
a monitoring signal, a schedule, a CLI invocation -- arrives here as one shape. That is the
whole point of FR-18.2's "adding an integration must not touch orchestration code": the
orchestrator reads a :class:`FactoryEvent` and never learns which provider produced it.

Two things are specified here rather than per-adapter, because specifying them per-adapter is
how two integrations end up with two meanings for one word:

* **Filter semantics** (FR-18.4). Every declared key must match; within a key, any listed
  value matches; an omitted key matches everything. Written once, applied identically.
* **Event identity** (FR-18.7). Redelivery is normal -- webhooks retry, and a provider that
  did not see a 200 will send again -- so identity is what stops one alert becoming two work
  items.

And one thing is stated rather than implemented, because it cannot be implemented here:
FR-18.5, filters gate *starting work*, never *access*. A narrower filter does not reduce what
an agent can reach. Access came from what was authorised on the provider, and lint says so.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from software_factory.digests import digest_parts
from software_factory.memory.records import utc_now


class Provider(enum.StrEnum):
    """Where an event came from. Extending this is how a new integration is introduced."""

    GIT_HOST = "git-host"
    CHAT = "chat"
    TRACKER = "tracker"
    WEBHOOK = "webhook"
    SIGNAL = "signal"
    SCHEDULE = "schedule"
    CLI = "cli"
    FACTORY_TOOLS = "factory-tools"


@dataclass(frozen=True, slots=True)
class Origin:
    """Where a reply goes (FR-18.8).

    Carried on every event because "reply in place" is not a nicety: a question asked in the
    factory's own log, about an issue somebody filed on a tracker, is a question that does
    not get answered.
    """

    provider: Provider
    ref: str
    thread: str = ""
    url: str = ""

    def render(self) -> str:
        thread = f" ({self.thread})" if self.thread else ""
        return f"{self.provider.value}:{self.ref}{thread}"


@dataclass(frozen=True, slots=True)
class FactoryEvent:
    """One normalised event, whatever produced it.

    ``attributes`` is what filters match against. Adapters put provider-specific facts here
    -- repository, branch, label, author, conversation, team -- rather than inventing fields
    on this class, so a new provider's vocabulary does not require a schema change that every
    other provider then carries.
    """

    id: str
    """Stable across redeliveries of the same underlying event (FR-18.7)."""

    provider: Provider
    event: str
    origin: Origin
    title: str = ""
    body: str = ""
    author: str = ""
    """The provider handle, unresolved. `identity.Directory.resolve_identity` maps it to a
    principal -- and an unmapped author may still trigger intake, because anyone can open an
    issue, but may not make a decision."""

    attributes: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    """For sources that repeat -- monitoring, error tracking. A recurring alert extends one
    work item rather than opening a thousand (FR-18.14)."""

    received_at: datetime = field(default_factory=utc_now)

    def attribute(self, key: str) -> Any:
        return self.attributes.get(key)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider.value,
            "event": self.event,
            "origin": self.origin.render(),
            "title": self.title,
            "author": self.author,
            "attributes": dict(sorted(self.attributes.items())),
            "fingerprint": self.fingerprint,
            "receivedAt": self.received_at.isoformat(),
        }


def event_identity(provider: Provider, *parts: str) -> str:
    """A stable id from the provider's own identifiers.

    Length-prefixed rather than separator-joined: a separator that can appear inside a part
    makes ``("a/b",)`` and ``("a", "b")`` produce the same id, which is a collision anyone
    who controls one identifier can produce deliberately -- and a forged id turns "this
    event was already handled" into a way of suppressing an event that was not.
    """
    return digest_parts(provider.value, *(part.strip() for part in parts))


def matches(filter_spec: dict[str, Any], event: FactoryEvent) -> bool:
    """Whether one filter selects one event (FR-18.4).

    The semantics, in full, because "specified once and applied identically everywhere" is
    the requirement:

    * Every declared key must match -- keys are ANDed.
    * Within a key, any listed value matches -- values are ORed.
    * An omitted key matches everything. An empty filter matches every event, which is why
      the default templates ship a restrictive one (FR-18.6).
    * ``{"in": [...]}`` and ``{"not_in": [...]}`` are supported explicitly, because the
      negative case is common (every branch but `main`) and expressing it as a list of every
      other value is not expressible at all.

    Matching is case-insensitive on strings: providers differ on the casing of labels and
    branch names, and a filter that fails because someone typed `Bug` instead of `bug` is a
    filter that gets deleted rather than fixed.
    """
    for key, expected in filter_spec.items():
        actual = _event_value(event, key)
        if not _key_matches(expected, actual):
            return False
    return True


def _event_value(event: FactoryEvent, key: str) -> Any:
    """Read a filter key off an event, with the common ones promoted from `attributes`."""
    if key == "author":
        return event.author
    if key == "event":
        return event.event
    if key == "provider":
        return event.provider.value
    return event.attributes.get(key)


def _key_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        if "in" in expected and not _any_of(expected["in"], actual):
            return False
        # Both forms may appear on one key: `{"in": [...], "not_in": [...]}` reads as
        # "one of these, but never these", which is how a branch filter is usually meant.
        return not ("not_in" in expected and _any_of(expected["not_in"], actual))
    return _any_of(expected, actual)


def _any_of(expected: Any, actual: Any) -> bool:
    """True when ``actual`` matches any of ``expected``.

    ``actual`` may itself be a list -- an event carries several labels -- and then the match
    is an intersection: an automation filtering on `label: [bug]` fires for an issue labelled
    `bug, urgent`. Requiring equality of lists would make label filters useless, since
    nobody knows the full label set at the time they write the filter.
    """
    wanted = _as_set(expected)
    if actual is None:
        return False
    return bool(wanted & _as_set(actual))


def _as_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value.strip().lower()}
    if isinstance(value, Sequence) and not isinstance(value, bytes | str):
        return {str(item).strip().lower() for item in value}
    return {str(value).strip().lower()}


def overlapping_keys(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Whether two filters could select the same event.

    Used by lint (FR-18.4: overlapping automations must be reported). Conservative on
    purpose: it reports a possible overlap rather than proving one, because a false report
    costs a reader thirty seconds and a missed one costs every matching event twice.
    """
    for key in set(left) & set(right):
        if not (_as_filter_values(left[key]) & _as_filter_values(right[key])):
            return False
    return True


def _as_filter_values(spec: Any) -> set[str]:
    if isinstance(spec, dict):
        return _as_set(spec.get("in", []))
    return _as_set(spec)
