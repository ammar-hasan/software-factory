"""The local HTTP API (PRD FR-19, FR-25.5).

`sf serve` printed the tool surface and bound no socket, so the factory was not callable by
anything but a person at a terminal. Every automation story rests on an API — and this
project already refused to put an *unauthenticated* decision channel on the dashboard,
because that is a privilege-escalation path. So the API carries the identity and capability
model `sf` gets from the operator's shell, on every request including the reads.

Most of these are refusals. A ledger is a factory's whole history, and an API in front of it
is the one surface where getting authorisation slightly wrong publishes everything.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from software_factory.identity.principals import Capability, Directory, Principal, PrincipalKind
from software_factory.ledger import EntryType, Ledger
from software_factory.observability.api import ApiData, ApiError, KeyStore, make_handler, serve


@pytest.fixture
def factory(tmp_path: Path) -> Path:
    from software_factory.scaffold import init_factory

    init_factory(tmp_path / "f", name="payments", owner="acme", repo="svc")
    return tmp_path / "f"


@pytest.fixture
def state(tmp_path: Path) -> Path:
    directory = tmp_path / ".factory"
    directory.mkdir()
    ledger = Ledger(directory / "ledger.jsonl")
    ledger.append(
        EntryType.RUN_STARTED,
        actor="builder",
        subject="wi-1",
        payload={"agent": "builder", "stage": "BUILD", "run": "wi-1:build:0"},
    )
    ledger.append(
        EntryType.RUN_FINISHED,
        actor="builder",
        subject="wi-1",
        payload={"status": "completed", "run": "wi-1:build:0"},
    )
    return directory


def people() -> Directory:
    return Directory(
        [
            Principal(
                id="amaya",
                kind=PrincipalKind.PERSON,
                display_name="Amaya",
                capabilities=frozenset({Capability.EMERGENCY_STOP}),
            ),
            Principal(id="watcher", kind=PrincipalKind.PERSON, display_name="Watcher"),
        ]
    )


@pytest.fixture
def api(state: Path, factory: Path):
    from http.server import ThreadingHTTPServer

    keys = KeyStore.in_state(state)
    _, amaya = keys.create(principal="amaya", label="tests")
    _, watcher = keys.create(principal="watcher", label="tests")

    data = ApiData(ledger_path=state / "ledger.jsonl", root=factory)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(data, directory=people()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", amaya, watcher
    finally:
        server.shutdown()
        server.server_close()


def call(base: str, path: str, key: str | None = None, body: dict | None = None):
    request = Request(
        f"{base}{path}",
        data=None if body is None else json.dumps(body).encode(),
        method="POST" if body is not None else "GET",
        headers=(
            {"Content-Type": "application/json"}
            | ({"Authorization": f"Bearer {key}"} if key else {})
        ),
    )
    with urlopen(request) as response:
        return response.status, json.loads(response.read())


# ------------------------------------------------------------------- authentication


def test_no_key_is_refused(api) -> None:
    """There is no anonymous tier. "Read-only" is not the same as "public"."""
    base, _amaya, _watcher = api

    with pytest.raises(HTTPError) as caught:
        call(base, "/v1/metrics")

    assert caught.value.code == 401


def test_a_wrong_key_is_refused_the_same_way_as_a_missing_one(api) -> None:
    """Distinguishing them tells a caller whether a key exists, which is the first thing
    worth knowing if you are guessing at them."""
    base, _amaya, _watcher = api

    with pytest.raises(HTTPError) as missing:
        call(base, "/v1/metrics")
    with pytest.raises(HTTPError) as wrong:
        call(base, "/v1/metrics", "sf_not-a-real-key")

    assert missing.value.code == wrong.value.code == 401


def test_a_valid_key_reaches_the_reads(api) -> None:
    base, amaya, _watcher = api

    status, body = call(base, "/v1/health", amaya)

    assert status == 200
    assert body["principal"] == "amaya"


def test_a_revoked_key_stops_working(api, state: Path) -> None:
    base, amaya, _watcher = api
    keys = KeyStore.in_state(state)
    issued = next(k for k in keys.all() if k.principal == "amaya")

    assert keys.revoke(issued.key_id) is True

    with pytest.raises(HTTPError) as caught:
        call(base, "/v1/health", amaya)
    assert caught.value.code == 401


# -------------------------------------------------------------------- authorisation


def test_a_privileged_route_needs_the_capability(api) -> None:
    """`watcher` authenticates and may read. Stopping the factory is a different question."""
    base, amaya, watcher = api

    with pytest.raises(HTTPError) as caught:
        call(base, "/v1/stop", watcher, {"workItem": "wi-1", "reason": "x"})
    assert caught.value.code == 403

    status, body = call(base, "/v1/stop", amaya, {"workItem": "wi-1", "reason": "runaway"})
    assert status == 200
    assert body["stopped"]["by"] == "amaya"


def test_an_unknown_route_is_refused_rather_than_guessed_at() -> None:
    """Adding an endpoint and forgetting to authorise it must fail closed."""
    from software_factory.observability.api import _needed

    known, capability = _needed("POST", "/v1/definition")
    assert known is False
    assert capability is None


def test_without_a_directory_nothing_privileged_is_permitted(state: Path, factory: Path) -> None:
    """A capability check that passes because no directory loaded looks like enforcement
    and is not."""
    from http.server import ThreadingHTTPServer

    _, key = KeyStore.in_state(state).create(principal="amaya")
    data = ApiData(ledger_path=state / "ledger.jsonl", root=factory)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(data, directory=None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(HTTPError) as caught:
            call(base, "/v1/stop", key, {"workItem": "wi-1", "reason": "x"})
        assert caught.value.code == 403
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------- the keys


def test_a_key_is_stored_hashed_and_never_recoverable(state: Path) -> None:
    """A key file an operator can read their key back out of is a second copy of the
    credential."""
    keys = KeyStore.in_state(state)
    _issued, secret = keys.create(principal="amaya")

    stored = (state / "api-keys.json").read_text()

    assert secret not in stored
    assert keys.resolve(secret) is not None


def test_a_key_file_is_not_world_readable(state: Path) -> None:
    KeyStore.in_state(state).create(principal="amaya")

    mode = (state / "api-keys.json").stat().st_mode & 0o777

    assert mode == 0o600, oct(mode)


def test_an_unreadable_key_store_denies_rather_than_permits(state: Path) -> None:
    """Unlike the stop file, this must not degrade to permissive: no keys means no access,
    which is the safe direction here."""
    (state / "api-keys.json").write_text("{not json", encoding="utf-8")

    assert KeyStore.in_state(state).all() == []
    assert KeyStore.in_state(state).resolve("anything") is None


# -------------------------------------------------------------------- the surface


def test_metrics_are_served_over_the_window(api) -> None:
    base, amaya, _watcher = api

    status, body = call(base, "/v1/metrics?days=30", amaya)

    assert status == 200
    assert body["days"] == 30
    assert body["runs"]["total"] == 1


def test_runs_and_one_run_are_both_reachable(api) -> None:
    base, amaya, _watcher = api

    _status, index = call(base, "/v1/runs", amaya)
    assert index["total"] == 1

    _status, one = call(base, f"/v1/runs/{index['runs'][0]['id']}", amaya)
    assert one["run"] == index["runs"][0]["id"]


def test_an_unknown_run_is_a_404(api) -> None:
    base, amaya, _watcher = api

    with pytest.raises(HTTPError) as caught:
        call(base, "/v1/runs/nonesuch", amaya)

    assert caught.value.code == 404


def test_an_event_goes_through_the_same_intake_as_everything_else(api) -> None:
    """The endpoint an integration actually needs, and it reports what it *would* start
    rather than running it: a run takes minutes and a request does not, so a handler that
    blocked on a model would have its client time out and retry — which is how one event
    becomes three runs."""
    base, amaya, _watcher = api

    status, body = call(
        base,
        "/v1/events",
        amaya,
        {"provider": "git-host", "event": "issue.labelled", "title": "a defect", "ref": "a/b#1"},
    )

    assert status == 200
    assert body["accepted"] is True
    assert body["by"] == "amaya"
    assert isinstance(body["outcomes"], list)


def test_an_unknown_provider_is_refused_with_the_ones_it_knows(api) -> None:
    base, amaya, _watcher = api

    with pytest.raises(HTTPError) as caught:
        call(base, "/v1/events", amaya, {"provider": "carrier-pigeon"})

    assert caught.value.code == 400
    assert "git-host" in caught.value.read().decode()


def test_a_body_that_is_not_json_is_refused(api) -> None:
    base, amaya, _watcher = api
    request = Request(
        f"{base}/v1/events",
        data=b"{not json",
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {amaya}"},
    )

    with pytest.raises(HTTPError) as caught:
        urlopen(request)

    assert caught.value.code == 400


def test_a_stop_without_a_reason_is_refused(api) -> None:
    """The same requirement `sf stop` makes: a stop that leaves no record is an unexplained
    gap in a run's history."""
    base, amaya, _watcher = api

    with pytest.raises(HTTPError) as caught:
        call(base, "/v1/stop", amaya, {"workItem": "wi-1"})

    assert caught.value.code == 400


# ------------------------------------------------------------------------ binding


def test_binding_beyond_loopback_with_no_keys_is_refused(state: Path) -> None:
    """A reachable API with no keys is a factory's whole history published to whoever finds
    the port. A documented footgun is still a footgun."""
    with pytest.raises(ApiError, match="no API keys"):
        serve(state / "ledger.jsonl", host="0.0.0.0", port=0)


def test_loopback_needs_no_keys_to_start(state: Path) -> None:
    """Starting is not the same as serving: every request still needs one."""
    server = serve(state / "ledger.jsonl", host="127.0.0.1", port=0)
    server.server_close()
