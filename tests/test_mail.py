"""
Tests for the e-mail channel: TLS, STARTTLS, no credential before TLS, the bounds (spec 005, RF-10).

Every test runs :class:`~tlsvigil.mail.MailChannel` against a
:class:`~smtp_server.LocalSMTPServer` on ``127.0.0.1``, so the session is real SMTP over real TLS.
The server records every line it receives: the tests that refuse a session assert that ``AUTH``
never came, and the controls that ``AUTH`` came after TLS. The network guard refuses any lookup of
the machine's own name, which :mod:`smtplib` would make without a ``local_hostname``.
"""

from __future__ import annotations

import email
import email.policy
import socket
import ssl
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

import pytest

from network_guard import NetworkGuard
from smtp_server import PASSWORD, USERNAME, LocalSMTPServer
from tlsvigil.alerts import Event, EventKind, Finding, Plan
from tlsvigil.certificate import create_context
from tlsvigil.config import Channel, SmtpSecurity, SmtpServer
from tlsvigil.delivery import Delivery, DeliveryOutcome
from tlsvigil.environment import SmtpSettings
from tlsvigil.i18n import Lang
from tlsvigil.mail import MAX_REPLY_LINES, MailChannel
from tlsvigil.messages import mail_body, mail_subject
from tlsvigil.state import ChannelState

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)

WARN = Finding(
    "certificate:example.com:443", "example.com:443", "expiring", None, NOW + timedelta(days=9)
)

NEW = Plan((Event(EventKind.NEW, WARN),), (WARN,))
QUIET = Plan((), (WARN,))

SENDER = "tlsvigil@example.com"
RECIPIENTS = ("first@example.org", "second@example.org")

# What the channel was told before the run; a failure that sent nothing gives it back.
BEFORE = ChannelState({"domain:example.org": WARN})
AFTER = ChannelState({WARN.key: WARN})

# The verbs of a whole session over STARTTLS, in order.
STARTTLS_SESSION = ["EHLO", "STARTTLS", "EHLO", "AUTH", "MAIL", "RCPT", "RCPT", "DATA", "QUIT"]

# What the `smtp` fixture gives: start a server with these keywords.
type Start = Callable[..., LocalSMTPServer]


@pytest.fixture
def smtp(https_context: ssl.SSLContext) -> Iterator[Start]:
    """
    Start local SMTP servers for the test, and stop them when it ends.

    Args:
        https_context (ssl.SSLContext): The server context, with a leaf for ``127.0.0.1`` from
            the test CA.

    Yields:
        Start: ``start(**settings)``, which starts a :class:`~smtp_server.LocalSMTPServer`
            with those settings and returns it.
    """
    with ExitStack() as stack:

        def start(**settings: object) -> LocalSMTPServer:
            """
            Start a server; it stops when the test ends.

            Args:
                **settings (object): The keyword arguments of the server.

            Returns:
                LocalSMTPServer: The running server.
            """
            # The settings are the server's own keywords; mypy cannot match them to `object`.
            server = LocalSMTPServer(https_context, **settings)  # type: ignore[arg-type]
            return stack.enter_context(server)

        yield start


def _channel(
    server: LocalSMTPServer,
    context: ssl.SSLContext,
    *,
    timeout: float = 5.0,
    sender: str = SENDER,
    password: str = PASSWORD,
) -> MailChannel:
    """
    Build the channel for a local server.

    Args:
        server (LocalSMTPServer): The running server; its ``security`` picks the channel's.
        context (ssl.SSLContext): The client context.
        timeout (float): Seconds for the whole session.
        sender (str): The ``From`` address.
        password (str): The password to sign in with.

    Returns:
        MailChannel: The channel.
    """
    security = SmtpSecurity(server.security)
    settings = SmtpSettings(
        SmtpServer(server.host, server.port, security), USERNAME, password, sender, RECIPIENTS
    )
    return MailChannel(settings, timeout=timeout, context=context)


