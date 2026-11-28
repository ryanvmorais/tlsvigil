"""
Tests for ``tlsvigil check --notify`` end to end (spec 005, RF-01, RF-07, RF-12, RNF-03).

:func:`~tlsvigil.cli.main` runs in-process with the seams of ADR-9: the environment is a
dictionary, the GitHub and Telegram fakes answer on the local HTTPS server, and the SMTP server of
the file is a :class:`~smtp_server.LocalSMTPServer` on ``127.0.0.1``. The checks are replaced by
reports built by hand (``reports.py``), so each run says exactly what was found, and the state
file is the default one in the test's own ``tmp_path``. Several runs in a row show what a channel
hears once, again at a reminder, and never twice.
"""

from __future__ import annotations

import json
import ssl
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import pytest

import reports
from fake_apis import (
    CHAT_ID,
    REPOSITORY,
    TELEGRAM_TOKEN,
    TOKEN,
    FakeGitHub,
    FakeTelegram,
    github_answer,
    telegram_answer,
)
from http_server import LocalHTTPServer
from smtp_server import PASSWORD, USERNAME, LocalSMTPServer
from tlsvigil import cli, environment
from tlsvigil.alerts import findings, plan
from tlsvigil.cli import EXIT_ATTENTION, EXIT_DELIVERY, EXIT_INTERNAL, EXIT_OK, EXIT_USAGE, main
from tlsvigil.config import load_config
from tlsvigil.delivery import DeliveryOutcome
from tlsvigil.environment import read_settings
from tlsvigil.i18n import Lang, translate
from tlsvigil.messages import telegram_text
from tlsvigil.notify import notify
from tlsvigil.runner import Report
from tlsvigil.state import DEFAULT_STATE
from tlsvigil.telegram import TelegramChannel

SENDER = "tlsvigil@example.com"
RECIPIENTS = ("first@example.org", "second@example.org")

# Every variable of the three channels, with the values the fakes accept.
ENVIRONMENT: Mapping[str, str] = {
    environment.GITHUB_TOKEN: TOKEN,
    environment.GITHUB_REPOSITORY: REPOSITORY,
    environment.TELEGRAM_TOKEN: TELEGRAM_TOKEN,
    environment.TELEGRAM_CHAT_ID: CHAT_ID,
    environment.SMTP_USERNAME: USERNAME,
    environment.SMTP_PASSWORD: PASSWORD,
    environment.SMTP_FROM: SENDER,
    environment.SMTP_TO: ", ".join(RECIPIENTS),
}

# The values no output may show, in both of the forms the Redactor knows.
PRIVATE = [TOKEN, TELEGRAM_TOKEN, CHAT_ID, PASSWORD, SENDER, *RECIPIENTS]
PRIVATE_FORMS = sorted({form for value in PRIVATE for form in (value, quote(value, safe=""))})

ALL_CHANNELS = ("github", "telegram", "smtp")

# A certificate under its threshold of 14 days, then at the reminder of 7, then renewed.
WARNING = reports.report(reports.verified("example.com", days_left=9))
REMINDER = reports.report(reports.verified("example.com", days_left=6))
RENEWED = reports.report(reports.verified("example.com", days_left=89))
# A host that cannot be reached: `error`, told from its second run in a row.
UNREACHABLE = reports.report(reports.unreachable("typo.example.com"))


# ---------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Result:
    """
    What one run of the command gave.

    Attributes:
        code (int): The exit code.
        out (str): The standard output.
        err (str): The standard error.
    """

    code: int
    out: str
    err: str

    def deliveries(self) -> list[str]:
        """
        Give the delivery lines of a text report: the lines after the summary.

        Returns:
            list[str]: One line per channel, in the order of the file. Every report here has
                items, so the summary is the line after the blank one.
        """
        lines = self.out.splitlines()
        return lines[lines.index("") + 2 :]


