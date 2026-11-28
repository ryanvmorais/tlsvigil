"""
A small SMTP server on the loopback interface for the e-mail tests (spec 005, RF-14; ADR-11).

:class:`LocalSMTPServer` listens on a free port of ``127.0.0.1`` and speaks enough of SMTP for the
e-mail channel: ``EHLO``, ``STARTTLS``, ``AUTH PLAIN`` and ``LOGIN``, ``MAIL``, ``RCPT``, ``DATA``,
``RSET``, ``NOOP`` and ``QUIT``, with TLS from the first byte (``tls``) or upgraded by ``STARTTLS``
(``starttls``). It records every line the client sends, so a test can tell whether ``AUTH`` came
before or after TLS, and every message it takes. It can also misbehave: leave ``STARTTLS`` out of
its ``EHLO`` while still offering ``AUTH`` (what a server stripped by an attacker looks like),
stay silent, stream continuation lines without end, turn the client away, hang up in the middle
of the session, or refuse the sender or some recipients.

The standard library lost ``smtpd`` in Python 3.12, and a dependency would not misbehave on
purpose. Each connection is served in its own thread, the threads are joined on exit, and their
exceptions are recorded instead of raised, as :class:`~http_server.LocalHTTPServer` does.
"""

from __future__ import annotations

import base64
import binascii
import socket
import socketserver
import ssl
import sys
import threading
from types import TracebackType
from typing import Any, BinaryIO, Final, Literal, Self, cast

__all__ = ["PASSWORD", "USERNAME", "LocalSMTPServer"]

# The user name and the password the server accepts; made-up values.
USERNAME: Final = "watcher@example.com"
PASSWORD: Final = "fake-password-of-the-tests"

# How often the serving loop wakes up to see whether the server was stopped.
_POLL_SECONDS: Final = 0.05

# A server-side cap on every blocking socket call, so a client that stops answering cannot hold
# a session thread, or the join on exit, for long.
_SOCKET_SECONDS: Final = 5.0

# The longest line read; SMTP's own limit is 1000 octets with the line break (RFC 5321).
_MAX_LINE: Final = 4096

# How the connection becomes TLS, as `SmtpSecurity` names it.
type Security = Literal["tls", "starttls"]
# How the server answers a connection: properly, or one way of misbehaving.
type Behaviour = Literal["normal", "silent", "endless", "busy", "hangup"]


class _Ended(Exception):
    """The client closed the connection in the middle of an exchange."""


