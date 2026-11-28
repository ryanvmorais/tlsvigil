"""
The e-mail channel: one plain-text message through an SMTP server over TLS (spec 005, RF-10).

:meth:`MailChannel.deliver` sends one e-mail when the plan has events, and nothing otherwise. The
session is TLS from the first byte (``"tls"``) or upgraded by ``STARTTLS`` (``"starttls"``), and a
server that does not offer ``STARTTLS`` ends it before the user name or the password is sent. The
certificate is verified with the context of every check (RF-05 of spec 002).

:mod:`smtplib` bounds each socket operation, not the session, and reads a reply of any number of
lines; it also looks up the machine's name for ``EHLO``. :class:`_BoundedSMTP` and
:class:`_BoundedSMTPSSL` close those gaps (ADR-6): one deadline for the whole session, the time
left set before each line read and each write, a reply refused past :data:`MAX_REPLY_LINES`, and
``localhost`` as the name given in ``EHLO``. Every failure becomes the delivery's message, with
the server's reply code and text escaped; a refused recipient is counted, never named.
"""

from __future__ import annotations

import contextlib
import email.policy
import smtplib
import socket
import ssl
from collections.abc import Buffer
from dataclasses import replace
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from typing import Final

from tlsvigil import https
from tlsvigil.alerts import Plan
from tlsvigil.certificate import create_context
from tlsvigil.config import Channel, SmtpSecurity
from tlsvigil.delivery import Delivery, DeliveryOutcome
from tlsvigil.environment import SmtpSettings
from tlsvigil.i18n import Lang, plural, translate
from tlsvigil.messages import mail_body, mail_subject
from tlsvigil.output import printable
from tlsvigil.state import ChannelState

__all__ = ["LOCAL_HOSTNAME", "MAX_MESSAGE_CHARS", "MAX_REPLY_LINES", "MailChannel", "ReplyTooLong"]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The name given in EHLO, so smtplib never looks up the machine's own (ADR-6).
LOCAL_HOSTNAME: Final = "localhost"

# The most lines one reply may have; a server's EHLO lists a dozen extensions at most.
MAX_REPLY_LINES: Final = 100

# The server's reply text, or an exception's, is cut to this many characters in a message.
MAX_MESSAGE_CHARS: Final = 200

# The longest reply line read, as smtplib's own `_MAXLINE`.
_MAX_LINE_BYTES: Final = 8192


# ---------------------------------------------------------------------------
# The bounded session (ADR-6)
# ---------------------------------------------------------------------------


class ReplyTooLong(smtplib.SMTPException):
    """A reply of the server ran past :data:`MAX_REPLY_LINES` lines."""


class _NoStartTLS(Exception):
    """The server's ``EHLO`` does not offer ``STARTTLS``; nothing secret was sent."""


class _Bounded(smtplib.SMTP):
    """
    The part of the bounded sessions they share: the deadline and the capped reply.

    Attributes:
        deadline (https.Deadline): The moment the whole session must have ended by.
    """

    deadline: https.Deadline

    def _bound(self) -> None:
        """
        Give the socket the time left before the deadline, as its timeout.

        Raises:
            TimeoutError: Once the deadline has passed; the session is closed.
        """
        try:
            left = self.deadline.remaining()
        except https.RequestError as error:
            self.close()
            raise TimeoutError(error.message) from None
        if self.sock is not None:
            self.sock.settimeout(left)

    def send(self, s: Buffer | str) -> None:
        """
        Send ``s`` within the time left.

        Args:
            s (Buffer | str): A command or the message, as :mod:`smtplib` gives it.
        """
        self._bound()
        super().send(s)

    def getreply(self) -> tuple[int, bytes]:
        r"""
        Read one reply of the server, within the time left and :data:`MAX_REPLY_LINES` lines.

        A copy of :meth:`smtplib.SMTP.getreply` (Python 3.12 to 3.15) with two changes: the
        socket timeout is set to the time left before each line, and a reply that is still going
        at the last line allowed is refused (ADR-6). A timeout is raised as it is, not turned
        into a disconnection, so the message can say what happened.

        Returns:
            tuple[int, bytes]: The reply code, ``-1`` when it cannot be read, and the text, its
                lines joined by ``\n``.

        Raises:
            TimeoutError: When the deadline passes before the reply ends.
            ReplyTooLong: When the reply runs past :data:`MAX_REPLY_LINES` lines.
            smtplib.SMTPServerDisconnected: When the connection ends or fails.
            smtplib.SMTPResponseException: When a line is longer than smtplib accepts.
        """
        lines: list[bytes] = []
        if self.sock is None:
            raise smtplib.SMTPServerDisconnected("please run connect() first")
        if self.file is None:
            self.file = self.sock.makefile("rb")
        while True:
            self._bound()
            try:
                line = self.file.readline(_MAX_LINE_BYTES + 1)
            except TimeoutError:
                self.close()
                raise
            except OSError as error:
                self.close()
                raise smtplib.SMTPServerDisconnected(
                    "Connection unexpectedly closed: " + str(error)
                ) from None
            if not line:
                self.close()
                raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
            if len(line) > _MAX_LINE_BYTES:
                self.close()
                raise smtplib.SMTPResponseException(500, b"Line too long.")
            lines.append(line[4:].strip(b" \t\r\n"))
            # A broken code ends the reply: no continuation line is read after it.
            try:
                code = int(line[:3])
            except ValueError:
                code = -1
                break
            if line[3:4] != b"-":
                break
            if len(lines) >= MAX_REPLY_LINES:
                self.close()
                raise ReplyTooLong(f"a reply longer than {MAX_REPLY_LINES} lines")
        return code, b"\n".join(lines)


