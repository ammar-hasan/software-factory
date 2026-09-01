"""HTTP transport for model providers (PRD FR-11.2, FR-11.10, NFR-11.3).

Two decisions are load-bearing here.

**Standard library only.** A provider adapter that pulls in an HTTP client makes the
package heavier for every user who never calls a hosted model, and NFR-11.3 says nothing
may require a paid hosted service to reach a correct result. `urllib.request` is enough
for a JSON POST, and being enough is the whole requirement.

**The transport is injectable.** Every adapter takes a `Transport` and the default one is
the only object in the package that opens a socket. That is what lets the entire provider
layer be tested without a network, which is what lets `scripts/run_offline_tests.py` stay
green (PR-2) -- and a provider layer that can only be tested against a live endpoint is
one that is tested rarely and therefore wrong.

Failure classification lives here rather than in each adapter because the retry decision
is a property of the transport layer, not of a vendor's message format. Getting it wrong
in one adapter and right in another is the kind of divergence nobody notices until a rate
limit is treated as a permanent failure at three in the morning.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from software_factory.providers.base import ProviderError

DEFAULT_TIMEOUT_S = 120.0
"""Generous, because a large model on a slow local runtime is not a failure.

A short timeout on a local endpoint is the single most common way a self-hosted setup is
declared broken when it was merely thinking.
"""

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
"""Statuses worth trying again.

Deliberately excludes 400, 401, 403, 404 and 422: a malformed request, a bad credential,
or a missing model does not become correct on the second attempt, and retrying it burns
budget while hiding the real cause behind a timeout.
"""

REDACTED = "<redacted>"

SECRET_HEADERS = frozenset({"authorization", "x-api-key", "api-key", "proxy-authorization"})
"""Headers never reproduced in an error message or a log line.

Credentials live only at this boundary (FR-11.2). A provider error that helpfully echoes
the request headers moves them into the ledger, which is append-only -- so the mistake is
not correctable after the fact.
"""


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return `headers` with credential values replaced.

    Used by every error path. The header *names* are kept because knowing that an
    `authorization` header was sent is diagnostic and the value is not.
    """
    return {k: (REDACTED if k.lower() in SECRET_HEADERS else v) for k, v in headers.items()}


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: dict[str, Any]
    raw: str = ""
    """The undecoded body, kept only when decoding failed, for the error message."""


@runtime_checkable
class Transport(Protocol):
    """Somewhere to send a JSON POST."""

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_s: float,
    ) -> Response:
        """POST `payload` and return the response, or raise :class:`ProviderError`."""
        ...


