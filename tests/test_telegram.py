"""
Tests for the Telegram channel: one plain-text message, and Telegram's refusals (spec 005, RF-09).

Every test runs :class:`~tlsvigil.telegram.TelegramChannel` against
:class:`~fake_apis.FakeTelegram` on the local HTTPS server, so the request is real HTTPS on
``127.0.0.1`` and ``api.telegram.org`` is never resolved. Each refusal checks that the token,
which travels in the path of the URL, is in no message.
"""

from __future__ import annotations

import ssl
from datetime import UTC, datetime, timedelta

import pytest

from fake_apis import CHAT_ID, TELEGRAM_TOKEN, FakeTelegram, Refusal, telegram_answer
from http_server import LocalHTTPServer, Route
from tlsvigil.alerts import Event, EventKind, Finding, Plan
from tlsvigil.certificate import create_context
from tlsvigil.config import Channel
from tlsvigil.delivery import Delivery, DeliveryOutcome
from tlsvigil.environment import TelegramSettings
from tlsvigil.i18n import Lang
from tlsvigil.messages import telegram_text
from tlsvigil.state import ChannelState
from tlsvigil.telegram import MAX_MESSAGE_CHARS, TelegramChannel

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)

WARN = Finding(
    "certificate:example.com:443", "example.com:443", "expiring", None, NOW + timedelta(days=9)
)

NEW = Plan((Event(EventKind.NEW, WARN),), (WARN,))
QUIET = Plan((), (WARN,))

# What the channel was told before the run: nothing yet.
EMPTY = ChannelState()


@pytest.fixture
def fake() -> FakeTelegram:
    """
    Give a bot that has sent nothing.

    Returns:
        FakeTelegram: No message yet.
    """
    return FakeTelegram()


@pytest.fixture
def api(fake: FakeTelegram, https_server: LocalHTTPServer) -> str:
    """
    Serve the fake on the local server.

    Args:
        fake (FakeTelegram): The fake.
        https_server (LocalHTTPServer): The running server.

    Returns:
        str: The API root.
    """
    return fake.install(https_server)


@pytest.fixture
def channel(api: str, client_context: ssl.SSLContext) -> TelegramChannel:
    """
    Build the channel with the fake's token and chat, pointed at the fake.

    Args:
        api (str): The API root.
        client_context (ssl.SSLContext): The client context that trusts the test CA.

    Returns:
        TelegramChannel: The channel.
    """
    settings = TelegramSettings(TELEGRAM_TOKEN, CHAT_ID)
    return TelegramChannel(settings, timeout=5.0, context=client_context, api_url=api)


def _deliver(
    channel: TelegramChannel, fake: FakeTelegram, plan: Plan, lang: Lang = Lang.EN
) -> tuple[Delivery, ChannelState]:
    """
    Deliver a plan to a channel that was told nothing, and check the request it sent.

    Args:
        channel (TelegramChannel): The channel.
        fake (FakeTelegram): The fake, to read its problems.
        plan (Plan): The plan.
        lang (Lang): The language of the run.

    Returns:
        tuple[Delivery, ChannelState]: What the channel gave.
    """
    delivery, state = channel.deliver(plan, EMPTY, NOW, lang)
    assert fake.problems == []
    return delivery, state


# ---------------------------------------------------------------------------
# The message
# ---------------------------------------------------------------------------


def test_a_plan_with_events_sends_one_plain_text_message_without_previews(
    channel: TelegramChannel, fake: FakeTelegram
) -> None:
    delivery, state = _deliver(channel, fake, NEW)

    assert delivery == Delivery(Channel.TELEGRAM, DeliveryOutcome.SENT)
    assert state == ChannelState({WARN.key: WARN})
    # Exactly these fields: no `parse_mode`, so Telegram reads the text as plain text.
    assert fake.messages == [
        {
            "chat_id": CHAT_ID,
            "text": telegram_text(NEW, NOW, Lang.EN),
            "link_preview_options": {"is_disabled": True},
        }
    ]


def test_a_plan_without_events_sends_nothing_and_keeps_the_view(
    channel: TelegramChannel, fake: FakeTelegram
) -> None:
    delivery, state = _deliver(channel, fake, QUIET)

    assert delivery == Delivery(Channel.TELEGRAM, DeliveryOutcome.UNCHANGED)
    assert state == ChannelState({WARN.key: WARN})
    assert fake.requests == 0