class _BoundedSMTP(_Bounded):
    """A plain session, upgraded by ``STARTTLS`` before anything secret is sent."""

    def __init__(self, host: str, port: int, *, deadline: https.Deadline) -> None:
        """
        Connect and read the server's greeting.

        Args:
            host (str): The server.
            port (int): Its port.
            deadline (https.Deadline): The deadline of the whole session.
        """
        self.deadline = deadline
        super().__init__(host, port, LOCAL_HOSTNAME, deadline.remaining())


class _BoundedSMTPSSL(_Bounded, smtplib.SMTP_SSL):
    """A session that is TLS from the first byte."""

    def __init__(
        self, host: str, port: int, *, deadline: https.Deadline, context: ssl.SSLContext
    ) -> None:
        """
        Connect, verify the server's certificate and read its greeting.

        Args:
            host (str): The server.
            port (int): Its port.
            deadline (https.Deadline): The deadline of the whole session.
            context (ssl.SSLContext): The client context.
        """
        self.deadline = deadline
        smtplib.SMTP_SSL.__init__(
            self, host, port, LOCAL_HOSTNAME, timeout=deadline.remaining(), context=context
        )


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def _text(value: bytes | str | object) -> str:
    """
    Give a reply's text, or an exception's, escaped and cut, for a message.

    Args:
        value (bytes | str | object): The text, as :mod:`smtplib` keeps it.

    Returns:
        str: The text on one line, at most :data:`MAX_MESSAGE_CHARS` characters.
    """
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    return printable(text)[:MAX_MESSAGE_CHARS]


def _with(sentence: str, detail: str) -> str:
    """
    Add a detail in parentheses to a sentence, when there is one.

    Args:
        sentence (str): The sentence of the catalogue.
        detail (str): The detail, possibly empty.

    Returns:
        str: ``sentence (detail)``, or the sentence alone.
    """
    return f"{sentence} ({detail})" if detail else sentence


def _reply(error: smtplib.SMTPResponseException) -> str:
    """
    Give the code and the text of a reply the session could not use.

    Args:
        error (smtplib.SMTPResponseException): The exception that carries them.

    Returns:
        str: Such as ``535 5.7.8 Authentication failed``, escaped and cut.
    """
    return _text(f"{error.smtp_code} {_text(error.smtp_error)}".strip())


def _failure(error: BaseException, lang: Lang) -> str:
    """
    Write why a session failed (RF-10).

    Args:
        error (BaseException): What the session raised.
        lang (Lang): The language of the run.

    Returns:
        str: The message, as plain text; a recipient is counted, never named.
    """
    # smtplib's exceptions are OSErrors, so they come before the connection failures.
    if isinstance(error, _NoStartTLS):
        return translate(lang, "mail.no_starttls")
    if isinstance(error, ReplyTooLong):
        return translate(lang, "mail.too_long", limit=MAX_REPLY_LINES)
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return plural(lang, "mail.recipients", len(error.recipients))
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return _with(translate(lang, "mail.auth"), _reply(error))
    if isinstance(error, smtplib.SMTPSenderRefused):
        return _with(translate(lang, "mail.sender"), _reply(error))
    if isinstance(error, smtplib.SMTPServerDisconnected):
        return translate(lang, "mail.closed")
    if isinstance(error, smtplib.SMTPResponseException):
        code = translate(lang, "mail.reply", code=error.smtp_code)
        return _with(code, _text(error.smtp_error))
    if isinstance(error, smtplib.SMTPException):
        return _with(translate(lang, "mail.other"), _text(error))
    if isinstance(error, socket.gaierror):
        kind = "dns"
    elif isinstance(error, ConnectionRefusedError):
        kind = "refused"
    elif isinstance(error, TimeoutError):
        kind = "timeout"
    elif isinstance(error, ssl.SSLError):  # before OSError, of which it is a subclass
        kind = "tls"
    else:
        kind = "other"
    unreachable = translate(lang, "mail.unreachable", kind=translate(lang, f"request.{kind}"))
    return _with(unreachable, _text(error))


# ---------------------------------------------------------------------------
# The channel
# ---------------------------------------------------------------------------