def _read_back(raw: bytes) -> EmailMessage:
    """
    Parse a message as a mail client would.

    Args:
        raw (bytes): The message as the server took it.

    Returns:
        EmailMessage: The parsed message.
    """
    message = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(message, EmailMessage)
    return message


def _no_secret(server: LocalSMTPServer) -> None:
    """
    Assert that the client sent neither ``AUTH`` nor any form of the credentials.

    Args:
        server (LocalSMTPServer): The server, with what it recorded.
    """
    assert "AUTH" not in server.verbs
    sent = " ".join(server.commands)
    assert USERNAME not in sent and PASSWORD not in sent


# ---------------------------------------------------------------------------
# A message delivered
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", [Lang.EN, Lang.PT_BR])
def test_a_message_over_starttls_signs_in_after_tls_and_reads_back_as_utf_8_text(
    smtp: Start, client_context: ssl.SSLContext, lang: Lang
) -> None:
    server = smtp(security="starttls")

    delivery, state = _channel(server, client_context).deliver(NEW, BEFORE, NOW, lang)

    assert delivery == Delivery(Channel.SMTP, DeliveryOutcome.SENT)
    assert state == AFTER
    # AUTH only after the second EHLO, which only comes over TLS: the positive control of the
    # tests that refuse a session before it.
    assert server.verbs == STARTTLS_SESSION
    assert server.envelopes == [(SENDER, RECIPIENTS)]
    [raw] = server.messages
    message = _read_back(raw)
    assert message.get_content_type() == "text/plain"
    assert message.get_content_charset() == "utf-8"
    # SMTP's line breaks are CRLF; the text is the body, line for line.
    assert message.get_content().replace("\r\n", "\n") == mail_body(NEW, NOW, lang)
    assert message["From"] == SENDER
    assert message["To"] == ", ".join(RECIPIENTS)
    assert message["Subject"] == mail_subject(NEW, lang)
    assert parsedate_to_datetime(message["Date"]) == NOW
    assert message["Message-ID"].endswith("@example.com>")
    # 7-bit on the wire, so a server without 8BITMIME takes it too.
    assert raw.isascii()


def test_a_message_over_implicit_tls_signs_in_at_once(
    smtp: Start, client_context: ssl.SSLContext
) -> None:
    server = smtp(security="tls")

    delivery, state = _channel(server, client_context).deliver(NEW, BEFORE, NOW, Lang.EN)

    assert delivery == Delivery(Channel.SMTP, DeliveryOutcome.SENT)
    assert state == AFTER
    assert server.verbs == ["EHLO", "AUTH", "MAIL", "RCPT", "RCPT", "DATA", "QUIT"]
    assert len(server.messages) == 1


@pytest.mark.parametrize("mechanism", ["PLAIN", "LOGIN"])
def test_each_mechanism_the_server_offers_signs_in(
    smtp: Start, client_context: ssl.SSLContext, mechanism: str
) -> None:
    server = smtp(mechanisms=(mechanism,))

    delivery, _ = _channel(server, client_context).deliver(NEW, BEFORE, NOW, Lang.EN)

    assert delivery.outcome is DeliveryOutcome.SENT
    assert any(line.upper().startswith(f"AUTH {mechanism}") for line in server.commands)


def test_a_plan_without_events_opens_no_session(
    smtp: Start, client_context: ssl.SSLContext
) -> None:
    server = smtp()

    delivery, state = _channel(server, client_context).deliver(QUIET, BEFORE, NOW, Lang.EN)

    assert delivery == Delivery(Channel.SMTP, DeliveryOutcome.UNCHANGED)
    assert state == AFTER
    assert server.commands == []


def test_the_machine_s_own_name_is_never_looked_up(
    smtp: Start, client_context: ssl.SSLContext, network_guard: NetworkGuard
) -> None:
    server = smtp()

    delivery, _ = _channel(server, client_context).deliver(NEW, BEFORE, NOW, Lang.EN)

    # The guard refuses any other lookup, so the session would have failed with an
    # AssertionError; the record shows the only name used.
    assert delivery.outcome is DeliveryOutcome.SENT
    assert set(network_guard.lookups) == {server.host}
    assert server.commands[0] == "ehlo localhost"


