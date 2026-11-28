"""
Tests for the RDAP check against the local HTTPS server (spec 003, RF-01, RF-03 to RF-06, RF-09).

Each test serves a bootstrap and the domain answers from ``https_server`` on ``127.0.0.1``, and
builds an :class:`~tlsvigil.rdap.RdapClient` that trusts the test CA and points at that
bootstrap. The bootstrap names the server itself for ``com`` and ``com.br``, an ``http://`` URL
only for ``web``, and nothing for ``org``. What the server recorded tells whether a request was
sent; the network guard, active as in every test, fails anything that leaves the loopback
interface. The days left are counted from a fixed time.
"""

from __future__ import annotations

import itertools
import json
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import trustme

from http_server import LocalHTTPServer, RecordedRequest, Route
from network_guard import NetworkGuard
from tlsvigil import __version__, rdap
from tlsvigil.rdap import (
    MAX_DOMAIN_BYTES,
    MAX_REDIRECTS,
    DomainResult,
    ErrorKind,
    Outcome,
    RdapClient,
    UnknownReason,
)

DATA = Path(__file__).parent / "data" / "rdap"
# A fixed, aware time: the days left never depend on the clock of the machine (RNF-05).
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
# A timeout long enough for a loaded CI machine; the timeout tests use a fraction of a second.
TIMEOUT = 5.0
# The paths the server answers: the bootstrap, and the base of the service it names.
BOOTSTRAP_PATH = "/dns.json"
BASE_PATH = "/rdap/"
DOMAIN_PATH = BASE_PATH + "domain/example.com"
# A documentation address (RFC 5737): nothing may ever connect to it.
PROXY = "http://192.0.2.1:8080"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def rdap_answer(name: str) -> Route:
    """
    Serve one of the hand-written answers of ``tests/data/rdap/``.

    Args:
        name (str): The file name, without ``.json``.

    Returns:
        Route: A ``200`` with the document and the RDAP media type.
    """
    body = (DATA / f"{name}.json").read_bytes()
    return Route(200, body, {"Content-Type": "application/rdap+json"})


def bootstrap_route(services: list[list[list[str]]]) -> Route:
    """
    Serve a bootstrap document with the given services.

    Args:
        services (list[list[list[str]]]): The ``services`` member, pairs of entries and URLs.

    Returns:
        Route: A ``200`` with the document.
    """
    document = {"version": "1.0", "publication": "2026-11-27T00:00:00Z", "services": services}
    return Route(200, json.dumps(document).encode(), {"Content-Type": "application/json"})


def serve_bootstrap(server: LocalHTTPServer, base_url: str | None = None) -> None:
    """
    Serve the bootstrap of the tests on ``server``.

    Args:
        server (LocalHTTPServer): The running server.
        base_url (str | None): The service of ``com`` and ``com.br``; ``None`` means the server's
            own ``/rdap/``.
    """
    service = server.url(BASE_PATH) if base_url is None else base_url
    server.routes[BOOTSTRAP_PATH] = bootstrap_route(
        [
            [["com", "com.br"], [service]],
            [["web"], [f"http://{server.host}:{server.port}{BASE_PATH}"]],
        ]
    )


def requests_to(server: LocalHTTPServer, prefix: str) -> list[RecordedRequest]:
    """
    Give the requests the server recorded whose path starts with ``prefix``.

    Args:
        server (LocalHTTPServer): The server.
        prefix (str): The start of the path.

    Returns:
        list[RecordedRequest]: The matching requests, in order.
    """
    return [request for request in server.requests if request.path.startswith(prefix)]


def connected_hosts(guard: NetworkGuard) -> list[object]:
    """
    Give the host of every connection the guard recorded.

    Args:
        guard (NetworkGuard): The record of the test.

    Returns:
        list[object]: The first item of each address tuple, in order; a path stays as it is.
    """
    return [address[0] if isinstance(address, tuple) else address for address in guard.connects]