@dataclass
class Harness:
    """
    The command, the fakes and the directory of one test.

    Attributes:
        directory (Path): The current directory of the runs, with ``tlsvigil.toml``.
        github (FakeGitHub): The issues of the repository.
        telegram (FakeTelegram): The bot.
        smtp (LocalSMTPServer): The mail server the file names.
        context (ssl.SSLContext): The client context that trusts the test CA.
        api (str): The root of both fake APIs.
        environ (dict[str, str]): The environment of the runs.
        runs (int): How many times the checks ran.
    """

    directory: Path
    github: FakeGitHub
    telegram: FakeTelegram
    smtp: LocalSMTPServer
    context: ssl.SSLContext
    api: str
    monkeypatch: pytest.MonkeyPatch
    capsys: pytest.CaptureFixture[str]
    environ: dict[str, str] = field(default_factory=lambda: dict(ENVIRONMENT))
    runs: int = 0

    @property
    def state(self) -> Path:
        """
        Give the default state file of the runs.

        Returns:
            Path: ``.tlsvigil-state.json`` in the directory.
        """
        return self.directory / DEFAULT_STATE

    def configure(
        self, channels: tuple[str, ...] | None = ALL_CHANNELS, *, lang: str | None = None
    ) -> Path:
        """
        Write ``tlsvigil.toml``: one host, and the channels given with the SMTP server.

        Args:
            channels (tuple[str, ...] | None): The ``channels`` list; ``None`` writes no
                ``[notifications]`` table at all.
            lang (str | None): The file's ``lang``, if any.

        Returns:
            Path: The file.
        """
        lines = [f'lang = "{lang}"'] if lang is not None else []
        lines += ["[certificates]", 'hosts = ["example.com"]']
        if channels is not None:
            listed = ", ".join(f'"{channel}"' for channel in channels)
            lines += [
                "[notifications]",
                f"channels = [{listed}]",
                "[notifications.smtp]",
                f'host = "{self.smtp.host}"',
                f"port = {self.smtp.port}",
                'security = "starttls"',
            ]
        path = self.directory / "tlsvigil.toml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def check(self, report: Report | Callable[[], Report], *args: str) -> Result:
        """
        Run ``tlsvigil check`` with the checks replaced by a report.

        Args:
            report (Report | Callable[[], Report]): What the checks find, or a function that
                gives it (or raises, for a crash).
            *args (str): The arguments after ``check``.

        Returns:
            Result: The exit code and both streams.
        """

        def _run(*_: object, **__: object) -> Report:
            self.runs += 1
            return report() if callable(report) else report

        self.monkeypatch.setattr(cli, "run", _run)
        self.capsys.readouterr()
        code = main(
            ["check", *args],
            context=self.context,
            environ=self.environ,
            github_api_url=self.api,
            telegram_api_url=self.api,
        )
        captured = self.capsys.readouterr()
        return Result(code, captured.out, captured.err)


@pytest.fixture
def harness(
    tmp_path: Path,
    https_server: LocalHTTPServer,
    https_context: ssl.SSLContext,
    client_context: ssl.SSLContext,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> Iterator[Harness]:
    """
    Give the command with the three fakes running and ``tmp_path`` as the current directory.

    Args:
        tmp_path (Path): The directory of the test.
        https_server (LocalHTTPServer): Serves both fake APIs.
        https_context (ssl.SSLContext): The server context, with a leaf for ``127.0.0.1``.
        client_context (ssl.SSLContext): The client context that trusts the test CA.
        monkeypatch (pytest.MonkeyPatch): Replaces the checks and the directory.
        capsys (pytest.CaptureFixture[str]): Captures both streams.

    Yields:
        Harness: The harness, with all three channels in ``tlsvigil.toml``.
    """
    monkeypatch.chdir(tmp_path)
    github, telegram = FakeGitHub(), FakeTelegram()
    api = github.install(https_server)
    telegram.install(https_server)
    with LocalSMTPServer(https_context) as smtp:
        harness = Harness(
            tmp_path, github, telegram, smtp, client_context, api, monkeypatch, capsys
        )
        harness.configure()
        yield harness
    assert github.problems == []
    assert telegram.problems == []


def _assert_nothing_private(*texts: str) -> None:
    """
    Assert that no private value, in any form, is in the texts.

    Args:
        *texts (str): The streams, the document, the state file.
    """
    for text in texts:
        for form in PRIVATE_FORMS:
            assert form not in text


# ---------------------------------------------------------------------------
# Without --notify (RF-01)
# ---------------------------------------------------------------------------


def test_without_notify_no_state_is_read_or_written_and_nothing_is_sent(
    harness: Harness,
) -> None:
    harness.state.write_text("not json", encoding="utf-8")

    result = harness.check(WARNING)

    assert result.code == EXIT_ATTENTION
    assert result.err == ""
    assert result.deliveries() == []
    assert harness.state.read_text(encoding="utf-8") == "not json"
    assert harness.github.operations == []
    assert harness.telegram.requests == 0
    assert harness.smtp.commands == []

    # The control: the same run with --notify reads the file, says why it is ignored, sends
    # to every channel and writes the state.
    notified = harness.check(WARNING, "--notify")

    assert notified.code == EXIT_ATTENTION
    assert "is not valid; this run counts as the first" in notified.err
    assert notified.deliveries() == ["github: sent (issue #1)", "telegram: sent", "smtp: sent"]
    assert json.loads(harness.state.read_text(encoding="utf-8"))["version"] == 1


def test_without_notify_the_json_has_no_notifications_key(harness: Harness) -> None:
    plain = json.loads(harness.check(WARNING, "--json").out)
    notified = json.loads(harness.check(WARNING, "--json", "--notify").out)

    assert "notifications" not in plain
    assert [entry["outcome"] for entry in notified["notifications"]] == ["sent"] * 3


# ---------------------------------------------------------------------------
# Usage and configuration errors (RF-01, RF-03)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "flag"),
    [
        (("--notify", "--host", "example.com"), "--notify"),
        (("--notify", "--domain", "example.com"), "--notify"),
        (("--state", "other.json"), "--state"),
    ],
    ids=["notify-with-host", "notify-with-domain", "state-without-notify"],
)
def test_a_flag_that_does_not_fit_is_a_usage_error(
    harness: Harness, args: tuple[str, ...], flag: str
) -> None:
    with pytest.raises(SystemExit) as raised:
        harness.check(WARNING, *args)

    assert raised.value.code == EXIT_USAGE
    assert f"argument {flag}:" in harness.capsys.readouterr().err
    assert harness.runs == 0


