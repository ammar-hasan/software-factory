"""The dashboard's views and its local-first server.

FR-15.8 asks for a local application with no external dependency, which is a constraint the
tests can actually check: the page loads nothing from elsewhere, the server binds loopback,
and nothing here is a decision channel.
"""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from software_factory.definition.models import Stage
from software_factory.ledger import EntryType, Ledger
from software_factory.memory.records import utc_now
from software_factory.observability import (
    activity_board,
    needs_attention,
    overview,
    run_inspector,
)
from software_factory.orchestrator.workitem import (
    Blocker,
    SourceContext,
    StageMachine,
    WorkClass,
    WorkItem,
)

# --------------------------------------------------------------------------- fixtures


def work_item(item_id: str = "wi-1", **kwargs) -> WorkItem:
    base: dict[str, object] = {
        "id": item_id,
        "factory": "payments",
        "title": "CSV importer mangles BOM headers",
        "request": "Uploading a UTF-8 CSV with a BOM names the first column oddly.",
        "source": SourceContext(provider="git-host", kind="issue", ref="acme/payments#42"),
        "work_class": WorkClass.DEFECT,
    }
    base.update(kwargs)
    return WorkItem(**base)  # type: ignore[arg-type]


def ledger_with(tmp_path: Path, *appends) -> Ledger:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for entry_type, subject, payload in appends:
        ledger.append(entry_type, actor="conductor", subject=subject, payload=payload)
    return ledger


# ------------------------------------------------------------------- needs attention


def test_a_healthy_work_item_needs_no_attention() -> None:
    """A board that flags everything is a board nobody reads twice."""
    assert not needs_attention(work_item()).needed


def test_a_blocked_work_item_is_flagged_with_what_would_clear_it() -> None:
    machine = StageMachine()
    item = work_item(stage=Stage.BUILD)
    machine.block(item, Blocker.EXTERNAL_DEPENDENCY, actor="conductor", action="restore the runner")

    attention = needs_attention(item)

    assert attention.needed
    assert "restore the runner" in attention.render()


def test_waiting_on_a_person_is_named_separately() -> None:
    """It is the one an operator can always clear themselves, and burying it in a list of
    blockers hides that."""
    machine = StageMachine()
    item = work_item(stage=Stage.BUILD)
    machine.block(item, Blocker.AWAITING_HUMAN, actor="conductor", action="approve the spec")

    assert "waiting on a person" in needs_attention(item).render()


def test_a_stalled_work_item_is_flagged() -> None:
    """Not a deadline -- the factory has no authority to impose one -- but a threshold past
    which "nothing has happened" is more likely a stall than progress."""
    item = work_item(created_at=utc_now() - timedelta(days=3))

    assert "no movement" in needs_attention(item).render()


def test_a_terminal_work_item_is_not_flagged_for_stalling() -> None:
    """Finished work is not stalled work, and flagging it would fill the board with things
    nobody can act on."""
    item = work_item(stage=Stage.COMPLETE, created_at=utc_now() - timedelta(days=30))

    assert "no movement" not in needs_attention(item).render()


def test_repeated_rework_is_flagged_as_not_converging() -> None:
    machine = StageMachine()
    item = work_item(stage=Stage.BUILD)
    for _ in range(2):
        machine.advance(item, Stage.REVIEW, actor="conductor", reason="built")
        machine.advance(item, Stage.BUILD, actor="critic", reason="changes requested")

    assert "not converging" in needs_attention(item).render()


def test_the_flag_is_mechanical_not_a_judgement() -> None:
    """A flag a model sets means something different each time. Same item, same state, same
    answer -- which is what makes a board's ordering trustworthy between viewings."""
    item = work_item(created_at=utc_now() - timedelta(days=3))
    now = utc_now()

    assert needs_attention(item, now=now).reasons == needs_attention(item, now=now).reasons


# ------------------------------------------------------------------- activity board


def test_flagged_work_items_sort_first() -> None:
    """The board exists to answer "what should I look at", and an ordering that buries the
    answer under everything else does not."""
    healthy = work_item("wi-2")
    stalled = work_item("wi-9", created_at=utc_now() - timedelta(days=3))

    board = activity_board([healthy, stalled])

    assert board["workItems"][0]["id"] == "wi-9"
    assert board["needingAttention"] == 1


def test_the_board_filters_by_stage() -> None:
    board = activity_board(
        [work_item("wi-1", stage=Stage.BUILD), work_item("wi-2", stage=Stage.REVIEW)],
        stage=Stage.REVIEW,
    )

    assert [row["id"] for row in board["workItems"]] == ["wi-2"]


# ------------------------------------------------------------------------ overview


def test_the_overview_reports_a_trend_against_the_preceding_window(tmp_path: Path) -> None:
    ledger = ledger_with(
        tmp_path,
        (EntryType.RUN_STARTED, "r1", {"agent": "builder"}),
        (EntryType.RUN_STARTED, "r2", {"agent": "builder"}),
    )

    body = overview(list(ledger.read()))

    assert body["current"]["runs"]["total"] == 2
    assert body["trend"]["runs"] == 2


