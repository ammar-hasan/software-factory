"""The git-host adapter, and the three outcome metrics it makes computable.

`changes_merged`, `autonomy` and `cycle_time_to_merge` have always had a row, a reason and
no implementation: "no git-host adapter is configured, so this cannot be observed". That
reason was honest and it was also a standing admission, because FR-15.14 says every quality
claim is unvalidated until O-2, O-3 and O-4 can be observed.

The dangerous one is autonomy. It is a share with zero in the numerator's condition, so a
factory that cannot tell whose commits are on a branch and treats *unknown* as *zero* reports
perfect autonomy for every change it has ever produced -- the most flattering possible
reading of the least information.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from software_factory.intake.adapters import Adapter, Deduplicator, Health
from software_factory.intake.events import Provider
from software_factory.integrations.git_host import (
    ChangeState,
    GitHostAdapter,
    GitHostCredentials,
    GitHostError,
    GitHostSignatureError,
    signature_header,
    verify_signature,
)
from software_factory.ledger import EntryType, Ledger
from software_factory.observability.metrics import Availability, Window, compute
from software_factory.providers.base import ProviderError
from software_factory.providers.transport import Response

SECRET = "a-webhook-secret"
TOKEN = "ghp-not-a-real-token-for-tests"
GIT_HOST = frozenset({"git-host"})


class FakeTransport:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers) or [Response(status=200, body={"ok": True})]
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        timeout_s: float = 30.0,
    ) -> Response:
        self.calls.append({"method": method, "url": url, "headers": headers, "payload": payload})
        answer = self.answers[0] if len(self.answers) == 1 else self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def post_json(self, url: str, **kwargs: Any) -> Response:
        return self.request("POST", url, **kwargs)


def adapter(*answers: Any, login: str = "payments-factory", repos: tuple[str, ...] = ()):
    return GitHostAdapter(
        credentials=GitHostCredentials(token=TOKEN, webhook_secret=SECRET, factory_login=login),
        repositories=frozenset(repos),
        transport=FakeTransport(*answers),
    )


def pull_payload(**overrides: Any) -> dict[str, Any]:
    pull = {
        "number": 128,
        "title": "Strip the BOM before parsing headers",
        "body": "Closes the BOM issue.",
        "merged": True,
        "draft": False,
        "created_at": "2026-08-30T09:00:00Z",
        "merged_at": "2026-08-30T15:00:00Z",
        "updated_at": "2026-08-30T15:00:00Z",
        "html_url": "https://git.test/acme/payments/pull/128",
        "user": {"login": "payments-factory"},
        "head": {"ref": "factory/wi_7cf13c5c9029"},
    }
    pull.update(overrides.pop("pull", {}))
    return {
        "_event": "pull_request",
        "action": overrides.pop("action", "closed"),
        "repository": {"full_name": "acme/payments"},
        "sender": {"login": "amaya"},
        "pull_request": pull,
        **overrides,
    }


def issue_payload(**overrides: Any) -> dict[str, Any]:
    issue = {
        "number": 42,
        "title": "CSV importer mangles BOM headers",
        "body": "Uploading a UTF-8 CSV with a BOM names the first column oddly.",
        "updated_at": "2026-08-29T12:00:00Z",
        "html_url": "https://git.test/acme/payments/issues/42",
        "user": {"login": "amaya"},
        "labels": [{"name": "bug"}, {"name": "factory"}],
    }
    issue.update(overrides.pop("issue", {}))
    return {
        "_event": "issues",
        "action": overrides.pop("action", "labeled"),
        "repository": {"full_name": "acme/payments"},
        "sender": {"login": "amaya"},
        "issue": issue,
        **overrides,
    }


# ------------------------------------------------------------------- delivery signatures


def test_a_correctly_signed_delivery_verifies() -> None:
    body = b'{"action":"opened"}'
    header = signature_header(webhook_secret=SECRET, body=body)

    verify_signature(webhook_secret=SECRET, body=body, signature=header["X-Hub-Signature-256"])


def test_a_delivery_signed_with_another_secret_is_refused() -> None:
    body = b'{"action":"opened"}'
    forged = signature_header(webhook_secret="someone-elses", body=body)

    with pytest.raises(GitHostSignatureError, match="does not match"):
        verify_signature(webhook_secret=SECRET, body=body, signature=forged["X-Hub-Signature-256"])


def test_a_tampered_body_is_refused() -> None:
    body = b'{"repository":"acme/payments"}'
    header = signature_header(webhook_secret=SECRET, body=body)

    with pytest.raises(GitHostSignatureError):
        verify_signature(
            webhook_secret=SECRET,
            body=b'{"repository":"acme/production"}',
            signature=header["X-Hub-Signature-256"],
        )


def test_verification_without_a_secret_refuses_rather_than_passing() -> None:
    with pytest.raises(GitHostSignatureError, match="no webhook secret"):
        verify_signature(webhook_secret="", body=b"{}", signature="sha256=x")


def test_signing_without_a_secret_is_refused() -> None:
    with pytest.raises(GitHostSignatureError, match="without a webhook secret"):
        signature_header(webhook_secret="", body=b"{}")


# --------------------------------------------------------------------------- normalising


def test_an_issue_becomes_a_factory_event() -> None:
    event = adapter().normalise(issue_payload())

    assert event is not None
    assert event.provider is Provider.GIT_HOST
    assert event.title == "CSV importer mangles BOM headers"
    assert event.origin.ref == "acme/payments#42"
    assert event.attributes["labels"] == ["bug", "factory"]


def test_backpressure_counts_the_repository_not_the_issue() -> None:
    """One repository emitting a thousand issues is the failure a per-issue bucket
    cannot see (FR-26.3)."""
    event = adapter().normalise(issue_payload())

    assert event is not None
    assert event.origin.source_key == "acme/payments"


def test_a_repository_this_factory_does_not_work_is_ignored() -> None:
    assert adapter(repos=("acme/other",)).normalise(issue_payload()) is None


def test_the_factorys_own_comment_does_not_retrigger_intake() -> None:
    """Posting a status update on an issue must not start work on the issue it is about."""
    raw = issue_payload(sender={"login": "payments-factory"})

    assert adapter().normalise(raw) is None


def test_redelivery_of_an_unchanged_event_is_the_same_event() -> None:
    """The delivery GUID is not the event id.

    Redelivering from the host's UI issues a *new* GUID for the same underlying event, so
    keying on it makes every manual redelivery a second work item.
    """
    first = adapter().normalise(issue_payload())
    again = adapter().normalise(issue_payload())

    assert first is not None and again is not None
    assert first.id == again.id

    dedupe = Deduplicator()
    dedupe.record(first)
    assert dedupe.seen(again)


def test_a_changed_issue_is_a_different_event() -> None:
    """Identity keyed on the subject's own updated-at, so an edit is a new event and an
    unchanged redelivery is not."""
    first = adapter().normalise(issue_payload())
    edited = adapter().normalise(issue_payload(issue={"updated_at": "2026-08-29T13:00:00Z"}))

    assert first is not None and edited is not None
    assert first.id != edited.id


def test_an_action_this_factory_does_not_act_on_is_ignored() -> None:
    assert adapter().normalise(issue_payload(action="assigned")) is None


def test_an_unhandled_webhook_is_ignored_rather_than_an_error() -> None:
    """A busy repository emits far more than any factory asked for."""
    assert adapter().normalise({**issue_payload(), "_event": "push"}) is None


# -------------------------------------------------------------------- change observation


def test_a_merged_change_is_observed_as_merged() -> None:
    observation = adapter().observe(pull_payload())

    assert observation is not None
    assert observation.state is ChangeState.MERGED
    assert observation.change == "acme/payments#128"
    assert observation.work_item == "wi_7cf13c5c9029"
    assert observation.cycle_time == timedelta(hours=6)


def test_a_change_closed_unmerged_is_not_a_merge() -> None:
    """Folding a rejection into "not merged yet" is how a rejection rate hides."""
    observation = adapter().observe(pull_payload(pull={"merged": False, "merged_at": None}))

    assert observation is not None
    assert observation.state is ChangeState.CLOSED


def test_a_push_to_the_branch_is_not_a_state_change() -> None:
    """Recording `synchronize` would count one change once per push."""
    assert adapter().observe(pull_payload(action="synchronize")) is None


def test_a_human_authored_change_carries_no_work_item() -> None:
    """Read from the branch, not the body: a body is edited by people, a branch name is not.

    This keeps a human's own pull request out of the factory's numerators.
    """
    observation = adapter().observe(pull_payload(pull={"head": {"ref": "amaya/hotfix"}}))

    assert observation is not None
    assert observation.work_item == ""


def test_human_commits_are_unknown_rather_than_zero_without_a_commit_list() -> None:
    """The single most dangerous default in this file.

    Autonomy is the share of merged changes with *zero* human commits. Unknown recorded as
    zero reports perfect autonomy for every change the factory ever produced.
    """
    observation = adapter().observe(pull_payload())

    assert observation is not None
    assert observation.human_commits is None


def test_human_commits_are_counted_when_the_commit_list_is_supplied() -> None:
    observation = adapter().observe(
        pull_payload(
            _commits=[
                {"author": {"login": "payments-factory"}},
                {"author": {"login": "payments-factory"}},
                {"author": {"login": "amaya"}},
            ]
        )
    )

    assert observation is not None
    assert observation.human_commits == 1


def test_without_the_factorys_own_login_commits_cannot_be_attributed() -> None:
    """A factory that does not know its own login cannot tell its commits from anyone's."""
    observation = adapter(login="").observe(pull_payload(_commits=[{"author": {"login": "amaya"}}]))

    assert observation is not None
    assert observation.human_commits is None