@pytest.mark.parametrize(
    "channels", [None, ()], ids=["no-notifications-table", "no-channel-listed"]
)
def test_notify_with_no_channel_is_a_configuration_error(
    harness: Harness, channels: tuple[str, ...] | None
) -> None:
    harness.configure(channels)

    result = harness.check(WARNING, "--notify")

    assert result.code == EXIT_USAGE
    assert "the configuration tlsvigil.toml cannot be used" in result.err
    assert "  notifications.channels: --notify needs at least one channel here" in result.err
    assert harness.runs == 0

    # The control: without --notify, the same file is fine.
    assert harness.check(WARNING).code == EXIT_ATTENTION
    assert harness.runs == 1


def test_a_missing_variable_stops_the_run_before_any_check(harness: Harness) -> None:
    del harness.environ[environment.TELEGRAM_TOKEN]
    harness.environ[environment.TELEGRAM_CHAT_ID] = "not a chat"

    result = harness.check(WARNING, "--notify")

    assert result.code == EXIT_USAGE
    assert result.err.splitlines() == [
        "Nothing was checked: the environment variables of the notifications cannot be used.",
        "  TLSVIGIL_TELEGRAM_TOKEN: the environment variable is not set, or is empty",
        "  TLSVIGIL_TELEGRAM_CHAT_ID: " + translate(Lang.EN, "env.invalid.chat_id"),
    ]
    assert "not a chat" not in result.err
    assert harness.runs == 0
    assert harness.github.operations == []
    assert harness.smtp.commands == []


def test_a_variable_of_a_channel_not_listed_is_not_read(harness: Harness) -> None:
    harness.configure(("telegram",))
    del harness.environ[environment.GITHUB_TOKEN]
    del harness.environ[environment.SMTP_PASSWORD]

    result = harness.check(WARNING, "--notify")

    assert result.code == EXIT_ATTENTION
    assert result.deliveries() == ["telegram: sent"]
    # No request to a channel not listed; the listed one got its message.
    assert harness.github.operations == []
    assert harness.smtp.commands == []
    assert len(harness.telegram.messages) == 1


# ---------------------------------------------------------------------------
# Runs in a row (RF-05 to RF-07)
# ---------------------------------------------------------------------------


def test_a_warning_is_told_once_reminded_and_then_resolved_and_closed(harness: Harness) -> None:
    first = harness.check(WARNING, "--notify")
    again = harness.check(WARNING, "--notify")
    reminded = harness.check(REMINDER, "--notify")
    renewed = harness.check(RENEWED, "--notify")

    assert first.code == again.code == reminded.code == EXIT_ATTENTION
    assert renewed.code == EXIT_OK
    assert first.deliveries() == ["github: sent (issue #1)", "telegram: sent", "smtp: sent"]
    assert again.deliveries() == [
        "github: nothing new (issue #1)",
        "telegram: nothing new",
        "smtp: nothing new",
    ]
    assert reminded.deliveries() == first.deliveries()
    assert renewed.deliveries() == first.deliveries()

    issue = harness.github.issues[1]
    assert list(harness.github.issues) == [1]
    assert len(issue.comments) == 2  # the reminder, then the end
    assert (issue.state, issue.state_reason) == ("closed", "completed")
    assert len(harness.telegram.messages) == 3
    assert len(harness.smtp.messages) == 3
    assert harness.runs == 4

    saved = json.loads(harness.state.read_text(encoding="utf-8"))
    assert saved["channels"]["github"]["issue"] is None
    assert all(part["told"] == {} for part in saved["channels"].values())