class LocalSMTPServer:
    """
    A loopback SMTP server that serves each connection in its own thread.

    Use it as a context manager: the port is open inside the ``with`` block only.

    Attributes:
        host (str): Always ``127.0.0.1``.
        port (int): The free port the server got; ``0`` until it starts.
        commands (list[str]): Every line the client sent outside ``DATA``, in order, the
            answers to an ``AUTH`` challenge included.
        verbs (list[str]): The command of each of those lines that is a command, upper case.
        messages (list[bytes]): Each message taken, as received, its dots unstuffed.
        envelopes (list[tuple[str, tuple[str, ...]]]): The sender and the accepted recipients
            of each message taken.
        errors (list[BaseException]): The exceptions the session threads caught, in order.
    """

    host = "127.0.0.1"

    def __init__(
        self,
        context: ssl.SSLContext,
        *,
        security: Security = "starttls",
        offer_starttls: bool = True,
        mechanisms: tuple[str, ...] = ("PLAIN", "LOGIN"),
        behaviour: Behaviour = "normal",
        interval: float = 0.0,
        refuse: frozenset[str] = frozenset(),
    ) -> None:
        """
        Prepare a server; it starts listening on ``__enter__``.

        Args:
            context (ssl.SSLContext): The server context, with the leaf for ``127.0.0.1``.
            security (Security): ``tls`` from the first byte, or ``starttls``.
            offer_starttls (bool): ``False`` to leave ``STARTTLS`` out of ``EHLO`` and refuse
                it, while ``AUTH`` is still offered.
            mechanisms (tuple[str, ...]): The ``AUTH`` mechanisms offered, among ``PLAIN`` and
                ``LOGIN``.
            behaviour (Behaviour): ``normal``; ``silent`` accepts and never sends a byte;
                ``endless`` greets with continuation lines that never end; ``busy`` greets
                with ``554`` and closes; ``hangup`` closes without a word after the first
                command.
            interval (float): The seconds between two lines of ``endless``.
            refuse (frozenset[str]): The sender ``MAIL`` refuses with ``553``, and the
                recipients ``RCPT`` refuses with ``550``.
        """
        self.context = context
        self.security = security
        self.offer_starttls = offer_starttls
        self.mechanisms = mechanisms
        self.behaviour = behaviour
        self.interval = interval
        self.refuse = refuse
        self.port = 0
        self.commands: list[str] = []
        self.verbs: list[str] = []
        self.messages: list[bytes] = []
        self.envelopes: list[tuple[str, tuple[str, ...]]] = []
        self.errors: list[BaseException] = []
        self.stopped = threading.Event()
        self._server: _Server | None = None
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
        self._server = _Server(self)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": _POLL_SECONDS},
            name="LocalSMTPServer",
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
        Stop the serving loop, end the sessions and close the listening socket.

        Args:
            exc_type (type[BaseException] | None): Unused.
            exc (BaseException | None): Unused.
            traceback (TracebackType | None): Unused.
        """
        self.stopped.set()  # ends a silent or endless session
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()  # joins the session threads (block_on_close)
        if self._thread is not None:
            self._thread.join(timeout=_SOCKET_SECONDS * 2)


class _Server(socketserver.ThreadingTCPServer):
    """The standard threaded server, with the errors recorded."""

    # Session threads are joined on close, so no thread outlives the test that started it.
    daemon_threads = False
    block_on_close = True

    def __init__(self, harness: LocalSMTPServer) -> None:
        """
        Bind ``127.0.0.1:0`` and listen.

        Args:
            harness (LocalSMTPServer): The owner, which holds the settings and the records.
        """
        self.harness = harness
        super().__init__((harness.host, 0), _Handler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """
        Record the exception of a session thread instead of printing it.

        Args:
            request (Any): The accepted socket.
            client_address (Any): The client's address.
        """
        error = sys.exc_info()[1]
        if error is not None:
            self.harness.errors.append(error)


class _Handler(socketserver.BaseRequestHandler):
    """Serve one connection as one SMTP session."""

    def handle(self) -> None:
        """Run the session of the accepted connection."""
        harness = cast(_Server, self.server).harness
        _Session(harness, cast(socket.socket, self.request)).run()


class _Session:
    """
    One SMTP session: the state of the conversation with one client.

    Attributes:
        harness (LocalSMTPServer): The server, with its settings and records.
        sock (socket.socket): The connection, a TLS one once secured.
        file (BinaryIO): The lines read from ``sock``.
        secure (bool): Whether the connection is TLS.
        signed_in (bool): Whether ``AUTH`` succeeded.
        sender (str | None): The sender of ``MAIL``.
        recipients (list[str]): The recipients ``RCPT`` accepted.
    """

    def __init__(self, harness: LocalSMTPServer, sock: socket.socket) -> None:
        """
        Start a session on an accepted connection.

        Args:
            harness (LocalSMTPServer): The server.
            sock (socket.socket): The accepted connection.
        """
        self.harness = harness
        sock.settimeout(_SOCKET_SECONDS)
        self.sock = sock
        self.file: BinaryIO = sock.makefile("rb")
        self.secure = False
        self.signed_in = False
        self.sender: str | None = None
        self.recipients: list[str] = []

    # -----------------------------------------------------------------------
    # Lines
    # -----------------------------------------------------------------------

    def _secure(self) -> None:
        """Run the TLS handshake as the server, and read lines over TLS from now on."""
        # The client waits for the reply before its handshake, so nothing is left buffered.
        self.file.close()
        self.sock = self.harness.context.wrap_socket(self.sock, server_side=True)
        self.file = self.sock.makefile("rb")
        self.secure = True

    def _reply(self, code: int, *texts: str) -> None:
        """
        Send one reply, its lines but the last marked as continued.

        Args:
            code (int): The reply code.
            *texts (str): The text of each line.
        """
        lines = [
            f"{code}{'-' if index < len(texts) - 1 else ' '}{text}\r\n"
            for index, text in enumerate(texts)
        ]
        self.sock.sendall("".join(lines).encode("utf-8"))

    def _line(self) -> bytes:
        """
        Read one line, its line break included.

        Returns:
            bytes: The line.

        Raises:
            _Ended: When the client closed the connection.
        """
        raw = self.file.readline(_MAX_LINE)
        if not raw:
            raise _Ended
        return raw

    def _text(self) -> str:
        """
        Read one line outside ``DATA``, and record it.

        Returns:
            str: The line without its line break.
        """
        line = self._line().decode("utf-8", "replace").rstrip("\r\n")
        self.harness.commands.append(line)
        return line

    # -----------------------------------------------------------------------
    # The session
    # -----------------------------------------------------------------------

    def run(self) -> None:
        """Serve the session until ``QUIT``, the client's close or the server's stop."""
        harness = self.harness
        try:
            if harness.behaviour == "silent":
                harness.stopped.wait()  # never read, never written: the client times out
                return
            if harness.security == "tls":
                self._secure()
            if harness.behaviour == "endless":
                self._endless()
                return
            if harness.behaviour == "busy":
                self._reply(554, "5.3.2 localhost Service not available")
                return
            self._reply(220, "localhost ESMTP TLSVigil test server")
            if harness.behaviour == "hangup":
                self._text()
                return  # the connection closes with no reply to the command
            while not harness.stopped.is_set() and self._command(self._text()):
                pass
        except _Ended:
            pass  # the client left; nothing to answer
        finally:
            self.sock.close()

    def _endless(self) -> None:
        """Greet with continuation lines until the client leaves or the server stops."""
        try:
            while not self.harness.stopped.is_set():
                self.sock.sendall(b"220-still greeting\r\n")
                if self.harness.interval:
                    self.harness.stopped.wait(self.harness.interval)
        except OSError:
            pass  # the client closed the connection, as it should

    def _command(self, line: str) -> bool:
        """
        Answer one command.

        Args:
            line (str): The command line.

        Returns:
            bool: ``False`` once the session is over.
        """
        word, _, argument = line.partition(" ")
        verb = word.upper()
        self.harness.verbs.append(verb)
        match verb:
            case "EHLO" | "HELO":
                self._reply(250, *self._extensions())
            case "STARTTLS":
                return self._starttls()
            case "AUTH":
                self._auth(argument)
            case "MAIL":
                self._mail(argument)
            case "RCPT":
                self._rcpt(argument)
            case "DATA":
                self._data()
            case "RSET":
                self.sender, self.recipients = None, []
                self._reply(250, "2.0.0 OK")
            case "NOOP":
                self._reply(250, "2.0.0 OK")
            case "QUIT":
                self._reply(221, "2.0.0 Bye")
                return False
            case _:
                self._reply(502, "5.5.2 Command not recognised")
        return True

    def _extensions(self) -> list[str]:
        """
        Give the lines of the ``EHLO`` reply.

        Returns:
            list[str]: The name, then the extensions offered.
        """
        harness = self.harness
        lines = ["localhost greets you"]
        if harness.security == "starttls" and harness.offer_starttls and not self.secure:
            lines.append("STARTTLS")
        # Offered in clear as well: what a server whose STARTTLS was stripped looks like.
        lines.append("AUTH " + " ".join(harness.mechanisms))
        return lines

    def _starttls(self) -> bool:
        """
        Answer ``STARTTLS`` and secure the connection, when it is offered.

        Returns:
            bool: ``True``; a failed handshake raises, and the server records it.
        """
        harness = self.harness
        if harness.security != "starttls" or not harness.offer_starttls or self.secure:
            self._reply(502, "5.5.1 STARTTLS not available")
            return True
        self._reply(220, "2.0.0 Ready to start TLS")
        self._secure()
        self.signed_in = False
        return True

    def _challenge(self, prompt: str) -> str:
        """
        Ask for the next part of an ``AUTH`` exchange.

        Args:
            prompt (str): The prompt, as plain text; it is sent in base64.

        Returns:
            str: The client's answer, still in base64.
        """
        self._reply(334, base64.b64encode(prompt.encode()).decode("ascii"))
        return self._text()

    def _auth(self, argument: str) -> None:
        """
        Answer ``AUTH PLAIN`` or ``AUTH LOGIN``, with or without an initial response.

        Args:
            argument (str): The mechanism and the initial response, if any.
        """
        mechanism, _, initial = argument.partition(" ")
        mechanism = mechanism.upper()
        if mechanism not in self.harness.mechanisms:
            self._reply(504, "5.5.4 Unrecognized authentication type")
            return
        try:
            if mechanism == "PLAIN":
                payload = base64.b64decode(initial or self._challenge(""), validate=True)
                _, user, password = payload.decode().split("\0")
            else:
                user = base64.b64decode(initial or self._challenge("Username:")).decode()
                password = base64.b64decode(self._challenge("Password:")).decode()
        except (binascii.Error, UnicodeDecodeError, ValueError):
            self._reply(501, "5.5.2 Cannot decode the response")
            return
        if (user, password) != (USERNAME, PASSWORD):
            self._reply(535, "5.7.8 Authentication credentials invalid")
            return
        self.signed_in = True
        self._reply(235, "2.7.0 Authentication successful")

    def _mail(self, argument: str) -> None:
        """
        Answer ``MAIL FROM``, once signed in, refusing the sender the server was told to.

        Args:
            argument (str): ``FROM:<address>`` and its parameters.
        """
        if not self.signed_in:
            self._reply(530, "5.7.0 Authentication required")
            return
        address = _address(argument)
        if address in self.harness.refuse:
            self._reply(553, f"5.7.1 <{address}>: Sender address rejected")
            return
        self.sender, self.recipients = address, []
        self._reply(250, "2.1.0 OK")

    def _rcpt(self, argument: str) -> None:
        """
        Answer ``RCPT TO``, refusing the recipients the server was told to.

        Args:
            argument (str): ``TO:<address>`` and its parameters.
        """
        address = _address(argument)
        if self.sender is None:
            self._reply(503, "5.5.1 MAIL first")
        elif address in self.harness.refuse:
            # The address in the reply, as real servers write it: the channel must not repeat it.
            self._reply(550, f"5.1.1 <{address}>: Recipient address rejected")
        else:
            self.recipients.append(address)
            self._reply(250, "2.1.5 OK")

    def _data(self) -> None:
        """Answer ``DATA``: read the message up to the lone dot and keep it."""
        if self.sender is None or not self.recipients:
            self._reply(554, "5.5.1 No valid recipients")
            return
        self._reply(354, "End data with <CR><LF>.<CR><LF>")
        lines: list[bytes] = []
        while (line := self._line()) != b".\r\n":
            lines.append(line[1:] if line.startswith(b".") else line)
        self.harness.messages.append(b"".join(lines))
        self.harness.envelopes.append((self.sender, tuple(self.recipients)))
        self.sender, self.recipients = None, []
        self._reply(250, "2.0.0 Queued")


def _address(argument: str) -> str:
    """
    Read the address of ``MAIL FROM`` or ``RCPT TO``.

    Args:
        argument (str): Such as ``FROM:<a@example.com> SIZE=100``.

    Returns:
        str: The address between the angle brackets, or the whole argument without them.
    """
    start, end = argument.find("<"), argument.find(">")
    return argument[start + 1 : end] if 0 <= start < end else argument