# ------------------------------------------------------------------------- the metrics


def ledger_with(tmp_path: Path, *observations: dict[str, Any]) -> Ledger:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for payload in observations:
        ledger.append(
            EntryType.CHANGE_OBSERVED,
            actor="git-host",
            subject=str(payload["change"]),
            payload=payload,
        )
    return ledger


def merged(change: str, *, human: int | None, hours: int = 6) -> dict[str, Any]:
    return {
        "change": change,
        "state": "merged",
        "openedAt": "2026-08-30T09:00:00+00:00",
        "mergedAt": f"2026-08-30T{9 + hours:02d}:00:00+00:00",
        "humanCommits": human,
    }


def measure(ledger: Ledger, name: str):
    report = compute(
        list(ledger.read()), window=Window.last(timedelta(days=3650)), integrations=GIT_HOST
    )
    found = report.measure(name)
    assert found is not None, name
    return found


def test_changes_merged_counts_observed_merges(tmp_path: Path) -> None:
    ledger = ledger_with(tmp_path, merged("a#1", human=0), merged("a#2", human=2))

    assert measure(ledger, "changes_merged").value == 2.0


def test_a_change_observed_twice_is_one_change(tmp_path: Path) -> None:
    """Counting observations would let a redelivery inflate the merge rate."""
    ledger = ledger_with(tmp_path, merged("a#1", human=0), merged("a#1", human=0))

    assert measure(ledger, "changes_merged").value == 1.0