def test_an_unavailable_metric_has_no_trend_rather_than_a_trend_of_zero(tmp_path: Path) -> None:
    """ "No change" and "we could not look" are different, and the second must not render as
    the first."""
    ledger = ledger_with(tmp_path, (EntryType.RUN_STARTED, "r1", {}))

    body = overview(list(ledger.read()))

    assert body["trend"]["changes_merged"] is None


# ------------------------------------------------------------------- run inspector


def test_the_run_inspector_reconstructs_a_run_from_the_ledger(tmp_path: Path) -> None:
    """The ledger is what survives. An inspector that only works while the run is in memory
    cannot be used for the runs anyone actually wants to inspect."""
    ledger = ledger_with(
        tmp_path,
        (EntryType.RUN_STARTED, "run-7", {"agent": "builder"}),
        (EntryType.TOOL_CALLED, "run-7", {"tool": "repo.read"}),
        (EntryType.GATE_EVALUATED, "run-7", {"gate": "tests-pass", "outcome": "pass"}),
        (EntryType.MODEL_CALLED, "run-7", {"costUnits": 3.5}),
    )

    body = run_inspector(list(ledger.read()), "run-7")

    assert body["toolCalls"] == 1
    assert body["gates"] == [{"gate": "tests-pass", "outcome": "pass"}]
    assert body["costUnits"] == 3.5
    assert "estimate" in body["costNote"]


def test_an_unknown_run_is_an_actionable_error(tmp_path: Path) -> None:
    ledger = ledger_with(tmp_path, (EntryType.RUN_STARTED, "run-7", {}))

    body = run_inspector(list(ledger.read()), "run-99")

    assert body["error"] == "run.unknown"
    assert "ledger segment" in body["remediation"]


# ------------------------------------------------------------------------- the server


@pytest.fixture
def dashboard(tmp_path: Path):
    from software_factory.observability.dash import serve

    ledger_with(
        tmp_path,
        (EntryType.RUN_STARTED, "r1", {"agent": "builder"}),
        (EntryType.GATE_EVALUATED, "wi-1", {"gate": "tests-pass", "outcome": "pass"}),
    )
    server = serve(tmp_path / "ledger.jsonl", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_the_dashboard_can_reach_its_own_api(dashboard: str) -> None:
    """The suite had no test that the client could load data at all.

    It checked the HTML for external URLs and never checked the one request the page
    actually makes -- which the CSP served alongside it forbade, so the entire client was
    inert under an enforcing browser and nothing here would have noticed.
    """
    with urlopen(f"{dashboard}/") as response:
        policy = response.headers["Content-Security-Policy"]
    with urlopen(f"{dashboard}/api/overview") as response:
        body = json.loads(response.read())

    assert "connect-src 'self'" in policy, "the page cannot fetch from its own origin"
    assert "current" in body


def test_the_dashboard_serves_a_page_with_no_external_resources(dashboard: str) -> None:
    """No framework, no CDN, no build step. A dashboard needing `npm install` to look at a
    factory running offline on a laptop fails PR-2 on the first day somebody tries it."""
    with urlopen(f"{dashboard}/") as response:
        body = response.read().decode("utf-8")

    assert "<title>software factory</title>" in body
    assert "http://" not in body.replace("http://127.0.0.1", "")
    assert "cdn" not in body.lower()
    assert "<script src" not in body


def test_the_dashboard_serves_the_overview_as_json(dashboard: str) -> None:
    with urlopen(f"{dashboard}/api/overview") as response:
        body = json.loads(response.read())

    assert body["view"] == "overview"
    assert body["current"]["runs"]["total"] == 1


def test_an_unknown_view_lists_the_ones_that_exist(dashboard: str) -> None:
    """404, not 200. A structured error returned as success is one a caller has to inspect
    to notice."""
    with pytest.raises(HTTPError) as caught:
        urlopen(f"{dashboard}/api/nonsense")

    assert caught.value.code == 404
    body = json.loads(caught.value.read())
    assert body["error"] == "view.unknown"
    assert "overview" in body["views"]


def test_the_run_view_requires_a_run_id(dashboard: str) -> None:
    with pytest.raises(HTTPError) as caught:
        urlopen(f"{dashboard}/api/run")

    assert caught.value.code == 400
    assert json.loads(caught.value.read())["error"] == "run.missing"


def test_the_activity_view_says_why_it_is_empty(dashboard: str) -> None:
    """An empty table reads as "no work". Saying the view needs orchestrator state reads as
    what it is."""
    with urlopen(f"{dashboard}/api/activity") as response:
        body = json.loads(response.read())

    assert body["workItems"] == []
    assert "not because the factory has no work" in body["note"]


def test_the_dashboard_offers_no_write_endpoint(dashboard: str) -> None:
    """Steering a live run is a decision channel and therefore authenticated and
    capability-checked (FR-25.5). An unauthenticated steering endpoint is a
    privilege-escalation path, so this server does not offer one."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(f"{dashboard}/api/overview", method="POST", data=b"{}")

    with pytest.raises(urllib.error.HTTPError) as caught:
        urlopen(request)

    assert caught.value.code in (404, 501)
