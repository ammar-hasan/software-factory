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
        # Enough for the activity board to rebuild one work item, which is the point: the
        # ledger carries the title, the moves, and the blocker.
        (
            EntryType.WORK_ITEM_CREATED,
            "wi-1",
            {"title": "CSV importer mangles BOM headers", "workClass": "defect"},
        ),
        (EntryType.WORK_ITEM_TRANSITION, "wi-1", {"from": "INTAKE", "to": "TRIAGE"}),
        (EntryType.WORK_ITEM_TRANSITION, "wi-1", {"from": "TRIAGE", "to": "BUILD"}),
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


def test_the_activity_view_rebuilds_work_items_from_the_ledger(dashboard: str) -> None:
    """It used to serve an empty board with a note saying it was "empty by construction".

    FR-15.2 says derived state is rebuildable from the ledger, and this was the one view
    that did not do it -- the entries carry the title, every stage move, and the blocker.
    The note now says what a ledger genuinely cannot supply instead.
    """
    with urlopen(f"{dashboard}/api/activity") as response:
        body = json.loads(response.read())

    assert [row["id"] for row in body["workItems"]] == ["wi-1"]
    assert body["workItems"][0]["title"] == "CSV importer mangles BOM headers"
    assert body["workItems"][0]["stage"] == "BUILD"
    assert "not recorded there" in body["note"]


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


# --------------------------------------------------------- the six views, all reachable


def test_the_run_index_lists_runs_so_the_inspector_can_be_reached() -> None:
    """The inspector could only be reached by somebody who already knew a run id.

    Nothing in the product produced one: the client asked through a browser `prompt()`.
    An index is not a convenience here, it is the half of FR-15.6 that makes the other
    half usable.
    """
    from software_factory.ledger.entry import LedgerEntry
    from software_factory.observability.views import run_index

    entries = [
        LedgerEntry(
            seq=1,
            ts="2026-01-01T00:00:00Z",
            type=EntryType.RUN_STARTED,
            actor="conductor",
            subject="wi-1:build:0",
            payload={"agent": "builder", "stage": "BUILD", "tier": "local", "workItem": "wi-1"},
        ),
        LedgerEntry(
            seq=2,
            ts="2026-01-01T00:00:01Z",
            type=EntryType.MODEL_CALLED,
            actor="builder",
            subject="qwen",
            payload={"run": "wi-1:build:0", "costUnits": 0.25},
        ),
        LedgerEntry(
            seq=3,
            ts="2026-01-01T00:00:02Z",
            type=EntryType.GATE_EVALUATED,
            actor="builder",
            subject="wi-1",
            payload={"run": "wi-1:build:0", "gate": "tests-pass", "outcome": "fail"},
        ),
        LedgerEntry(
            seq=4,
            ts="2026-01-01T00:00:03Z",
            type=EntryType.RUN_FINISHED,
            actor="builder",
            subject="wi-1:build:0",
            payload={"status": "blocked", "reason": "a gate refused"},
        ),
    ]

    index = run_index(entries)

    assert index["total"] == 1
    row = index["runs"][0]
    assert row["id"] == "wi-1:build:0"
    assert row["agent"] == "builder"
    assert row["status"] == "blocked"
    assert row["modelCalls"] == 1
    assert row["costUnits"] == 0.25
    # A gate whose outcome is not a pass is a failed gate. Counting only literal "fail"
    # would let "refused" and "error" render as green.
    assert row["gatesFailed"] == 1


def test_the_run_index_costs_agree_with_the_inspector(tmp_path: Path) -> None:
    """Two places that report one run's cost must compute it the same way.

    Two numbers for one run is worse than one number, because a reader has to decide which
    to believe and has nothing to decide with.
    """
    from software_factory.observability.views import run_index

    ledger = ledger_with(
        tmp_path,
        (EntryType.RUN_STARTED, "r1", {"agent": "builder", "stage": "BUILD"}),
        (EntryType.MODEL_CALLED, "qwen", {"run": "r1", "costUnits": 0.5}),
        (EntryType.MODEL_CALLED, "qwen", {"run": "r1", "costUnits": 0.25}),
        (EntryType.RUN_FINISHED, "r1", {"status": "ok"}),
    )
    entries = list(ledger.read())

    assert run_index(entries)["runs"][0]["costUnits"] == run_inspector(entries, "r1")["costUnits"]


