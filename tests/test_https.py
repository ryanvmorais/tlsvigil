"""
Tests of the bounded HTTPS exchange (spec 005, Stage 0).

Every request goes to a :class:`~http_server.LocalHTTPServer` on ``127.0.0.1`` that trusts the
test CA, so nothing reaches the network (spec 005, RF-14). The RDAP tests of spec 003 cover the
same exchange through the client; these cover what the channels rely on: any status returned as
is, a redirect never followed, a request body sent whole, and the deadline and the cap on their
own (RNF-02, RNF-05).
"""

from __future__ import annotations

import json
import socket
import ssl
import threading
import time
from collections.abc import Callable
from typing import Final

import pytest

from http_server import LocalHTTPServer, RecordedRequest, Route
from tlsvigil.certificate import create_context
from tlsvigil.https import Deadline, RequestError, Response, deadline_after, request

# A timeout long enough for a loaded CI machine; the timeout tests use a fraction of a second.
TIMEOUT: Final = 5.0
# A cap small enough to cross in a test, large enough for every fixed answer here.
CAP: Final = 1024


def send(
    server: LocalHTTPServer,
    context: ssl.SSLContext,
    path: str = "/",
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    cap: int = CAP,
    timeout: float = TIMEOUT,
) -> Response:
    """
    Send one request to a path of the local server.

    Args:
        server (LocalHTTPServer): The running server.
        context (ssl.SSLContext): The client context.
        path (str): The path to ask.
        method (str): The method.
        headers (dict[str, str] | None): The caller's headers.
        body (bytes | None): The body to send.
        cap (int): The largest body accepted.
        timeout (float): The seconds of the deadline, set now.

    Returns:
        Response: The answer.
    """
    return request(
        method,
        server.url(path),
        headers=headers,
        body=body,
        cap=cap,
        deadline=deadline_after(timeout),
        context=context,
    )


def bounded_error(call: Callable[[], Response], limit: float) -> RequestError:
    """
    Run a request in a thread, fail the test when it is still running after ``limit``, and give
    the error it raised.

    A request that its deadline does not end would otherwise hold the test until the server
    stops; this turns it into a failure.

    Args:
        call (Callable[[], Response]): The request.
        limit (float): The seconds it may take.

    Returns:
        RequestError: The error the request raised; the test fails when it returned instead.
    """
    outcome: list[Response | RequestError] = []

    def run() -> None:
        """Keep what the request gave or raised."""
        try:
            outcome.append(call())
        except RequestError as error:
            outcome.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(limit)
    assert not thread.is_alive(), f"the request was still running after {limit} s"
    assert isinstance(outcome[0], RequestError), f"the request returned {outcome[0]!r}"
    return outcome[0]


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