class MailChannel:
    """
    Deliver a plan by e-mail, from the sender to every recipient (RF-10).

    Attributes:
        settings (SmtpSettings): The server, the user name and password, and the addresses.
        timeout (float): Seconds for the whole session.
    """

    def __init__(
        self, settings: SmtpSettings, *, timeout: float, context: ssl.SSLContext | None = None
    ) -> None:
        """
        Keep the settings; nothing is sent until :meth:`deliver`.

        Args:
            settings (SmtpSettings): The server, the user name and password, and the addresses.
            timeout (float): Seconds for the whole session, the timeout of the configuration.
            context (ssl.SSLContext | None): The client context; ``None`` means
                :func:`~tlsvigil.certificate.create_context`. The tests pass one that also
                trusts their own CA.
        """
        self.settings = settings
        self.timeout = timeout
        self._context = create_context() if context is None else context

    def _message(self, plan: Plan, now: datetime, lang: Lang) -> EmailMessage:
        """
        Build the e-mail of a run.

        Args:
            plan (Plan): What the channel hears in this run; its events are not empty.
            now (datetime): The moment the run started, aware.
            lang (Lang): The language of the run.

        Returns:
            EmailMessage: A ``text/plain`` UTF-8 message with its headers.

        Raises:
            ValueError: When a header value holds a line break; the policy refuses it, so no
                value reaches a second header.
        """
        settings = self.settings
        message = EmailMessage(policy=email.policy.SMTP)
        message["From"] = settings.sender
        message["To"] = ", ".join(settings.recipients)
        message["Subject"] = mail_subject(plan, lang)
        message["Date"] = format_datetime(now)
        # The sender's domain, never the machine's name, which make_msgid looks up otherwise.
        message["Message-ID"] = make_msgid(domain=settings.sender.rpartition("@")[2])
        # Quoted-printable keeps the body 7-bit, so a server without 8BITMIME takes it too.
        message.set_content(mail_body(plan, now, lang), cte="quoted-printable")
        return message

    def _open(self, deadline: https.Deadline) -> _Bounded:
        """
        Connect and secure the session, ready to sign in.

        Args:
            deadline (https.Deadline): The deadline of the whole session.

        Returns:
            _Bounded: The session, over TLS.

        Raises:
            _NoStartTLS: When ``"starttls"`` was chosen and the server does not offer it.
            OSError: For any failure of the connection, the handshake or a reply;
                :mod:`smtplib`'s exceptions are among them.
        """
        server = self.settings.server
        if server.security is SmtpSecurity.TLS:
            return _BoundedSMTPSSL(
                server.host, server.port, deadline=deadline, context=self._context
            )
        session = _BoundedSMTP(server.host, server.port, deadline=deadline)
        try:
            session.ehlo()
            # Required, never opportunistic: without it the password would travel in clear.
            if not session.has_extn("starttls"):
                raise _NoStartTLS
            session.starttls(context=self._context)
            session.ehlo()
        except BaseException:
            session.close()
            raise
        return session

    def _send(self, message: EmailMessage, lang: Lang) -> tuple[bool, str | None]:
        """
        Sign in and send the e-mail, in one session.

        Args:
            message (EmailMessage): The e-mail.
            lang (Lang): The language of the run, for the message of a failure.

        Returns:
            tuple[bool, str | None]: Whether the server took the e-mail for at least one
                recipient, and why the delivery failed, or ``None``.
        """
        settings = self.settings
        try:
            session = self._open(https.deadline_after(self.timeout))
            try:
                session.login(settings.username, settings.password)
                refused = session.send_message(message, settings.sender, list(settings.recipients))
                # The e-mail was taken; a goodbye that fails changes nothing.
                with contextlib.suppress(OSError):
                    session.quit()
            finally:
                session.close()
        except (OSError, _NoStartTLS) as error:
            return False, _failure(error, lang)
        if refused:
            # The others got it: told once, so a repeat would reach them twice.
            return True, plural(lang, "mail.recipients", len(refused))
        return True, None

    def deliver(
        self, plan: Plan, part: ChannelState, now: datetime, lang: Lang
    ) -> tuple[Delivery, ChannelState]:
        """
        Deliver a plan by e-mail: one message when it has events, none otherwise (RF-10).

        Args:
            plan (Plan): What the channel hears in this run.
            part (ChannelState): What the channel was told.
            now (datetime): The moment the run started, aware; the days left count from it.
            lang (Lang): The language of the run.

        Returns:
            tuple[Delivery, ChannelState]: How the delivery ended, and what the channel knows
                afterwards: the view once the server took the e-mail for any recipient or there
                was nothing to send, and the old part when it took it for none.
        """
        told = replace(part, told={finding.key: finding for finding in plan.view})
        if not plan.events:
            return Delivery(Channel.SMTP, DeliveryOutcome.UNCHANGED), told
        try:
            message = self._message(plan, now, lang)
        except ValueError:
            return (
                Delivery(Channel.SMTP, DeliveryOutcome.FAILED, translate(lang, "mail.header")),
                part,
            )
        sent, failure = self._send(message, lang)
        if failure is not None:
            return Delivery(Channel.SMTP, DeliveryOutcome.FAILED, failure), told if sent else part
        return Delivery(Channel.SMTP, DeliveryOutcome.SENT), told
