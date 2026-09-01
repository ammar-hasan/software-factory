"""Scheduled triggers, and the cron semantics that decide when work happens.

`TriggerSchedule` was a validated model nothing read: a factory could declare a nightly
sweep, pass `sf validate` and `sf lint` clean, and never run it once. That is worse than an
absent feature — an absent feature is discovered in the documentation, and this one is
discovered a month later by noticing that something never happened.

The tests that matter here are the ones about *when*. A scheduler that is wrong about time
is wrong quietly: it fires less often than its author expected, or seventy-two times at
once, and both look like the system working until somebody reads a bill or a backlog.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from software_factory.cli import app
from software_factory.ledger import EntryType, Ledger
from software_factory.orchestrator.schedule import (
    Cron,
    CronError,
    Schedule,
    ScheduledTrigger,
    describe,
)

runner = CliRunner()


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# --------------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    ("expression", "moment", "expected"),
    [
        ("* * * * *", "2026-03-04T09:13", True),
        ("0 3 * * *", "2026-03-04T03:00", True),
        ("0 3 * * *", "2026-03-04T03:01", False),
        ("*/15 * * * *", "2026-03-04T09:30", True),
        ("*/15 * * * *", "2026-03-04T09:31", False),
        ("0 9-17 * * *", "2026-03-04T17:00", True),
        ("0 9-17 * * *", "2026-03-04T18:00", False),
        ("0 0 1 * *", "2026-03-01T00:00", True),
        ("0 0 1 * *", "2026-03-02T00:00", False),
        ("30 4 * jan *", "2026-01-09T04:30", True),
        ("30 4 * jan *", "2026-02-09T04:30", False),
        # 2026-03-04 is a Wednesday.
        ("0 8 * * wed", "2026-03-04T08:00", True),
        ("0 8 * * thu", "2026-03-04T08:00", False),
        ("0 8 * * 3", "2026-03-04T08:00", True),
    ],
)
def test_cron_matches_the_instants_it_should(expression: str, moment: str, expected: bool) -> None:
    assert Cron.parse(expression).matches(at(moment)) is expected


def test_sunday_is_both_zero_and_seven() -> None:
    """Every cron anyone has used accepts both. Accepting one is how a schedule silently
    never fires."""
    # 2026-03-08 is a Sunday.
    assert Cron.parse("0 6 * * 0").matches(at("2026-03-08T06:00"))
    assert Cron.parse("0 6 * * 7").matches(at("2026-03-08T06:00"))


def test_day_of_month_and_day_of_week_are_ored_when_both_are_restricted() -> None:
    """Standard cron, and the direction of the mistake matters.

    `0 0 1 * mon` means the first of the month *or* every Monday. ANDing them would make it
    fire only on a first-of-the-month that is also a Monday — perhaps twice a year — and a
    schedule that fires less often than expected is the failure nobody notices.
    """
    cron = Cron.parse("0 0 1 * mon")

    assert cron.matches(at("2026-03-01T00:00")), "the first of the month did not match"
    assert cron.matches(at("2026-03-02T00:00")), "a Monday did not match"
    assert not cron.matches(at("2026-03-03T00:00"))


def test_a_restricted_day_of_month_alone_is_not_ored_with_every_weekday() -> None:
    cron = Cron.parse("0 0 1 * *")

    assert cron.matches(at("2026-03-01T00:00"))
    assert not cron.matches(at("2026-03-02T00:00"))


@pytest.mark.parametrize(
    "expression",
    [
        "0 3 * *",
        "0 3 * * * *",
        "sixty 3 * * *",
        "60 3 * * *",
        "0 24 * * *",
        "0 3 0 * *",
        "0 3 * 13 *",
        "0 3 * * 8",
        "*/0 3 * * *",
        "10-2 3 * * *",
        "0 3 * mon *",
    ],
)
def test_an_expression_this_scheduler_cannot_read_is_refused(expression: str) -> None:
    """Every cron that "helpfully" accepts an unparseable field runs something at a time
    nobody intended."""
    with pytest.raises(CronError):
        Cron.parse(expression)


def test_a_refusal_says_what_to_write_instead() -> None:
    with pytest.raises(CronError) as caught:
        Cron.parse("0 3 * *")

    assert caught.value.remediation


def test_cron_is_evaluated_in_utc_whatever_the_host_thinks() -> None:
    """A schedule that means a different instant on two machines fires twice or not at all
    when a factory moves, and shifts by an hour twice a year."""
    naive = datetime(2026, 3, 4, 3, 0)
    aware = datetime(2026, 3, 4, 3, 0, tzinfo=timezone(timedelta(hours=5)))

    cron = Cron.parse("0 3 * * *")
    assert cron.matches(naive)
    assert not cron.matches(aware), "an offset timestamp was read as if it were UTC"


# ------------------------------------------------------------------------- due-ness


def trigger(cron: str = "0 * * * *", name: str = "sweep") -> ScheduledTrigger:
    return ScheduledTrigger(
        automation="nightly", event="scheduled.tick", cron=Cron.parse(cron), name=name
    )


def test_a_trigger_that_has_never_fired_is_due_for_its_last_occurrence() -> None:
    schedule = Schedule(triggers=(trigger(),))

    due = schedule.due(now=at("2026-03-04T09:13"))

    assert len(due) == 1
    assert due[0].occurrence == at("2026-03-04T09:00")


def test_a_trigger_already_fired_for_that_occurrence_is_not_due_again() -> None:
    """Two evaluations seconds apart must not both claim the same scheduled instant."""
    schedule = Schedule(triggers=(trigger(),))
    schedule.last_fired = {"nightly:sweep": at("2026-03-04T09:00")}

    assert schedule.due(now=at("2026-03-04T09:13")) == []


def test_a_missed_window_fires_once_and_reports_what_it_skipped() -> None:
    """The single most expensive thing a scheduler can get wrong.

    Three days down with an hourly sweep is not seventy-two pieces of work waiting. It is
    one, and seventy-one identical duplicates — and firing them all is a spend event that
    arrives exactly when an operator is least able to watch, just after bringing something
    back up. The skipped count is still recorded: "we were down and it did not run" is a
    fact, and a scheduler that swallows it hides an outage.
    """
    schedule = Schedule(triggers=(trigger(),))
    schedule.last_fired = {"nightly:sweep": at("2026-03-01T09:00")}

    due = schedule.due(now=at("2026-03-04T09:13"))

    assert len(due) == 1, "a missed window produced more than one firing"
    assert due[0].occurrence == at("2026-03-04T09:00")
    assert due[0].skipped == 71


def test_the_trigger_id_survives_a_reschedule() -> None:
    """Changing 03:00 to 04:00 is a reschedule of the same job.

    An id derived from the cron text would make the edited schedule a brand new trigger
    with no history — so it would fire immediately on the first evaluation after the edit,
    at whatever time somebody happened to save the file.
    """
    before = ScheduledTrigger(automation="a", event="e", cron=Cron.parse("0 3 * * *"), name="sweep")
    after = ScheduledTrigger(automation="a", event="e", cron=Cron.parse("0 4 * * *"), name="sweep")

    assert before.id == after.id


def test_upcoming_reports_the_next_fire_for_each_trigger() -> None:
    schedule = Schedule(triggers=(trigger("0 3 * * *"),))

    ((_, when),) = schedule.upcoming(now=at("2026-03-04T09:13"))

    assert when == at("2026-03-05T03:00")


def test_a_reading_is_given_only_where_it_is_exact() -> None:
    """A natural-language cron renderer that is subtly wrong gets trusted."""
    assert describe(Cron.parse("0 3 * * *")) == "daily at 03:00 UTC"
    assert describe(Cron.parse("30 * * * *")) == "hourly at :30 UTC"
    assert describe(Cron.parse("* * * * *")) == "every minute"
    assert "17 9-17" in describe(Cron.parse("17 9-17 * * mon-fri"))


# ------------------------------------------------------------- definition and history


def factory_with_schedule(tmp_path: Path, cron: str = "0 3 * * *", enabled: bool = True) -> Path:
    from software_factory.scaffold import init_factory

    root = tmp_path / "f"
    init_factory(root, name="payments", owner="acme", repo="svc")
    directory = root / "automations" / "nightly"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "automation.md").write_text(
        "---\n"
        f"enabled: {'true' if enabled else 'false'}\n"
        "agent: scout\n"
        "triggers:\n"
        "  - provider: schedule\n"
        "    event: scheduled.tick\n"
        "    schedule:\n"
        "      name: sweep\n"
        f'      cron: "{cron}"\n'
        "---\n\n"
        "Sweep the repository for dead code.\n",
        encoding="utf-8",
    )
    return root


def test_schedules_are_read_from_the_definition(tmp_path: Path) -> None:
    from software_factory.definition import load_strict

    schedule = Schedule.from_definition(load_strict(factory_with_schedule(tmp_path)))

    assert [t.id for t in schedule.triggers] == ["nightly:sweep"]
    assert schedule.triggers[0].cron.source == "0 3 * * *"


def test_a_disabled_automation_declares_no_schedule(tmp_path: Path) -> None:
    """`enabled: false` is how an operator turns something off; a scheduler that ignored it
    would make that switch a lie."""
    from software_factory.definition import load_strict

    schedule = Schedule.from_definition(load_strict(factory_with_schedule(tmp_path, enabled=False)))

    assert schedule.triggers == ()


def test_firing_history_comes_from_the_ledger(tmp_path: Path) -> None:
    """A scheduler holding state in memory forgets across a restart and re-fires; one with
    its own state file has a second source of truth to reconcile."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        EntryType.SCHEDULE_FIRED,
        actor="scheduler",
        subject="nightly:sweep",
        payload={"occurrence": at("2026-03-04T03:00").isoformat()},
    )

    schedule = Schedule(triggers=(trigger("0 3 * * *"),)).with_history(ledger.read())

    assert schedule.last_fired["nightly:sweep"] == at("2026-03-04T03:00")
    assert schedule.due(now=at("2026-03-04T09:00")) == []


