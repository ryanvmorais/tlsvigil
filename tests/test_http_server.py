"""
Tests for the local HTTPS server of the RDAP and exchange tests (spec 003, RF-08; spec 005, RF-14).

Each test drives the ``https_server`` fixture with :mod:`http.client` and ``client_context``, the
context of the check with the test CA trusted, so the server is exercised the way the RDAP client
will use it: over a verified TLS connection to ``127.0.0.1``. Nothing leaves the loopback
interface; the network guard is active as in every test.
"""

from __future__ import annotations

import http.client
import ssl
import time

import pytest

from http_server import LocalHTTPServer, RecordedRequest, Route
from tlsvigil.certificate import create_context

# A client timeout long enough for a loaded CI machine, short enough to fail fast.
_TIMEOUT = 5.0


def _get(
    server: LocalHTTPServer,
    context: ssl.SSLContext,
    path: str,
    headers: dict[str, str] | None = None,
) -> tuple[int, http.client.HTTPMessage, bytes]:
    """
    Send one ``GET`` to the server and read the whole answer.

    Args:
        server (LocalHTTPServer): The running server.
        context (ssl.SSLContext): The client context.
        path (str): The request path.
        headers (dict[str, str] | None): Extra request headers.

    Returns:
        tuple[int, http.client.HTTPMessage, bytes]: The status, the headers and the body.
    """
    conn = http.client.HTTPSConnection(server.host, server.port, timeout=_TIMEOUT, context=context)
    try:
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        return response.status, response.headers, response.read()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fixed answers
# ---------------------------------------------------------------------------