def bounded(check: Callable[[], DomainResult], limit: float) -> DomainResult:
    """
    Run a check in a thread and fail the test when it is still running after ``limit``.

    A request that its deadline does not end would otherwise hold the test until the server
    stops; this turns it into a failure.

    Args:
        check (Callable[[], DomainResult]): The check to run.
        limit (float): The seconds it may take.

    Returns:
        DomainResult: The result of the check.
    """
    results: list[DomainResult] = []
    thread = threading.Thread(target=lambda: results.append(check()), daemon=True)
    thread.start()
    thread.join(limit)
    assert not thread.is_alive(), f"the check was still running after {limit} s"
    return results[0]


def assert_error(result: DomainResult, kind: ErrorKind) -> None:
    """
    Assert that a result is an error of ``kind``, with a message and no date.

    Args:
        result (DomainResult): The result of a check.
        kind (ErrorKind): The kind expected.
    """
    assert result.outcome is Outcome.ERROR
    assert result.error_kind is kind
    assert result.message
    assert result.expiration is None
    assert result.days_left is None
    assert result.reason is None


@pytest.fixture
def registry(https_server: LocalHTTPServer) -> LocalHTTPServer:
    """
    Serve the bootstrap of the tests on ``https_server``; the test adds the domain answers.

    Args:
        https_server (LocalHTTPServer): The running server.

    Returns:
        LocalHTTPServer: The same server.
    """
    serve_bootstrap(https_server)
    return https_server


@pytest.fixture
def make_client(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> Callable[..., RdapClient]:
    """
    Build clients that trust the test CA and read the bootstrap of ``https_server``.

    Args:
        https_server (LocalHTTPServer): The server that holds the bootstrap.
        client_context (ssl.SSLContext): The context of the check with the test CA trusted.

    Returns:
        Callable[..., RdapClient]: ``make(timeout=TIMEOUT)``.
    """

    def make(timeout: float = TIMEOUT) -> RdapClient:
        """Build one client."""
        return RdapClient(
            timeout=timeout,
            context=client_context,
            bootstrap_url=https_server.url(BOOTSTRAP_PATH),
        )

    return make


@pytest.fixture
def second_server(https_context: ssl.SSLContext) -> Iterator[LocalHTTPServer]:
    """
    Start one more server with the leaf for ``127.0.0.1``, on another port.

    Args:
        https_context (ssl.SSLContext): The server context of ``https_server``.

    Yields:
        LocalHTTPServer: The running server, with no routes.
    """
    with LocalHTTPServer(https_context) as server:
        yield server


# ---------------------------------------------------------------------------
# Dated and undated
# ---------------------------------------------------------------------------


def test_a_dated_record_gives_its_expiration_and_days_left(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient]
) -> None:
    registry.routes[DOMAIN_PATH] = rdap_answer("example.com")

    result = make_client().check_domain("Example.COM.", now=NOW)

    assert result == DomainResult(
        "example.com",
        NOW,
        Outcome.DATED,
        server="127.0.0.1",
        expiration=datetime(2027, 8, 13, 4, 0, tzinfo=UTC),
        days_left=258,
        registry_name="EXAMPLE.COM",
        statuses=(
            "client delete prohibited",
            "client transfer prohibited",
            "client update prohibited",
        ),
    )
    assert [request.path for request in registry.requests] == [BOOTSTRAP_PATH, DOMAIN_PATH]


def test_a_record_without_an_expiration_is_undated(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient]
) -> None:
    registry.routes[DOMAIN_PATH] = rdap_answer("no-expiration")

    result = make_client().check_domain("example.com", now=NOW)

    assert result == DomainResult(
        "example.com",
        NOW,
        Outcome.UNDATED,
        server="127.0.0.1",
        registry_name="example.com",
        statuses=("active",),
        reason=UnknownReason.NO_EXPIRATION,
    )