# ---------------------------------------------------------------------------
# No credential before TLS
# ---------------------------------------------------------------------------


def test_a_server_without_starttls_is_left_before_anything_secret_is_sent(
    smtp: Start, client_context: ssl.SSLContext
) -> None:
    # The server still offers AUTH in clear, as one whose STARTTLS was stripped would.
    server = smtp(offer_starttls=False)

    delivery, state = _channel(server, client_context).deliver(NEW, BEFORE, NOW, Lang.EN)

    assert delivery == Delivery(
        Channel.SMTP,
        DeliveryOutcome.FAILED,
        "the SMTP server does not offer STARTTLS, so the user name was not sent",
    )
    assert state == BEFORE
    assert server.verbs == ["EHLO"]
    _no_secret(server)


@pytest.mark.parametrize("security", ["starttls", "tls"])
def test_a_server_whose_certificate_is_not_trusted_is_left_before_anything_secret_is_sent(
    smtp: Start, security: str
) -> None:
    server = smtp(security=security)
    # The context of the check without the test CA: the server's leaf does not verify.
    channel = _channel(server, create_context())

    delivery, state = channel.deliver(NEW, BEFORE, NOW, Lang.EN)

    assert delivery.outcome is DeliveryOutcome.FAILED
    assert delivery.message is not None
    assert delivery.message.startswith(
        "the SMTP server could not be reached: the TLS connection failed ("
    )
    assert "CERTIFICATE_VERIFY_FAILED" in delivery.message
    assert state == BEFORE
    _no_secret(server)


def test_a_wrong_password_fails_with_the_server_s_reply_and_not_the_password(
    smtp: Start, client_context: ssl.SSLContext
) -> None:
    server = smtp()
    channel = _channel(server, client_context, password="another-password-entirely")

    delivery, state = channel.deliver(NEW, BEFORE, NOW, Lang.EN)

    assert delivery == Delivery(
        Channel.SMTP,
        DeliveryOutcome.FAILED,
        "the SMTP server refused the user name or the password "
        "(535 5.7.8 Authentication credentials invalid)",
    )
    assert state == BEFORE
    assert server.messages == []


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (
            {"behaviour": "busy"},
            "the SMTP server answered 554 (5.3.2 localhost Service not available)",
        ),
        ({"behaviour": "hangup"}, "the SMTP server closed the connection"),
        (
            {"refuse": frozenset({SENDER})},
            "the SMTP server refused the sender (553 5.7.1 <tlsvigil@example.com>: "
            "Sender address rejected)",
        ),
    ],
    ids=["busy", "hangup", "sender"],
)
def test_a_server_that_turns_the_session_down_fails_with_its_reply(
    smtp: Start, client_context: ssl.SSLContext, settings: dict[str, object], message: str
) -> None:
    server = smtp(**settings)

    delivery, state = _channel(server, client_context).deliver(NEW, BEFORE, NOW, Lang.EN)

    assert delivery == Delivery(Channel.SMTP, DeliveryOutcome.FAILED, message)
    assert state == BEFORE
    assert server.messages == []


# ---------------------------------------------------------------------------
# The bounds of the session (ADR-6)
# ---------------------------------------------------------------------------


def test_a_silent_server_is_bounded_by_the_deadline(
    smtp: Start, client_context: ssl.SSLContext
) -> None:
    server = smtp(behaviour="silent")
    started = time.monotonic()

    delivery, state = _channel(server, client_context, timeout=0.3).deliver(
        NEW, BEFORE, NOW, Lang.EN
    )

    assert time.monotonic() - started < 2
    assert delivery.message is not None
    assert delivery.message.startswith(
        "the SMTP server could not be reached: the server did not answer within the timeout"
    )
    assert state == BEFORE