def test_a_fixed_route_answers_its_status_headers_and_body(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    body = b'{"objectClassName": "domain"}'
    https_server.routes["/rdap/domain/example.com"] = Route(
        200, body, {"Content-Type": "application/rdap+json"}
    )

    status, headers, read = _get(https_server, client_context, "/rdap/domain/example.com")

    assert status == 200
    assert headers["Content-Type"] == "application/rdap+json"
    assert headers["Content-Length"] == str(len(body))
    assert headers["Connection"] == "close"
    assert read == body
    assert https_server.errors == []


def test_a_content_length_given_by_the_route_replaces_the_real_one(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    # A test of the size cap announces a large body and sends none; the client never reads it.
    https_server.routes["/big"] = Route(200, b"", {"Content-Length": "614400"})
    conn = http.client.HTTPSConnection(
        https_server.host, https_server.port, timeout=_TIMEOUT, context=client_context
    )
    try:
        conn.request("GET", "/big")
        response = conn.getresponse()
        assert response.getheader("Content-Length") == "614400"
        assert response.headers.get_all("Content-Length") == ["614400"]
    finally:
        conn.close()


def test_a_redirect_route_answers_its_location(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    https_server.routes["/old"] = Route.redirect(301, "https://localhost/new")

    status, headers, body = _get(https_server, client_context, "/old")

    assert status == 301
    assert headers["Location"] == "https://localhost/new"
    assert body == b""


def test_a_path_without_a_route_answers_404(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    https_server.routes["/known"] = Route(200, b"ok")

    status, _, body = _get(https_server, client_context, "/unknown")
    control, _, _ = _get(https_server, client_context, "/known")

    assert status == 404
    assert body == b""
    assert control == 200


def test_the_routes_can_change_while_the_server_runs(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    # The bootstrap document names the server's own URL, known only after the port is bound.
    https_server.routes["/dns.json"] = Route(200, https_server.url("/rdap/").encode())

    _, _, body = _get(https_server, client_context, "/dns.json")

    assert body == f"https://127.0.0.1:{https_server.port}/rdap/".encode()


# ---------------------------------------------------------------------------
# Recorded requests
# ---------------------------------------------------------------------------


def test_each_request_is_recorded_with_its_path_and_headers(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    https_server.routes["/dns.json"] = Route(200, b"{}")

    _get(https_server, client_context, "/dns.json", {"User-Agent": "tlsvigil/test"})
    _get(https_server, client_context, "/rdap/domain/example.com?x=1")

    assert [(r.method, r.path) for r in https_server.requests] == [
        ("GET", "/dns.json"),
        ("GET", "/rdap/domain/example.com?x=1"),
    ]
    first = https_server.requests[0]
    assert first.header("user-agent") == "tlsvigil/test"
    assert first.header("Host") == f"127.0.0.1:{https_server.port}"
    assert first.header("Cookie") is None


# ---------------------------------------------------------------------------
# Computed answers (spec 005, RF-14)
# ---------------------------------------------------------------------------


def _send(
    server: LocalHTTPServer, context: ssl.SSLContext, method: str, body: bytes | None
) -> tuple[int, bytes]:
    """
    Send one request with a body to ``/issues`` and read the whole answer.

    Args:
        server (LocalHTTPServer): The running server.
        context (ssl.SSLContext): The client context.
        method (str): The method.
        body (bytes | None): The body, or ``None`` for none.

    Returns:
        tuple[int, bytes]: The status and the body.
    """
    conn = http.client.HTTPSConnection(server.host, server.port, timeout=_TIMEOUT, context=context)
    try:
        conn.request(method, "/issues", body=body)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def test_a_handled_route_answers_from_the_method_and_the_body_it_recorded(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    def issues(request: RecordedRequest) -> Route:
        """Create on POST, edit on PATCH, refuse anything else."""
        match request.method:
            case "POST":
                return Route(201, b"created " + request.body)
            case "PATCH":
                return Route(200, b"edited " + request.body)
        return Route(405)

    https_server.routes["/issues"] = Route.handled(issues)

    assert _send(https_server, client_context, "POST", b"one") == (201, b"created one")
    assert _send(https_server, client_context, "PATCH", b"two") == (200, b"edited two")
    assert _send(https_server, client_context, "GET", None) == (405, b"")
    assert [(r.method, r.body) for r in https_server.requests] == [
        ("POST", b"one"),
        ("PATCH", b"two"),
        ("GET", b""),
    ]
    assert https_server.errors == []


def test_a_handler_that_gives_another_handler_is_recorded_as_an_error(
    https_context: ssl.SSLContext, client_context: ssl.SSLContext
) -> None:
    nested = Route.handled(lambda request: Route(200))
    # A server of its own, so its exit joins the request thread before `errors` is read.
    with (
        LocalHTTPServer(
            https_context, {"/issues": Route.handled(lambda request: nested)}
        ) as server,
        pytest.raises((http.client.HTTPException, OSError)),
    ):
        _send(server, client_context, "POST", b"{}")

    assert len(server.requests) == 1
    assert [type(error) for error in server.errors] == [ValueError]


# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------


def test_a_client_that_does_not_trust_the_leaf_fails_and_the_server_keeps_answering(
    https_context: ssl.SSLContext, client_context: ssl.SSLContext
) -> None:
    # The test runs its own server, so the request threads are joined before the errors are read.
    with LocalHTTPServer(https_context, {"/ok": Route(200, b"ok")}) as server:
        # The context of the check without the test CA: the leaf's issuer is unknown to it.
        with pytest.raises(ssl.SSLCertVerificationError):
            _get(server, create_context(), "/ok")
        status, _, body = _get(server, client_context, "/ok")

    assert (status, body) == (200, b"ok")
    assert [r.path for r in server.requests] == ["/ok"]  # the failed client sent none
    # The failed handshake, recorded: an `SSLError` from the client's alert, or on Windows a
    # `ConnectionResetError` when the client's close arrives first; both are `OSError`s.
    assert len(server.errors) == 1
    assert isinstance(server.errors[0], OSError)


# ---------------------------------------------------------------------------
# Misbehaving answers
# ---------------------------------------------------------------------------


def test_a_silent_route_reads_the_request_and_never_answers(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    https_server.routes["/silent"] = Route.silent()
    conn = http.client.HTTPSConnection(
        https_server.host, https_server.port, timeout=0.3, context=client_context
    )
    try:
        conn.request("GET", "/silent")
        with pytest.raises(TimeoutError):
            conn.getresponse()
    finally:
        conn.close()

    assert [r.path for r in https_server.requests] == ["/silent"]


def test_a_drip_route_sends_one_byte_at_a_time(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    https_server.routes["/drip"] = Route.drip(0.05)
    conn = http.client.HTTPSConnection(
        https_server.host, https_server.port, timeout=_TIMEOUT, context=client_context
    )
    try:
        conn.request("GET", "/drip")
        response = conn.getresponse()
        started = time.monotonic()
        received = b"".join(response.read1(1) for _ in range(3))
        elapsed = time.monotonic() - started
    finally:
        conn.close()

    assert response.status == 200
    assert response.getheader("Content-Length") is None
    assert response.getheader("Content-Type") == "application/rdap+json"
    assert response.getheader("Connection") == "close"
    assert received == b"   "
    assert elapsed >= 0.1  # three bytes, about 0.05 s apart: they were not sent together


def test_an_endless_route_sends_past_any_cap_without_a_content_length(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    https_server.routes["/endless"] = Route.endless()
    conn = http.client.HTTPSConnection(
        https_server.host, https_server.port, timeout=_TIMEOUT, context=client_context
    )
    total = 0
    try:
        conn.request("GET", "/endless")
        response = conn.getresponse()
        while total <= 1_048_576:  # twice the domain cap of spec 003, ADR-4
            chunk = response.read1(65_536)
            assert chunk, "the endless route ended"
            total += len(chunk)
    finally:
        conn.close()

    assert response.getheader("Content-Length") is None
    assert response.getheader("Content-Type") == "application/rdap+json"
    assert response.getheader("Connection") == "close"
    assert total > 1_048_576