@pytest.mark.parametrize(
    "domain",
    [
        pytest.param("example.org", id="absent from the bootstrap"),
        pytest.param("example.web", id="http:// only"),
    ],
)
def test_a_top_level_domain_without_an_https_service_sends_no_domain_request(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient], domain: str
) -> None:
    registry.routes[DOMAIN_PATH] = rdap_answer("example.com")
    client = make_client()

    result = client.check_domain(domain, now=NOW)

    assert result == DomainResult(
        domain, NOW, Outcome.UNDATED, reason=UnknownReason.NO_RDAP_SERVICE
    )
    assert requests_to(registry, BASE_PATH) == []
    # Control: a covered domain on the same client does reach the registry.
    assert client.check_domain("example.com", now=NOW).outcome is Outcome.DATED
    assert len(requests_to(registry, BASE_PATH)) == 1


def test_without_a_time_the_check_counts_from_now_in_utc(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient]
) -> None:
    before = datetime.now(UTC)

    result = make_client().check_domain("example.org")

    assert result.checked_at.tzinfo is UTC
    assert before <= result.checked_at <= datetime.now(UTC)


@pytest.mark.parametrize(
    ("domain", "now"),
    [
        pytest.param("https://example.com", NOW, id="a URL"),
        pytest.param("example.com", datetime(2026, 11, 27, 12, 0), id="a naive now"),
    ],
)
def test_a_bad_argument_sends_no_request(
    registry: LocalHTTPServer,
    make_client: Callable[..., RdapClient],
    network_guard: NetworkGuard,
    domain: str,
    now: datetime,
) -> None:
    registry.routes[DOMAIN_PATH] = rdap_answer("example.com")
    client = make_client()

    with pytest.raises(ValueError, match=r"domain|now"):
        client.check_domain(domain, now=now)

    assert registry.requests == []
    assert network_guard.connects == []
    # Control: a good call on the same client is sent.
    assert client.check_domain("example.com", now=NOW).outcome is Outcome.DATED
    assert network_guard.connects != []


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def test_one_bootstrap_serves_every_domain_of_a_client(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient]
) -> None:
    registry.routes[DOMAIN_PATH] = rdap_answer("example.com")
    registry.routes[BASE_PATH + "domain/example.com.br"] = rdap_answer("example.com.br")
    client = make_client()

    first = client.check_domain("example.com", now=NOW)
    second = client.check_domain("example.com.br", now=NOW)

    assert (first.outcome, second.outcome) == (Outcome.DATED, Outcome.DATED)
    assert second.days_left == 90
    assert len(requests_to(registry, BOOTSTRAP_PATH)) == 1
    assert len(requests_to(registry, BASE_PATH)) == 2


@pytest.mark.parametrize(
    ("route", "message"),
    [
        pytest.param(Route(500), "HTTP 500", id="status 500"),
        pytest.param(Route(200, b"<html>"), "invalid bootstrap document", id="not JSON"),
        pytest.param(
            Route(200, b'{"services": [["com"]]}'),
            "invalid bootstrap document",
            id="malformed services",
        ),
    ],
)
def test_a_failed_bootstrap_is_kept_and_sends_no_domain_request(
    https_server: LocalHTTPServer,
    make_client: Callable[..., RdapClient],
    route: Route,
    message: str,
) -> None:
    https_server.routes[BOOTSTRAP_PATH] = route
    https_server.routes[DOMAIN_PATH] = rdap_answer("example.com")
    client = make_client()

    first = client.check_domain("example.com", now=NOW)
    second = client.check_domain("example.com.br", now=NOW)

    for result in (first, second):
        assert_error(result, ErrorKind.BOOTSTRAP)
        assert result.message is not None
        assert result.message.startswith(message)
        assert result.server is None
    # Fetched once: the failure is kept for the run, like a bootstrap that was read.
    assert [request.path for request in https_server.requests] == [BOOTSTRAP_PATH]


