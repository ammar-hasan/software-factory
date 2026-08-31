"""Egress enumeration and recording evidence.

Two requirements, one shared principle: an answer that omits what it could not determine
reads as a complete answer. `sf audit --egress` reports indeterminate destinations rather
than dropping them, and a missing recording is an artifact rather than an absence.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from software_factory.definition.egress import Certainty, enumerate_egress
from software_factory.definition.loader import load_strict
from software_factory.evals.recording import (
    NotRecorded,
    Recording,
    RecordingKind,
    RecordingPolicy,
    Unavailable,
    visual_evidence_statement,
)
from software_factory.scaffold import init_factory

# ---------------------------------------------------------------------------- egress


def factory(tmp_path: Path, **edits: str) -> Path:
    init_factory(tmp_path, name="ref", owner="amaya", repo="service")
    runner = tmp_path / "runners" / "default.yaml"
    text = runner.read_text(encoding="utf-8")
    for old, new in edits.items():
        text = text.replace(old, new)
    runner.write_text(text, encoding="utf-8")
    return tmp_path


def test_the_reference_scaffold_is_offline_capable(tmp_path: Path) -> None:
    """PR-2: local is the reference implementation, not a degraded mode. A scaffold that
    reached the network on day one would make that false out of the box."""
    report = enumerate_egress(load_strict(factory(tmp_path)))

    assert report.offline_capable
    assert report.destinations == ()


def test_a_declared_allowlist_is_enumerated(tmp_path: Path) -> None:
    root = factory(
        tmp_path,
        **{"network: none": "network: allowlist\nnetworkAllowlist: [pypi.org, proxy.internal]"},
    )

    report = enumerate_egress(load_strict(root))

    targets = {d.target for d in report.by_certainty(Certainty.DECLARED)}
    assert {"pypi.org", "proxy.internal"} <= targets
    assert not report.offline_capable


def test_an_open_network_is_reported_as_reaching_anything(tmp_path: Path) -> None:
    """ "Open" is not a destination list, and rendering it as an empty one would read as
    reaching nothing."""
    report = enumerate_egress(load_strict(factory(tmp_path, **{"network: none": "network: open"})))

    wildcard = next(d for d in report.destinations if d.target == "*")
    assert wildcard.certainty is Certainty.DECLARED
    assert "anything the machine can reach" in wildcard.detail


def test_setup_commands_are_reported_as_not_determinable(tmp_path: Path) -> None:
    """A `pip install` reaches an index whose host is in a config file this cannot read.
    Omitting it would make the report read as complete."""
    root = factory(tmp_path, **{"setupCommands: []": "setupCommands:\n  - pip install -e ."})

    report = enumerate_egress(load_strict(root))

    indeterminate = report.by_certainty(Certainty.INDETERMINATE)
    assert indeterminate
    assert "arbitrary shell" in indeterminate[0].detail


def test_a_definition_with_indeterminate_egress_is_not_offline_capable(tmp_path: Path) -> None:
    """A setup command that might install a package is exactly the thing that makes "this
    factory is offline" false while leaving the declared destination list empty."""
    root = factory(tmp_path, **{"setupCommands: []": "setupCommands:\n  - pip install -e ."})

    assert not enumerate_egress(load_strict(root)).offline_capable


def test_every_destination_names_where_it_came_from(tmp_path: Path) -> None:
    """An operator reading an unexpected destination needs to know where to go and change
    it."""
    root = factory(tmp_path, **{"network: none": "network: allowlist\nnetworkAllowlist: [x.test]"})

    for destination in enumerate_egress(load_strict(root)).destinations:
        assert destination.source.strip()


def test_the_report_carries_its_own_caveat(tmp_path: Path) -> None:
    root = factory(tmp_path, **{"setupCommands: []": "setupCommands:\n  - curl example.test"})

    body = enumerate_egress(load_strict(root)).as_dict()

    assert "would read as a complete list" in str(body["note"])