def test_a_2xx_gives_its_status_body_and_lowercase_headers(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    https_server.routes["/"] = Route(200, b"hello", {"X-Request-Id": "abc"})

    response = send(https_server, client_context)

    assert response.status == 200
    assert response.body == b"hello"
    assert response.headers["x-request-id"] == "abc"
    assert response.headers["content-length"] == "5"
    assert all(name == name.lower() for name in response.headers)


@pytest.mark.parametrize("status", [404, 422, 500])
def test_a_status_outside_2xx_is_returned_with_its_body(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext, status: int
) -> None:
    https_server.routes["/"] = Route(status, b'{"message": "no"}')

    response = send(https_server, client_context)

    assert response.status == status
    assert response.body == b'{"message": "no"}'


def test_a_redirect_is_returned_and_not_followed(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    https_server.routes["/old"] = Route.redirect(301, "/new")
    https_server.routes["/new"] = Route(200, b"moved here")

    response = send(https_server, client_context, "/old")

    assert response.status == 301
    assert response.headers["location"] == "/new"
    assert [recorded.path for recorded in https_server.requests] == ["/old"]


def test_a_body_of_exactly_the_cap_is_read_whole(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    https_server.routes["/"] = Route(200, b"x" * CAP)

    response = send(https_server, client_context)

    assert response.body == b"x" * CAP


# ---------------------------------------------------------------------------
# Requests with a body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_a_request_body_and_its_headers_arrive_as_sent(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext, method: str
) -> None:
    body = json.dumps({"title": "TLSVigil", "body": "ação"}).encode()

    def echo(recorded: RecordedRequest) -> Route:
        """Answer with the body that arrived, so the round trip is seen from both sides."""
        return Route(201, recorded.body, {"Content-Type": "application/json"})

    https_server.routes["/issues"] = Route.handled(echo)

    response = send(
        https_server,
        client_context,
        "/issues",
        method=method,
        # The caller's `Connection` loses to the one the exchange always sends.
        headers={"Content-Type": "application/json", "Connection": "keep-alive"},
        body=body,
    )

    assert response.status == 201
    assert response.body == body
    [recorded] = https_server.requests
    assert recorded.method == method
    assert recorded.body == body
    assert recorded.header("Content-Type") == "application/json"
    assert recorded.header("Content-Length") == str(len(body))
    assert recorded.header("Accept-Encoding") == "identity"
    assert recorded.header("Connection") == "close"


# ---------------------------------------------------------------------------
# The deadline and the cap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "route",
    [
        pytest.param(Route.silent(), id="silent"),
        # One byte every 50 ms resets a socket timeout of 0.3 s forever; only the deadline ends it.
        pytest.param(Route.drip(0.05), id="drip"),
    ],
)
def test_a_server_slower_than_the_deadline_is_a_timeout(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext, route: Route
) -> None:
    https_server.routes["/"] = route

    started = time.monotonic()
    error = bounded_error(lambda: send(https_server, client_context, timeout=0.3), limit=3)

    assert error.kind == "timeout"
    assert time.monotonic() - started < 2


def test_a_deadline_already_passed_sends_nothing(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    https_server.routes["/"] = Route(200, b"ok")
    passed = Deadline(time.monotonic() - 1, TIMEOUT)

    with pytest.raises(RequestError) as caught:
        request("GET", https_server.url("/"), cap=CAP, deadline=passed, context=client_context)

    assert caught.value.kind == "timeout"
    assert caught.value.message == f"the request took longer than {TIMEOUT} s"
    assert https_server.requests == []
    # Positive control: the same request with a deadline ahead reaches the server.
    assert send(https_server, client_context).status == 200
    assert len(https_server.requests) == 1


def test_a_body_that_never_ends_is_too_large(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    https_server.routes["/"] = Route.endless()

    error = bounded_error(lambda: send(https_server, client_context, timeout=1), limit=3)

    assert error.kind == "too_large"
    assert error.message == f"the answer is larger than {CAP // 1024} KiB"


def test_an_announced_body_over_the_cap_is_refused_before_it_is_read(
    https_server: LocalHTTPServer, client_context: ssl.SSLContext
) -> None:
    # Two bytes sent, one more than the cap announced: a read of the body would wait for the
    # missing bytes until the deadline, so `too_large` here means the body was never read.
    https_server.routes["/"] = Route(200, b"{}", {"Content-Length": str(CAP + 1)})

    error = bounded_error(lambda: send(https_server, client_context), limit=3)

    assert error.kind == "too_large"


# ---------------------------------------------------------------------------
# Failures to connect
# ---------------------------------------------------------------------------


def test_a_server_the_context_does_not_trust_is_a_tls_error(
    https_server: LocalHTTPServer,
) -> None:
    https_server.routes["/"] = Route(200, b"ok")

    # The system store only: the test CA is not in it.
    with pytest.raises(RequestError) as caught:
        send(https_server, create_context())

    assert caught.value.kind == "tls"
    assert "certificate" in caught.value.message.lower()


def test_a_closed_port_is_refused(client_context: ssl.SSLContext) -> None:
    # A port that was free a moment ago; nothing listens on it now.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    # 5 seconds, because a refused connection on the Windows loopback takes about two.
    with pytest.raises(RequestError) as caught:
        request(
            "GET",
            f"https://127.0.0.1:{port}/",
            cap=CAP,
            deadline=deadline_after(TIMEOUT),
            context=client_context,
        )

    assert caught.value.kind == "refused"
