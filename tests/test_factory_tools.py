"""The factory's own tool surface, and the leases it takes.

Four constraints, and every test is about one of them: one record for locally continued work,
the server never touches the caller's files, picking up does not claim, and unpushed work is
invisible.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from software_factory.definition.models import Stage
from software_factory.factory_tools import (
    ActionClass,
    FactoryToolServer,
    Held,
    Lease,
    LeaseBook,
)
from software_factory.memory.records import utc_now
from software_factory.orchestrator.workitem import SourceContext, WorkClass, WorkItem

# --------------------------------------------------------------------------- fixtures


def work_item(item_id: str = "wi-1", **kwargs) -> WorkItem:
    base: dict[str, object] = {
        "id": item_id,
        "factory": "payments",
        "title": "CSV importer mangles BOM headers",
        "request": "Uploading a UTF-8 CSV with a BOM names the first column oddly.",
        "source": SourceContext(provider="git-host", kind="issue", ref="acme/payments#42"),
        "work_class": WorkClass.DEFECT,
        "base_commit": "abc123",
    }
    base.update(kwargs)
    return WorkItem(**base)  # type: ignore[arg-type]


def server(*items: WorkItem, **kwargs) -> FactoryToolServer:
    return FactoryToolServer(
        factory_name="payments",
        work_items={item.id: item for item in (items or (work_item(),))},
        **kwargs,
    )


# ----------------------------------------------------------------------------- leases


def test_reading_is_unleased_and_acting_externally_is_not() -> None:
    """The lease is keyed on (work item, action class) rather than on the work item: two
    actors reading one item is fine, two actors both opening a change for it is not."""
    book = LeaseBook()

    assert book.held("wi-1", ActionClass.OPEN_CHANGE) is None
    assert isinstance(
        book.acquire("wi-1", ActionClass.OPEN_CHANGE, holder="run-1", intent="x"), Lease
    )


def test_a_second_actor_is_told_who_holds_the_lease_and_what_they_are_doing() -> None:
    """FR-19.5. A refusal that does not name the holder just moves the race one retry later."""
    book = LeaseBook()
    book.acquire("wi-1", ActionClass.OPEN_CHANGE, holder="run-1", intent="opening the change")

    refused = book.acquire("wi-1", ActionClass.OPEN_CHANGE, holder="run-2", intent="also opening")

    assert isinstance(refused, Held)
    assert "run-1" in refused.message
    assert "opening the change" in refused.message


def test_two_action_classes_on_one_work_item_do_not_conflict() -> None:
    book = LeaseBook()
    book.acquire("wi-1", ActionClass.OPEN_CHANGE, holder="run-1", intent="opening")

    other = book.acquire("wi-1", ActionClass.COMMENT, holder="run-2", intent="commenting")

    assert isinstance(other, Lease)


def test_a_lease_expires_without_renewal() -> None:
    """A lease that outlives its holder's crash blocks the work item until a human
    intervenes."""
    book = LeaseBook()
    now = utc_now()
    book.acquire(
        "wi-1",
        ActionClass.OPEN_CHANGE,
        holder="run-1",
        intent="opening",
        ttl=timedelta(minutes=5),
        now=now,
    )

    later = book.acquire(
        "wi-1",
        ActionClass.OPEN_CHANGE,
        holder="run-2",
        intent="opening",
        now=now + timedelta(minutes=6),
    )

    assert isinstance(later, Lease)


def test_expiry_is_computed_on_read_not_swept() -> None:
    """A sweep would leave a lease "held" until something ran, which is exactly the window
    where a crashed holder blocks a work item nobody is working on."""
    book = LeaseBook()
    now = utc_now()
    book.acquire(
        "wi-1", ActionClass.COMMENT, holder="run-1", intent="x", ttl=timedelta(seconds=1), now=now
    )

    assert book.held("wi-1", ActionClass.COMMENT, now=now + timedelta(seconds=2)) is None


def test_reacquiring_your_own_lease_renews_it() -> None:
    """An actor that loops -- open, push, update -- would otherwise have to remember whether
    this pass is its first."""
    book = LeaseBook()
    now = utc_now()
    first = book.acquire(
        "wi-1", ActionClass.UPDATE_CHANGE, holder="run-1", intent="pushing", now=now
    )
    assert isinstance(first, Lease)

    # The token, not the name. `holder` is a string the caller supplies and nothing
    # authenticates, so repeating it renewed a lease the caller might not hold.
    again = book.acquire(
        "wi-1",
        ActionClass.UPDATE_CHANGE,
        holder="run-1",
        intent="pushing",
        now=now + timedelta(minutes=2),
        token=first.token,
    )

    assert isinstance(again, Lease)
    assert again.expires_at() > now + timedelta(minutes=5)


def test_releasing_someone_elses_lease_fails() -> None:
    """Silently allowing it would be a way to defeat the whole mechanism."""
    book = LeaseBook()
    lease = book.acquire("wi-1", ActionClass.COMMENT, holder="run-1", intent="x")
    assert isinstance(lease, Lease)

    assert book.release("wi-1", ActionClass.COMMENT, holder="run-2") is False
    # Nor by naming the holder without their token: the name is not the claim.
    assert book.release("wi-1", ActionClass.COMMENT, holder="run-1") is False
    assert book.held("wi-1", ActionClass.COMMENT) is not None
    assert book.release("wi-1", ActionClass.COMMENT, holder="run-1", token=lease.token) is True


def test_active_leases_on_a_work_item_are_listable() -> None:
    """What FR-19.5's "expose active runs" reads."""
    book = LeaseBook()
    book.acquire("wi-1", ActionClass.COMMENT, holder="run-1", intent="commenting")
    book.acquire("wi-1", ActionClass.OPEN_CHANGE, holder="run-1", intent="opening")

    assert [lease.action for lease in book.active_for("wi-1")] == [
        ActionClass.COMMENT,
        ActionClass.OPEN_CHANGE,
    ]


