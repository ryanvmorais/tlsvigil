"""
Tests for the certificate check (spec 002).

The context is tested on its own; the check runs against :class:`~tls_server.LocalServer` on
``127.0.0.1`` with certificates made during the test (``trustme`` and ``certs.py``), and
``client_context`` is the real context of the check with the test CA added. Each test takes the
time once and builds its certificates relative to it (RNF-04). The network guard of
``conftest.py`` sees every lookup and connect, so a test can also assert that none happened.
"""

from __future__ import annotations

import errno
import math
import socket
import ssl
import warnings
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
import trustme

from certs import PemLeaf, leaf_without_aki, self_signed_leaf
from network_guard import NetworkGuard
from tls_server import LocalServer
from tlsvigil.certificate import (
    CertificateResult,
    ErrorKind,
    HostProblem,
    InvalidHost,
    InvalidReason,
    Outcome,
    check_certificate,
    create_context,
    validate_host,
)

Serve = Callable[..., LocalServer]


def _now() -> datetime:
    """
    Take the time a test counts from, to the second.

    Returns:
        datetime: The current time in UTC without microseconds, because a certificate stores
            its dates to the second: an expiry of ``now + 90 days`` is then exactly 90 days away.
    """
    return datetime.now(UTC).replace(microsecond=0)


def _leaf(
    ca: trustme.CA,
    now: datetime,
    *,
    valid_for: timedelta,
    valid_since: timedelta = timedelta(days=1),
    name: str = "localhost",
) -> trustme.LeafCert:
    """
    Issue a leaf for ``name``, valid from ``now - valid_since`` to ``now + valid_for``.

    Args:
        ca (trustme.CA): The authority that signs it.
        now (datetime): The test's time.
        valid_for (timedelta): From ``now`` to the expiry; negative for an expired leaf.
        valid_since (timedelta): From the start of the validity to ``now``; negative for a leaf
            not valid yet.
        name (str): The DNS name or IP address the leaf is for.

    Returns:
        trustme.LeafCert: The leaf, with the chain up to ``ca``.
    """
    return ca.issue_cert(name, not_before=now - valid_since, not_after=now + valid_for)


# ---------------------------------------------------------------------------
# The context (RF-05, RNF-02, ADR-2)
# ---------------------------------------------------------------------------


def test_the_context_requires_tls_1_2_or_newer() -> None:
    assert create_context().minimum_version is ssl.TLSVersion.TLSv1_2


def test_the_context_verifies_the_chain_as_python_3_12_does() -> None:
    # 3.13 added both flags to the defaults; without this, the verdict would depend on the
    # Python that runs the check (ADR-2).
    flags = create_context().verify_flags

    assert not flags & ssl.VERIFY_X509_STRICT
    assert not flags & ssl.VERIFY_X509_PARTIAL_CHAIN


def test_the_context_keeps_the_default_verification_flags() -> None:
    # Positive control for the test above: clearing two flags must not clear the rest.
    flags = create_context().verify_flags

    assert flags & ssl.VERIFY_X509_TRUSTED_FIRST


def test_the_context_matches_the_host_against_the_subject_alternative_names_only() -> None:
    assert create_context().hostname_checks_common_name is False


def test_the_context_requires_a_verified_certificate_for_the_host() -> None:
    context = create_context()

    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True


# ---------------------------------------------------------------------------
# Verified (RF-01, RF-05, RNF-02, RNF-03)
# ---------------------------------------------------------------------------


def test_a_certificate_90_days_from_expiry_has_90_days_left(
    ca: trustme.CA, serve: Serve, client_context: ssl.SSLContext
) -> None:
    now = _now()
    server = serve(_leaf(ca, now, valid_for=timedelta(days=90)))

    result = check_certificate("localhost", server.port, now=now, context=client_context)

    # The issuer names have tests of their own; every other field is pinned here.
    assert result == CertificateResult(
        host="localhost",
        port=server.port,
        checked_at=now,
        outcome=Outcome.VERIFIED,
        not_after=now + timedelta(days=90),
        days_left=90,
        issuer_organization=result.issuer_organization,
        issuer_common_name=result.issuer_common_name,
    )