def test_every_declared_view_is_served(dashboard: str) -> None:
    """`views.py` has always said "the dashboard's six views" and the server offered three.

    `definition_view`, `evaluation_view` and `registry_view` were written, exported, tested
    in isolation and called by nothing -- the exact failure this codebase keeps finding in
    itself. A view reachable from no URL is a view no operator can look at.
    """
    from software_factory.observability.dash import VIEWS

    assert len(VIEWS) == 6
    for view in VIEWS:
        with urlopen(f"{dashboard}/api/{view}") as response:
            assert response.status == 200, view
            body = json.loads(response.read())
        assert "error" not in body, (view, body)


def test_a_view_whose_data_is_absent_says_which_and_why(dashboard: str) -> None:
    """Not an HTTP error: the factory is fine and one panel has nothing behind it.

    The fixture's ledger sits in a bare tmp directory with no factory tree, so the
    definition genuinely cannot be loaded. Serving 500, or an empty page, would both be
    wrong -- availability with a reason is how every metric in this codebase reports the
    same situation.
    """
    with urlopen(f"{dashboard}/api/definition") as response:
        body = json.loads(response.read())

    assert body["available"] is False
    assert "factory.yaml" in body["reason"] or "no factory" in body["reason"].lower()


def test_the_registry_view_reports_a_missing_memory_log_rather_than_zero(dashboard: str) -> None:
    """Zero memories and an unreadable memory log must not render as the same thing."""
    with urlopen(f"{dashboard}/api/registry") as response:
        body = json.loads(response.read())

    assert body["memory"]["available"] is False
    assert "memory" in body["memory"]["reason"]


def test_the_registry_view_reads_a_real_memory_log(tmp_path: Path) -> None:
    """And when the log is there, the numbers come from it."""
    from software_factory.memory import MemoryStore
    from software_factory.memory.records import Kind, Lane, Memory, Scope, Source, SourceKind
    from software_factory.observability.dash import DashboardData

    ledger_with(tmp_path, (EntryType.RUN_STARTED, "r1", {"agent": "builder"}))
    store = MemoryStore(tmp_path / "memory.jsonl")
    store.put(
        Memory(
            id=MemoryStore.new_id(),
            lane=Lane.CANDIDATE,
            kind=Kind.FACT,
            scope=Scope.REPOSITORY,
            scope_ref="acme/payments",
            content="The BOM shows up as a zero-width space in the first header.",
            provenance=(Source(kind=SourceKind.FILE, ref="src/importers/csv.py"),),
        ),
        op="admit",
        actor="builder",
        reason="observed while fixing wi-1",
    )

    body = DashboardData(tmp_path / "ledger.jsonl").payload("registry", {})

    assert body["memory"]["available"] is True
    assert body["memory"]["total"] == 1


def test_the_definition_view_loads_a_real_factory(tmp_path: Path) -> None:
    """The scaffold `sf init` writes must be legible to the dashboard beside it."""
    from software_factory.observability.dash import DashboardData
    from software_factory.scaffold import init_factory

    init_factory(tmp_path, name="payments")
    state = tmp_path / ".factory"
    state.mkdir(exist_ok=True)
    ledger_with(state, (EntryType.RUN_STARTED, "r1", {"agent": "builder"}))

    body = DashboardData(state / "ledger.jsonl").payload("definition", {})

    assert body["factory"] == "payments"
    # FR-2.1: a conductor and at least one specialist. A definition view that renders an
    # empty agent list for a real factory is worse than none.
    assert len(body["agents"]) >= 2


def test_the_client_lists_exactly_the_views_the_server_serves() -> None:
    """A nav button with no endpoint behind it is a dead link the operator finds first."""
    from software_factory.observability.dash import INDEX_HTML, VIEWS

    for view in VIEWS:
        assert f"'{view}'" in INDEX_HTML, view


def test_the_client_never_asks_the_operator_to_type_a_run_id() -> None:
    """`prompt()` was the only way to open the inspector, and it is not a way."""
    from software_factory.observability.dash import INDEX_HTML

    assert "prompt(" not in INDEX_HTML