@runtime_checkable
class RequestTransport(Protocol):
    """A transport that can issue any JSON request, not only a POST.

    Separate from :class:`Transport` because model endpoints only ever POST, and widening
    the protocol every provider is written against to serve one integration would make
    every provider stub implement a method no provider calls. An integration needs `GET`
    for the read-only calls a health check and a lookup are made of, and faking one with a
    method-override header is a lie the host does not honour.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        timeout_s: float,
    ) -> Response:
        """Issue `method` and return the response, or raise :class:`ProviderError`."""
        ...


class UrllibTransport:
    """The only object in the package that opens a socket to a model endpoint."""

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> Response:
        return self.request("POST", url, headers=headers, payload=payload, timeout_s=timeout_s)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> Response:
        # A body only when there is one. A GET carrying `null` is a GET some hosts refuse
        # and others answer differently, and neither is what the caller asked for.
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers=(
                {"content-type": "application/json", **headers} if data is not None else headers
            ),
            method=method,
        )
        if request.type not in ("http", "https"):
            # A `file:` or `gopher:` URL in a config file is either a mistake or an
            # attempt to make the provider read the local disk. Neither should reach
            # urlopen.
            raise ProviderError(
                f"model endpoint must be http or https, got {request.type!r} in {url!r}",
                retryable=False,
            )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return _decode(response.status, response.read())
        except urllib.error.HTTPError as exc:
            # An HTTP error still carries a body, and the body is where the provider says
            # what was wrong. Discarding it turns every 400 into "400".
            body = exc.read()
            decoded = _decode(exc.code, body)
            raise _from_status(url, exc.code, decoded) from exc
        except urllib.error.URLError as exc:
            raise ProviderError(
                f"cannot reach {url}: {exc.reason}",
                retryable=True,
            ) from exc
        except TimeoutError as exc:
            # `socket.timeout` is an alias of this since 3.10, so one clause covers both.
            raise ProviderError(f"{url} timed out after {timeout_s:g}s", retryable=True) from exc


def _decode(status: int, body: bytes) -> Response:
    text = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return Response(status=status, body={}, raw=text[:2000])
    if not isinstance(parsed, dict):
        return Response(status=status, body={}, raw=text[:2000])
    return Response(status=status, body=parsed, raw="")


def _from_status(url: str, status: int, response: Response) -> ProviderError:
    """Turn an HTTP failure into a typed one the loop can act on (FR-11.10)."""
    detail = _message_from(response)
    return ProviderError(
        f"{url} returned {status}{': ' + detail if detail else ''}",
        retryable=status in RETRYABLE_STATUS,
        status=status,
    )


def _message_from(response: Response) -> str:
    """Pull the provider's own explanation out of an error body.

    Every vendor nests it differently and none of them documents the shape as stable, so
    this tries the known spellings and falls back to the raw text rather than asserting a
    schema on an error path.
    """
    error = response.body.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message
    if isinstance(error, str):
        return error
    message = response.body.get("message")
    if isinstance(message, str):
        return message
    return response.raw[:400]


class RetryingTransport:
    """Wraps a transport with bounded retries and exponential backoff.

    Retries live here rather than in the loop because the loop's business is escalation
    and budgets, and a 503 that succeeds on the second attempt is not an escalation
    trigger (FR-11.4 requires a *recorded* trigger, and a transient network blip is not
    evidence that a task needs a larger model).

    The sleep function is injectable so tests exercise the backoff schedule without
    actually waiting -- a retry test that really sleeps is a test that gets deleted.
    """

    def __init__(
        self,
        inner: Transport,
        *,
        attempts: int = 3,
        base_delay_s: float = 0.5,
        sleep: Any = time.sleep,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self._inner = inner
        self._attempts = attempts
        self._base_delay_s = base_delay_s
        self._sleep = sleep
        self.delays: list[float] = []
        """Every delay actually waited, so a test can assert the schedule."""

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> Response:
        last: ProviderError | None = None
        for attempt in range(self._attempts):
            try:
                return self._inner.post_json(
                    url, headers=headers, payload=payload, timeout_s=timeout_s
                )
            except ProviderError as exc:
                if not exc.retryable:
                    raise
                last = exc
                if attempt + 1 < self._attempts:
                    delay = self._base_delay_s * (2**attempt)
                    self.delays.append(delay)
                    self._sleep(delay)
        assert last is not None
        raise ProviderError(
            f"{last} (after {self._attempts} attempts)",
            retryable=True,
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> Response:
        """The same backoff, for a transport that is not a model endpoint.

        Delegated rather than duplicated: an integration wrapped in retries that used a
        different schedule from the model path would make one of the two schedules a
        surprise, and the surprising one is always the one nobody tested.
        """
        inner = self._inner
        if not isinstance(inner, RequestTransport):
            raise ProviderError(
                f"{type(inner).__name__} cannot issue a {method}; it only posts JSON",
                retryable=False,
            )
        last: ProviderError | None = None
        for attempt in range(self._attempts):
            try:
                return inner.request(
                    method, url, headers=headers, payload=payload, timeout_s=timeout_s
                )
            except ProviderError as exc:
                if not exc.retryable:
                    raise
                last = exc
                if attempt + 1 < self._attempts:
                    delay = self._base_delay_s * (2**attempt)
                    self.delays.append(delay)
                    self._sleep(delay)
        assert last is not None
        raise ProviderError(f"{last} (after {self._attempts} attempts)", retryable=True)