# ------------------------------------------------------------------------- recording


def test_a_truncated_recording_must_say_why() -> None:
    """FR-22.7. A half-recording rendered as a recording is worse than none: a reviewer
    watches it, sees the change work up to the cut, and approves."""
    with pytest.raises(ValueError, match="reads as a complete recording"):
        Recording(
            id="r1",
            kind=RecordingKind.BROWSER,
            location="rec/r1.cast",
            duration=timedelta(seconds=12),
            digest="abc",
            truncated=True,
        )


def test_a_truncated_recording_carries_the_flag_into_the_evidence_bundle() -> None:
    """`EvidenceItem.truncated` is a field rather than a property precisely so this cannot be
    lost in translation."""
    recording = Recording(
        id="r1",
        kind=RecordingKind.SCREEN,
        location="rec/r1.mp4",
        duration=timedelta(seconds=12),
        digest="abc",
        truncated=True,
        truncated_reason="the recorder was killed at the wall clock",
    )

    item = recording.as_evidence()

    assert item.truncated
    assert "truncated" in item.summary()


def test_a_missing_recording_is_an_artifact_not_an_absence() -> None:
    """Without this, "we did not record" and "there was nothing to record" produce the same
    empty bundle, and a reviewer cannot tell a change that skipped visual verification from
    one that never needed it."""
    absent = NotRecorded(kind=RecordingKind.BROWSER, reason=Unavailable.NO_DISPLAY)

    item = absent.as_evidence()

    assert item.location.startswith("absent://")
    assert "no display" in absent.describe()


def test_each_reason_carries_its_own_remediation() -> None:
    """A reviewer seeing "recording unavailable" for the fortieth time needs to know whether
    it is the same cause, and `sf doctor` can only fix a cause it can name."""
    for reason in Unavailable:
        absent = NotRecorded(kind=RecordingKind.SCREEN, reason=reason)
        assert absent.remediation.strip()
        assert reason.value.replace("_", " ") in absent.describe()


def test_two_absences_with_the_same_cause_are_the_same_artifact() -> None:
    first = NotRecorded(kind=RecordingKind.BROWSER, reason=Unavailable.NO_DISPLAY)
    second = NotRecorded(kind=RecordingKind.BROWSER, reason=Unavailable.NO_DISPLAY)
    different = NotRecorded(kind=RecordingKind.BROWSER, reason=Unavailable.NOT_ENABLED)

    assert first.as_evidence().digest == second.as_evidence().digest
    assert first.as_evidence().digest != different.as_evidence().digest


def test_the_absence_statement_is_explicit_text() -> None:
    """FR-22.3's "never to silence". "Explicit" means a reviewer reads it, and a flag in a
    JSON payload nobody renders is not explicit."""
    statement = visual_evidence_statement(
        [NotRecorded(kind=RecordingKind.SCREEN, reason=Unavailable.NO_DISPLAY)]
    )

    assert "Visual evidence is absent" in statement
    assert "display server" in statement


def test_no_attempt_at_all_is_stated_differently_from_a_failed_attempt() -> None:
    """ "We tried and could not" and "we never tried" are different facts about a change."""
    assert "was attempted" in visual_evidence_statement([])
    assert "is absent" in visual_evidence_statement(
        [NotRecorded(kind=RecordingKind.BROWSER, reason=Unavailable.NO_BROWSER)]
    )


def test_a_complete_recording_reports_plainly() -> None:
    statement = visual_evidence_statement(
        [
            Recording(
                id="r1",
                kind=RecordingKind.BROWSER,
                location="rec/r1.mp4",
                duration=timedelta(seconds=30),
                digest="abc",
            )
        ]
    )

    assert statement == "1 visual recording(s) attached."


