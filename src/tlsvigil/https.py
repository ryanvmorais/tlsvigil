"""
One bounded HTTPS exchange, shared by the RDAP client and the notification channels (spec 005).

:func:`request` sends one request over one connection and reads the whole answer within a cap on
its size and a deadline over the whole exchange, as the RDAP client of spec 003 did on its own
(ADR-3 and ADR-4 there). It returns any status, a redirect included, and never follows one: what
to do with a status is the caller's policy (spec 005, ADR-1). Every failure to connect, to hand
shake or to read becomes a :class:`RequestError` with one of a few kinds.

No proxy variable is read, because :mod:`http.client` looks at none (spec 003, ADR-2).
"""

from __future__ import annotations

import http.client
import socket
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import urlsplit

__all__ = [
    "Deadline",
    "RequestError",
    "RequestErrorKind",
    "Response",
    "deadline_after",
    "request",
]

RequestErrorKind = Literal["dns", "refused", "timeout", "tls", "too_large", "other"]

# Sent on every request, over any header of the caller with the same name.
_FIXED_HEADERS: Final = {
    # No compression, so no compression bomb to bound (spec 003, ADR-4).
    "Accept-Encoding": "identity",
    # One request per connection: nothing is reused between two requests.
    "Connection": "close",
}
# A bounded read, so a body past the cap is refused after one chunk more at most (ADR-4).
_READ_SIZE: Final = 64 * 1024
# Named so that a URL with the port written out and one without reach the same port.
_HTTPS_PORT: Final = 443


# ---------------------------------------------------------------------------
# The answer, the failure and the deadline
# ---------------------------------------------------------------------------


class RequestError(Exception):
    """
    A request that ended without an answer, with the kind of failure.

    Attributes:
        kind (RequestErrorKind): ``dns``, ``refused``, ``timeout``, ``tls``, ``too_large`` or
            ``other``.
        message (str): The text of the failure, as plain text.
    """

    def __init__(self, kind: RequestErrorKind, message: str) -> None:
        """
        Keep the kind and the message.

        Args:
            kind (RequestErrorKind): The kind of failure.
            message (str): The text of the failure.
        """
        super().__init__(message)
        self.kind: RequestErrorKind = kind
        self.message = message


@dataclass(frozen=True, slots=True)
class Response:
    """
    The whole answer to one request, whatever its status.

    Attributes:
        status (int): The status code.
        headers (Mapping[str, str]): The headers, the names in lowercase and the first value of
            each.
        body (bytes): The whole body, within the cap of the request.
    """

    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class Deadline:
    """
    The moment a request, or a series of them, must have ended by (spec 003, ADR-3).

    Attributes:
        at (float): The moment, on the :func:`time.monotonic` clock.
        timeout (float): The seconds it was set for, named in the message of a timeout.
    """

    at: float
    timeout: float

    def remaining(self) -> float:
        """
        Give the seconds left, or fail once the deadline has passed.

        Returns:
            float: The seconds left, greater than ``0``.

        Raises:
            RequestError: ``timeout``, once the deadline has passed.
        """
        left = self.at - time.monotonic()
        if left <= 0:
            raise RequestError("timeout", f"the request took longer than {self.timeout} s")
        return left


def deadline_after(timeout: float) -> Deadline:
    """
    Set a deadline ``timeout`` seconds from now.

    One deadline can cover several requests, such as a request and the redirects that follow it.

    Args:
        timeout (float): The seconds, a finite number greater than ``0``; the caller validates it.

    Returns:
        Deadline: The deadline.
    """
    return Deadline(time.monotonic() + timeout, timeout)


# ---------------------------------------------------------------------------
# The exchange
# ---------------------------------------------------------------------------


def _message(exc: BaseException) -> str:
    """
    Give the text of ``exc`` as the library wrote it, or its type when the text is empty.

    Args:
        exc (BaseException): The exception that was caught.

    Returns:
        str: A non-empty message.
    """
    return str(exc) or type(exc).__name__


def _too_large(cap: int) -> RequestError:
    """
    Give the failure of an answer over its cap.

    Args:
        cap (int): The cap, in bytes.

    Returns:
        RequestError: ``too_large``, with the cap in KiB in the message.
    """
    return RequestError("too_large", f"the answer is larger than {cap // 1024} KiB")


def _request_target(url: str) -> str:
    """
    Give the path and the query of a URL, as the request line sends them.

    Args:
        url (str): An absolute URL.

    Returns:
        str: The path (``/`` when empty) and, when there is one, ``?`` and the query; the
            fragment is never sent.
    """
    parts = urlsplit(url)
    target = parts.path or "/"
    return f"{target}?{parts.query}" if parts.query else target


