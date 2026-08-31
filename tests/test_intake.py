"""Intake: normalised events, filter semantics, the adapter contract, and the pipeline.

Intake is the surface an attacker reaches first, so the theme is that every refusal here is
explicit and says what to do about it. The other theme is FR-18.5, which is a property of
the *design* rather than of any function: filters gate starting work, never access.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from software_factory.economics.scheduling import Backpressure, SourceLimits
from software_factory.identity import Capability, Directory, Principal, PrincipalKind
from software_factory.intake import (
    Automation,
    Deduplicator,
    FactoryEvent,
    Health,
    HealthReport,
    Ignored,
    Origin,
    Pipeline,
    Provider,
    Refused,
    Registry,
    Reply,
    Started,
    event_identity,
    matches,
    overlapping_keys,
)
from software_factory.memory.records import utc_now

# ---------------------------------------------------------------------------- fixtures


def event(**kwargs: Any) -> FactoryEvent:
    base: dict[str, Any] = {
        "id": "evt-1",
        "provider": Provider.GIT_HOST,
        "event": "issue.labelled",
        "origin": Origin(provider=Provider.GIT_HOST, ref="acme/payments#42"),
        "title": "CSV importer mangles BOM headers",
        "author": "amaya",
        "attributes": {"repository": "acme/payments", "label": ["bug", "importer"]},
    }
    base.update(kwargs)
    return FactoryEvent(**base)


def automation(**kwargs: Any) -> Automation:
    base: dict[str, Any] = {
        "name": "labelled-issue",
        "agent": "conductor",
        "prompt": "Triage this issue.",
        "provider": "git-host",
        "event": "issue.labelled",
        "filter": {"label": "bug"},
        "require_known_author": False,
    }
    base.update(kwargs)
    return Automation(**base)


def directory_with(*identities: str) -> Directory:
    return Directory(
        [
            Principal(
                id="amaya",
                kind=PrincipalKind.PERSON,
                capabilities=frozenset({Capability.APPROVE_SPEC}),
                identities=frozenset(identities),
            )
        ]
    )


# --------------------------------------------------------------------- event identity


def test_the_same_underlying_event_has_the_same_id() -> None:
    """Webhooks retry, and a provider that did not see a 200 sends again."""
    assert event_identity(Provider.GIT_HOST, "acme/payments", "42", "labelled") == event_identity(
        Provider.GIT_HOST, "acme/payments", "42", "labelled"
    )


def test_different_events_have_different_ids() -> None:
    assert event_identity(Provider.GIT_HOST, "acme/payments", "42") != event_identity(
        Provider.GIT_HOST, "acme/payments", "43"
    )


def test_an_identifier_containing_the_separator_cannot_forge_another_id() -> None:
    assert event_identity(Provider.GIT_HOST, "a␟b") != event_identity(Provider.GIT_HOST, "a", "b")


# ------------------------------------------------------------------- filter semantics


def test_an_empty_filter_matches_everything() -> None:
    """Which is why the default templates ship a restrictive one (FR-18.6)."""
    assert matches({}, event())


def test_every_declared_key_must_match() -> None:
    assert matches({"repository": "acme/payments", "label": "bug"}, event())
    assert not matches({"repository": "acme/payments", "label": "security"}, event())


def test_any_listed_value_within_a_key_matches() -> None:
    assert matches({"label": ["security", "bug"]}, event())


def test_a_filter_matches_against_a_list_valued_attribute_by_intersection() -> None:
    """An issue labelled `bug, importer` matches a filter on `bug`. Requiring list equality
    would make label filters useless: nobody knows the full label set when they write one."""
    assert matches({"label": "importer"}, event())


def test_matching_is_case_insensitive() -> None:
    """A filter that fails because someone typed `Bug` is a filter that gets deleted rather
    than fixed."""
    assert matches({"label": "BUG"}, event())


def test_the_in_form_selects_from_a_set() -> None:
    assert matches({"label": {"in": ["bug", "security"]}}, event())
    assert not matches({"label": {"in": ["security"]}}, event())


def test_the_not_in_form_excludes() -> None:
    """The negative case is common -- every branch but `main` -- and expressing it as a list
    of every other value is not expressible at all."""
    assert matches({"label": {"not_in": ["wontfix"]}}, event())
    assert not matches({"label": {"not_in": ["bug"]}}, event())


def test_in_and_not_in_compose_on_one_key() -> None:
    assert matches({"label": {"in": ["bug", "chore"], "not_in": ["wontfix"]}}, event())
    assert not matches({"label": {"in": ["bug"], "not_in": ["importer"]}}, event())


def test_an_absent_attribute_does_not_match_a_declared_key() -> None:
    assert not matches({"branch": "main"}, event())


def test_author_event_and_provider_are_filterable() -> None:
    """Promoted from `attributes` because every provider has them, and an adapter that had
    to remember to copy them into attributes would eventually not."""
    assert matches({"author": "amaya", "event": "issue.labelled", "provider": "git-host"}, event())


def test_overlapping_filters_are_reported_conservatively() -> None:
    """FR-18.4 requires lint to report overlap. A false report costs a reader thirty seconds
    and a missed one costs every matching event twice."""
    assert overlapping_keys({"label": "bug"}, {"label": ["bug", "security"]})
    assert not overlapping_keys({"label": "bug"}, {"label": "security"})
    assert overlapping_keys({"label": "bug"}, {"branch": "main"})


# ------------------------------------------------------------------------- adapters


def test_an_unhealthy_adapter_must_say_why() -> None:
    """ "Park with the reason" (FR-18.9) is not satisfiable by a status with no reason, and
    an operator reading "unavailable" learns nothing they can act on."""
    with pytest.raises(ValueError, match="must say why"):
        HealthReport(provider=Provider.CHAT, status=Health.UNAVAILABLE)


def test_a_degraded_adapter_still_accepts_events() -> None:
    """Working but rate-limited is not the same as down, and treating it as down drops work
    that would have succeeded."""
    report = HealthReport(
        provider=Provider.CHAT, status=Health.DEGRADED, detail="rate limited until 12:04"
    )

    assert report.accepts_events


def test_an_unavailable_adapter_does_not_accept_events() -> None:
    report = HealthReport(
        provider=Provider.CHAT, status=Health.UNAVAILABLE, detail="token rejected"
    )

    assert not report.accepts_events


class StubAdapter:
    """Enough of the protocol to be registered. The protocol is structural, so this does not
    inherit from it -- which is the point: an integration depends on nothing."""

    def __init__(self, provider: Provider, report: HealthReport | None = None) -> None:
        self.provider = provider
        self._report = report or HealthReport(provider=provider, status=Health.HEALTHY)
        self.replies: list[Reply] = []

    def authenticate(self) -> bool:
        return True

    def subscribe(self, events: Any) -> None:
        return None

    def normalise(self, raw: dict[str, Any]) -> FactoryEvent | None:
        return event(**raw) if raw else None

    def resolve_identity(self, raw: dict[str, Any]) -> str:
        return str(raw.get("author", ""))

    def reply(self, ev: FactoryEvent, reply: Reply) -> bool:
        self.replies.append(reply)
        return True

    def health(self) -> HealthReport:
        return self._report


def test_a_stub_adapter_satisfies_the_protocol_structurally() -> None:
    from software_factory.intake import Adapter

    assert isinstance(StubAdapter(Provider.GIT_HOST), Adapter)


def test_two_adapters_for_one_provider_are_refused() -> None:
    """Two adapters for one provider means two answers to "where does this reply go"."""
    registry = Registry()
    registry.register(StubAdapter(Provider.GIT_HOST))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(StubAdapter(Provider.GIT_HOST))


def test_a_health_check_that_raises_is_recorded_not_propagated() -> None:
    """One broken integration must not take down the health check that exists to report
    it."""

    class Exploding(StubAdapter):
        def health(self) -> HealthReport:
            raise RuntimeError("connection reset")

    registry = Registry()
    registry.register(Exploding(Provider.CHAT))

    reports = registry.check()

    assert reports[0].status is Health.UNAVAILABLE
    assert "RuntimeError" in reports[0].detail


def test_a_provider_with_no_adapter_still_accepts_work() -> None:
    """The CLI and the factory's own tool server produce events with no adapter behind them,
    and refusing those would make a local factory unable to start work at all (FR-18.10)."""
    assert Registry().accepts(Provider.CLI)


# ---------------------------------------------------------------------- deduplication


def test_a_redelivered_event_is_recognised() -> None:
    """`seen` is a pure query; `record` is the write.

    They used to be one mutating read, which meant an event refused by *anything* after the
    dedupe check had already been written down as accepted and could never be retried.
    """
    dedupe = Deduplicator()

    assert not dedupe.seen(event())
    dedupe.record(event())
    assert dedupe.seen(event())


def test_the_dedupe_window_expires() -> None:
    """A provider retries for minutes, not months, and a set that never forgets is a memory
    leak with a compliance question attached."""
    dedupe = Deduplicator(window=timedelta(hours=1))
    now = utc_now()
    dedupe.record(event(), now=now)

    assert not dedupe.seen(event(), now=now + timedelta(hours=2))
    assert len(dedupe) == 0


# -------------------------------------------------------------------------- pipeline


def test_a_matching_event_starts_work() -> None:
    pipeline = Pipeline(automations=[automation()])

    outcomes = pipeline.receive(event())

    assert len(outcomes) == 1
    assert isinstance(outcomes[0], Started)
    assert outcomes[0].agent == "conductor"


def test_one_event_matching_two_automations_starts_two_runs() -> None:
    """FR-18.4. Collapsing that to one outcome would make the second match invisible."""
    pipeline = Pipeline(automations=[automation(), automation(name="second", agent="scout")])

    outcomes = pipeline.receive(event())

    assert [o.automation for o in outcomes if isinstance(o, Started)] == [
        "labelled-issue",
        "second",
    ]


def test_an_event_nothing_matches_is_ignored_not_refused() -> None:
    """Most events are not for this factory. Treating them as errors would make every
    unrelated push an incident."""
    pipeline = Pipeline(automations=[automation(filter={"label": "security"})])

    assert isinstance(pipeline.receive(event())[0], Ignored)


def test_a_disabled_automation_does_not_fire() -> None:
    pipeline = Pipeline(automations=[automation(enabled=False)])

    assert isinstance(pipeline.receive(event())[0], Ignored)


def test_a_redelivery_does_not_duplicate_work() -> None:
    """FR-18.7, and the reason deduplication runs first: a retry is not load."""
    pipeline = Pipeline(automations=[automation()])
    pipeline.receive(event())

    refused = pipeline.receive(event())[0]

    assert isinstance(refused, Refused)
    assert refused.code == "intake.redelivered"


def test_an_unavailable_provider_parks_rather_than_dropping() -> None:
    """FR-18.9. An integration being down is not the work being wrong."""
    registry = Registry()
    registry.register(
        StubAdapter(
            Provider.GIT_HOST,
            HealthReport(
                provider=Provider.GIT_HOST, status=Health.UNAVAILABLE, detail="token rejected"
            ),
        )
    )
    registry.check()
    pipeline = Pipeline(automations=[automation()], registry=registry)

    refused = pipeline.receive(event())[0]

    assert isinstance(refused, Refused)
    assert refused.parks_work
    assert "token rejected" in refused.message


def test_backpressure_applies_after_deduplication() -> None:
    """A storm of identical alerts must not consume rate-limit slots, or it parks a source
    over work that was never real."""
    pipeline = Pipeline(
        automations=[automation()],
        backpressure=Backpressure(SourceLimits(max_per_window=1, window=timedelta(minutes=10))),
    )
    pipeline.receive(event(id="evt-1"))

    limited = pipeline.receive(event(id="evt-2"))[0]

    assert isinstance(limited, Refused)
    assert limited.code == "intake.rate_limited"


def test_an_automation_requiring_a_known_author_refuses_a_stranger() -> None:
    """FR-18.6: for sources where an author need not be a factory member."""
    pipeline = Pipeline(
        automations=[automation(require_known_author=True)],
        directory=directory_with("git-host:someone-else"),
    )

    refused = pipeline.receive(event())[0]

    assert isinstance(refused, Refused)
    assert refused.code == "intake.unknown_author"
    assert "requireKnownAuthor" in refused.remediation


def test_a_mapped_author_passes_the_trust_check() -> None:
    pipeline = Pipeline(
        automations=[automation(require_known_author=True)],
        directory=directory_with("git-host:amaya"),
    )

    assert isinstance(pipeline.receive(event())[0], Started)


def test_author_trust_is_per_automation_not_factory_wide() -> None:
    """An automation may legitimately accept anyone -- a public bug report is work -- while
    another accepts only members. A factory-wide answer forces the strictest one on all."""
    pipeline = Pipeline(
        automations=[
            automation(name="open", require_known_author=False),
            automation(name="members-only", require_known_author=True),
        ],
        directory=Directory(),
    )

    outcomes = pipeline.receive(event())

    assert isinstance(outcomes[0], Started)
    assert isinstance(outcomes[1], Refused)


def test_an_anonymous_event_is_refused_where_an_author_is_required() -> None:
    pipeline = Pipeline(automations=[automation(require_known_author=True)])

    refused = pipeline.receive(event(author=""))[0]

    assert isinstance(refused, Refused)
    assert refused.code == "intake.anonymous_author"


def test_a_recurring_signal_deduplicates_by_fingerprint() -> None:
    """FR-18.14: a recurring alert extends one work item rather than opening a thousand."""
    pipeline = Pipeline(
        automations=[automation(provider="signal", event="alert.firing")],
    )
    signal = {
        "provider": Provider.SIGNAL,
        "event": "alert.firing",
        "origin": Origin(provider=Provider.SIGNAL, ref="payments-prod"),
        "fingerprint": "deploy-failed-payments",
        "attributes": {"label": "bug"},
    }

    assert isinstance(pipeline.receive(event(id="a1", **signal))[0], Started)
    repeated = pipeline.receive(event(id="a2", **signal))[0]

    assert isinstance(repeated, Refused)
    assert repeated.code == "intake.duplicate"


def test_a_forged_fingerprint_cannot_suppress_a_real_alert() -> None:
    """The same collision, on the surface where it matters most: an attacker who controls
    alert text could otherwise construct a fingerprint matching a real alert's, and the real
    one would be dropped as a duplicate of the fabricated one."""
    from software_factory.economics import fingerprint_of

    assert fingerprint_of("deploy failed", "payments") != fingerprint_of("deploy failedpayments")


def test_a_failure_signature_is_not_forgeable_across_field_boundaries() -> None:
    from software_factory.improvement import Failure

    joined = Failure(run_id="r", work_item_id="w", stage="BUILDbuilder", agent="", gate="g")
    split = Failure(run_id="r", work_item_id="w", stage="BUILD", agent="builder", gate="g")

    assert joined.signature() != split.signature()