@pytest.mark.parametrize(
    ("valid_for", "days_left"),
    [
        pytest.param(timedelta(days=30, hours=1), 30, id="30 days and an hour"),
        pytest.param(timedelta(days=30, seconds=-1), 29, id="a second short of 30 days"),
        pytest.param(timedelta(hours=2), 0, id="two hours"),
    ],
)
def test_the_days_left_are_rounded_down(
    ca: trustme.CA,
    serve: Serve,
    client_context: ssl.SSLContext,
    valid_for: timedelta,
    days_left: int,
) -> None:
    now = _now()
    server = serve(_leaf(ca, now, valid_for=valid_for))

    result = check_certificate("localhost", server.port, now=now, context=client_context)

    assert result.outcome is Outcome.VERIFIED
    assert result.days_left == days_left


def test_a_time_later_than_the_expiry_gives_zero_days_not_a_negative_number(
    ca: trustme.CA, serve: Serve, client_context: ssl.SSLContext
) -> None:
    # The system clock still accepts the certificate; only the `now` given is past its expiry.
    now = _now()
    server = serve(_leaf(ca, now, valid_for=timedelta(hours=1)))

    result = check_certificate(
        "localhost", server.port, now=now + timedelta(hours=2), context=client_context
    )

    assert result.outcome is Outcome.VERIFIED
    assert result.days_left == 0


def test_the_time_given_is_kept_in_utc(
    ca: trustme.CA, serve: Serve, client_context: ssl.SSLContext
) -> None:
    now = _now()
    server = serve(_leaf(ca, now, valid_for=timedelta(days=10)))
    in_sao_paulo = now.astimezone(timezone(timedelta(hours=-3)))

    result = check_certificate("localhost", server.port, now=in_sao_paulo, context=client_context)

    assert result.checked_at == now
    assert result.checked_at.tzinfo is UTC
    assert result.days_left == 10


def test_the_issuer_organization_is_read(serve: Serve) -> None:
    # A trustme CA names an organization and a unit, but no common name.
    ca = trustme.CA(organization_name="TLSVigil Test Authority")
    context = create_context()
    ca.configure_trust(context)
    server = serve(_leaf(ca, _now(), valid_for=timedelta(days=30)))

    result = check_certificate("localhost", server.port, context=context)

    assert result.outcome is Outcome.VERIFIED
    assert result.issuer_organization == "TLSVigil Test Authority"
    assert result.issuer_common_name is None


def test_the_issuer_common_name_is_read(serve: Serve) -> None:
    # A self-signed leaf is its own issuer, with a common name and no organization; trusting it
    # directly makes it verify.
    leaf = self_signed_leaf("localhost")
    context = create_context()
    context.load_verify_locations(cadata=leaf.chain_pem[0].decode())
    server = serve(leaf)

    result = check_certificate("localhost", server.port, context=context)

    assert result.outcome is Outcome.VERIFIED, result.message
    assert result.issuer_common_name == "localhost"
    assert result.issuer_organization is None


def test_a_peer_certificate_without_an_issuer_gives_no_issuer_names(
    monkeypatch: pytest.MonkeyPatch,
    ca: trustme.CA,
    serve: Serve,
    client_context: ssl.SSLContext,
) -> None:
    # The names are optional data: their absence leaves them `None` and the check verified.
    real_getpeercert = ssl.SSLSocket.getpeercert

    def without_issuer(self: ssl.SSLSocket, binary_form: bool = False) -> Any:
        """Return the real peer certificate minus its issuer."""
        cert = real_getpeercert(self)
        assert cert is not None
        return {key: value for key, value in cert.items() if key != "issuer"}

    monkeypatch.setattr(ssl.SSLSocket, "getpeercert", without_issuer)
    server = serve(_leaf(ca, _now(), valid_for=timedelta(days=30)))

    result = check_certificate("localhost", server.port, context=client_context)

    assert result.outcome is Outcome.VERIFIED
    assert result.issuer_organization is None
    assert result.issuer_common_name is None