def test_without_a_context_the_client_trusts_the_system_store_only(
    registry: LocalHTTPServer,
) -> None:
    # The test CA is in no system store, so the bootstrap server fails verification.
    client = RdapClient(timeout=TIMEOUT, bootstrap_url=registry.url(BOOTSTRAP_PATH))

    result = client.check_domain("example.com", now=NOW)

    assert_error(result, ErrorKind.BOOTSTRAP)
    assert result.message is not None
    assert "CERTIFICATE_VERIFY_FAILED" in result.message


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        pytest.param(404, ErrorKind.NOT_FOUND, id="404"),
        pytest.param(429, ErrorKind.RATE_LIMITED, id="429"),
        pytest.param(503, ErrorKind.HTTP, id="503"),
    ],
)
def test_a_status_outside_2xx_is_an_error_with_its_code(
    registry: LocalHTTPServer,
    make_client: Callable[..., RdapClient],
    status: int,
    kind: ErrorKind,
) -> None:
    registry.routes[DOMAIN_PATH] = Route(status)

    result = make_client().check_domain("example.com", now=NOW)

    assert_error(result, kind)
    assert result.server == "127.0.0.1"
    assert result.message is not None
    assert result.message.startswith(f"HTTP {status}")
    # Not repeated, a 429 included (requirements, resolved decision 7).
    assert len(requests_to(registry, BASE_PATH)) == 1


def test_a_closed_port_is_refused(
    https_server: LocalHTTPServer, make_client: Callable[..., RdapClient]
) -> None:
    # A port that was free a moment ago; nothing listens on it now.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    serve_bootstrap(https_server, f"https://127.0.0.1:{port}{BASE_PATH}")

    # 5 seconds, because a refused connection on the Windows loopback takes about two.
    result = make_client(timeout=5).check_domain("example.com", now=NOW)

    assert_error(result, ErrorKind.REFUSED)
    assert result.server == "127.0.0.1"


@pytest.mark.parametrize(
    "route",
    [
        pytest.param(Route.silent(), id="silent"),
        # One byte every 50 ms resets a socket timeout of 0.3 s forever; only the deadline ends it.
        pytest.param(Route.drip(0.05), id="drip"),
    ],
)
def test_a_server_slower_than_the_timeout_is_a_timeout(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient], route: Route
) -> None:
    registry.routes[DOMAIN_PATH] = route
    client = make_client(timeout=0.3)

    started = time.monotonic()
    result = bounded(lambda: client.check_domain("example.com", now=NOW), limit=3)

    assert_error(result, ErrorKind.TIMEOUT)
    # The bootstrap and the domain request each get their own 0.3 s.
    assert time.monotonic() - started < 2


def test_a_deadline_that_passes_before_the_body_is_read_is_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
    registry: LocalHTTPServer,
    make_client: Callable[..., RdapClient],
) -> None:
    registry.routes[DOMAIN_PATH] = rdap_answer("example.com")
    client = make_client()
    client.check_domain("example.org", now=NOW)  # the bootstrap, on the real clock
    # The clock of the module only: the deadline is set at 0, the connection is opened at 1, the
    # status line read at 2, and the first read of the body would start at 6, past 5 seconds.
    ticks = itertools.chain([0.0, 1.0, 2.0], itertools.repeat(6.0))
    monkeypatch.setattr(rdap, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    result = client.check_domain("example.com", now=NOW)

    assert_error(result, ErrorKind.TIMEOUT)
    assert result.message == f"the request took longer than {TIMEOUT} s"
    assert len(requests_to(registry, DOMAIN_PATH)) == 1  # sent; only its body was not read


def test_a_registry_the_client_does_not_trust_is_a_tls_error(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient]
) -> None:
    # A leaf from a CA the client was never given.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    trustme.CA().issue_cert("127.0.0.1").configure_cert(context)
    with LocalHTTPServer(context) as untrusted:
        untrusted.routes[DOMAIN_PATH] = rdap_answer("example.com")
        serve_bootstrap(registry, untrusted.url(BASE_PATH))

        result = make_client().check_domain("example.com", now=NOW)

    assert_error(result, ErrorKind.TLS)
    assert result.message is not None
    assert "CERTIFICATE_VERIFY_FAILED" in result.message
    assert untrusted.requests == []


