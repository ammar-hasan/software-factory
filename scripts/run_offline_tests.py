#!/usr/bin/env python3
"""Run the test suite with outbound network access denied.

Local is the reference implementation, not a degraded mode (PRD PR-2), so the suite has
to pass with no network at all. If it does not, something has taken a dependency it
should not have.

The guard patches *connection* rather than replacing ``socket.socket``: the class is
subclassed by :mod:`ssl`, so swapping it out breaks imports rather than blocking traffic.
Name resolution is blocked too, since a test that resolves a host is already reaching out.
"""

from __future__ import annotations

import socket
import sys

import pytest


class NetworkDeniedError(OSError):
    """Raised instead of opening a connection, so a failure names the cause."""


def _deny(*_args: object, **_kwargs: object) -> None:
    raise NetworkDeniedError(
        "network access is not permitted in the offline job; the suite must pass with no network"
    )


def install_guard() -> None:
    socket.socket.connect = _deny  # type: ignore[method-assign]
    socket.socket.connect_ex = _deny  # type: ignore[method-assign]
    socket.create_connection = _deny  # type: ignore[assignment]
    socket.getaddrinfo = _deny  # type: ignore[assignment]


if __name__ == "__main__":
    install_guard()
    sys.exit(pytest.main(["-q", "--no-cov", "-p", "no:cacheprovider", *sys.argv[1:]]))