def test_a_reply_that_never_ends_is_refused_at_the_line_cap(
    smtp: Start, client_context: ssl.SSLContext
) -> None:
    server = smtp(behaviour="endless")
    started = time.monotonic()

    # Without the cap, the deadline would end the session instead, with another message.
    delivery, state = _channel(server, client_context, timeout=3.0).deliver(
        NEW, BEFORE, NOW, Lang.EN
    )

    assert time.monotonic() - started < 3
    assert delivery == Delivery(
        Channel.SMTP,
        DeliveryOutcome.FAILED,
        f"the SMTP server sent a reply longer than {MAX_REPLY_LINES} lines",
    )
    assert state == BEFORE


def test_a_reply_that_drips_lines_is_bounded_by_the_deadline_of_the_whole_session(
    smtp: Start, client_context: ssl.SSLContext
) -> None:
    # Each line comes well inside the timeout, so only the time left before each line, not a
    # timeout per read, ends the session before the line cap does (100 lines take 5 s).
    server = smtp(behaviour="endless", interval=0.05)
    started = time.monotonic()

    delivery, _ = _channel(server, client_context, timeout=0.4).deliver(NEW, BEFORE, NOW, Lang.EN)

    assert time.monotonic() - started < 2
    assert delivery.message is not None
    assert delivery.message.startswith(
        "the SMTP server could not be reached: the server did not answer within the timeout"
    )


def test_a_closed_port_is_refused(client_context: ssl.SSLContext) -> None:
    # A port that was free a moment ago; nothing listens on it now. The timeout is 5 seconds,
    # because a refused connection on the Windows loopback takes about two.
    with socket.create_server(("127.0.0.1", 0)) as probe:
        port = int(probe.getsockname()[1])
    settings = SmtpSettings(
        SmtpServer("127.0.0.1", port, SmtpSecurity.STARTTLS), USERNAME, PASSWORD, SENDER, RECIPIENTS
    )

    delivery, state = MailChannel(settings, timeout=5.0, context=client_context).deliver(
        NEW, BEFORE, NOW, Lang.EN
    )

    assert delivery.message is not None
    assert delivery.message.startswith(
        "the SMTP server could not be reached: the server refused the connection"
    )
    assert state == BEFORE


# ---------------------------------------------------------------------------
# Recipients and headers
# ---------------------------------------------------------------------------


def test_a_refused_recipient_is_counted_never_named_and_the_others_are_told(
    smtp: Start, client_context: ssl.SSLContext
) -> None:
    server = smtp(refuse=frozenset({RECIPIENTS[1]}))

    delivery, state = _channel(server, client_context).deliver(NEW, BEFORE, NOW, Lang.EN)

    assert delivery == Delivery(
        Channel.SMTP, DeliveryOutcome.FAILED, "the SMTP server refused 1 recipient"
    )
    # The first recipient got it: told once, so the next run does not send it again.
    assert state == AFTER
    assert server.envelopes == [(SENDER, RECIPIENTS[:1])]


def test_every_recipient_refused_fails_and_keeps_the_old_part(
    smtp: Start, client_context: ssl.SSLContext
) -> None:
    server = smtp(refuse=frozenset(RECIPIENTS))

    delivery, state = _channel(server, client_context).deliver(NEW, BEFORE, NOW, Lang.PT_BR)

    assert delivery == Delivery(
        Channel.SMTP, DeliveryOutcome.FAILED, "o servidor SMTP recusou 2 destinatários"
    )
    assert state == BEFORE
    assert server.messages == []
    assert not any(address in str(delivery.message) for address in RECIPIENTS)


def test_a_header_value_with_a_line_break_is_refused_before_any_connection(
    smtp: Start, client_context: ssl.SSLContext
) -> None:
    server = smtp()
    channel = _channel(server, client_context, sender=SENDER + "\r\nBcc: spy@example.net")

    delivery, state = channel.deliver(NEW, BEFORE, NOW, Lang.EN)

    assert delivery == Delivery(
        Channel.SMTP,
        DeliveryOutcome.FAILED,
        "a header of the e-mail would hold a line break, so nothing was sent",
    )
    assert state == BEFORE
    assert server.commands == []