def test_a_registry_name_that_does_not_resolve_is_a_dns_error(
    monkeypatch: pytest.MonkeyPatch,
    registry: LocalHTTPServer,
    make_client: Callable[..., RdapClient],
    network_guard: NetworkGuard,
) -> None:
    resolve = socket.getaddrinfo  # the guard's wrapper

    def no_such_registry(host: Any, *args: Any, **kwargs: Any) -> Any:
        """Answer as the resolver does for an unknown name; delegate the loopback."""
        if host == "rdap.invalid":
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", no_such_registry)
    serve_bootstrap(registry, "https://rdap.invalid/")

    result = make_client().check_domain("example.com", now=NOW)

    assert_error(result, ErrorKind.DNS)
    assert result.server == "rdap.invalid"
    assert set(connected_hosts(network_guard)) == {"127.0.0.1"}


def test_a_url_http_client_cannot_encode_is_another_error(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient]
) -> None:
    # The bootstrap is data: a path outside ASCII reaches http.client, which cannot send it.
    serve_bootstrap(registry, registry.url("/régistre/"))

    result = make_client().check_domain("example.com", now=NOW)

    assert_error(result, ErrorKind.OTHER)
    assert requests_to(registry, "/r") == []


# ---------------------------------------------------------------------------
# Bounds and redirects
# ---------------------------------------------------------------------------


def test_an_announced_body_over_the_cap_is_too_large(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient]
) -> None:
    # Announced, not sent: only the header can tell the client the body is too large.
    announced = str(MAX_DOMAIN_BYTES + 88 * 1024)  # 600 KiB
    registry.routes[DOMAIN_PATH] = Route(200, b"{}", {"Content-Length": announced})

    result = make_client().check_domain("example.com", now=NOW)

    assert_error(result, ErrorKind.TOO_LARGE)
    assert result.message == "the answer is larger than 512 KiB"


def test_a_chunked_body_is_read_whole(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient]
) -> None:
    # Split in three chunks; with Transfer-Encoding, http.client ignores the Content-Length the
    # server adds.
    body = (DATA / "example.com.json").read_bytes()
    parts = (body[:100], body[100:200], body[200:])
    chunked = b"".join(b"%x\r\n%s\r\n" % (len(part), part) for part in parts) + b"0\r\n\r\n"
    registry.routes[DOMAIN_PATH] = Route(200, chunked, {"Transfer-Encoding": "chunked"})

    result = make_client().check_domain("example.com", now=NOW)

    assert result.outcome is Outcome.DATED
    assert result.days_left == 258


def test_a_body_that_never_ends_is_too_large(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient]
) -> None:
    registry.routes[DOMAIN_PATH] = Route.endless()
    client = make_client(timeout=1)

    result = bounded(lambda: client.check_domain("example.com", now=NOW), limit=3)

    assert_error(result, ErrorKind.TOO_LARGE)


@pytest.mark.parametrize(
    "location",
    [
        pytest.param("https://localhost:{port}/moved", id="another host"),
        pytest.param("http://127.0.0.1:{port}/moved", id="http"),
        pytest.param("https://127.0.0.1:99999/moved", id="a port out of range"),
        pytest.param(None, id="no Location"),
    ],
)
def test_a_redirect_off_the_origin_is_not_followed(
    registry: LocalHTTPServer,
    make_client: Callable[..., RdapClient],
    network_guard: NetworkGuard,
    location: str | None,
) -> None:
    registry.routes["/moved"] = rdap_answer("example.com")
    if location is None:
        registry.routes[DOMAIN_PATH] = Route(302)
    else:
        registry.routes[DOMAIN_PATH] = Route.redirect(301, location.format(port=registry.port))

    result = make_client().check_domain("example.com", now=NOW)

    assert_error(result, ErrorKind.REDIRECT)
    assert requests_to(registry, "/moved") == []
    assert "localhost" not in network_guard.lookups


