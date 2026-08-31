"""The adapter contract every integration implements (PRD FR-18.2, FR-18.9).

Six methods, and the shape of the list is the requirement: *authenticate, subscribe,
normalise, resolve identity, reply, report health*. FR-18.2 says adding an integration must
not touch orchestration code, and that only holds if the orchestrator's whole view of a
provider is this protocol.

Two of the six are easy to get wrong in a way that only shows up in production:

* **normalise** must produce a stable event id (FR-18.7). Webhooks retry, and a provider
  that did not see a 200 sends again. An adapter whose id includes a delivery timestamp
  turns every retry into a second work item.
* **health** is not a boolean. FR-18.9 requires an unhealthy adapter to *park* affected work
  as BLOCKED with the reason rather than dropping events, and "park with the reason"
  needs a reason -- which a boolean does not carry.

The protocol is structural (``typing.Protocol``), so an adapter does not import from here to
satisfy it. That keeps the dependency pointing the right way: integrations depend on nothing,
and the orchestrator depends on the shape.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from software_factory.intake.events import FactoryEvent, Provider
from software_factory.memory.records import utc_now


class Health(enum.StrEnum):
    """Whether an adapter can be relied on right now."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    """Working, but not fully: rate-limited, or a subscription is stale. Events still
    arrive, so work continues -- with the degradation stated in the pack, as everywhere
    else in this design."""

    UNAVAILABLE = "unavailable"
    """Not working. Affected work items park as BLOCKED rather than failing, because an
    integration being down is not the work being wrong."""


@dataclass(frozen=True, slots=True)
class HealthReport:
    """An adapter's state, with enough detail to act on.

    ``detail`` is required for anything but ``HEALTHY``: FR-18.9's "park with the reason"
    is not satisfiable by a status with no reason attached, and an operator reading
    "unavailable" learns nothing they can do something about.
    """

    provider: Provider
    status: Health
    detail: str = ""
    checked_at: datetime = field(default_factory=utc_now)
    retry_after: timedelta | None = None

    def __post_init__(self) -> None:
        if self.status is not Health.HEALTHY and not self.detail.strip():
            raise ValueError(
                f"a {self.status.value} adapter must say why; 'park with the reason' "
                "(FR-18.9) needs a reason"
            )

    @property
    def accepts_events(self) -> bool:
        return self.status is not Health.UNAVAILABLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "status": self.status.value,
            "detail": self.detail,
            "checkedAt": self.checked_at.isoformat(),
            "retryAfter": (
                None if self.retry_after is None else int(self.retry_after.total_seconds())
            ),
        }


@dataclass(frozen=True, slots=True)
class Reply:
    """Something to say back to where the work came from (FR-18.8)."""

    body: str
    kind: str = "status"
    """``status``, ``question``, ``result``, or an adapter-specific kind. Carried because a
    question needs to be visually distinct from a status update in every provider that can
    make it so -- a question buried in a status feed is a question nobody answers."""


@runtime_checkable
class Adapter(Protocol):
    """What an integration must provide. Nothing more, and nothing about orchestration."""

    provider: Provider

    def authenticate(self) -> bool:
        """Establish credentials. False rather than raising: a provider being unreachable
        at startup is normal, and it must not stop the factory booting."""
        ...

    def subscribe(self, events: Iterable[str]) -> None:
        """Register interest. Idempotent -- re-subscribing after a reconnect is the common
        path, not the exception."""
        ...

    def normalise(self, raw: dict[str, Any]) -> FactoryEvent | None:
        """Turn a provider payload into a factory event, or ``None`` to ignore it.

        ``None`` is a normal answer: providers send events nobody subscribed to, and an
        adapter that raised on them would make every unrelated repository push an error.
        """
        ...

    def resolve_identity(self, raw: dict[str, Any]) -> str:
        """The provider handle of whoever caused this, for `Directory.resolve_identity`."""
        ...

    def reply(self, event: FactoryEvent, reply: Reply) -> bool:
        """Post back to the originating context. False when the reply could not be
        delivered -- which is worth knowing, because a question nobody received is a
        checkpoint that will time out for the wrong reason."""
        ...

    def health(self) -> HealthReport:
        """Current state. Called on a schedule, not only on failure."""
        ...


class Deduplicator:
    """Redelivery protection over event identity (FR-18.7).

    Bounded by a window rather than growing forever: a provider retries for minutes, not
    months, and a set that never forgets is a memory leak with a compliance question
    attached.
    """

    def __init__(self, *, window: timedelta = timedelta(hours=24)) -> None:
        self.window = window
        self._seen: dict[str, datetime] = {}

    def seen(self, event: FactoryEvent, *, now: datetime | None = None) -> bool:
        """True when this event has already been **accepted**. Does not record.

        A pure query, deliberately. This used to record on first sight, which contradicted
        its own docstring and made every refusal permanent: an event turned away because an
        adapter was down or backpressure was engaged had already been written down as
        accepted, so the retry those refusals ask for came back `intake.redelivered`
        forever. FR-18.9 promises that work parks rather than being dropped; it was dropped.
        """
        now = now or utc_now()
        self._expire(now)
        return event.id in self._seen

    def record(self, event: FactoryEvent, *, now: datetime | None = None) -> None:
        """Mark this event accepted, so a provider's retry does not start a second run.

        Called once the pipeline has decided to act on the event -- not before, or a refusal
        the caller is told to retry becomes a refusal they can never get past.
        """
        now = now or utc_now()
        self._expire(now)
        self._seen[event.id] = now

    def _expire(self, now: datetime) -> None:
        cutoff = now - self.window
        self._seen = {key: at for key, at in self._seen.items() if at >= cutoff}

    def __len__(self) -> int:
        return len(self._seen)


@dataclass(slots=True)
class Registry:
    """The adapters a factory has, and their health.

    Health is stored rather than polled on read: an operator asking "what is broken" should
    not cause six network calls, and a stale health report with a timestamp is more honest
    than a fresh one that took ten seconds to produce.
    """

    adapters: dict[Provider, Adapter] = field(default_factory=dict)
    last_health: dict[Provider, HealthReport] = field(default_factory=dict)

    def register(self, adapter: Adapter) -> None:
        if adapter.provider in self.adapters:
            raise ValueError(
                f"an adapter for {adapter.provider.value!r} is already registered; two "
                "adapters for one provider means two answers to 'where does this reply go'"
            )
        self.adapters[adapter.provider] = adapter

    def check(self, *, now: datetime | None = None) -> list[HealthReport]:
        """Poll every adapter. An adapter that raises is recorded as unavailable rather
        than propagating: one broken integration must not take down the health check that
        exists to report it."""
        reports: list[HealthReport] = []
        for provider, adapter in sorted(self.adapters.items(), key=lambda pair: pair[0].value):
            try:
                report = adapter.health()
            except Exception as exc:
                report = HealthReport(
                    provider=provider,
                    status=Health.UNAVAILABLE,
                    detail=f"health check raised {type(exc).__name__}",
                    checked_at=now or utc_now(),
                )
            self.last_health[provider] = report
            reports.append(report)
        return reports

    def ensure_checked(
        self,
        provider: Provider,
        *,
        max_age: timedelta = timedelta(minutes=1),
        now: datetime | None = None,
    ) -> HealthReport | None:
        """Refresh one adapter's health if what we hold is missing or stale.

        `accepts()` reads `last_health`, and nothing called `check()` -- so `last_health` was
        always empty, `accepts()` always returned True, and the whole
        `provider_unavailable` branch of intake was unreachable. FR-18.9's "park rather
        than fail" could not fire.

        Bounded staleness rather than a poll per event: the class docstring is right that an
        operator asking what is broken should not cause six network calls, and a webhook
        storm should not cause a thousand. A report a minute old is a good enough answer to
        "is this integration up", and it is a far better one than no report at all.
        """
        adapter = self.adapters.get(provider)
        if adapter is None:
            return None
        now = now or utc_now()
        held = self.last_health.get(provider)
        if held is not None and now - held.checked_at < max_age:
            return held
        try:
            report = adapter.health()
        except Exception as exc:
            report = HealthReport(
                provider=provider,
                status=Health.UNAVAILABLE,
                detail=f"health check raised {type(exc).__name__}",
                checked_at=now,
            )
        self.last_health[provider] = report
        return report

    def unhealthy(self) -> list[HealthReport]:
        return [r for r in self.last_health.values() if r.status is not Health.HEALTHY]

    def accepts(self, provider: Provider) -> bool:
        """Whether work from this provider can proceed.

        An unknown provider accepts: the CLI and the factory's own tool server produce
        events with no adapter behind them, and refusing those would make a local factory
        unable to start work at all (FR-18.10).
        """
        report = self.last_health.get(provider)
        return report is None or report.accepts_events