# ------------------------------------------------------------------------ read surface


def test_work_items_are_listable_and_filterable_by_stage() -> None:
    tools = server(work_item("wi-1"), work_item("wi-2", stage=Stage.BUILD))

    assert len(tools.list_work_items()["workItems"]) == 2
    assert [i["id"] for i in tools.list_work_items(stage="BUILD")["workItems"]] == ["wi-2"]


def test_search_looks_at_title_and_request() -> None:
    tools = server()

    assert tools.search_work_items("byte-order")["workItems"] == []
    assert len(tools.search_work_items("BOM")["workItems"]) == 1
    assert len(tools.search_work_items("uploading")["workItems"]) == 1


def test_getting_a_work_item_shows_its_active_runs_and_leases() -> None:
    """Nothing here locks, so this is how a second actor finds out rather than colliding."""
    tools = server(active_runs={"wi-1": ["run-7"]})
    tools.leases.acquire("wi-1", ActionClass.OPEN_CHANGE, holder="run-7", intent="opening")

    body = tools.get_work_item("wi-1")

    assert body["activeRuns"] == ["run-7"]
    assert "run-7 is opening" in body["leases"][0]


def test_an_unknown_work_item_is_an_actionable_error() -> None:
    body = server().get_work_item("wi-999")

    assert body["error"] == "work_item.unknown"
    assert "list_work_items" in body["remediation"]


def test_notification_routes_say_they_are_best_effort() -> None:
    """FR-19.7 requires them to be described as such, and a route list with no such note is
    a list somebody will treat as a delivery guarantee."""
    body = server(notification_routes=("chat:#payments",)).list_notification_routes()

    assert body["routes"] == ["chat:#payments"]
    assert "best-effort" in body["note"]


# ------------------------------------------------------------------------ picking up


