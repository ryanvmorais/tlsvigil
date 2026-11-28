"""
Tests for the channels' settings from the environment and the Redactor (spec 005, RF-03, RNF-03).

The environment is a plain mapping handed to :func:`~tlsvigil.environment.read_settings`, never
``os.environ``, so no variable of the machine running the tests is read.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from tlsvigil import config as config_module
from tlsvigil import environment as environment_module
from tlsvigil.config import Channel, ConfigError, Notifications, SmtpSecurity, SmtpServer
from tlsvigil.environment import (
    REDACTED,
    VARIABLES,
    ChannelSettings,
    GitHubSettings,
    Redactor,
    SmtpSettings,
    TelegramSettings,
    read_settings,
)
from tlsvigil.i18n import MESSAGES, Lang, translate

SERVER = SmtpServer("smtp.example.com", 587, SmtpSecurity.STARTTLS)
EVERY_CHANNEL = Notifications((Channel.GITHUB, Channel.TELEGRAM, Channel.SMTP), SERVER)

# One valid value for each variable, as a workflow would pass them.
GOOD: Mapping[str, str] = {
    "GITHUB_TOKEN": "ghs_16C7e42F292c6912E7710c838347Ae178B4a",
    "GITHUB_REPOSITORY": "octo-org/my.site_v2",
    "TLSVIGIL_TELEGRAM_TOKEN": "123456789:AAE-bot_Token-Value",
    "TLSVIGIL_TELEGRAM_CHAT_ID": "-1001234567890",
    "TLSVIGIL_SMTP_USERNAME": "alerts@example.com",
    "TLSVIGIL_SMTP_PASSWORD": "pass word with spaces",
    "TLSVIGIL_SMTP_FROM": "alerts@example.com",
    "TLSVIGIL_SMTP_TO": "ops@example.com, owner@example.org",
}

EVERY_VARIABLE = [name for names in VARIABLES.values() for name in names]


class RecordingEnviron(Mapping[str, str]):
    """
    An environment that remembers which variables were read.

    Attributes:
        read (list[str]): Every name looked up, in order.
    """

    def __init__(self, values: Mapping[str, str]) -> None:
        """
        Keep the values.

        Args:
            values (Mapping[str, str]): The variables.
        """
        self._values = dict(values)
        self.read: list[str] = []

    def __getitem__(self, name: str) -> str:
        """
        Record the name and give its value.

        Args:
            name (str): The variable.

        Returns:
            str: Its value.
        """
        self.read.append(name)
        return self._values[name]

    def __iter__(self) -> Iterator[str]:
        """
        Iterate over the names; ``read_settings`` must not, so it fails the test.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError("the environment was listed, not read by name")

    def __len__(self) -> int:
        """
        Give the number of variables.

        Returns:
            int: How many there are.
        """
        return len(self._values)


def _with(**changes: str | None) -> dict[str, str]:
    """
    Give the good environment with some variables changed or removed.

    Args:
        **changes (str | None): The new value of each variable, ``None`` to remove it.

    Returns:
        dict[str, str]: The environment.
    """
    environ = dict(GOOD)
    for name, value in changes.items():
        if value is None:
            environ.pop(name, None)
        else:
            environ[name] = value
    return environ


def _problems(environ: Mapping[str, str]) -> list[tuple[str, str]]:
    """
    Read every channel from an environment that must be refused.

    Args:
        environ (Mapping[str, str]): The environment.

    Returns:
        list[tuple[str, str]]: The place and the key of each problem, after checking that no
        problem has parameters and that each key is in the catalogue.
    """
    with pytest.raises(ConfigError) as caught:
        read_settings(EVERY_CHANNEL, environ)
    for problem in caught.value.problems:
        assert problem.message in MESSAGES, problem
        assert problem.params == {}, problem
    return [(problem.place, problem.message) for problem in caught.value.problems]


# ---------------------------------------------------------------------------
# A good environment
# ---------------------------------------------------------------------------