def test_markdown_and_html_in_outside_text_reach_the_chat_as_they_are(
    channel: TelegramChannel, fake: FakeTelegram
) -> None:
    name = "*bold* _it_ <b>x</b> [a](https://example.net) @user #1 `tick` www.example.org"
    odd = Finding("certificate:odd:443", name, "invalid", "expired")

    delivery, _ = _deliver(channel, fake, Plan((Event(EventKind.NEW, odd),), (odd,)))

    assert delivery.outcome is DeliveryOutcome.SENT
    [message] = fake.messages
    assert name in str(message["text"])
    assert "parse_mode" not in message


def test_the_message_is_written_in_the_language_of_the_run(
    channel: TelegramChannel, fake: FakeTelegram
) -> None:
    _deliver(channel, fake, NEW, Lang.PT_BR)

    [message] = fake.messages
    assert message["text"] == telegram_text(NEW, NOW, Lang.PT_BR)
    assert message["text"] != telegram_text(NEW, NOW, Lang.EN)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("refusal", "description"),
    [
        ("token", "Unauthorized"),
        ("chat", "Bad Request: chat not found"),
        ("blocked", "Forbidden: bot was blocked by the user"),
        ("rate", "Too Many Requests: retry after 7"),
    ],
)
def test_a_refusal_fails_with_telegram_s_description_and_keeps_the_old_part(
    channel: TelegramChannel, fake: FakeTelegram, refusal: Refusal, description: str
) -> None:
    fake.refuse(refusal)
    told = ChannelState({"domain:example.org": WARN})

    delivery, state = channel.deliver(NEW, told, NOW, Lang.EN)

    assert delivery == Delivery(
        Channel.TELEGRAM, DeliveryOutcome.FAILED, f"Telegram refused the message: {description}"
    )
    assert state == told
    assert fake.requests == 1
    assert TELEGRAM_TOKEN not in str(delivery.message)


def test_a_description_with_a_line_break_is_escaped_and_cut(
    channel: TelegramChannel, fake: FakeTelegram
) -> None:
    fake.answer = telegram_answer(400, "Bad Request:\nline\x1b[31m " + "x" * 400)

    delivery, _ = _deliver(channel, fake, NEW)

    prefix = "Telegram refused the message: "
    assert delivery.message is not None
    assert delivery.message.startswith(prefix + "Bad Request:\\nline\\x1b[31m x")
    assert len(delivery.message) == len(prefix) + MAX_MESSAGE_CHARS


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (telegram_answer(500), "Telegram answered HTTP 500"),
        (Route(502, b"<html>Bad Gateway</html>"), "Telegram answered HTTP 502"),
        (Route(200, b"not json"), "Telegram sent an answer TLSVigil cannot read"),
        (Route(200, b'{"ok": "true"}'), "Telegram answered HTTP 200"),
    ],
    ids=["no-description", "not-json", "ok-not-json", "ok-not-true"],
)
def test_an_answer_without_a_description_fails_with_its_status(
    channel: TelegramChannel, fake: FakeTelegram, answer: Route, message: str
) -> None:
    fake.answer = answer

    delivery, state = _deliver(channel, fake, NEW)

    assert delivery == Delivery(Channel.TELEGRAM, DeliveryOutcome.FAILED, message)
    assert state == EMPTY


def test_an_api_that_cannot_be_reached_fails_without_the_url(api: str, fake: FakeTelegram) -> None:
    # A context without the test CA: the handshake fails, and the error knows the URL's host.
    settings = TelegramSettings(TELEGRAM_TOKEN, CHAT_ID)
    channel = TelegramChannel(settings, timeout=5.0, context=create_context(), api_url=api)

    delivery, state = channel.deliver(NEW, EMPTY, NOW, Lang.EN)

    assert delivery == Delivery(
        Channel.TELEGRAM,
        DeliveryOutcome.FAILED,
        "the Telegram Bot API could not be reached: the TLS connection failed",
    )
    assert state == EMPTY
    assert fake.requests == 0
