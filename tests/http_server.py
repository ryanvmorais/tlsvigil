"""
A small HTTPS server on the loopback interface for the RDAP, exchange and channel tests.

:class:`LocalHTTPServer` listens on a free port of ``127.0.0.1`` and answers each path with the
:class:`Route` a test gave it: a fixed status, headers and body (a redirect is one of these), one
of three misbehaving answers (``silent``, ``drip`` and ``endless``) that a client must bound with
its timeout and its size cap, or a ``handler`` that computes the answer from the request, as the
fake APIs of spec 005 do. A path without a route answers ``404``. ``GET``, ``POST`` and ``PATCH``
are answered alike, and the body of a request is recorded with it. Each connection is
wrapped in TLS in its own request thread, so a client that rejects the certificate never blocks
the server, and every thread records its exceptions instead of raising them, as
:class:`~tls_server.LocalServer` does (spec 003, RF-08 and ADR-7). One request is answered per
connection (``Connection: close``), as the RDAP client asks.
"""

from __future__ import annotations

import http.server
import socket
import socketserver
import ssl
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Literal, Self, cast

# How often the serving loop wakes up to see whether the server was stopped.
_POLL_SECONDS = 0.05

# A server-side cap on every blocking socket call (the handshake, reading the request, a write),
# so a client that stops answering cannot hold a request thread, or the join on exit, for long.
_SOCKET_SECONDS = 5.0

# The size of each write of the `endless` route: the client's read size (spec 003, ADR-4).
_ENDLESS_CHUNK = b" " * 65_536

# The media type of an RDAP answer (RFC 9083), sent by the routes that make up their own headers.
_RDAP_JSON = "application/rdap+json"

RouteKind = Literal["fixed", "silent", "drip", "endless", "handler"]

# ---------------------------------------------------------------------------
# Routes and recorded requests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    """
    How the server answers one path.

    ``Route(status, body, headers)`` is a fixed answer; the class methods build the others.

    Attributes:
        status (int): The status of a fixed answer; ``200`` for the misbehaving kinds.
        body (bytes): The body of a fixed answer, sent whole.
        headers (Mapping[str, str]): Extra headers of a fixed answer. A ``Content-Length`` given
            here replaces the real one, so a test can announce a body larger than it sends.
        kind (RouteKind): ``fixed``, one of the misbehaving answers, or ``handler``.
        interval (float): The seconds between two bytes of the ``drip`` kind.
        handler (Callable[[RecordedRequest], Route] | None): The function of the ``handler``
            kind, which gives the route to answer a request with; ``None`` for the other kinds.
    """

    status: int = 200
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)
    kind: RouteKind = "fixed"
    interval: float = 0.0
    handler: Callable[[RecordedRequest], Route] | None = None

    @classmethod
    def redirect(cls, status: int, location: str) -> Route:
        """
        Build a redirect, a fixed answer with a ``Location`` and no body.

        Args:
            status (int): The redirect status, such as ``301`` or ``307``.
            location (str): The ``Location`` header, sent as given (absolute or relative).

        Returns:
            Route: The redirect.
        """
        return cls(status, b"", {"Location": location})

    @classmethod
    def silent(cls) -> Route:
        """
        Build an answer that reads the request and never sends a byte, until the server stops.

        Returns:
            Route: The silent answer; the client waits for its timeout.
        """
        return cls(kind="silent")

    @classmethod
    def drip(cls, byte_interval: float) -> Route:
        """
        Build an answer that sends its headers, then one byte of body at a time, forever.

        Args:
            byte_interval (float): The seconds between two bytes; each byte alone resets a
                per-read socket timeout, so only a deadline over the whole request ends it.

        Returns:
            Route: The dripping answer.
        """
        return cls(kind="drip", interval=byte_interval)

    @classmethod
    def endless(cls) -> Route:
        """
        Build an answer with no ``Content-Length`` that sends 64 KiB chunks, forever.

        Returns:
            Route: The endless answer; only a cap on the bytes read ends it.
        """
        return cls(kind="endless")

    @classmethod
    def handled(cls, handler: Callable[[RecordedRequest], Route]) -> Route:
        """
        Build an answer that a function computes from each request, after it is recorded.

        Args:
            handler (Callable[[RecordedRequest], Route]): Gives the route to answer with, of any
                kind but ``handler``; it runs in the request thread, and what it raises is
                recorded in ``errors``.

        Returns:
            Route: The computed answer.
        """
        return cls(kind="handler", handler=handler)


@dataclass(frozen=True)
class RecordedRequest:
    """
    One request the server read, kept so a test can count requests and read them.

    Attributes:
        method (str): The method, such as ``GET``, ``POST`` or ``PATCH``.
        path (str): The request target as sent, with its query string.
        headers (tuple[tuple[str, str], ...]): Every header line, in order, repeats included.
        body (bytes): The body, as many bytes as its ``Content-Length`` announced; empty
            without one.
    """

    method: str
    path: str
    headers: tuple[tuple[str, str], ...]
    body: bytes = b""

    def header(self, name: str) -> str | None:
        """
        Read the first value of a header.

        Args:
            name (str): The header name, in any case.

        Returns:
            str | None: The value, or ``None`` when the request did not send that header.
        """
        wanted = name.lower()
        return next((value for key, value in self.headers if key.lower() == wanted), None)


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------


class LocalHTTPServer:
    """
    A loopback HTTPS server that answers each path by its route, in background threads.

    Use it as a context manager: the port is open inside the ``with`` block only. The routes can
    change while it runs, so a test can build a bootstrap document that names the server's own
    URL after the port is known.

    Attributes:
        host (str): Always ``127.0.0.1``.
        port (int): The free port the server got; ``0`` until it starts.
        routes (dict[str, Route]): The answer of each request path (with its query string).
        requests (list[RecordedRequest]): The requests read so far, in order.
        errors (list[BaseException]): The exceptions the request threads caught, in order.
    """

    host = "127.0.0.1"

    def __init__(self, context: ssl.SSLContext, routes: Mapping[str, Route] | None = None) -> None:
        """
        Prepare a server; it starts listening on ``__enter__``.

        Args:
            context (ssl.SSLContext): The server context, with the certificate loaded.
            routes (Mapping[str, Route] | None): The first routes; more can be added later.
        """
        self.context = context
        self.port = 0
        self.routes: dict[str, Route] = dict(routes or {})
        self.requests: list[RecordedRequest] = []
        self.errors: list[BaseException] = []
        self.stopped = threading.Event()
        self._lock = threading.Lock()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    def url(self, path: str) -> str:
        """
        Build the URL of a path on this server.

        Args:
            path (str): The path, starting with ``/``.

        Returns:
            str: ``https://127.0.0.1:<port><path>``.
        """
        return f"https://{self.host}:{self.port}{path}"

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def __enter__(self) -> Self:
        """
        Bind a free loopback port and start the serving thread.

        Returns:
            Self: The running server.
        """
        self._server = _Server(self)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": _POLL_SECONDS},
            name="LocalHTTPServer",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """
        Release the waiting routes, stop the serving loop and join every request thread.

        Args:
            exc_type (type[BaseException] | None): Unused.
            exc (BaseException | None): Unused.
            traceback (TracebackType | None): Unused.
        """
        self.stopped.set()  # first, so `silent` and `drip` threads return
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()  # closes the listener and joins the request threads
        if self._thread is not None:
            self._thread.join(timeout=_SOCKET_SECONDS * 2)

    # -----------------------------------------------------------------------
    # Records, called from the request threads
    # -----------------------------------------------------------------------

    def record_request(self, request: RecordedRequest) -> None:
        """
        Keep one request that was read.

        Args:
            request (RecordedRequest): The request.
        """
        with self._lock:
            self.requests.append(request)

    def record_error(self, error: BaseException) -> None:
        """
        Keep one exception a request thread caught.

        Args:
            error (BaseException): The exception.
        """
        with self._lock:
            self.errors.append(error)


class _Server(http.server.ThreadingHTTPServer):
    """The standard threaded server, with TLS per connection and the errors recorded."""

    # Request threads are joined on close, so no thread outlives the test that started it.
    daemon_threads = False
    block_on_close = True

    def __init__(self, harness: LocalHTTPServer) -> None:
        """
        Bind ``127.0.0.1:0`` and listen.

        Args:
            harness (LocalHTTPServer): The owner, which holds the context, routes and records.
        """
        self.harness = harness
        super().__init__((harness.host, 0), _Handler)

    def server_bind(self) -> None:
        """Bind without ``HTTPServer``'s reverse lookup of the host, which no test needs."""
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)

    def finish_request(self, request: Any, client_address: Any) -> None:
        """
        Run the TLS handshake in the request thread, then answer the request.

        Args:
            request (Any): The accepted socket.
            client_address (Any): The client's address.
        """
        sock = cast(socket.socket, request)
        sock.settimeout(_SOCKET_SECONDS)
        with self.harness.context.wrap_socket(sock, server_side=True) as tls:
            _Handler(tls, client_address, self)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """
        Record the exception of a request thread instead of printing it.

        Args:
            request (Any): Unused.
            client_address (Any): Unused.
        """
        error = sys.exception()
        if error is not None:
            self.harness.record_error(error)