def test_every_channel_is_read_from_a_good_environment() -> None:
    assert read_settings(EVERY_CHANNEL, GOOD) == ChannelSettings(
        github=GitHubSettings("ghs_16C7e42F292c6912E7710c838347Ae178B4a", "octo-org/my.site_v2"),
        telegram=TelegramSettings("123456789:AAE-bot_Token-Value", "-1001234567890"),
        smtp=SmtpSettings(
            SERVER,
            "alerts@example.com",
            "pass word with spaces",
            "alerts@example.com",
            ("ops@example.com", "owner@example.org"),
        ),
    )


def test_surrounding_whitespace_is_stripped() -> None:
    environ = _with(
        GITHUB_TOKEN="  ghs_abc\n",
        TLSVIGIL_TELEGRAM_CHAT_ID="\t@my_channel ",
        TLSVIGIL_SMTP_TO=" ops@example.com ,owner@example.org\n",
    )
    settings = read_settings(EVERY_CHANNEL, environ)
    assert settings.github is not None and settings.github.token == "ghs_abc"
    assert settings.telegram is not None and settings.telegram.chat_id == "@my_channel"
    assert settings.smtp is not None
    assert settings.smtp.recipients == ("ops@example.com", "owner@example.org")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GITHUB_REPOSITORY", "a/b"),
        ("GITHUB_REPOSITORY", "Octo-Org/.github"),
        ("TLSVIGIL_TELEGRAM_TOKEN", "1:a"),
        ("TLSVIGIL_TELEGRAM_CHAT_ID", "42"),
        ("TLSVIGIL_TELEGRAM_CHAT_ID", "@abcde"),
        ("TLSVIGIL_TELEGRAM_CHAT_ID", "@" + "a" * 32),
        ("TLSVIGIL_SMTP_PASSWORD", "p@ss:wörd;<>"),
        ("TLSVIGIL_SMTP_FROM", "first.last+tag@mail.example.com"),
        ("TLSVIGIL_SMTP_TO", "one@example.com"),
    ],
)
def test_each_form_accepts_its_edge(name: str, value: str) -> None:
    """Controls for the malformed values below: each form takes its smallest and odd values."""
    read_settings(EVERY_CHANNEL, _with(**{name: value}))


def test_a_listed_channel_alone_gets_its_settings() -> None:
    settings = read_settings(Notifications((Channel.TELEGRAM,), None), GOOD)
    assert settings.github is None and settings.smtp is None
    assert settings.telegram == TelegramSettings("123456789:AAE-bot_Token-Value", "-1001234567890")


# ---------------------------------------------------------------------------
# Missing and malformed variables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", EVERY_VARIABLE)
@pytest.mark.parametrize("value", [None, "", "   \t\n"], ids=["missing", "empty", "blank"])
def test_a_missing_empty_or_blank_variable_is_named(name: str, value: str | None) -> None:
    assert _problems(_with(**{name: value})) == [(name, "env.missing")]


