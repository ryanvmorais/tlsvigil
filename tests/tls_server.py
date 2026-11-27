"""
A small threaded server on ``127.0.0.1`` for the certificate tests.

:class:`LocalServer` listens on a free loopback port and answers each connection in one of
three ways: ``tls`` completes a TLS handshake with the server context it was given and closes;
``silent`` accepts and never sends a byte, so a client waits for its timeout; ``plain`` answers
with a line of plain-text HTTP, which is not TLS. The serving thread records its own exceptions
instead of raising them (a client that rejects the certificate is the normal case), so pytest's
``filterwarnings = ["error"]`` sees no unhandled thread exception. Every socket it opens is
closed on exit (spec 002, RF-07).
"""

from __future__ import annotations

import socket
import ssl
import threading
from types import TracebackType
from typing import Literal, Self

# How often the accept loop wakes up to see whether the server was stopped.
_POLL_SECONDS = 0.05

# A server-side cap on one handshake, so a client that never answers cannot hold the thread.
_HANDSHAKE_SECONDS = 5.0

# What the `plain` mode answers: an HTTP error line, the classic reply of a port without TLS.
_PLAIN_REPLY = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n"

Mode = Literal["tls", "silent", "plain"]


class LocalServer:
    """
    A loopback server that answers every connection the same way, in a background thread.

    Use it as a context manager: the port is open inside the ``with`` block only.

    Attributes:
        host (str): Always ``127.0.0.1``.
        port (int): The free port the server got; ``0`` until it starts.
        accepted (int): How many connections were accepted so far.
        errors (list[BaseException]): The exceptions the serving thread caught, in order.
    """

    host = "127.0.0.1"

    def __init__(self, mode: Mode, context: ssl.SSLContext | None = None) -> None:
        """
        Prepare a server; it starts listening on ``__enter__``.

        Args:
            mode (Mode): How to answer each connection.
            context (ssl.SSLContext | None): The server context, required for ``tls``.

        Raises:
            ValueError: When ``tls`` has no context.
        """
        if mode == "tls" and context is None:
            raise ValueError("the tls mode needs a server context")
        self.mode = mode
        self.context = context
        self.port = 0
        self.accepted = 0
        self.errors: list[BaseException] = []
        self._held: list[socket.socket] = []
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def __enter__(self) -> Self:
        """
        Bind a free loopback port and start the serving thread.

        Returns:
            Self: The running server.
        """
        listener = socket.create_server((self.host, 0))
        listener.settimeout(_POLL_SECONDS)
        self._listener = listener
        self.port = listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, name="LocalServer", daemon=True)
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """
        Stop the thread and close every socket the server opened.

        Args:
            exc_type (type[BaseException] | None): Unused.
            exc (BaseException | None): Unused.
            traceback (TracebackType | None): Unused.
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=_HANDSHAKE_SECONDS * 2)
        if self._listener is not None:
            self._listener.close()
        for held in self._held:
            held.close()

    # -----------------------------------------------------------------------
    # Serving thread
    # -----------------------------------------------------------------------

    def _serve(self) -> None:
        """Accept connections until stopped, answering each one by the mode."""
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except TimeoutError:
                continue
            self.accepted += 1
            try:
                self._answer(conn)
            except Exception as exc:  # the client side decides what the test expects
                self.errors.append(exc)

    def _answer(self, conn: socket.socket) -> None:
        """
        Answer one accepted connection.

        Args:
            conn (socket.socket): The accepted connection; it is closed here, or held open
                until exit in the ``silent`` mode.
        """
        if self.mode == "silent":
            self._held.append(conn)  # never read, never written: the client times out
            return
        with conn:
            conn.settimeout(_HANDSHAKE_SECONDS)
            if self.mode == "plain":
                conn.recv(4096)  # the ClientHello, read so the close is not a reset
                conn.sendall(_PLAIN_REPLY)
                return
            assert self.context is not None
            with self.context.wrap_socket(conn, server_side=True):
                pass  # the handshake ran in wrap_socket; nothing is sent after it