def test_with_no_observation_a_merge_rate_is_insufficient_not_zero(tmp_path: Path) -> None:
    """A handoff is the factory saying it produced something; only the repository can say
    it was taken."""
    ledger = Ledger(tmp_path / "ledger.jsonl")

    assert measure(ledger, "changes_merged").availability is Availability.INSUFFICIENT_DATA


def test_autonomy_is_the_share_with_no_human_commits(tmp_path: Path) -> None:
    ledger = ledger_with(
        tmp_path, merged("a#1", human=0), merged("a#2", human=0), merged("a#3", human=3)
    )

    found = measure(ledger, "autonomy")
    assert found.value == pytest.approx(2 / 3, abs=1e-4)
    assert found.sample == 3


def test_unattributed_changes_are_excluded_from_autonomy_not_counted_as_autonomous(
    tmp_path: Path,
) -> None:
    """The trap this whole adapter is built to avoid.

    Two merged changes: one known clean, one with no attribution. Autonomy must be 1.0 over
    a sample of one with the exclusion stated -- never 1.0 over a sample of two, which reads
    as a factory that needed no human help on either.
    """
    ledger = ledger_with(tmp_path, merged("a#1", human=0), merged("a#2", human=None))

    found = measure(ledger, "autonomy")
    assert found.value == 1.0
    assert found.sample == 1
    assert found.excludes, "the unattributed change vanished without a word"


def test_autonomy_with_nothing_attributable_is_insufficient_not_perfect(tmp_path: Path) -> None:
    ledger = ledger_with(tmp_path, merged("a#1", human=None), merged("a#2", human=None))

    assert measure(ledger, "autonomy").availability is Availability.INSUFFICIENT_DATA


def test_cycle_time_is_the_median_hours_to_merge(tmp_path: Path) -> None:
    ledger = ledger_with(
        tmp_path,
        merged("a#1", human=0, hours=2),
        merged("a#2", human=0, hours=6),
        merged("a#3", human=0, hours=10),
    )

    found = measure(ledger, "cycle_time_to_merge")
    assert found.value == 6.0
    assert "median" in found.unit