@pytest.mark.parametrize(
    ("name", "value", "key"),
    [
        ("GITHUB_TOKEN", "ghs_abc def", "env.invalid.token"),
        ("GITHUB_TOKEN", "ghs_abc\r\nX-Injected: 1", "env.invalid.token"),
        ("GITHUB_REPOSITORY", "my-site", "env.invalid.repository"),
        ("GITHUB_REPOSITORY", "octo-org/site/extra", "env.invalid.repository"),
        ("GITHUB_REPOSITORY", "octo_org/site", "env.invalid.repository"),
        ("TLSVIGIL_TELEGRAM_TOKEN", "123456789AAEbotToken", "env.invalid.telegram_token"),
        ("TLSVIGIL_TELEGRAM_TOKEN", "bot:AAE", "env.invalid.telegram_token"),
        ("TLSVIGIL_TELEGRAM_TOKEN", "123:AAE/../x", "env.invalid.telegram_token"),
        ("TLSVIGIL_TELEGRAM_CHAT_ID", "12ab34", "env.invalid.chat_id"),
        ("TLSVIGIL_TELEGRAM_CHAT_ID", "@abcd", "env.invalid.chat_id"),
        ("TLSVIGIL_TELEGRAM_CHAT_ID", "@" + "a" * 33, "env.invalid.chat_id"),
        ("TLSVIGIL_TELEGRAM_CHAT_ID", "--5", "env.invalid.chat_id"),
        ("TLSVIGIL_SMTP_USERNAME", "user\x00name", "env.invalid.text"),
        ("TLSVIGIL_SMTP_PASSWORD", "pass\nword", "env.invalid.text"),
        ("TLSVIGIL_SMTP_PASSWORD", "pass\x85word", "env.invalid.text"),
        ("TLSVIGIL_SMTP_FROM", "alerts.example.com", "env.invalid.address"),
        ("TLSVIGIL_SMTP_FROM", "alerts@@example.com", "env.invalid.address"),
        ("TLSVIGIL_SMTP_FROM", "a@b@example.com", "env.invalid.address"),
        ("TLSVIGIL_SMTP_FROM", "@example.com", "env.invalid.address"),
        ("TLSVIGIL_SMTP_FROM", "alerts@", "env.invalid.address"),
        ("TLSVIGIL_SMTP_FROM", "Alerts <alerts@example.com>", "env.invalid.address"),
        ("TLSVIGIL_SMTP_FROM", "alerts,boss@example.com", "env.invalid.address"),
        ("TLSVIGIL_SMTP_FROM", "alerts@example.com, boss@example.com", "env.invalid.address"),
        ("TLSVIGIL_SMTP_FROM", "al erts@example.com", "env.invalid.address"),
        ("TLSVIGIL_SMTP_FROM", "alerts@example.com\nBcc: x@y.z", "env.invalid.address"),
        ("TLSVIGIL_SMTP_TO", "ops@example.com,", "env.invalid.addresses"),
        ("TLSVIGIL_SMTP_TO", "ops@example.com,,owner@example.org", "env.invalid.addresses"),
        ("TLSVIGIL_SMTP_TO", "ops@example.com, Owner <o@example.org>", "env.invalid.addresses"),
        ("TLSVIGIL_SMTP_TO", "ops@example.com; owner@example.org", "env.invalid.addresses"),
        ("TLSVIGIL_SMTP_TO", "ops@example.com, owner", "env.invalid.addresses"),
        ("TLSVIGIL_SMTP_TO", "a@b@example.com", "env.invalid.addresses"),
    ],
)
def test_a_malformed_variable_is_named_and_its_value_never_shown(
    name: str, value: str, key: str
) -> None:
    assert _problems(_with(**{name: value})) == [(name, key)]
    for lang in Lang:
        assert value.strip() not in translate(lang, key)


def test_every_problem_is_reported_at_once_in_the_order_of_the_table() -> None:
    assert _problems({}) == [(name, "env.missing") for name in EVERY_VARIABLE]
    assert EVERY_VARIABLE == [
        "GITHUB_TOKEN",
        "GITHUB_REPOSITORY",
        "TLSVIGIL_TELEGRAM_TOKEN",
        "TLSVIGIL_TELEGRAM_CHAT_ID",
        "TLSVIGIL_SMTP_USERNAME",
        "TLSVIGIL_SMTP_PASSWORD",
        "TLSVIGIL_SMTP_FROM",
        "TLSVIGIL_SMTP_TO",
    ]


def test_missing_and_malformed_variables_come_together() -> None:
    environ = _with(GITHUB_REPOSITORY="site", TLSVIGIL_TELEGRAM_TOKEN=None)
    assert _problems(environ) == [
        ("GITHUB_REPOSITORY", "env.invalid.repository"),
        ("TLSVIGIL_TELEGRAM_TOKEN", "env.missing"),
    ]


# ---------------------------------------------------------------------------
# Only the channels listed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", list(Channel))
def test_only_the_variables_of_the_listed_channel_are_read(channel: Channel) -> None:
    environ = RecordingEnviron(GOOD)
    server = SERVER if channel is Channel.SMTP else None
    read_settings(Notifications((channel,), server), environ)
    # The listed channel was read (the control), and nothing else was.
    assert environ.read == list(VARIABLES[channel])


def test_a_bad_variable_of_a_channel_not_listed_is_never_judged() -> None:
    environ = _with(TLSVIGIL_TELEGRAM_TOKEN="not a token", TLSVIGIL_SMTP_TO="nobody")
    settings = read_settings(Notifications((Channel.GITHUB,), None), environ)
    assert settings.github is not None
    assert (settings.telegram, settings.smtp) == (None, None)