class _Handler(http.server.BaseHTTPRequestHandler):
    """Answer one request by the route of its path; one request per connection."""

    protocol_version = "HTTP/1.1"
    timeout = _SOCKET_SECONDS

    def do_GET(self) -> None:
        """Answer a ``GET``."""
        self._answer()

    def do_POST(self) -> None:
        """Answer a ``POST``."""
        self._answer()

    def do_PATCH(self) -> None:
        """Answer a ``PATCH``."""
        self._answer()

    def _answer(self) -> None:
        """Read the body, record the request and answer it by its route, or with ``404``."""
        harness = cast(_Server, self.server).harness
        self.close_connection = True
        length = int(self.headers.get("Content-Length") or 0)
        request = RecordedRequest(
            self.command, self.path, tuple(self.headers.items()), self.rfile.read(length)
        )
        harness.record_request(request)
        route = harness.routes.get(self.path, Route(404))
        if route.handler is not None:
            route = route.handler(request)
        match route.kind:
            case "fixed":
                self._send_head(route.status, route.headers, len(route.body))
                self.wfile.write(route.body)
            case "silent":
                harness.stopped.wait()
            case "drip":
                self._send_head(200, {"Content-Type": _RDAP_JSON}, None)
                while not harness.stopped.wait(route.interval):
                    self.wfile.write(b" ")
            case "endless":
                self._send_head(200, {"Content-Type": _RDAP_JSON}, None)
                while not harness.stopped.is_set():
                    self.wfile.write(_ENDLESS_CHUNK)
            case "handler":
                raise ValueError("a handler gave another handler route")

    def _send_head(self, status: int, headers: Mapping[str, str], length: int | None) -> None:
        """
        Send the status line and the headers.

        Args:
            status (int): The status code.
            headers (Mapping[str, str]): The headers of the route.
            length (int | None): The body length, sent as ``Content-Length`` unless the route
                gives its own; ``None`` sends none, so the body ends when the connection closes.
        """
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        if length is not None and not any(name.lower() == "content-length" for name in headers):
            self.send_header("Content-Length", str(length))
        self.send_header("Connection", "close")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """
        Keep the access log out of the test output.

        Args:
            format (str): Unused.
            *args (Any): Unused.
        """