def test_without_a_git_host_the_metrics_say_so_rather_than_reporting_zero(
    tmp_path: Path,
) -> None:
    """Unchanged behaviour, asserted so implementing the metrics did not quietly remove it."""
    ledger = ledger_with(tmp_path, merged("a#1", human=0))
    report = compute(list(ledger.read()), window=Window.last(timedelta(days=3650)))

    for name in ("changes_merged", "autonomy", "cycle_time_to_merge"):
        found = report.measure(name)
        assert found is not None and found.availability is Availability.UNAVAILABLE
        assert "no git-host adapter is configured" in (found.reason or "")


# ------------------------------------------------------------------------ health, secrets


def test_a_working_adapter_is_healthy() -> None:
    assert adapter().health().status is Health.HEALTHY


def test_not_knowing_our_own_login_is_degraded_because_autonomy_cannot_be_computed() -> None:
    report = adapter(login="").health()

    assert report.status is Health.DEGRADED
    assert "autonomy" in report.detail


def test_rate_limiting_is_degraded_not_unavailable() -> None:
    report = adapter(ProviderError("api returned 403", retryable=False, status=403)).health()

    assert report.status is Health.DEGRADED


def test_an_unreachable_host_is_unavailable_with_a_reason() -> None:
    report = adapter(ProviderError("cannot reach api", retryable=True)).health()

    assert report.status is Health.UNAVAILABLE
    assert report.detail


def test_credentials_never_print_their_secrets() -> None:
    text = repr(GitHostCredentials(token=TOKEN, webhook_secret=SECRET))

    assert TOKEN not in text
    assert SECRET not in text


def test_reaching_the_host_without_a_token_is_refused() -> None:
    host = GitHostAdapter(
        credentials=GitHostCredentials(token="", webhook_secret=SECRET),
        transport=FakeTransport(),
    )

    assert host.authenticate() is False


def test_normalising_a_saved_delivery_needs_no_credentials_at_all() -> None:
    """FR-18.10's local parity. This path must stay offline."""
    host = GitHostAdapter(credentials=GitHostCredentials(), transport=FakeTransport())

    assert host.normalise(issue_payload()) is not None
    assert host.transport.calls == []


def test_a_read_carries_no_body() -> None:
    """A GET with a JSON body is a read some hosts treat as something else."""
    host = adapter()
    host.health()

    assert host.transport.calls[-1]["method"] == "GET"
    assert host.transport.calls[-1]["payload"] is None


def test_the_adapter_satisfies_the_protocol() -> None:
    assert isinstance(adapter(), Adapter)
    assert adapter().provider is Provider.GIT_HOST


def test_opening_a_change_returns_its_reference() -> None:
    host = adapter(Response(status=201, body={"number": 128}))

    assert (
        host.open_change(
            repository="acme/payments",
            title="Strip the BOM",
            body="see the evidence bundle",
            head="factory/wi_1",
        )
        == "acme/payments#128"
    )


def test_a_host_that_accepts_a_change_but_names_no_number_is_an_error() -> None:
    host = adapter(Response(status=201, body={}))

    with pytest.raises(GitHostError, match="no number"):
        host.open_change(repository="acme/payments", title="t", body="b", head="factory/wi_1")


# ------------------------------------------------------------------ the CLI, end to end


def test_sf_change_observe_records_an_observation(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from software_factory.cli import app

    saved = tmp_path / "pr.json"
    saved.write_text(json.dumps(pull_payload()))
    ledger_path = tmp_path / "ledger.jsonl"

    result = CliRunner().invoke(
        app,
        ["change", "observe", str(saved), "--ledger", str(ledger_path), "--json"],
        env={"SF_GIT_HOST_FACTORY_LOGIN": "payments-factory"},
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["observation"]["state"] == "merged"
    assert measure(Ledger(ledger_path), "changes_merged").value == 1.0


def test_sf_change_observe_with_commits_makes_autonomy_computable(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from software_factory.cli import app

    saved = tmp_path / "pr.json"
    saved.write_text(json.dumps(pull_payload()))
    commits = tmp_path / "commits.json"
    commits.write_text(json.dumps([{"author": {"login": "payments-factory"}}]))
    ledger_path = tmp_path / "ledger.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "change",
            "observe",
            str(saved),
            "--ledger",
            str(ledger_path),
            "--commits",
            str(commits),
            "--json",
        ],
        env={"SF_GIT_HOST_FACTORY_LOGIN": "payments-factory"},
    )

    assert result.exit_code == 0, result.output
    assert measure(Ledger(ledger_path), "autonomy").value == 1.0