def test_an_error_is_held_on_its_first_run_and_told_on_the_second(harness: Harness) -> None:
    harness.configure(("telegram",))

    first = harness.check(UNREACHABLE, "--notify")
    second = harness.check(UNREACHABLE, "--notify")

    assert first.code == second.code == EXIT_ATTENTION
    assert first.deliveries() == ["telegram: nothing new"]
    assert second.deliveries() == ["telegram: sent"]
    assert len(harness.telegram.messages) == 1
    assert json.loads(harness.state.read_text(encoding="utf-8"))["error_runs"] == {
        "certificate:typo.example.com:443": 2
    }


def test_a_failed_channel_gives_exit_four_and_hears_it_on_the_next_run(harness: Harness) -> None:
    harness.telegram.refuse("token")

    failed = harness.check(WARNING, "--notify")

    assert failed.code == EXIT_DELIVERY
    github, telegram, smtp = failed.deliveries()
    assert (github, smtp) == ("github: sent (issue #1)", "smtp: sent")
    assert telegram.startswith("telegram: failed: ")
    assert harness.telegram.messages == []

    harness.telegram.answer = None
    retried = harness.check(WARNING, "--notify")

    assert retried.code == EXIT_ATTENTION
    assert retried.deliveries() == [
        "github: nothing new (issue #1)",
        "telegram: sent",
        "smtp: nothing new",
    ]
    # The channels that succeeded are not told twice.
    assert harness.github.issues[1].comments == []
    assert len(harness.smtp.messages) == 1
    assert len(harness.telegram.messages) == 1


def test_exit_four_even_when_the_checks_found_nothing(harness: Harness) -> None:
    assert harness.check(WARNING, "--notify").code == EXIT_ATTENTION
    harness.telegram.refuse("rate")

    resolved = harness.check(RENEWED, "--notify")

    # Everything is fine now, but the end of the warning did not reach the chat.
    assert resolved.code == EXIT_DELIVERY
    assert resolved.deliveries()[1].startswith("telegram: failed: ")

    harness.telegram.answer = None
    assert harness.check(RENEWED, "--notify").code == EXIT_OK
    assert len(harness.telegram.messages) == 2


def test_the_json_carries_the_exit_code_four_and_each_outcome(harness: Harness) -> None:
    harness.telegram.refuse("chat")

    result = harness.check(WARNING, "--notify", "--json")
    document = json.loads(result.out)

    assert result.code == document["exit_code"] == EXIT_DELIVERY
    assert [
        (entry["channel"], entry["outcome"], entry["issue"]) for entry in document["notifications"]
    ] == [("github", "sent", 1), ("telegram", "failed", None), ("smtp", "sent", None)]
    assert document["notifications"][1]["message"].startswith("Telegram refused")


@pytest.mark.parametrize(
    ("lang", "sent", "nothing_new"),
    [(Lang.EN, "sent", "nothing new"), (Lang.PT_BR, "enviado", "nada de novo")],
    ids=["en", "pt-BR"],
)
def test_both_languages(harness: Harness, lang: Lang, sent: str, nothing_new: str) -> None:
    harness.configure(("telegram",), lang=str(lang))

    first = harness.check(WARNING, "--notify")
    again = harness.check(WARNING, "--notify")

    assert first.deliveries() == [f"telegram: {sent}"]
    assert again.deliveries() == [f"telegram: {nothing_new}"]
    answers, _ = findings(WARNING, {})
    expected = telegram_text(plan({}, answers), WARNING.started_at, lang)
    assert harness.telegram.messages[0]["text"] == expected


# ---------------------------------------------------------------------------
# The state file (RF-07)
# ---------------------------------------------------------------------------


def test_a_state_file_that_cannot_be_written_is_a_warning_with_the_exit_code_unchanged(
    harness: Harness,
) -> None:
    missing = harness.directory / "no-such-directory" / "state.json"

    result = harness.check(WARNING, "--notify", "--state", str(missing))

    assert result.code == EXIT_ATTENTION
    assert result.deliveries() == ["github: sent (issue #1)", "telegram: sent", "smtp: sent"]
    assert result.err == (
        f"The state file {missing} cannot be written; the next run may repeat a notification.\n"
    )
    assert not harness.state.exists()


