"""
The Telegram channel: one message to one chat through the Bot API (spec 005, RF-09).

:meth:`TelegramChannel.deliver` sends one ``POST`` to ``sendMessage`` when the plan has events,
and nothing otherwise. The text goes without ``parse_mode``, so Telegram reads it as plain text
and no outside text can become markup, and with link previews off. The token travels in the path
of the URL, which never reaches a message; the only text of Telegram's that does is the
``description`` of a refusal, escaped and cut. Nothing is retried within a run.
"""

from __future__ import annotations

import json
import ssl
from dataclasses import replace
from datetime import datetime
from typing import Final

from tlsvigil import __version__, https
from tlsvigil.alerts import Plan
from tlsvigil.certificate import create_context
from tlsvigil.config import Channel
from tlsvigil.delivery import Delivery, DeliveryOutcome
from tlsvigil.environment import TelegramSettings
from tlsvigil.i18n import Lang, translate
from tlsvigil.messages import telegram_text
from tlsvigil.output import printable
from tlsvigil.state import ChannelState

__all__ = ["API_URL", "MAX_ANSWER_BYTES", "MAX_MESSAGE_CHARS", "TelegramChannel"]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The root of the Bot API; the tests give the channel a local server's URL instead.
API_URL: Final = "https://api.telegram.org"

# The largest answer read: a sent message comes back whole, and the text is at most 4096 units.
MAX_ANSWER_BYTES: Final = 64 * 1024

# Telegram's own `description` is cut to this many characters before it reaches a message.
MAX_MESSAGE_CHARS: Final = 200


def _refusal(response: https.Response, lang: Lang) -> str | None:
    """
    Read why Telegram refused a message, or tell that it did not.

    Args:
        response (https.Response): The answer, whatever its status.
        lang (Lang): The language of the run.

    Returns:
        str | None: ``None`` when the answer's ``ok`` is ``true``; otherwise the message:
            Telegram's ``description``, escaped and cut, or the status without one.
    """
    try:
        document = json.loads(response.body)
    except ValueError:
        document = None
    if not isinstance(document, dict):
        if 200 <= response.status < 300:
            return translate(lang, "telegram.invalid")
        return translate(lang, "telegram.http", status=response.status)
    # `is True`: only the JSON `true` says the message was sent.
    if document.get("ok") is True:
        return None
    description = document.get("description")
    if isinstance(description, str) and description:
        cut = printable(description)[:MAX_MESSAGE_CHARS]
        return translate(lang, "telegram.refused", description=cut)
    return translate(lang, "telegram.http", status=response.status)


# ---------------------------------------------------------------------------
# The channel
# ---------------------------------------------------------------------------


class TelegramChannel:
    """
    Deliver a plan to one Telegram chat (RF-09).

    Attributes:
        settings (TelegramSettings): The bot's token and the chat.
        timeout (float): Seconds for the whole request.
        api_url (str): The ``https://`` root of the Bot API.
    """

    def __init__(
        self,
        settings: TelegramSettings,
        *,
        timeout: float,
        context: ssl.SSLContext | None = None,
        api_url: str = API_URL,
    ) -> None:
        """
        Keep the settings; nothing is sent until :meth:`deliver`.

        Args:
            settings (TelegramSettings): The bot's token and the chat.
            timeout (float): Seconds for the whole request, the timeout of the configuration.
            context (ssl.SSLContext | None): The client context; ``None`` means
                :func:`~tlsvigil.certificate.create_context`. The tests pass one that also
                trusts their own CA.
            api_url (str): The root of the Bot API; the tests point it at a local server.
        """
        self.settings = settings
        self.timeout = timeout
        self.api_url = api_url.rstrip("/")
        self._context = create_context() if context is None else context

    def _send(self, text: str, lang: Lang) -> str | None:
        """
        Send one message to the chat.

        Args:
            text (str): The plain text.
            lang (Lang): The language of the run, for the message of a failure.

        Returns:
            str | None: ``None`` once Telegram accepted the message; otherwise why not.
        """
        document = {
            "chat_id": self.settings.chat_id,
            "text": text,
            # No `parse_mode`: the text is plain, so nothing in it is read as markup (ADR-5).
            "link_preview_options": {"is_disabled": True},
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"tlsvigil/{__version__}",
        }
        url = f"{self.api_url}/bot{self.settings.token}/sendMessage"
        try:
            response = https.request(
                "POST",
                url,
                headers=headers,
                body=json.dumps(document, ensure_ascii=False).encode("utf-8"),
                cap=MAX_ANSWER_BYTES,
                deadline=https.deadline_after(self.timeout),
                context=self._context,
            )
        except https.RequestError as error:
            # The kind only: the URL holds the token, and no part of it may reach a message.
            kind = translate(lang, f"request.{error.kind}")
            return translate(lang, "telegram.unreachable", kind=kind)
        return _refusal(response, lang)

    def deliver(
        self, plan: Plan, part: ChannelState, now: datetime, lang: Lang
    ) -> tuple[Delivery, ChannelState]:
        """
        Deliver a plan to the chat: one message when it has events, none otherwise (RF-09).

        Args:
            plan (Plan): What the channel hears in this run.
            part (ChannelState): What the channel was told.
            now (datetime): The moment the run started, aware; the days left count from it.
            lang (Lang): The language of the run.

        Returns:
            tuple[Delivery, ChannelState]: How the delivery ended, and what the channel knows
                afterwards: the view once the message was sent or there was nothing to send,
                and the old part after a failure, so the next run tells it again.
        """
        told = replace(part, told={finding.key: finding for finding in plan.view})
        if not plan.events:
            return Delivery(Channel.TELEGRAM, DeliveryOutcome.UNCHANGED), told
        failure = self._send(telegram_text(plan, now, lang), lang)
        if failure is not None:
            return Delivery(Channel.TELEGRAM, DeliveryOutcome.FAILED, failure), part
        return Delivery(Channel.TELEGRAM, DeliveryOutcome.SENT), told
