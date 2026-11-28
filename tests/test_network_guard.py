"""
Tests for the network guard every test runs behind (spec 002, RF-07).

The positive controls go through the real stack (a lookup of ``localhost``, a TLS handshake
with a local server made by the ``serve`` fixture), so they prove that the guard lets loopback
traffic through. The refusals use documentation addresses (``192.0.2.0/24``, ``2001:db8::/32``)
and the reserved ``example.com``; each asserts :class:`NetworkAccessError` and the guard's own
record, which shows that the wrapper answered, not a network.
"""

from __future__ import annotations

import re
import socket
import ssl
from collections.abc import Callable

import pytest
import trustme

from network_guard import NetworkAccessError, NetworkGuard
from tls_server import LocalServer

# ---------------------------------------------------------------------------
# Positive controls: loopback is allowed
# ---------------------------------------------------------------------------


def test_resolving_localhost_is_allowed(network_guard: NetworkGuard) -> None:
    infos = socket.getaddrinfo("localhost", 443, type=socket.SOCK_STREAM)

    assert infos, "localhost resolved to nothing"
    assert network_guard.lookups == ["localhost"]


def test_a_tls_handshake_with_the_local_server_is_allowed(
    network_guard: NetworkGuard, ca: trustme.CA, serve: Callable[..., LocalServer]
) -> None:
    server = serve(ca.issue_cert("localhost"))
    client = ssl.create_default_context()
    # The default since Python 3.10, written out so static analysis sees no TLS 1.0 or 1.1.
    client.minimum_version = ssl.TLSVersion.TLSv1_2
    ca.configure_trust(client)

    with (
        socket.create_connection((server.host, server.port), timeout=5) as sock,
        client.wrap_socket(sock, server_hostname="localhost") as tls,
    ):
        peer = tls.getpeercert()

    assert peer is not None
    assert ("DNS", "localhost") in peer["subjectAltName"]
    assert network_guard.connects == [(server.host, server.port)]


def test_the_older_lookups_of_a_loopback_address_are_allowed(network_guard: NetworkGuard) -> None:
    assert socket.gethostbyname("127.0.0.1") == "127.0.0.1"
    assert socket.gethostbyname_ex("localhost")[2]

    assert network_guard.lookups == ["127.0.0.1", "localhost"]


# ---------------------------------------------------------------------------
# Refusals: anything outside loopback
# ---------------------------------------------------------------------------


def test_resolving_a_public_name_is_refused(network_guard: NetworkGuard) -> None:
    with pytest.raises(NetworkAccessError, match=re.escape("example.com")):
        socket.getaddrinfo("example.com", 443)

    assert network_guard.lookups == ["example.com"]


@pytest.mark.parametrize(
    ("family", "address"),
    [
        (socket.AF_INET, ("192.0.2.1", 443)),
        (socket.AF_INET6, ("2001:db8::1", 443, 0, 0)),
        (socket.AF_INET, ("example.com", 443)),  # a name resolved inside C, past getaddrinfo
    ],
    ids=["ipv4", "ipv6", "name"],
)
@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_connecting_outside_loopback_is_refused(
    network_guard: NetworkGuard,
    family: socket.AddressFamily,
    address: tuple[object, ...],
    method: str,
) -> None:
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        pytest.skip("this platform cannot open a socket of that family")

    with sock, pytest.raises(NetworkAccessError, match=re.escape(str(address[0]))):
        getattr(sock, method)(address)

    assert network_guard.connects == [address]


def test_create_connection_to_a_public_name_is_refused_before_any_connect(
    network_guard: NetworkGuard,
) -> None:
    with pytest.raises(NetworkAccessError, match=re.escape("example.com")):
        socket.create_connection(("example.com", 443), timeout=1)

    assert network_guard.lookups == ["example.com"]
    assert network_guard.connects == []


@pytest.mark.parametrize("name", ["gethostbyname", "gethostbyname_ex", "gethostbyaddr"])
def test_an_older_lookup_of_a_public_name_is_refused(
    network_guard: NetworkGuard, name: str
) -> None:
    with pytest.raises(NetworkAccessError, match=re.escape("example.com")):
        getattr(socket, name)("example.com")

    assert network_guard.lookups == ["example.com"]


def test_looking_up_the_machine_s_own_name_is_refused(network_guard: NetworkGuard) -> None:
    # What smtplib does without a local_hostname: getfqdn() looks up gethostname().
    machine = socket.gethostname()
    if machine.lower().rstrip(".") == "localhost":
        pytest.skip("this machine is named localhost")

    with pytest.raises(NetworkAccessError, match=re.escape(machine)):
        socket.getfqdn()

    assert network_guard.lookups == [machine]
