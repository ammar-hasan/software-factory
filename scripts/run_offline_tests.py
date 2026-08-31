#!/usr/bin/env python3
"""Run the test suite with outbound network access denied.

Local is the reference implementation, not a degraded mode (PRD PR-2), so the suite has
to pass with no network at all. If it does not, something has taken a dependency it
should not have.

The guard patches *connection* rather than replacing ``socket.socket``: the class is
subclassed by :mod:`ssl`, so swapping it out breaks imports rather than blocking traffic.
Name resolution is blocked outright, since a test that resolves a host is already
reaching out.

Loopback is the one exception, and it is a narrow one: a connection is permitted only to
a port **this process is listening on**. The dashboard tests start an HTTP server and
talk to it, which is an in-process round trip rather than a network dependency -- denying
it proves nothing about offline capability and only means the dashboard goes untested in
the job that matters most for it.

The narrowness is the point. A blanket loopback exemption would also permit a test to
reach a model runtime on `127.0.0.1:11434`, which is exactly the hidden dependency this
job exists to catch. Ollama is not bound by this process, so it stays denied.
"""

from __future__ import annotations

import socket
import sys

import pytest

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_bound_ports: set[int] = set()
"""Ports this process is listening on, recorded as they are bound.

Read from ``getsockname()`` rather than the ``bind`` argument: a server that binds port 0
is assigned one by the kernel, and the argument would record 0 forever.
"""

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_bind = socket.socket.bind


class NetworkDeniedError(OSError):
    """Raised instead of opening a connection, so a failure names the cause."""


def _remember_bind(self: socket.socket, address: object) -> None:
    result = _real_bind(self, address)
    try:
        bound = self.getsockname()
    except OSError:  # pragma: no cover - a socket that cannot report its own name
        return result
    if isinstance(bound, tuple) and len(bound) >= 2 and isinstance(bound[1], int):
        _bound_ports.add(bound[1])
    return result


def _is_own_loopback(address: object) -> bool:
    if not isinstance(address, tuple) or len(address) < 2:
        return False
    host, port = address[0], address[1]
    if not isinstance(port, int) or port not in _bound_ports:
        return False
    return isinstance(host, str) and host in LOOPBACK_HOSTS


def _denied(address: object) -> NetworkDeniedError:
    return NetworkDeniedError(
        f"network access is not permitted in the offline job; the suite must pass with "
        f"no network (attempted {address!r})"
    )


def _guarded_connect(self: socket.socket, address: object) -> None:
    if _is_own_loopback(address):
        return _real_connect(self, address)
    raise _denied(address)


def _guarded_connect_ex(self: socket.socket, address: object) -> int:
    if _is_own_loopback(address):
        return _real_connect_ex(self, address)
    raise _denied(address)


def _deny_resolution(*args: object, **_kwargs: object) -> None:
    raise NetworkDeniedError(
        f"name resolution is not permitted in the offline job (attempted {args[:1]!r})"
    )


def _guarded_create_connection(address: object, *args: object, **kwargs: object) -> object:
    """`create_connection` resolves before it connects, so it needs its own guard.

    Left to the resolution guard it would fail with a DNS error even for a loopback
    address the process is listening on, and the message would send the reader to the
    wrong subsystem.
    """
    if not _is_own_loopback(address):
        raise _denied(address)
    host, port = address[0], address[1]  # type: ignore[index]
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _real_connect(sock, (host, port))
    return sock


def install_guard() -> None:
    socket.socket.bind = _remember_bind  # type: ignore[method-assign]
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    socket.create_connection = _guarded_create_connection  # type: ignore[assignment]
    socket.getaddrinfo = _deny_resolution  # type: ignore[assignment]


if __name__ == "__main__":
    install_guard()
    sys.exit(pytest.main(["-q", "--no-cov", "-p", "no:cacheprovider", *sys.argv[1:]]))