def test_a_certificate_for_an_ip_address_verifies_when_checked_by_that_address(
    ca: trustme.CA, serve: Serve, client_context: ssl.SSLContext
) -> None:
    now = _now()
    server = serve(_leaf(ca, now, valid_for=timedelta(days=30), name="127.0.0.1"))

    result = check_certificate("127.0.0.1", server.port, now=now, context=client_context)

    assert result.outcome is Outcome.VERIFIED
    assert result.days_left == 30


def test_a_leaf_without_an_authority_key_identifier_verifies(
    ca: trustme.CA, serve: Serve, client_context: ssl.SSLContext
) -> None:
    # Browsers and Python 3.12 accept it; VERIFY_X509_STRICT, a 3.13 default, refuses it (ADR-2).
    server = serve(leaf_without_aki(ca))

    result = check_certificate("localhost", server.port, context=client_context)

    assert result.outcome is Outcome.VERIFIED, result.message


def test_without_a_context_the_check_trusts_the_system_store_only(
    ca: trustme.CA, serve: Serve
) -> None:
    # Positive control for `client_context`: the test CA is in no system store, so the default
    # context refuses the leaf the other tests verify. The time defaults to the current one.
    before = datetime.now(UTC)
    server = serve(_leaf(ca, _now(), valid_for=timedelta(days=30)))

    result = check_certificate("localhost", server.port)

    assert result.outcome is Outcome.INVALID
    assert result.reason is InvalidReason.UNTRUSTED
    assert before <= result.checked_at <= datetime.now(UTC)


# ---------------------------------------------------------------------------
# Invalid: the verification failed (RF-03)
# ---------------------------------------------------------------------------

# Each case gives the leaf to serve, whether to send its intermediates, and the host to check.
InvalidCase = Callable[[trustme.CA, datetime], tuple[trustme.LeafCert | PemLeaf, bool, str]]


def _expired(ca: trustme.CA, now: datetime) -> tuple[trustme.LeafCert | PemLeaf, bool, str]:
    """Return a leaf that expired yesterday, after 30 days of validity."""
    leaf = _leaf(ca, now, valid_for=-timedelta(days=1), valid_since=timedelta(days=30))
    return leaf, True, "localhost"


def _not_yet_valid(ca: trustme.CA, now: datetime) -> tuple[trustme.LeafCert | PemLeaf, bool, str]:
    """Return a leaf whose validity starts tomorrow."""
    leaf = _leaf(ca, now, valid_for=timedelta(days=90), valid_since=-timedelta(days=1))
    return leaf, True, "localhost"


def _other_name(ca: trustme.CA, now: datetime) -> tuple[trustme.LeafCert | PemLeaf, bool, str]:
    """Return a leaf issued for ``other.test``, checked as ``localhost``."""
    return _leaf(ca, now, valid_for=timedelta(days=30), name="other.test"), True, "localhost"


def _name_by_address(ca: trustme.CA, now: datetime) -> tuple[trustme.LeafCert | PemLeaf, bool, str]:
    """Return a leaf issued for ``localhost``, checked as ``127.0.0.1``."""
    return _leaf(ca, now, valid_for=timedelta(days=30)), True, "127.0.0.1"


def _self_signed(ca: trustme.CA, now: datetime) -> tuple[trustme.LeafCert | PemLeaf, bool, str]:
    """Return a leaf that signs itself."""
    return self_signed_leaf(), True, "localhost"


def _untrusted_ca(ca: trustme.CA, now: datetime) -> tuple[trustme.LeafCert | PemLeaf, bool, str]:
    """Return a leaf signed by a second CA the client does not trust."""
    return _leaf(trustme.CA(), now, valid_for=timedelta(days=30)), True, "localhost"


