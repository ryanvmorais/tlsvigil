"""
The network guard of the test suite: no test reaches a real network.

:func:`install` wraps the two calls every connection in the standard library passes through,
:func:`socket.getaddrinfo` and :meth:`socket.socket.connect` (plus ``connect_ex``), so that
``socket.create_connection``, :mod:`ssl`, :mod:`http.client` and :mod:`urllib` are all covered.
A name resolves only when it is ``localhost`` or an IP literal, and a connection goes only to
the loopback interface (``127.0.0.0/8``, ``::1``). Anything else raises
:class:`NetworkAccessError`, an ``AssertionError``: code under test that catches ``OSError``
cannot turn it into a quiet result, so the test fails. ``conftest.py`` installs it for every
test (spec 002, RF-07 and ADR-5).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any

import pytest

# Names the guard lets resolve: the loopback name only. IP literals resolve too (no lookup is
# made for them), and the connect wrapper decides whether the address is allowed.
_ALLOWED_NAMES = frozenset({"localhost"})

# ---------------------------------------------------------------------------
# Result and error
# ---------------------------------------------------------------------------


class NetworkAccessError(AssertionError):
    """A test tried to resolve a name or reach an address outside the loopback interface."""


@dataclass
class NetworkGuard:
    """
    What the guard saw during one test, for the tests that assert on it.

    Attributes:
        lookups (list[object]): The host of every :func:`socket.getaddrinfo` call, allowed or
            not, in order.
        connects (list[object]): The address of every ``connect`` and ``connect_ex`` call,
            allowed or not, in order.
    """

    lookups: list[object] = field(default_factory=list)
    connects: list[object] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _ip_literal(host: object) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """
    Parse ``host`` as an IP address, without any lookup.

    Args:
        host (object): The host part of an address, as the socket API received it.

    Returns:
        ipaddress.IPv4Address | ipaddress.IPv6Address | None: The address, or ``None`` when
            ``host`` is a name (or not text at all).
    """
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return None
    try:
        # An IPv6 scope (`fe80::1%eth0`) is not part of the address itself.
        return ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None


def name_allowed(host: object) -> bool:
    """
    Tell whether ``host`` may be resolved.

    Args:
        host (object): The host given to :func:`socket.getaddrinfo`.

    Returns:
        bool: ``True`` for ``None`` (a local wildcard), an IP literal or an allowed name.
    """
    if host is None or _ip_literal(host) is not None:
        return True
    name = host.decode("ascii", "replace") if isinstance(host, bytes) else str(host)
    return name.lower().rstrip(".") in _ALLOWED_NAMES


def address_allowed(address: object) -> bool:
    """
    Tell whether a socket may connect to ``address``.

    Args:
        address (object): The address given to ``connect``: a ``(host, port, ...)`` tuple for
            the IP families, a path for ``AF_UNIX``.

    Returns:
        bool: ``True`` for a loopback address, the ``localhost`` name or a local socket path.
    """
    if isinstance(address, str | bytes):
        return True  # AF_UNIX: a path on this machine, never the network
    if not isinstance(address, tuple) or not address:
        return False
    ip = _ip_literal(address[0])
    if ip is not None:
        return ip.is_loopback
    # `connect(("name", port))` resolves inside C, past the getaddrinfo wrapper, so the name is
    # checked here as well.
    return name_allowed(address[0])


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


def install(monkeypatch: pytest.MonkeyPatch) -> NetworkGuard:
    """
    Wrap the lookup and connect calls of :mod:`socket` until ``monkeypatch`` undoes them.

    Args:
        monkeypatch (pytest.MonkeyPatch): The test's patcher, which restores the real calls
            when the test ends.

    Returns:
        NetworkGuard: The record of the lookups and connections made while installed.
    """
    guard = NetworkGuard()
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        """Record the lookup; refuse a name that is not a loopback name."""
        guard.lookups.append(host)
        if not name_allowed(host):
            raise NetworkAccessError(f"test tried to resolve {host!r}, not a loopback name")
        return real_getaddrinfo(host, *args, **kwargs)

    def connect(self: socket.socket, address: Any) -> None:
        """Record the connect; refuse an address outside loopback."""
        guard.connects.append(address)
        if not address_allowed(address):
            raise NetworkAccessError(f"test tried to connect to {address!r}, not loopback")
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        """Record the connect; refuse an address outside loopback."""
        guard.connects.append(address)
        if not address_allowed(address):
            raise NetworkAccessError(f"test tried to connect to {address!r}, not loopback")
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    return guard