def test_state_chooses_the_file(harness: Harness) -> None:
    chosen = harness.directory / "chosen.json"

    harness.check(WARNING, "--notify", "--state", str(chosen))
    again = harness.check(WARNING, "--notify", "--state", str(chosen))

    assert again.deliveries()[1:] == ["telegram: nothing new", "smtp: nothing new"]
    assert chosen.exists()
    assert not harness.state.exists()


def test_a_bug_in_a_channel_is_exit_three_after_the_channels_before_it_are_saved(
    harness: Harness,
) -> None:
    deliver = TelegramChannel.deliver
    calls = []

    def broken(self: TelegramChannel, *args: object, **kwargs: object) -> object:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("a planted bug in the channel")
        # The plan, the part, the moment and the language pass through as objects, which
        # mypy cannot match to the typed parameters of the real method.
        return deliver(self, *args, **kwargs)  # type: ignore[arg-type]

    harness.monkeypatch.setattr(TelegramChannel, "deliver", broken)

    crashed = harness.check(WARNING, "--notify")

    assert crashed.code == EXIT_INTERNAL
    assert "RuntimeError: a planted bug in the channel" in crashed.err
    saved = json.loads(harness.state.read_text(encoding="utf-8"))
    assert list(saved["channels"]) == ["github"]

    fixed = harness.check(WARNING, "--notify")

    assert fixed.code == EXIT_ATTENTION
    assert fixed.deliveries() == [
        "github: nothing new (issue #1)",
        "telegram: sent",
        "smtp: sent",
    ]
    assert harness.github.issues[1].comments == []


# ---------------------------------------------------------------------------
# No secret in any output (RNF-03; ADR-7)
# ---------------------------------------------------------------------------


def _echo_secrets(harness: Harness) -> None:
    """
    Make every channel fail with an answer that quotes a secret or an address.

    Args:
        harness (Harness): The fakes to change.
    """
    harness.github.answers["list"] = github_answer(500, f"server error near {TOKEN}")
    harness.telegram.answer = telegram_answer(401, f"Unauthorized {TELEGRAM_TOKEN} {CHAT_ID}")
    harness.smtp.refuse = frozenset({SENDER})


def test_each_channel_echoes_a_secret_in_its_raw_message(harness: Harness) -> None:
    # The premise of the next test: the channels' own messages do hold the values.
    _echo_secrets(harness)
    config = load_config(harness.directory / "tlsvigil.toml")
    assert config.notifications is not None
    settings = read_settings(config.notifications, harness.environ)

    deliveries = notify(
        WARNING,
        config,
        settings,
        harness.directory / "raw.json",
        lang=Lang.EN,
        warn=lambda line: None,
        context=harness.context,
        github_api_url=harness.api,
        telegram_api_url=harness.api,
    )

    assert [delivery.outcome for delivery in deliveries] == [DeliveryOutcome.FAILED] * 3
    github, telegram, smtp = (delivery.message or "" for delivery in deliveries)
    assert TOKEN in github
    assert TELEGRAM_TOKEN in telegram
    assert CHAT_ID in telegram
    assert SENDER in smtp


@pytest.mark.parametrize("json_flag", [(), ("--json",)], ids=["text", "json"])
def test_no_secret_reaches_a_stream_or_the_state_file(
    harness: Harness, json_flag: tuple[str, ...]
) -> None:
    _echo_secrets(harness)

    result = harness.check(WARNING, "--notify", *json_flag)

    assert result.code == EXIT_DELIVERY
    assert environment.REDACTED in result.out
    _assert_nothing_private(result.out, result.err, harness.state.read_text(encoding="utf-8"))


def test_no_secret_reaches_the_traceback_of_a_crash(harness: Harness) -> None:
    error = RuntimeError(
        f"planted crash: {TOKEN} {quote(TELEGRAM_TOKEN, safe='')} {SENDER} {PASSWORD}"
    )
    # The premise: the raw text of the exception holds the values.
    assert all(value in str(error) for value in (TOKEN, SENDER, PASSWORD))

    def crash() -> Report:
        raise error

    result = harness.check(crash, "--notify")

    assert result.code == EXIT_INTERNAL
    assert "Traceback (most recent call last)" in result.err
    assert "RuntimeError: planted crash: *** *** *** ***" in result.err
    _assert_nothing_private(result.out, result.err)