def test_no_channel_reads_nothing() -> None:
    environ = RecordingEnviron(GOOD)
    assert read_settings(Notifications((), SERVER), environ) == ChannelSettings(None, None, None)
    assert environ.read == []


def test_smtp_listed_without_its_server_is_a_bug_not_a_problem() -> None:
    with pytest.raises(ValueError, match="smtp"):
        read_settings(Notifications((Channel.SMTP,), None), GOOD)


# ---------------------------------------------------------------------------
# The private values and the Redactor
# ---------------------------------------------------------------------------


def test_the_private_values_are_the_tokens_the_password_the_chat_and_the_addresses() -> None:
    settings = read_settings(EVERY_CHANNEL, GOOD)
    assert settings.private_values() == (
        GOOD["GITHUB_TOKEN"],
        GOOD["TLSVIGIL_TELEGRAM_TOKEN"],
        GOOD["TLSVIGIL_TELEGRAM_CHAT_ID"],
        GOOD["TLSVIGIL_SMTP_PASSWORD"],
        "alerts@example.com",
        "ops@example.com",
        "owner@example.org",
    )
    assert ChannelSettings(None, None, None).private_values() == ()


def test_the_redactor_replaces_every_value() -> None:
    redact = Redactor(["s3cret", "ops@example.com"])
    text = "login s3cret failed; mail to ops@example.com refused (s3cret)"
    assert redact(text) == f"login {REDACTED} failed; mail to {REDACTED} refused ({REDACTED})"


def test_the_redactor_replaces_a_token_percent_encoded_in_a_url() -> None:
    token = GOOD["TLSVIGIL_TELEGRAM_TOKEN"]
    url = "https://api.telegram.org/bot123456789%3AAAE-bot_Token-Value/sendMessage"
    # The encoded form really differs from the token, so only that form can match here.
    assert token not in url
    redacted = Redactor([token])(f"cannot reach {url}")
    assert redacted == f"cannot reach https://api.telegram.org/bot{REDACTED}/sendMessage"


def test_the_redactor_replaces_an_address_percent_encoded_in_a_query() -> None:
    redacted = Redactor(["ops@example.com"])("GET /?to=ops%40example.com")
    assert redacted == f"GET /?to={REDACTED}"


def test_the_redactor_replaces_the_longer_of_two_values_whole() -> None:
    """One value inside another: replaced shortest first, the rest of the longer would remain."""
    redact = Redactor(["abc", "abcdef-123"])
    assert redact("x abcdef-123 y abc z") == f"x {REDACTED} y {REDACTED} z"


def test_the_redactor_reads_a_value_as_text_not_as_a_pattern() -> None:
    redact = Redactor(["a.c", "(x|y)"])
    assert redact("abc a.c x (x|y)") == f"abc {REDACTED} x {REDACTED}"


def test_a_redactor_without_values_changes_nothing() -> None:
    assert Redactor([])("nothing to hide") == "nothing to hide"
    assert Redactor([""])("an empty value matches nowhere") == "an empty value matches nowhere"


def test_the_redactor_hides_every_private_value_of_the_settings() -> None:
    settings = read_settings(EVERY_CHANNEL, GOOD)
    text = " | ".join(settings.private_values())
    redacted = Redactor(settings.private_values())(text)
    assert all(value not in redacted for value in settings.private_values())


# ---------------------------------------------------------------------------
# Agreement with the configuration and the catalogue
# ---------------------------------------------------------------------------


def test_every_variable_the_configuration_names_is_read_here() -> None:
    named = {
        variable
        for variables in (
            *config_module._SECRET_KEYS.values(),
            *config_module._SECRET_TABLES.values(),
        )
        for variable in variables
    }
    assert named == set(EVERY_VARIABLE)


def test_every_key_the_module_writes_is_in_the_catalogue() -> None:
    source = Path(environment_module.__file__).read_text(encoding="utf-8")
    keys = set(re.findall(r'"(env\.[a-z_.]+)"', source))
    assert len(keys) >= 8  # the regular expression found them
    assert keys <= set(MESSAGES)
