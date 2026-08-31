"""From a normalised event to a started work item (PRD FR-18.3-18.10).

The ordering here *is* the policy, and each step is refusable with a reason:

    dedupe → health → backpressure → match automations → author trust → work item

Deduplication first, because a redelivery is not load and must not consume a rate-limit
slot. Health next, because an event from an unavailable provider parks rather than starts
(FR-18.9) -- and parking a work item you cannot reply to is worse than not starting it.
Backpressure third, since by now the event is real, new, and from a working provider, which
is exactly what a rate limit is meant to measure. Matching last, because one event may match
several automations and each match is its own run (FR-18.4).

Author trust (FR-18.6) is checked per-match rather than globally: an automation may
legitimately accept anyone -- a public bug report -- while another accepts only members. A
factory-wide answer would force the strictest automation's policy onto all of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from software_factory.economics.scheduling import (
    Backpressure,
    Priority,
    Queued,
)
from software_factory.economics.scheduling import (
    Rejected as BackpressureRejected,
)
from software_factory.identity.principals import Directory
from software_factory.intake.adapters import Deduplicator, Registry
from software_factory.intake.events import FactoryEvent, matches
from software_factory.memory.records import utc_now


@dataclass(frozen=True, slots=True)
class Automation:
    """One binding of a trigger to an agent and a prompt (FR-18.3).

    A thin runtime view of `AutomationDefinition`, so the pipeline does not depend on how a
    factory is stored and a test can state one in three lines.
    """

    name: str
    agent: str
    prompt: str
    provider: str
    event: str
    filter: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    require_known_author: bool = True
    """FR-18.6. Restrictive by default: for sources where an author need not be a factory
    member, the templates must set a restrictive filter rather than an open one, and a
    default that had to be remembered would not be one."""

    priority: Priority = Priority.NORMAL

    def selects(self, event: FactoryEvent) -> bool:
        if not self.enabled:
            return False
        # Folded, because `matches()` folds `provider` and `event` when they appear as
        # filter keys. The same two fields comparing case-sensitively here and
        # case-insensitively there means one spelling selects and the other does not, for
        # reasons nothing in the definition explains.
        if not _same(self.provider, event.provider.value) or not _same(self.event, event.event):
            return False
        return matches(self.filter, event)


def _same(left: str, right: str) -> bool:
    """The comparison `matches()` uses, applied to the two keys it does not see."""
    return left.strip().casefold() == right.strip().casefold()


@dataclass(frozen=True, slots=True)
class Started:
    """An event that produced work. One per matching automation."""

    event: FactoryEvent
    automation: str
    agent: str
    queued: Queued


@dataclass(frozen=True, slots=True)
class Ignored:
    """An event nothing matched. Not an error -- most events are not for this factory."""

    event: FactoryEvent
    reason: str = "no automation matched"


@dataclass(frozen=True, slots=True)
class Refused:
    """An event that should have started work and did not. Always says what to do."""

    event: FactoryEvent
    code: str
    message: str
    remediation: str
    parks_work: bool = False
    """True when the right response is to park affected work as BLOCKED rather than drop
    the event (FR-18.9)."""


Outcome = Started | Ignored | Refused


@dataclass(slots=True)
class Pipeline:
    """Intake, end to end.

    Every collaborator is injected. That is not ceremony: intake is the surface an attacker
    reaches first, and a pipeline that constructs its own deduplicator and its own
    backpressure is one a test cannot put into the states that matter.
    """

    automations: list[Automation] = field(default_factory=list)
    directory: Directory = field(default_factory=Directory)
    registry: Registry = field(default_factory=Registry)
    deduplicator: Deduplicator = field(default_factory=Deduplicator)
    backpressure: Backpressure = field(default_factory=Backpressure)

    def receive(self, event: FactoryEvent, *, now: datetime | None = None) -> list[Outcome]:
        """Run one event through intake. Returns one outcome per matching automation.

        A list rather than a single outcome because FR-18.4 says one event may match several
        automations and each match starts its own run. Collapsing that to one outcome would
        make the second match invisible.
        """
        now = now or utc_now()

        if self.deduplicator.seen(event, now=now):
            return [
                Refused(
                    event=event,
                    code="intake.redelivered",
                    message=f"event {event.id} has already been accepted",
                    remediation=(
                        "Nothing to do: providers retry when they do not see a 200, and "
                        "this is the mechanism that stops a retry becoming a second work "
                        "item."
                    ),
                )
            ]

        # Refreshed before it is consulted. `accepts()` reads stored health, and nothing
        # ever populated it, so this branch was dead and every integration read as up.
        self.registry.ensure_checked(event.provider, now=now)
        if not self.registry.accepts(event.provider):
            report = self.registry.last_health.get(event.provider)
            return [
                Refused(
                    event=event,
                    code="intake.provider_unavailable",
                    message=(
                        f"the {event.provider.value} adapter is unavailable: "
                        f"{report.detail if report else 'unknown'}"
                    ),
                    remediation=(
                        "Affected work parks as BLOCKED rather than failing: an integration "
                        "being down is not the work being wrong. Restore the adapter and "
                        "resume."
                    ),
                    parks_work=True,
                )
            ]

        selected = [a for a in self.automations if a.selects(event)]
        if not selected:
            # Matched nothing, so it costs nothing. Backpressure used to run *before* this,
            # and 33 events matching no automation would rate-limit and then trip the
            # breaker, parking a real source for an hour -- reachable by anyone who can
            # comment on an issue or fire a webhook, without ever touching an automation.
            # The docstring on the fingerprint case has the principle exactly right ("a
            # duplicate is not evidence of load") and it applies identically here: an
            # event nobody acts on is not load.
            return [Ignored(event=event)]

        admitted = self.backpressure.admit(
            Queued(
                id=event.id,
                source=f"{event.provider.value}:{event.origin.source_key}",
                fingerprint=event.fingerprint,
                queued_at=now,
            ),
            now=now,
        )
        if isinstance(admitted, BackpressureRejected):
            return [
                Refused(
                    event=event,
                    code=admitted.code,
                    message=admitted.message,
                    remediation=admitted.remediation,
                )
            ]

        outcomes: list[Outcome] = []
        for automation in selected:
            refusal = self._author_check(event, automation)
            if refusal is not None:
                outcomes.append(refusal)
                continue
            outcomes.append(
                Started(
                    event=event,
                    automation=automation.name,
                    agent=automation.agent,
                    queued=Queued(
                        id=f"{event.id}:{automation.name}",
                        source=f"{event.provider.value}:{event.origin.source_key}",
                        priority=automation.priority,
                        fingerprint=event.fingerprint,
                        queued_at=now,
                    ),
                )
            )

        if any(isinstance(outcome, Started) for outcome in outcomes):
            # Recorded only once something actually started. Recording earlier -- as this
            # used to, inside the `seen` check -- made every refusal permanent: the
            # `provider_unavailable` and backpressure refusals both tell the caller to
            # retry, and the retry came back `intake.redelivered` forever.
            #
            # An `Ignored` event and an author refusal are deliberately not recorded. Both
            # are decisions about the *current* configuration, and neither has an effect to
            # duplicate: if an operator adds the automation or maps the identity, the
            # provider's next delivery should be able to match.
            self.deduplicator.record(event, now=now)
        return outcomes

    def _author_check(self, event: FactoryEvent, automation: Automation) -> Refused | None:
        """FR-18.6, per automation rather than factory-wide.

        An automation may legitimately accept anyone -- a public bug report is work -- while
        another accepts only members. A factory-wide answer forces the strictest
        automation's policy onto all of them.
        """
        if not automation.require_known_author:
            return None
        if not event.author:
            return Refused(
                event=event,
                code="intake.anonymous_author",
                message=f"{automation.name!r} requires a known author and the event has none",
                remediation=(
                    "Set `requireKnownAuthor: false` if this automation should accept "
                    "anonymous input, understanding that its prompt will then carry text "
                    "written by anyone."
                ),
            )
        principal = self.directory.resolve_identity(event.provider.value, event.author)
        if principal is None:
            return Refused(
                event=event,
                code="intake.unknown_author",
                message=(
                    f"{event.provider.value}:{event.author} is not mapped to a principal, and "
                    f"{automation.name!r} requires a known author"
                ),
                remediation=(
                    "Map the identity in `principals/`, or set `requireKnownAuthor: false` "
                    "on this automation. An unmapped identity may trigger intake where the "
                    "automation allows it, but may never make a decision."
                ),
            )
        return None