def _read_body(
    response: http.client.HTTPResponse, sock: socket.socket, cap: int, deadline: Deadline
) -> bytes:
    """
    Read a body in chunks, within the cap and the deadline (spec 003, ADR-3 and ADR-4).

    Args:
        response (http.client.HTTPResponse): The answer, its headers read.
        sock (socket.socket): The socket under the answer; its timeout is set to the time left
            before each read.
        cap (int): The largest body accepted, in bytes.
        deadline (Deadline): The deadline of the request.

    Returns:
        bytes: The whole body.

    Raises:
        RequestError: ``too_large`` past the cap, ``timeout`` once the deadline has passed.
    """
    chunks: list[bytes] = []
    total = 0
    # The answer closes itself, and the socket with it, at the end of the body: at its
    # Content-Length, its last chunk, or the close of the connection. An empty read always ends
    # it, and the deadline bounds the loop in any case. Never `settimeout` after the end: the
    # socket is closed by then.
    while not response.isclosed():
        # A byte now and then resets a socket timeout; the deadline bounds the sum (ADR-3).
        sock.settimeout(deadline.remaining())
        chunk = response.read1(_READ_SIZE)
        total += len(chunk)
        if total > cap:
            raise _too_large(cap)
        chunks.append(chunk)
    return b"".join(chunks)


def _exchange(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    cap: int,
    deadline: Deadline,
    context: ssl.SSLContext,
) -> Response:
    """
    Send one request over one connection, closed on every path, and read its answer.

    Args:
        method (str): The method.
        url (str): The URL to ask.
        headers (Mapping[str, str]): Every header to send, ``Host`` aside.
        body (bytes | None): The body to send, or ``None``.
        cap (int): The largest body accepted, in bytes.
        deadline (Deadline): The deadline of the request.
        context (ssl.SSLContext): The client context.

    Returns:
        Response: The answer.

    Raises:
        RequestError: For a body over the cap or the deadline passed.
        OSError: From the connection, the handshake or a read; :func:`request` maps it.
        http.client.HTTPException: From the status line or the headers; :func:`request` maps it.
        ValueError: From a URL whose port is not a number in range, or a host or a path that
            :mod:`http.client` cannot encode; :func:`request` maps it.
    """
    parts = urlsplit(url)
    port = _HTTPS_PORT if parts.port is None else parts.port  # not `or`: port 0 is not 443
    connection = http.client.HTTPSConnection(
        parts.hostname or "", port, timeout=deadline.remaining(), context=context
    )
    try:
        connection.request(method, _request_target(url), body=body, headers=dict(headers))
        # Kept, because http.client hands the socket to the answer and forgets it.
        sock = connection.sock
        if sock is None:  # pragma: no cover - request() always leaves a connected socket
            raise http.client.NotConnected("no connection after the request")
        sock.settimeout(deadline.remaining())
        with connection.getresponse() as response:
            # Refused before the body is read (ADR-4).
            if response.length is not None and response.length > cap:
                raise _too_large(cap)
            received: dict[str, str] = {}
            for name, value in response.getheaders():
                received.setdefault(name.lower(), value)
            return Response(response.status, received, _read_body(response, sock, cap, deadline))
    finally:
        connection.close()


def request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    cap: int,
    deadline: Deadline,
    context: ssl.SSLContext,
) -> Response:
    """
    Send one bounded HTTPS request and give its whole answer, whatever the status (ADR-1).

    A redirect is returned like any other status, never followed. ``Accept-Encoding: identity``
    and ``Connection: close`` are always sent; with a body, so is its ``Content-Length``.

    Args:
        method (str): The method, such as ``GET``, ``POST`` or ``PATCH``.
        url (str): An ``https://`` URL with a host; the scheme is not read, the request always
            goes over TLS.
        headers (Mapping[str, str] | None): The caller's headers, such as ``Accept`` and
            ``User-Agent``; two names that differ only in case are two headers.
        body (bytes | None): The body to send, or ``None`` for none.
        cap (int): The largest body accepted, in bytes.
        deadline (Deadline): The moment the whole exchange must have ended by.
        context (ssl.SSLContext): The client context; it verifies the server and sets the
            minimum TLS version.

    Returns:
        Response: The status, the headers and the body.

    Raises:
        RequestError: For every way the request can fail, with its kind and message.
    """
    sent = {**(headers or {}), **_FIXED_HEADERS}
    try:
        return _exchange(method, url, sent, body, cap, deadline, context)
    except RequestError:
        raise
    except socket.gaierror as exc:
        raise RequestError("dns", _message(exc)) from None
    except ConnectionRefusedError as exc:
        raise RequestError("refused", _message(exc)) from None
    except TimeoutError as exc:
        raise RequestError("timeout", _message(exc)) from None
    except ssl.SSLError as exc:  # before OSError, of which it is a subclass
        raise RequestError("tls", _message(exc)) from None
    except (http.client.HTTPException, OSError, ValueError) as exc:
        # ValueError: a port out of range, or a host or a path http.client cannot encode
        # (spec 003, deviation 7).
        raise RequestError("other", _message(exc)) from None