def test_picking_up_returns_commands_and_creates_nothing() -> None:
    """FR-19.4: the server never modifies the caller's files. A tool server that writes into
    a caller's checkout is one nobody can point at a repository they care about."""
    tools = server()

    setup = tools.pick_up("wi-1")["setup"]

    assert setup["branch"] == "factory/wi-1"
    assert setup["baseCommit"] == "abc123"
    assert any("git worktree add" in command for command in setup["commands"])


def test_picking_up_warns_that_it_claims_nothing() -> None:
    """FR-19.5's "the docs must warn plainly about duplicate work", in the response itself
    rather than in documentation somebody has to have read."""
    setup = server().pick_up("wi-1")["setup"]

    assert "does not claim" in setup["warning"]
    assert "announce_pickup" in setup["warning"]


def test_picking_up_surfaces_who_else_is_active() -> None:
    tools = server(active_runs={"wi-1": ["run-3"]})

    assert tools.pick_up("wi-1")["activeRuns"] == ["run-3"]


def test_announcing_a_pickup_is_one_call_and_not_a_lock() -> None:
    tools = server(active_runs={"wi-1": ["run-3"]})

    body = tools.announce_pickup("wi-1", actor="amaya", intent="taking this locally")

    assert body["announced"] is True
    assert body["othersActive"] == ["run-3"]
    assert tools.read_conversation("wi-1")["messages"][0]["kind"] == "pickup"


def test_the_same_work_item_is_continued_not_copied() -> None:
    """FR-19.3: there is no second identity for locally continued work."""
    tools = server()
    before = len(tools.work_items)

    tools.pick_up("wi-1")
    tools.announce_pickup("wi-1", actor="amaya", intent="taking this")

    assert len(tools.work_items) == before
    assert tools.pick_up("wi-1")["setup"]["workItem"] == "wi-1"


# ------------------------------------------------------------------------ handing back


def test_a_handoff_without_a_push_is_refused() -> None:
    """FR-19.6. The factory cannot see a commit that exists only on one laptop, and
    recording the handoff anyway records work nobody else can find."""
    body = server().hand_back("wi-1", actor="amaya", changed="fixed the BOM handling")

    assert body["accepted"] is False
    assert body["code"] == "handoff.nothing_pushed"
    assert "only on your machine" in body["remediation"]


def test_a_handoff_without_a_summary_is_refused() -> None:
    """The next actor reads this instead of the diff."""
    body = server().hand_back("wi-1", actor="amaya", branch="factory/wi-1", changed="  ")

    assert body["accepted"] is False
    assert body["code"] == "handoff.no_summary"


def test_a_complete_handoff_is_accepted_and_recorded() -> None:
    tools = server()

    body = tools.hand_back(
        "wi-1",
        actor="amaya",
        branch="factory/wi-1",
        changed="strip the BOM in the header reader",
        validated="pytest -k bom",
        remaining="the exporter still writes one",
    )

    assert body["accepted"] is True
    assert "no second identity" in body["note"]
    assert tools.read_conversation("wi-1")["messages"][0]["kind"] == "handoff"


def test_two_actors_cannot_both_hand_the_same_item_back() -> None:
    """FR-19.5a: not locking a work item is deliberate, and must not extend to external
    effects -- two handoffs is two visible artifacts.

    This passed for the wrong reason. It relied on the *lease*, and the lease refused only
    when the second actor volunteered a different name -- so `actor="amaya"` bypassed it,
    and after the lease's TTL the duplicate was available to anyone. A handoff already on
    the record is not a concurrency problem, so it is the record that refuses now.
    """
    tools = server()
    tools.hand_back("wi-1", actor="amaya", branch="factory/wi-1", changed="fixed it")

    second = tools.hand_back("wi-1", actor="bo", branch="factory/wi-1-b", changed="also fixed it")
    impostor = tools.hand_back("wi-1", actor="amaya", branch="factory/wi-1-c", changed="again")

    assert second["accepted"] is False
    assert second["code"] == "handoff.already_recorded"
    assert "amaya" in second["message"]
    assert impostor["accepted"] is False, "naming the first actor bypassed the refusal"