def _no_intermediate(ca: trustme.CA, now: datetime) -> tuple[trustme.LeafCert | PemLeaf, bool, str]:
    """Return a leaf signed by an intermediate, served without that intermediate."""
    return _leaf(ca.create_child_ca(), now, valid_for=timedelta(days=30)), False, "localhost"


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        pytest.param(_expired, InvalidReason.EXPIRED, id="expired"),
        pytest.param(_not_yet_valid, InvalidReason.NOT_YET_VALID, id="not yet valid"),
        pytest.param(_other_name, InvalidReason.HOSTNAME_MISMATCH, id="another name"),
        pytest.param(_name_by_address, InvalidReason.HOSTNAME_MISMATCH, id="name by address"),
        pytest.param(_self_signed, InvalidReason.UNTRUSTED, id="self-signed"),
        pytest.param(_untrusted_ca, InvalidReason.UNTRUSTED, id="untrusted CA"),
        pytest.param(_no_intermediate, InvalidReason.UNTRUSTED, id="missing intermediate"),
    ],
)
def test_a_failed_verification_is_invalid_with_its_reason(
    ca: trustme.CA,
    serve: Serve,
    client_context: ssl.SSLContext,
    case: InvalidCase,
    reason: InvalidReason,
) -> None:
    now = _now()
    leaf, full_chain, host = case(ca, now)
    server = serve(leaf, full_chain=full_chain)

    result = check_certificate(host, server.port, now=now, context=client_context)

    assert result.outcome is Outcome.INVALID
    assert result.reason is reason, result.message
    assert result.message
    assert result.days_left is None
    assert result.not_after is None
    assert result.error_kind is None
    # One handshake, never a second one without verification to read the date (RF-03).
    assert server.accepted == 1


def test_the_same_leaf_with_its_intermediate_verifies(
    ca: trustme.CA, serve: Serve, client_context: ssl.SSLContext
) -> None:
    # Positive control for the missing-intermediate case: the chain itself is sound.
    server = serve(_leaf(ca.create_child_ca(), _now(), valid_for=timedelta(days=30)))

    result = check_certificate("localhost", server.port, context=client_context)

    assert result.outcome is Outcome.VERIFIED, result.message


# ---------------------------------------------------------------------------
# Error: the host could not be checked (RF-04, RF-05)
# ---------------------------------------------------------------------------


def _assert_error(result: CertificateResult, kind: ErrorKind) -> None:
    """
    Assert that ``result`` is an ``error`` of ``kind``, with a message and no date.

    Args:
        result (CertificateResult): The result of the check.
        kind (ErrorKind): The kind expected.
    """
    assert result.outcome is Outcome.ERROR
    assert result.error_kind is kind, result.message
    assert result.message
    assert result.days_left is None
    assert result.not_after is None
    assert result.reason is None


def test_a_closed_port_is_refused(client_context: ssl.SSLContext) -> None:
    # A port that was free a moment ago; nothing listens on it now.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    # 5 seconds, because a refused connection on the Windows loopback takes about two.
    result = check_certificate("127.0.0.1", port, timeout=5, now=_now(), context=client_context)

    _assert_error(result, ErrorKind.REFUSED)


def test_a_server_that_never_answers_the_handshake_times_out(
    client_context: ssl.SSLContext,
) -> None:
    with LocalServer("silent") as server:
        result = check_certificate(
            "127.0.0.1", server.port, timeout=0.2, now=_now(), context=client_context
        )

    _assert_error(result, ErrorKind.TIMEOUT)


def test_a_port_without_tls_is_a_tls_error(client_context: ssl.SSLContext) -> None:
    with LocalServer("plain") as server:
        result = check_certificate("127.0.0.1", server.port, now=_now(), context=client_context)

    _assert_error(result, ErrorKind.TLS)