# ------------------------------------------------------------------ the CLI, end to end


def test_sf_schedule_list_shows_what_the_definition_asks_for(tmp_path: Path) -> None:
    root = factory_with_schedule(tmp_path)

    result = runner.invoke(app, ["schedule", "list", str(root), "--json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)["schedules"]
    assert rows[0]["trigger"] == "nightly:sweep"
    assert rows[0]["reading"] == "daily at 03:00 UTC"
    assert rows[0]["nextFire"]


def test_sf_schedule_run_fires_and_records_so_a_second_run_does_not(tmp_path: Path) -> None:
    """The whole point: due-ness is derived from the ledger, so firing is idempotent."""
    root = factory_with_schedule(tmp_path, cron="* * * * *")
    ledger_path = tmp_path / "ledger.jsonl"

    first = runner.invoke(
        app, ["schedule", "run", str(root), "--ledger", str(ledger_path), "--json"]
    )
    assert first.exit_code == 0, first.output
    assert len(json.loads(first.stdout)["fired"]) == 1

    second = runner.invoke(
        app, ["schedule", "run", str(root), "--ledger", str(ledger_path), "--json"]
    )
    assert json.loads(second.stdout)["fired"] == [], "the same occurrence fired twice"


def test_sf_schedule_run_dry_run_records_nothing(tmp_path: Path) -> None:
    root = factory_with_schedule(tmp_path, cron="* * * * *")
    ledger_path = tmp_path / "ledger.jsonl"

    result = runner.invoke(
        app, ["schedule", "run", str(root), "--ledger", str(ledger_path), "--dry-run", "--json"]
    )

    assert len(json.loads(result.stdout)["fired"]) == 1
    assert not ledger_path.exists() or not list(Ledger(ledger_path).read())


def test_sf_schedule_due_reports_without_firing(tmp_path: Path) -> None:
    root = factory_with_schedule(tmp_path, cron="* * * * *")
    ledger_path = tmp_path / "ledger.jsonl"

    result = runner.invoke(
        app, ["schedule", "due", str(root), "--ledger", str(ledger_path), "--json"]
    )

    assert len(json.loads(result.stdout)["due"]) == 1
    assert not ledger_path.exists() or not list(Ledger(ledger_path).read())