def test_a_second_handoff_is_available_when_it_says_it_amends_the_first() -> None:
    """The legitimate case -- an amended branch, a corrected change reference -- must stay
    possible, and must say so on the record rather than looking like a duplicate."""
    tools = server()
    tools.hand_back("wi-1", actor="amaya", branch="factory/wi-1", changed="fixed it")

    amended = tools.hand_back(
        "wi-1", actor="amaya", branch="factory/wi-1", changed="fixed it properly", amends=True
    )

    assert amended["accepted"] is True
    assert amended["amends"] is True


# ------------------------------------------------------------------------ tool specs


def test_every_published_tool_has_a_schema_and_a_handler() -> None:
    for spec in server().specs():
        assert spec.name.startswith("factory.")
        assert spec.description.strip()
        assert spec.input_schema["type"] == "object"
        assert callable(spec.handler)


def test_the_surface_publishes_its_own_usage_guidance() -> None:
    """FR-19.9: a calling agent picks up the correct workflow without an operator explaining
    it. A schema says what is accepted; guidance says what to do with it."""
    specs = {spec.name: spec for spec in server().specs()}

    assert "never touches your files" in specs["factory.pick_up"].guidance
    assert "Push first" in specs["factory.hand_back"].guidance


def test_tools_with_external_effects_are_marked() -> None:
    """These are the ones that take a lease."""
    external = {spec.name for spec in server().specs() if spec.external}

    assert external == {"factory.hand_back"}


def test_every_published_handler_is_callable_through_its_schema() -> None:
    """A published schema whose handler does not accept it is a surface that fails on first
    contact with the agent it was published for.

    This asserted only `isinstance(result, dict)` -- and every refusal is a dict, so the one
    handler that genuinely rejected its own published schema (`factory.hand_back`, which
    refuses without a pushed branch) was the case it exercised, and it passed. Two changes:
    each payload is validated against the published schema first, so a payload this test
    accepts is one an agent could actually have sent; and the result must be an *acceptance*
    rather than merely a dict.
    """
    import jsonschema

    tools = server()
    calls: dict[str, dict[str, object]] = {
        "factory.list_work_items": {},
        "factory.search_work_items": {"query": "BOM"},
        "factory.get_work_item": {"work_item_id": "wi-1"},
        "factory.pick_up": {"work_item_id": "wi-1"},
        "factory.announce_pickup": {"work_item_id": "wi-1", "actor": "a", "intent": "i"},
        "factory.read_conversation": {"work_item_id": "wi-1"},
        "factory.message_conductor": {"work_item_id": "wi-1", "actor": "a", "text": "t"},
        "factory.list_notification_routes": {},
        "factory.hand_back": {
            "work_item_id": "wi-1",
            "actor": "a",
            "changed": "c",
            "branch": "factory/wi-1",
        },
    }

    for spec in tools.specs():
        assert spec.name in calls, f"{spec.name} is published and untested"
        payload = calls[spec.name]
        jsonschema.validate(payload, spec.input_schema)
        result = spec.handler(**payload)
        assert isinstance(result, dict)
        assert "error" not in result, (spec.name, result)
        assert result.get("accepted", True) is True, (spec.name, result)


def test_a_payload_the_handoff_schema_rejects_is_one_the_handler_rejects() -> None:
    """The two must agree in both directions, or the schema is decoration."""
    import jsonschema

    tools = server()
    spec = next(s for s in tools.specs() if s.name == "factory.hand_back")
    unpushed = {"work_item_id": "wi-1", "actor": "a", "changed": "c"}

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(unpushed, spec.input_schema)
    assert spec.handler(**unpushed)["accepted"] is False


@pytest.mark.parametrize(
    "handler",
    ["get_work_item", "read_conversation", "pick_up"],
)
def test_read_handlers_refuse_an_unknown_work_item_consistently(handler: str) -> None:
    body = getattr(server(), handler)("wi-999")

    assert body["error"] == "work_item.unknown"