def test_a_name_that_does_not_resolve_is_a_dns_error(
    monkeypatch: pytest.MonkeyPatch, network_guard: NetworkGuard, client_context: ssl.SSLContext
) -> None:
    def no_such_name(*args: object, **kwargs: object) -> list[Any]:
        """Answer as the resolver does for an unknown name."""
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", no_such_name)

    result = check_certificate("no-such-host.invalid", now=_now(), context=client_context)

    _assert_error(result, ErrorKind.DNS)
    assert network_guard.connects == []


@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        pytest.param(TimeoutError("timed out"), ErrorKind.TIMEOUT, id="connect timeout"),
        pytest.param(
            OSError(errno.ENETUNREACH, "Network is unreachable"), ErrorKind.OTHER, id="other"
        ),
    ],
)
def test_a_connection_that_fails_before_the_handshake_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
    client_context: ssl.SSLContext,
    failure: OSError,
    kind: ErrorKind,
) -> None:
    # Loopback never times out on connect nor is unreachable, so the failure is injected.
    def fail(*args: object, **kwargs: object) -> socket.socket:
        """Fail the way the connection would."""
        raise failure

    monkeypatch.setattr(socket, "create_connection", fail)

    result = check_certificate("127.0.0.1", 443, now=_now(), context=client_context)

    _assert_error(result, kind)


def test_a_peer_certificate_without_an_expiry_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
    ca: trustme.CA,
    serve: Serve,
    client_context: ssl.SSLContext,
) -> None:
    # A verified handshake always carries notAfter; the check reports its absence, never a date.
    server = serve(_leaf(ca, _now(), valid_for=timedelta(days=30)))
    monkeypatch.setattr(ssl.SSLSocket, "getpeercert", lambda self, binary_form=False: {})

    result = check_certificate("localhost", server.port, context=client_context)

    _assert_error(result, ErrorKind.OTHER)


def test_a_server_limited_to_tls_1_1_is_a_tls_error(
    ca: trustme.CA, serve: Serve, client_context: ssl.SSLContext
) -> None:
    # Positive control first: a client that allows TLS 1.1 completes the handshake, so the
    # failure below comes from the check's TLS 1.2 minimum, not from a broken server (RF-05).
    # TLS 1.1 is deprecated in `ssl`, so its warnings are expected here and only here.
    def tls_1_1_only(context: ssl.SSLContext) -> None:
        """Limit the server to TLS 1.1, which needs the lowest OpenSSL security level."""
        context.minimum_version = ssl.TLSVersion.TLSv1_1
        context.maximum_version = ssl.TLSVersion.TLSv1_1
        context.set_ciphers("DEFAULT:@SECLEVEL=0")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        server = serve(_leaf(ca, _now(), valid_for=timedelta(days=30)), configure=tls_1_1_only)
        legacy_client = ssl.create_default_context()
        ca.configure_trust(legacy_client)
        legacy_client.minimum_version = ssl.TLSVersion.TLSv1_1
        legacy_client.set_ciphers("DEFAULT:@SECLEVEL=0")
        try:
            with (
                socket.create_connection((server.host, server.port), timeout=5) as sock,
                legacy_client.wrap_socket(sock, server_hostname="localhost") as tls,
            ):
                version = tls.version()
        except ssl.SSLError as exc:
            pytest.skip(f"this platform cannot complete a TLS 1.1 handshake: {exc}")
    assert version == "TLSv1.1"

    result = check_certificate("localhost", server.port, now=_now(), context=client_context)

    _assert_error(result, ErrorKind.TLS)
    assert server.accepted == 2


# ---------------------------------------------------------------------------
# Validation: bad arguments never reach a socket (RF-06)
# ---------------------------------------------------------------------------

