"""A local HTTP API, so something other than a person at a terminal can drive this (FR-19).

`sf serve` prints the factory's tool surface and deliberately binds no socket, with a
recorded reason: the work items a running factory holds live in the orchestrator's state,
and a command that has none would be offering an option that does nothing. The reason is
sound and the consequence was that the factory was not callable. Every automation story the
reference product tells — an error monitor opening a change, a webhook starting work, a
script sharding a backlog — rests on an API.

The constraint here is ours rather than theirs. This project already refused to put an
unauthenticated steering endpoint on the dashboard, because an unauthenticated decision
channel is a privilege-escalation path. So this API carries the identity and capability
model that `sf` currently gets from the operator's shell, and it carries it on *every*
request including the reads: a ledger is a factory's whole history, and "read-only" is not
the same as "public".

Five decisions:

**Keys are stored hashed and shown once.** A key file an operator can read their key back
out of is a key file that is also a credential, and the usual outcome is that it gets
copied somewhere with weaker permissions.

**Loopback unless keys exist.** Binding to an address other than loopback with no keys
configured is refused, rather than being a documented footgun.

**Reads are authorised too.** Every request resolves to a principal, and the principal's
capabilities decide what it can do. There is no anonymous tier.

**A body has a ceiling.** An endpoint that reads until EOF is a memory bound set by whoever
is calling.

**Every call is recorded with its principal.** An action taken through the API and one taken
at a terminal must be equally explicable afterwards.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from software_factory.errors import ErrorCode, FactoryError
from software_factory.memory.records import utc_now

#: Loopback, like the dashboard and for the same reason.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8788

#: Where issued keys live, inside the state directory.
KEY_FILE = "api-keys.json"

#: The largest request body this will read.
MAX_BODY_BYTES = 1_000_000

#: How the key is presented. A header, never a query parameter: a URL ends up in access
#: logs, in browser history, and in the `Referer` of whatever the page links to next.
AUTH_HEADER = "authorization"
BEARER = "bearer "


class ApiError(FactoryError):
    """A request this API will not serve."""

    code = ErrorCode.NOT_AUTHORIZED


@dataclass(frozen=True, slots=True)
class IssuedKey:
    """A key as it is stored: the principal it acts as, and a hash.

    The key itself is not here. `create` returns it once and it is never recoverable, which
    is the difference between a key store and a second copy of the credential.
    """

    key_id: str
    principal: str
    digest: str
    created_at: datetime
    label: str = ""

    def matches(self, presented: str) -> bool:
        return hmac.compare_digest(self.digest, _digest(presented))

    def as_dict(self) -> dict[str, Any]:
        return {
            "keyId": self.key_id,
            "principal": self.principal,
            "digest": self.digest,
            "createdAt": self.created_at.isoformat(),
            "label": self.label,
        }


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class KeyStore:
    """The keys a state directory holds."""

    path: Path

    @classmethod
    def in_state(cls, state_dir: Path) -> KeyStore:
        return cls(path=Path(state_dir) / KEY_FILE)

    def create(self, *, principal: str, label: str = "") -> tuple[IssuedKey, str]:
        """Issue a key. Returns the record and the secret, which is not stored."""
        secret = f"sf_{secrets.token_urlsafe(32)}"
        issued = IssuedKey(
            key_id=secrets.token_hex(8),
            principal=principal,
            digest=_digest(secret),
            created_at=utc_now(),
            label=label,
        )
        keys = [*self.all(), issued]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([k.as_dict() for k in keys], indent=2), encoding="utf-8")
        # Restrictive before anything is in it would be better still, but the file is
        # written whole each time; this is the moment it exists with content.
        self.path.chmod(0o600)
        return issued, secret

    def revoke(self, key_id: str) -> bool:
        keys = self.all()
        keep = [k for k in keys if k.key_id != key_id]
        if len(keep) == len(keys):
            return False
        self.path.write_text(json.dumps([k.as_dict() for k in keep], indent=2), encoding="utf-8")
        return True

    def all(self) -> list[IssuedKey]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Unlike the stop file, an unreadable key store must *not* degrade to
            # permissive. No keys means no access, which is the safe direction here.
            return []
        return [
            IssuedKey(
                key_id=str(e.get("keyId", "")),
                principal=str(e.get("principal", "")),
                digest=str(e.get("digest", "")),
                created_at=_time(e.get("createdAt")),
                label=str(e.get("label", "")),
            )
            for e in raw
            if isinstance(e, dict)
        ]

    def resolve(self, presented: str) -> IssuedKey | None:
        """The key this secret is, or None.

        Every candidate is compared even after a match, so the time taken does not depend
        on how far down the list the right key sits.
        """
        found: IssuedKey | None = None
        for key in self.all():
            if key.matches(presented):
                found = key
        return found


def _time(raw: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return utc_now()


@dataclass(frozen=True, slots=True)
class ApiData:
    """Everything the API can serve, from one state directory."""

    ledger_path: Path
    root: Path | None = None
    keys: KeyStore | None = None
    integrations: frozenset[str] = frozenset()

    def key_store(self) -> KeyStore:
        return self.keys or KeyStore.in_state(self.ledger_path.parent)

    # ------------------------------------------------------------------- the endpoints

    def handle(
        self, method: str, path: str, params: dict[str, list[str]], body: dict[str, Any], who: str
    ) -> tuple[int, dict[str, Any]]:
        from software_factory.ledger.log import Ledger

        if method == "GET" and path == "/v1/health":
            return 200, {"ok": True, "principal": who}

        if method == "GET" and path == "/v1/metrics":
            from software_factory.observability.metrics import Window, compute

            days = _days(params)
            entries = list(Ledger(self.ledger_path).read())
            report = compute(
                entries,
                window=Window.last(timedelta(days=days)),
                integrations=self.integrations,
            )
            return 200, {"days": days, **report.as_dict()}

        if method == "GET" and path == "/v1/runs":
            from software_factory.observability.views import run_index

            return 200, run_index(list(Ledger(self.ledger_path).read()))

        if method == "GET" and path.startswith("/v1/runs/"):
            from software_factory.observability.views import run_inspector

            run_id = path.removeprefix("/v1/runs/")
            found = run_inspector(list(Ledger(self.ledger_path).read()), run_id)
            return (404 if found.get("error") else 200), found

        if method == "GET" and path == "/v1/work-items":
            from software_factory.observability.views import activity_board, work_items_from

            return 200, activity_board(work_items_from(list(Ledger(self.ledger_path).read())))

        if method == "POST" and path == "/v1/events":
            return self._receive(body, who)

        if method == "POST" and path == "/v1/stop":
            return self._stop(body, who)

        return 404, {"error": "not_found", "message": f"no route for {method} {path}"}

    def _receive(self, body: dict[str, Any], who: str) -> tuple[int, dict[str, Any]]:
        """Put one event through intake. The endpoint an integration actually needs.

        Reports what it *would* start rather than running it. A run takes minutes and a
        request does not, and an HTTP handler that blocks on a model is a handler whose
        client times out and retries — which is how one event becomes three runs.
        """
        from software_factory.definition import load_strict
        from software_factory.intake import FactoryEvent, Origin, Provider
        from software_factory.intake.events import event_identity
        from software_factory.intake.loading import pipeline_from

        if self.root is None:
            return 503, {
                "error": "no_factory",
                "message": "this API was started without a factory root, so intake is unavailable",
            }
        provider = str(body.get("provider", "webhook"))
        if provider not in set(Provider):
            return 400, {
                "error": "unknown_provider",
                "message": f"{provider!r} is not a provider this factory knows",
                "known": sorted(p.value for p in Provider),
            }
        definition = load_strict(self.root)
        event = FactoryEvent(
            id=str(body.get("id"))
            or event_identity(
                Provider(provider),
                str(body.get("ref", "")),
                str(body.get("event", "")),
                str(body.get("title", "")),
            ),
            provider=Provider(provider),
            event=str(body.get("event", "")),
            origin=Origin(
                provider=Provider(provider),
                ref=str(body.get("ref", "api")),
                source=str(body.get("source", "")),
            ),
            title=str(body.get("title", "")),
            body=str(body.get("body", "")),
            author=str(body.get("author", "")),
            attributes=dict(body.get("attributes") or {}),
        )
        outcomes = pipeline_from(definition).receive(event)
        return 200, {
            "accepted": True,
            "by": who,
            "event": event.id,
            "outcomes": [
                {
                    "kind": type(o).__name__.lower(),
                    "automation": getattr(o, "automation", None),
                    "agent": getattr(o, "agent", None),
                    "reason": getattr(o, "reason", None),
                    "message": getattr(o, "message", None),
                }
                for o in outcomes
            ],
        }

    def _stop(self, body: dict[str, Any], who: str) -> tuple[int, dict[str, Any]]:
        from software_factory.orchestrator.stopping import StopBook

        subject = str(body.get("workItem", "")).strip()
        reason = str(body.get("reason", "")).strip()
        if not subject or not reason:
            return 400, {
                "error": "incomplete",
                "message": "`workItem` and `reason` are both required",
            }
        stop = StopBook.in_state(self.ledger_path.parent).request(subject, by=who, reason=reason)
        return 200, {"stopped": stop.as_dict()}


def _days(params: dict[str, list[str]]) -> int:
    raw = params.get("days", ["7"])[0]
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return 7
    return max(1, min(days, 3650))


#: Which capability each route needs. A route with no entry is refused rather than allowed:
#: adding an endpoint and forgetting to authorise it must fail closed.
ROUTE_CAPABILITY: dict[tuple[str, str], str | None] = {
    ("GET", "/v1/health"): None,
    ("GET", "/v1/metrics"): None,
    ("GET", "/v1/runs"): None,
    ("GET", "/v1/work-items"): None,
    ("POST", "/v1/events"): None,
    ("POST", "/v1/stop"): "emergency_stop",
}


def _needed(method: str, path: str) -> tuple[bool, str | None]:
    """(known route, capability). Unknown routes are refused, not guessed at."""
    if path.startswith("/v1/runs/"):
        return True, None
    key = (method, path)
    if key not in ROUTE_CAPABILITY:
        return False, None
    return True, ROUTE_CAPABILITY[key]


def make_handler(data: ApiData, *, directory: Any = None) -> type[BaseHTTPRequestHandler]:
    """Build a handler bound to one factory's data and its principal directory."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "software-factory-api"

        def do_GET(self) -> None:
            self._serve("GET")

        def do_POST(self) -> None:
            self._serve("POST")

        def log_message(self, format: str, *args: Any) -> None:
            """Silent. An API that prints a line per request buries whatever the operator
            was watching, and the ledger is the record that matters."""

        def _serve(self, method: str) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            presented = ""
            header = self.headers.get(AUTH_HEADER) or ""
            if header.lower().startswith(BEARER):
                presented = header[len(BEARER) :].strip()
            key = data.key_store().resolve(presented) if presented else None
            if key is None:
                # The same answer for a missing key and a wrong one. Distinguishing them
                # tells a caller whether a key exists, which is the first thing worth
                # knowing if you are guessing at them.
                self._respond(
                    401, {"error": "unauthorized", "message": "a valid API key is required"}
                )
                return

            known, capability = _needed(method, parsed.path)
            if not known:
                self._respond(
                    404, {"error": "not_found", "message": f"no route for {method} {parsed.path}"}
                )
                return
            if capability is not None and not _holds(directory, key.principal, capability):
                self._respond(
                    403,
                    {
                        "error": "forbidden",
                        "message": f"{key.principal} does not hold {capability}",
                    },
                )
                return

            body: dict[str, Any] = {}
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY_BYTES:
                    self._respond(
                        413, {"error": "too_large", "message": "request body is too large"}
                    )
                    return
                raw = self.rfile.read(length) if length else b""
                if raw:
                    try:
                        parsed_body = json.loads(raw)
                    except json.JSONDecodeError:
                        self._respond(400, {"error": "bad_json", "message": "body is not JSON"})
                        return
                    if not isinstance(parsed_body, dict):
                        self._respond(
                            400, {"error": "bad_json", "message": "body must be an object"}
                        )
                        return
                    body = parsed_body

            try:
                status, payload = data.handle(method, parsed.path, params, body, key.principal)
            except FactoryError as exc:
                status, payload = 400, {"error": exc.code.value, "message": exc.message}
            except Exception as exc:
                status, payload = (
                    500,
                    {
                        "error": "internal",
                        "message": f"{type(exc).__name__} while serving {parsed.path}",
                    },
                )
            self._respond(status, payload)

        def _respond(self, status: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, indent=2, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def _holds(directory: Any, principal_id: str, capability: str) -> bool:
    """Whether this principal holds the capability.

    Without a directory, nothing privileged is permitted. A capability check that passes
    because no directory was loaded is a capability check that is worse than none: it looks
    like enforcement and is not.
    """
    if directory is None:
        return False
    from software_factory.identity.principals import Capability

    try:
        wanted = Capability(capability)
    except ValueError:
        return False
    principal = getattr(directory, "_by_id", {}).get(principal_id)
    return bool(principal is not None and principal.active and principal.holds(wanted))


def serve(
    ledger_path: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    root: Path | None = None,
    directory: Any = None,
    integrations: frozenset[str] = frozenset(),
    ready: Any = None,
) -> ThreadingHTTPServer:
    """Start the API. Returns the server so a caller can shut it down."""
    data = ApiData(ledger_path=ledger_path, root=root, integrations=integrations)
    if host not in ("127.0.0.1", "localhost", "::1") and not data.key_store().all():
        raise ApiError(
            f"refusing to bind {host} with no API keys issued",
            remediation=(
                "Issue a key with `sf api key create --principal <id>` first. A reachable "
                "API with no keys is a factory's whole history published to whoever finds "
                "the port."
            ),
        )
    server = ThreadingHTTPServer((host, port), make_handler(data, directory=directory))
    if ready is not None:
        ready(f"http://{host}:{server.server_address[1]}/v1/health")
    return server
