"""
The channels' secrets and addresses, read from the environment, and the Redactor (spec 005).

:func:`read_settings` reads the variables of the channels a ``[notifications]`` table lists, and
only those (RF-03): the GitHub variables keep GitHub's names and the others start with
``TLSVIGIL_`` (ADR-10). Every missing, empty or malformed variable is a
:class:`~tlsvigil.config.Problem` placed at the variable's name, and none of them holds the value,
which may be a real token.

The :class:`Redactor` replaces those values in any text the command writes (RNF-03, ADR-7): the
messages of TLSVigil never hold a secret, but the text of an exception from :mod:`http.client`,
:mod:`smtplib` or a bug may quote a URL or a header.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Final
from urllib.parse import quote

from tlsvigil.config import Channel, ConfigError, Notifications, Problem, SmtpServer

__all__ = [
    "GITHUB_REPOSITORY",
    "GITHUB_TOKEN",
    "REDACTED",
    "SMTP_FROM",
    "SMTP_PASSWORD",
    "SMTP_TO",
    "SMTP_USERNAME",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_TOKEN",
    "VARIABLES",
    "ChannelSettings",
    "GitHubSettings",
    "Redactor",
    "SmtpSettings",
    "TelegramSettings",
    "read_settings",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GITHUB_TOKEN: Final = "GITHUB_TOKEN"
GITHUB_REPOSITORY: Final = "GITHUB_REPOSITORY"
TELEGRAM_TOKEN: Final = "TLSVIGIL_TELEGRAM_TOKEN"
TELEGRAM_CHAT_ID: Final = "TLSVIGIL_TELEGRAM_CHAT_ID"
SMTP_USERNAME: Final = "TLSVIGIL_SMTP_USERNAME"
SMTP_PASSWORD: Final = "TLSVIGIL_SMTP_PASSWORD"
SMTP_FROM: Final = "TLSVIGIL_SMTP_FROM"
SMTP_TO: Final = "TLSVIGIL_SMTP_TO"

# The variables of each channel, in the order they are read and their problems reported.
VARIABLES: Final[Mapping[Channel, tuple[str, ...]]] = {
    Channel.GITHUB: (GITHUB_TOKEN, GITHUB_REPOSITORY),
    Channel.TELEGRAM: (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID),
    Channel.SMTP: (SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM, SMTP_TO),
}

# What every value of a secret is replaced with in an output.
REDACTED: Final = "***"

# GitHub's own rules: a login of letters, digits and '-', a repository name that adds '.' and '_'.
_REPOSITORY: Final = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
# The bot's numeric id, ':', and the secret part (the form @BotFather gives).
_TELEGRAM_TOKEN: Final = re.compile(r"[0-9]+:[A-Za-z0-9_-]+")
# A chat's number (a group or a channel is negative) or a public channel's @username.
_CHAT_ID: Final = re.compile(r"-?[0-9]+|@[A-Za-z0-9_]{5,32}")
# What would let one value carry a display name, a second address or a header (RFC 5322).
_ADDRESS_SPECIALS: Final = frozenset('<>()[],;:"')


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GitHubSettings:
    """
    What the ``github`` channel reads from the environment.

    Attributes:
        token (str): The workflow's token, with ``issues: write``.
        repository (str): ``owner/name``, as the runner sets it.
    """

    token: str
    repository: str


@dataclass(frozen=True, slots=True)
class TelegramSettings:
    """
    What the ``telegram`` channel reads from the environment.

    Attributes:
        token (str): The bot's token.
        chat_id (str): A chat's number, possibly negative, or ``@channelname``.
    """

    token: str
    chat_id: str


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    """
    What the ``smtp`` channel needs: the server of the file and the rest from the environment.

    Attributes:
        server (SmtpServer): The ``[notifications.smtp]`` table.
        username (str): The name to sign in with.
        password (str): Its password.
        sender (str): The ``From`` address.
        recipients (tuple[str, ...]): One or more ``To`` addresses, in the order given.
    """

    server: SmtpServer
    username: str
    password: str
    sender: str
    recipients: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChannelSettings:
    """
    The settings of each channel listed; a channel not listed is ``None``.

    Attributes:
        github (GitHubSettings | None): When ``github`` is listed.
        telegram (TelegramSettings | None): When ``telegram`` is listed.
        smtp (SmtpSettings | None): When ``smtp`` is listed.
    """

    github: GitHubSettings | None
    telegram: TelegramSettings | None
    smtp: SmtpSettings | None

    def private_values(self) -> tuple[str, ...]:
        """
        Give the values no output may show (RNF-03).

        Returns:
            tuple[str, ...]: The two tokens, the password, the chat and the addresses of the
            channels listed. The chat and the addresses are not secrets, but they are personal,
            and an :mod:`smtplib` exception quotes the recipients it refused.
        """
        values: list[str] = []
        if self.github is not None:
            values.append(self.github.token)
        if self.telegram is not None:
            values += (self.telegram.token, self.telegram.chat_id)
        if self.smtp is not None:
            values += (self.smtp.password, self.smtp.sender, *self.smtp.recipients)
        return tuple(values)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def read_settings(notifications: Notifications, environ: Mapping[str, str]) -> ChannelSettings:
    """
    Read the variables of the channels listed, and only those (RF-03; ADR-10).

    Each value is stripped of surrounding whitespace, and an empty value counts as missing. The
    problems come in the order of :data:`VARIABLES`, all of them at once.

    Args:
        notifications (Notifications): The validated table.
        environ (Mapping[str, str]): The environment, ``os.environ`` outside the tests.

    Returns:
        ChannelSettings: The settings of each channel listed.

    Raises:
        ConfigError: With every missing, empty or malformed variable, placed at its name.
        ValueError: When ``smtp`` is listed without its server, which validation refuses.
    """
    reader = _Reader(environ)
    github = telegram = smtp = None
    if Channel.GITHUB in notifications.channels:
        github_token = reader.read(GITHUB_TOKEN, _is_token, "env.invalid.token")
        repository = reader.read(GITHUB_REPOSITORY, _REPOSITORY.fullmatch, "env.invalid.repository")
        github = GitHubSettings(github_token, repository)
    if Channel.TELEGRAM in notifications.channels:
        telegram_token = reader.read(
            TELEGRAM_TOKEN, _TELEGRAM_TOKEN.fullmatch, "env.invalid.telegram_token"
        )
        chat_id = reader.read(TELEGRAM_CHAT_ID, _CHAT_ID.fullmatch, "env.invalid.chat_id")
        telegram = TelegramSettings(telegram_token, chat_id)
    if Channel.SMTP in notifications.channels:
        if notifications.smtp is None:
            raise ValueError("the smtp channel is listed without its [notifications.smtp] table")
        username = reader.read(SMTP_USERNAME, _is_text, "env.invalid.text")
        password = reader.read(SMTP_PASSWORD, _is_text, "env.invalid.text")
        sender = reader.read(SMTP_FROM, _is_address, "env.invalid.address")
        recipients = reader.read(SMTP_TO, _is_address_list, "env.invalid.addresses")
        smtp = SmtpSettings(
            notifications.smtp,
            username,
            password,
            sender,
            tuple(part.strip() for part in recipients.split(",")),
        )
    if reader.problems:
        raise ConfigError(reader.problems)
    return ChannelSettings(github, telegram, smtp)


class _Reader:
    """
    Reads variables one by one and keeps the problems, so every one is reported at once.

    Attributes:
        problems (list[Problem]): In the order the variables were read.
    """

    def __init__(self, environ: Mapping[str, str]) -> None:
        """
        Keep the environment to read from.

        Args:
            environ (Mapping[str, str]): The environment.
        """
        self._environ = environ
        self.problems: list[Problem] = []

    def read(self, name: str, accepts: Callable[[str], object], invalid: str) -> str:
        """
        Read one variable and judge its form.

        Args:
            name (str): The variable.
            accepts (Callable[[str], object]): Truthy for a value of the right form.
            invalid (str): The catalogue key of a value of the wrong form.

        Returns:
            str: The stripped value; on a problem, an empty string, never used since the
            settings are not built.
        """
        value = (self._environ.get(name) or "").strip()
        if not value:
            self.problems.append(Problem(name, "env.missing"))
            return ""
        if not accepts(value):
            # The value stays out of the problem: it may be a real token (RNF-03).
            self.problems.append(Problem(name, invalid))
            return ""
        return value


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


def _has_control(value: str) -> bool:
    """
    Tell whether a value holds a control character (C0, DEL or C1).

    Args:
        value (str): The stripped value.

    Returns:
        bool: ``True`` when one is found; a line break would let a value start a new header.
    """
    return any(unicodedata.category(char) == "Cc" for char in value)


def _is_text(value: str) -> bool:
    """
    Accept a user name or a password: anything without a control character.

    Args:
        value (str): The stripped value.

    Returns:
        bool: Whether the value has the form.
    """
    return not _has_control(value)


def _is_token(value: str) -> bool:
    """
    Accept a token that goes in a header: no whitespace and no control character.

    Args:
        value (str): The stripped value.

    Returns:
        bool: Whether the value has the form.
    """
    return not _has_control(value) and not any(char.isspace() for char in value)


def _is_address(value: str) -> bool:
    """
    Accept one bare address, ``local@domain``; a display name is refused (RF-03).

    No character that RFC 5322 gives a meaning to in a header is allowed, so no value can carry a
    second address or a header of its own.

    Args:
        value (str): The value, or one item of a list, stripped.

    Returns:
        bool: Whether the value has the form.
    """
    local, at, domain = value.partition("@")
    if not at or not local or not domain or "@" in domain:
        return False
    if _has_control(value) or any(char.isspace() for char in value):
        return False
    return not _ADDRESS_SPECIALS.intersection(value)


def _is_address_list(value: str) -> bool:
    """
    Accept one or more bare addresses separated by commas, with whitespace around each.

    Args:
        value (str): The stripped value.

    Returns:
        bool: Whether every item has the form; an empty item (``a@b,,c@d``) does not.
    """
    return all(_is_address(part.strip()) for part in value.split(","))


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class Redactor:
    """
    Replace every form of the private values in a text with :data:`REDACTED` (RNF-03, ADR-7).

    Each value is replaced as written and percent-encoded (the Telegram token sits in the path of
    a URL, and an address in a query), the longest first, so a value that holds another is
    replaced whole and not around the shorter one.
    """

    def __init__(self, values: Iterable[str]) -> None:
        """
        Compile one pattern for every form of every value.

        Args:
            values (Iterable[str]): The values, usually :meth:`ChannelSettings.private_values`;
                an empty value is ignored, since it would match everywhere.
        """
        forms = {form for value in values if value for form in (value, quote(value, safe=""))}
        # At a position, re tries the alternatives in order: the longest first wins.
        ordered = sorted(forms, key=len, reverse=True)
        self._pattern = re.compile("|".join(map(re.escape, ordered))) if ordered else None

    def __call__(self, text: str) -> str:
        """
        Clean one text.

        Args:
            text (str): Any text about to be written.

        Returns:
            str: The text with every form of every value replaced.
        """
        if self._pattern is None:
            return text
        return self._pattern.sub(REDACTED, text)