BAD_ARGUMENTS: list[Any] = [
    # Hosts
    pytest.param({"host": ""}, "host", HostProblem.EMPTY, id="empty host"),
    pytest.param({"host": None}, "host", HostProblem.EMPTY, id="host not a string"),
    pytest.param({"host": b"localhost"}, "host", HostProblem.EMPTY, id="host as bytes"),
    pytest.param({"host": " localhost"}, "host", HostProblem.WHITESPACE, id="leading space"),
    pytest.param({"host": "localhost\n"}, "host", HostProblem.WHITESPACE, id="trailing newline"),
    pytest.param({"host": "local host"}, "host", HostProblem.WHITESPACE, id="inner space"),
    pytest.param({"host": "https://localhost"}, "host", HostProblem.URL_OR_PATH, id="URL"),
    pytest.param({"host": "localhost/path"}, "host", HostProblem.URL_OR_PATH, id="path"),
    pytest.param({"host": "[::1]"}, "host", HostProblem.URL_OR_PATH, id="bracketed address"),
    pytest.param({"host": "user@localhost"}, "host", HostProblem.URL_OR_PATH, id="user info"),
    pytest.param(
        {"host": "localhost:443"}, "host", HostProblem.PORT_IN_HOST, id="port in the host"
    ),
    pytest.param(
        {"host": "127.0.0.1:443"}, "host", HostProblem.PORT_IN_HOST, id="port after an IPv4 address"
    ),
    # Ports
    pytest.param({"port": 0}, "port", HostProblem.PORT, id="port 0"),
    pytest.param({"port": 65536}, "port", HostProblem.PORT, id="port 65536"),
    pytest.param({"port": -443}, "port", HostProblem.PORT, id="negative port"),
    pytest.param({"port": True}, "port", HostProblem.PORT, id="port as bool"),
    pytest.param({"port": 443.0}, "port", HostProblem.PORT, id="port as float"),
    pytest.param({"port": "443"}, "port", HostProblem.PORT, id="port as string"),
    # Timeouts
    pytest.param({"timeout": 0}, "timeout", None, id="timeout 0"),
    pytest.param({"timeout": -1.0}, "timeout", None, id="negative timeout"),
    pytest.param({"timeout": math.inf}, "timeout", None, id="infinite timeout"),
    pytest.param({"timeout": math.nan}, "timeout", None, id="timeout NaN"),
    pytest.param({"timeout": True}, "timeout", None, id="timeout as bool"),
    pytest.param({"timeout": "5"}, "timeout", None, id="timeout as string"),
    pytest.param({"timeout": None}, "timeout", None, id="timeout None"),
    # Time: naive on purpose, the one kind of time the check refuses
    pytest.param({"now": datetime(2026, 1, 1)}, "now", None, id="naive now"),
]


@pytest.mark.parametrize(("bad", "argument", "code"), BAD_ARGUMENTS)
def test_a_bad_argument_is_refused_before_any_lookup_or_connection(
    network_guard: NetworkGuard, bad: dict[str, Any], argument: str, code: HostProblem | None
) -> None:
    """A bad host or port carries its rule as a code (spec 004, ADR-2); the others do not."""
    arguments: dict[str, Any] = {"host": "localhost", "port": 443, "timeout": 5.0} | bad
    host = arguments.pop("host")
    port = arguments.pop("port")

    with pytest.raises(ValueError, match=f"^{argument} must") as raised:
        check_certificate(host, port, **arguments)

    if code is None:
        assert type(raised.value) is ValueError
    else:
        assert isinstance(raised.value, InvalidHost)
        assert raised.value.code is code
    assert network_guard.lookups == []
    assert network_guard.connects == []


def test_valid_arguments_reach_the_server(
    network_guard: NetworkGuard, ca: trustme.CA, serve: Serve, client_context: ssl.SSLContext
) -> None:
    # Positive control for the table above: the same guard records the connection.
    server = serve(_leaf(ca, _now(), valid_for=timedelta(days=30), name="127.0.0.1"))

    result = check_certificate("127.0.0.1", server.port, timeout=5, context=client_context)

    assert result.outcome is Outcome.VERIFIED
    assert network_guard.connects == [(server.host, server.port)]


@pytest.mark.parametrize("host", ["::1", "2001:db8::1", "fe80::1"])
def test_an_ipv6_address_is_a_valid_host(host: str) -> None:
    # Positive control for the ':' rule: an IPv6 address is the one host that may contain ':'.
    validate_host(host, 443)
