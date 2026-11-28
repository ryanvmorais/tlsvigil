"""
The orchestration of ``tlsvigil check --notify``: state in, one plan per channel, state out.

:func:`notify` reads the state file, asks :mod:`tlsvigil.alerts` what each channel listed has to
hear, hands each channel its plan in the order of ``channels``, and writes the state once, at the
end (spec 005, RF-01, RF-07; ADR-2). A channel keeps the part it gives back: advanced when the
delivery succeeded, the old one when it failed, so the next run tells a failed channel again and
the others never twice. A failure on one channel never stops the next one; an unexpected exception
inside a channel is a bug, and it reaches the command's exit ``3`` after the parts of the channels
already delivered are saved.

Nothing here writes to a stream: the lines about the state file go to the ``warn`` callable, which
the command passes through its :class:`~tlsvigil.environment.Redactor` (ADR-7).
"""

from __future__ import annotations

import ssl
from collections.abc import Callable
from pathlib import Path

from tlsvigil import github, telegram
from tlsvigil.alerts import findings, plan
from tlsvigil.config import Channel, Config
from tlsvigil.delivery import Delivery
from tlsvigil.environment import ChannelSettings
from tlsvigil.github import GitHubChannel
from tlsvigil.i18n import Lang, translate
from tlsvigil.mail import MailChannel
from tlsvigil.output import printable
from tlsvigil.runner import Report
from tlsvigil.state import ChannelState, State, load_state, reset_for, save_state
from tlsvigil.telegram import TelegramChannel

__all__ = ["notify"]

# A union, not a base class: the three channels only share the shape of deliver(), and the
# union lets mypy check that each one has it.
type _Channel = GitHubChannel | TelegramChannel | MailChannel


def _channels(
    settings: ChannelSettings,
    timeout: float,
    context: ssl.SSLContext | None,
    github_api_url: str,
    telegram_api_url: str,
) -> dict[Channel, _Channel]:
    """
    Build the channel of each settings read.

    Args:
        settings (ChannelSettings): The settings of the channels listed.
        timeout (float): The timeout of the configuration.
        context (ssl.SSLContext | None): The client context; ``None`` means the default one.
        github_api_url (str): The root of the GitHub REST API.
        telegram_api_url (str): The root of the Telegram Bot API.

    Returns:
        dict[Channel, _Channel]: One channel per settings that are not ``None``.
    """
    built: dict[Channel, _Channel] = {}
    if settings.github is not None:
        built[Channel.GITHUB] = GitHubChannel(
            settings.github, timeout=timeout, context=context, api_url=github_api_url
        )
    if settings.telegram is not None:
        built[Channel.TELEGRAM] = TelegramChannel(
            settings.telegram, timeout=timeout, context=context, api_url=telegram_api_url
        )
    if settings.smtp is not None:
        built[Channel.SMTP] = MailChannel(settings.smtp, timeout=timeout, context=context)
    return built


def notify(
    report: Report,
    config: Config,
    settings: ChannelSettings,
    state_path: Path,
    *,
    lang: Lang,
    warn: Callable[[str], None],
    context: ssl.SSLContext | None = None,
    github_api_url: str = github.API_URL,
    telegram_api_url: str = telegram.API_URL,
) -> tuple[Delivery, ...]:
    """
    Deliver a report to every channel listed, and keep what each one was told (RF-01, RF-07).

    Args:
        report (Report): What the run found.
        config (Config): The configuration, with a ``[notifications]`` table that lists at
            least one channel.
        settings (ChannelSettings): The settings of the channels listed.
        state_path (Path): The state file, read at the start and written at the end.
        lang (Lang): The language of the run and of every message.
        warn (Callable[[str], None]): Takes each line about the state file: why it was read as
            empty, or why it could not be written.
        context (ssl.SSLContext | None): The client context of every channel; ``None`` means
            the default one. Only the tests pass it (ADR-9).
        github_api_url (str): The root of the GitHub REST API. Only the tests change it.
        telegram_api_url (str): The root of the Telegram Bot API. Only the tests change it.

    Returns:
        tuple[Delivery, ...]: One delivery per channel, in the order of ``channels``.

    Raises:
        ValueError: When the configuration has no ``[notifications]`` table, which the command
            refuses before any check runs.
    """
    if config.notifications is None:
        raise ValueError("notify() needs a configuration with a [notifications] table")
    order = config.notifications.channels
    shown = printable(str(state_path))

    state, notice = load_state(state_path)
    if notice is not None:
        params = {key: printable(value) for key, value in notice.params.items()}
        warn(translate(lang, notice.key, **params))

    repository = settings.github.repository if settings.github is not None else None
    state = reset_for(state, order, repository)
    answers, error_runs = findings(report, state.error_runs)
    built = _channels(settings, config.timeout, context, github_api_url, telegram_api_url)

    parts: dict[Channel, ChannelState] = dict(state.channels)
    deliveries: list[Delivery] = []
    try:
        for channel in order:
            part = parts.get(channel, ChannelState())
            delivery, parts[channel] = built[channel].deliver(
                plan(part.told, answers), part, report.started_at, lang
            )
            deliveries.append(delivery)
    finally:
        # Also after a bug in a channel: the channels already delivered must not hear it again.
        try:
            save_state(state_path, State(error_runs, parts))
        except OSError:
            warn(translate(lang, "state.unwritable", path=shown))
    return tuple(deliveries)