def test_a_redirect_to_another_port_is_not_followed(
    registry: LocalHTTPServer,
    make_client: Callable[..., RdapClient],
    second_server: LocalHTTPServer,
) -> None:
    second_server.routes[DOMAIN_PATH] = rdap_answer("example.com")
    registry.routes[DOMAIN_PATH] = Route.redirect(307, second_server.url(DOMAIN_PATH))

    result = make_client().check_domain("example.com", now=NOW)

    assert_error(result, ErrorKind.REDIRECT)
    assert second_server.requests == []


def chain_of_redirects(server: LocalHTTPServer, hops: int) -> None:
    """
    Make the domain path redirect ``hops`` times on the same server before the answer.

    The first hop is relative and the others absolute, so both forms of ``Location`` are read.

    Args:
        server (LocalHTTPServer): The registry.
        hops (int): The number of redirects before the answer.
    """
    paths = [DOMAIN_PATH] + [f"/hop/{number}" for number in range(1, hops + 1)]
    for number, (path, target) in enumerate(itertools.pairwise(paths)):
        location = target if number == 0 else server.url(target)
        server.routes[path] = Route.redirect(308 if number % 2 else 301, location)
    server.routes[paths[-1]] = rdap_answer("example.com")


@pytest.mark.parametrize("hops", [1, MAX_REDIRECTS])
def test_redirects_on_the_same_origin_are_followed_up_to_the_limit(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient], hops: int
) -> None:
    chain_of_redirects(registry, hops)

    result = make_client().check_domain("example.com", now=NOW)

    assert result.outcome is Outcome.DATED
    assert result.server == "127.0.0.1"
    assert len(requests_to(registry, "/hop/")) == hops


def test_a_redirect_past_the_limit_is_an_error(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient]
) -> None:
    chain_of_redirects(registry, MAX_REDIRECTS + 1)

    result = make_client().check_domain("example.com", now=NOW)

    assert_error(result, ErrorKind.REDIRECT)
    assert result.message == f"more than {MAX_REDIRECTS} redirects"
    # The last hop, which holds the answer, was never asked.
    assert requests_to(registry, f"/hop/{MAX_REDIRECTS + 1}") == []


# ---------------------------------------------------------------------------
# The requests
# ---------------------------------------------------------------------------


def test_every_request_identifies_the_tool_and_carries_no_credential(
    registry: LocalHTTPServer, make_client: Callable[..., RdapClient]
) -> None:
    registry.routes[DOMAIN_PATH] = rdap_answer("example.com")

    make_client().check_domain("example.com", now=NOW)

    assert len(registry.requests) == 2  # the bootstrap and the domain
    for request in registry.requests:
        assert request.method == "GET"
        assert request.header("User-Agent") == f"tlsvigil/{__version__}"
        assert request.header("Accept") == "application/rdap+json"
        assert request.header("Accept-Encoding") == "identity"
        for name in ("Cookie", "Authorization", "Proxy-Authorization"):
            assert request.header(name) is None


def test_the_proxy_variables_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
    registry: LocalHTTPServer,
    make_client: Callable[..., RdapClient],
    network_guard: NetworkGuard,
) -> None:
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, PROXY)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    registry.routes[DOMAIN_PATH] = rdap_answer("example.com")

    result = make_client().check_domain("example.com", now=NOW)

    # Positive control: the local server was reached directly.
    assert result.outcome is Outcome.DATED
    assert len(registry.requests) == 2
    assert set(connected_hosts(network_guard)) == {"127.0.0.1"}
    assert "192.0.2.1" not in network_guard.lookups