def test_terminal_recordings_do_not_satisfy_a_visual_expectation() -> None:
    """A command transcript is not a screenshot, and counting it as one would let every
    user-facing change claim visual evidence it does not have."""
    statement = visual_evidence_statement(
        [
            Recording(
                id="r1",
                kind=RecordingKind.TERMINAL,
                location="rec/r1.cast",
                duration=timedelta(seconds=30),
                digest="abc",
            )
        ]
    )

    assert "was attempted" in statement


def test_the_policy_expects_visual_evidence_only_for_user_facing_work() -> None:
    """A chore that renames a variable needs no screenshot, and demanding one teaches people
    to attach meaningless ones."""
    policy = RecordingPolicy()

    assert policy.expects_visual("feature", user_facing=True)
    assert not policy.expects_visual("feature", user_facing=False)
    assert not policy.expects_visual("chore", user_facing=True)


def test_disabling_recording_removes_the_expectation_not_the_statement() -> None:
    """FR-22.3: optional but first-class. Off means no expectation; it does not mean the
    absence stops being recorded."""
    policy = RecordingPolicy(enabled=False)

    assert not policy.expects_visual("feature", user_facing=True)
    assert "is absent" in visual_evidence_statement(
        [NotRecorded(kind=RecordingKind.SCREEN, reason=Unavailable.NOT_ENABLED)]
    )


# ------------------------------------------------- model endpoints resolve to hosts


def factory_with_tier(tmp_path: Path, *, provider: str, local: str = "true") -> Path:
    """A scaffold whose first tier names `provider`, so egress can be asked about it."""
    init_factory(tmp_path, name="ref", owner="amaya", repo="service")
    path = tmp_path / "factory.yaml"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "      provider: local\n      model: local-model\n      contextWindow: 32000",
        f"      provider: {provider}\n      model: local-model\n      contextWindow: 32000",
        1,
    )
    text = text.replace(
        "      local: true\n      capabilities: [code, tools]",
        f"      local: {local}\n      capabilities: [code, tools]",
        1,
    )
    path.write_text(text, encoding="utf-8")
    return tmp_path


def test_a_hosted_tier_reports_its_actual_endpoint(tmp_path: Path) -> None:
    """An operator cannot approve egress to "anthropic (model endpoint)".

    The report used to name the provider and mark it implied, because nothing could turn
    a provider name into an address. The registry can, so it does.
    """
    report = enumerate_egress(load_strict(factory_with_tier(tmp_path, provider="anthropic")))

    hosted = [d for d in report.destinations if "anthropic.com" in d.target]
    assert hosted, [d.target for d in report.destinations]
    assert hosted[0].certainty is Certainty.DECLARED
    assert not report.offline_capable


def test_a_local_tier_is_not_reported_as_a_destination(tmp_path: Path) -> None:
    """Loopback inference is not egress, and listing it teaches operators to skim."""
    report = enumerate_egress(load_strict(factory_with_tier(tmp_path, provider="local")))

    assert report.offline_capable
    assert not [d for d in report.destinations if "11434" in d.target]


def test_an_unknown_provider_is_indeterminate_not_implied(tmp_path: Path) -> None:
    """We cannot say where it goes; "somewhere, probably" is the invention to avoid."""
    report = enumerate_egress(load_strict(factory_with_tier(tmp_path, provider="mystery-llm")))

    unknown = [d for d in report.destinations if "mystery-llm" in d.target]
    assert unknown and unknown[0].certainty is Certainty.INDETERMINATE
    assert not report.offline_capable


def test_a_local_flag_does_not_override_a_hosted_provider(tmp_path: Path) -> None:
    """The flag is the author's assertion; the endpoint is the fact.

    A tier marked `local: true` pointed at a hosted provider would otherwise vanish from
    the egress report entirely -- the one case where the report would be silently wrong in
    the direction that matters.
    """
    report = enumerate_egress(
        load_strict(factory_with_tier(tmp_path, provider="openai", local="true"))
    )

    hosted = [d for d in report.destinations if "openai.com" in d.target]
    assert hosted, [d.target for d in report.destinations]
    assert "declares `local: true`" in hosted[0].detail
