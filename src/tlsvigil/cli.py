"""
TLSVigil command line: the ``tlsvigil`` entry point (spec 004).

Built on :mod:`argparse` so the package keeps its zero-dependency promise (spec 001, ADR-1).
``tlsvigil check`` loads the configuration (the file or the ``--host`` and ``--domain`` flags),
runs the checks, writes the report as text or JSON and turns it into an exit code: ``0`` when
nothing needs attention, ``1`` when something does, ``2`` when nothing could be checked, and
``3`` for a bug (ADR-3). With ``--notify`` it also reads the channels' settings from the
environment, delivers the result (spec 005) and exits ``4`` when a delivery failed; from the
moment the settings exist, every text it writes passes through the
:class:`~tlsvigil.environment.Redactor` (spec 005, ADR-7).

:func:`main` takes the argument list and returns the exit code instead of exiting, which lets
the tests drive it in-process. Its keyword-only arguments are the test seams (ADR-7): no flag and
no environment variable can point a run at another server.
"""

from __future__ import annotations

import argparse
import os
import ssl
import sys
import traceback
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Final

from tlsvigil import __version__, github, rdap, telegram
from tlsvigil.config import (
    DEFAULT_CONFIG,
    Config,
    ConfigError,
    Problem,
    config_from_names,
    load_config,
)
from tlsvigil.delivery import Delivery, DeliveryOutcome
from tlsvigil.environment import ChannelSettings, Redactor, read_settings
from tlsvigil.i18n import Lang, parse_lang, translate
from tlsvigil.notify import notify
from tlsvigil.output import printable, render_json, render_text
from tlsvigil.runner import run
from tlsvigil.state import DEFAULT_STATE

__all__ = [
    "EXIT_ATTENTION",
    "EXIT_DELIVERY",
    "EXIT_INTERNAL",
    "EXIT_OK",
    "EXIT_USAGE",
    "build_parser",
    "main",
]

# ---------------------------------------------------------------------------
# Exit codes (RF-09)
# ---------------------------------------------------------------------------

# Every result is `ok` or `unknown`.
EXIT_OK: Final = 0
# At least one result is `expiring`, `invalid` or `error`.
EXIT_ATTENTION: Final = 1
# A usage error, a configuration error or nothing to check: no check ran. argparse uses it too.
EXIT_USAGE: Final = 2
# A bug: neither 0 nor 1, so a script never reads a crash as a result (ADR-3).
EXIT_INTERNAL: Final = 3
# With --notify, a delivery failed, whatever the checks found (spec 005, RF-12).
EXIT_DELIVERY: Final = 4

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _lang(text: str) -> Lang:
    """
    Read the value of ``--lang``, without regard to case.

    Args:
        text (str): The value given, such as ``pt-BR`` or ``PT-br``.

    Returns:
        Lang: The language.

    Raises:
        argparse.ArgumentTypeError: For any other value; argparse then exits ``2``.
    """
    lang = parse_lang(text)
    if lang is None:
        raise argparse.ArgumentTypeError(f"invalid language {text!r} (choose from 'en', 'pt-BR')")
    return lang


def _parsers() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    """
    Build the main parser and the parser of ``check``.

    Returns:
        tuple[argparse.ArgumentParser, argparse.ArgumentParser]: The ``tlsvigil`` parser, and
            the ``check`` parser it holds, which :func:`main` needs to report the clash of
            ``--config`` with the names in the usage of ``check``.
    """
    parser = argparse.ArgumentParser(
        prog="tlsvigil",
        description="Warn before a TLS certificate or a domain expires.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"tlsvigil {__version__}",
    )
    commands = parser.add_subparsers(dest="command", title="commands", metavar="COMMAND")

    check = commands.add_parser(
        "check",
        help="check the certificates and domains of the configuration",
        description=(
            "Check every host and domain of tlsvigil.toml (or of --config), or only the names "
            "given with --host and --domain. Exit codes: 0 when nothing needs attention, 1 "
            "when something expires soon, is invalid or could not be checked, 2 when nothing "
            "was checked (a usage or configuration error), 3 for an internal error, 4 when a "
            "notification could not be delivered (with --notify, whatever the checks found)."
        ),
    )
    check.add_argument(
        "--config",
        type=Path,
        metavar="PATH",
        help=f"the configuration file (default: {DEFAULT_CONFIG} in the current directory)",
    )
    check.add_argument(
        "--host",
        action="append",
        default=[],
        metavar="HOST[:PORT]",
        help="check the certificate of this host instead of the file; repeat for more",
    )
    check.add_argument(
        "--domain",
        action="append",
        default=[],
        metavar="NAME",
        help="check the expiry of this domain instead of the file; repeat for more",
    )
    check.add_argument("--json", action="store_true", help="write the report as JSON")
    check.add_argument(
        "--lang",
        type=_lang,
        metavar="{en,pt-BR}",
        help="the language of the output (default: the file's lang, else en)",
    )
    check.add_argument(
        "--notify",
        action="store_true",
        help="deliver the result to the channels of the file's [notifications] table",
    )
    check.add_argument(
        "--state",
        type=Path,
        metavar="PATH",
        help=f"the state file of --notify (default: {DEFAULT_STATE} in the current directory)",
    )
    return parser, check


def build_parser() -> argparse.ArgumentParser:
    """
    Build the argument parser for the ``tlsvigil`` command.

    Returns:
        argparse.ArgumentParser: The parser, with ``--version`` wired to the installed package
            version and the ``check`` sub-command.
    """
    return _parsers()[0]


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------


def _escape_unencodable() -> None:
    """
    Make the standard streams escape a character they cannot encode, instead of failing (RNF-03).

    A redirected output on Windows may use a legacy code page; a name or an issuer outside it
    then arrives as its Python escape and the run keeps its exit code. A stream without
    ``reconfigure`` (one a caller put in place) is left as it is.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


def _identity(text: str) -> str:
    """
    Leave a text as it is: the redaction before the settings of the channels exist.

    Args:
        text (str): Any text about to be written.

    Returns:
        str: The same text.
    """
    return text


def _write_report(text: str) -> None:
    """
    Write the report to the standard output, even when the reader has gone.

    ``tlsvigil check | head -1`` closes the pipe before the last line; as the Python
    documentation advises, the standard output then points at :data:`os.devnull`, so the flush
    at exit does not fail again.

    Args:
        text (str): The rendered report.
    """
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)


# ---------------------------------------------------------------------------
# Configuration errors
# ---------------------------------------------------------------------------


def _write_problems(
    error: ConfigError,
    lang: Lang,
    config: Path | None,
    names: bool,
    *,
    environment: bool = False,
) -> None:
    """
    Write every problem of the configuration to the standard error (RF-02, RF-05).

    Args:
        error (ConfigError): The problems, in the order of the file.
        lang (Lang): The language of the messages.
        config (Path | None): The ``--config`` value; ``None`` for the default file.
        names (bool): Whether the names came from ``--host`` and ``--domain``.
        environment (bool): Whether the problems are the variables of the channels (spec 005,
            RF-03); each is placed at the variable's name and none holds its value.
    """
    if environment:
        lines = [translate(lang, "cli.environment_heading")]
    elif names:
        lines = [translate(lang, "cli.names_heading")]
    else:
        path = printable(str(config if config is not None else DEFAULT_CONFIG))
        lines = [translate(lang, "cli.file_heading", path=path)]

    for problem in error.problems:
        # The place and the values repeat the file as it was written: escaped, like any
        # text from the outside (ADR-4).
        params = {key: printable(value) for key, value in problem.params.items()}
        sentence = translate(lang, problem.message, **params)
        lines.append(
            f"  {printable(problem.place)}: {sentence}" if problem.place else f"  {sentence}"
        )

    # Only the default file gets the hint: a path given by hand was meant to exist.
    if (
        config is None
        and not names
        and not environment
        and error.problems[0].message == "config.missing"
    ):
        lines.append(translate(lang, "cli.missing_hint", path=DEFAULT_CONFIG))

    sys.stderr.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _load(args: argparse.Namespace) -> Config:
    """
    Build the configuration from the names, or else from the file (RF-02, RF-03).

    Args:
        args (argparse.Namespace): The parsed ``check`` arguments.

    Returns:
        Config: The validated configuration; with ``--notify``, one that lists a channel.

    Raises:
        ConfigError: With every problem found.
    """
    if args.host or args.domain:
        # The names replace the file: it is not read, even when it exists (RF-03).
        return config_from_names(args.host, args.domain)
    config = load_config(args.config if args.config is not None else Path(DEFAULT_CONFIG))
    if args.notify and (config.notifications is None or not config.notifications.channels):
        # Checked after the file validates, so a file with other problems lists those first.
        problem = Problem("notifications.channels", "config.no_channel")
        raise ConfigError([problem], config.lang)
    return config


def _exit_code(attention: bool, deliveries: Sequence[Delivery]) -> int:
    """
    Turn the result of a run into the exit code (RF-09; spec 005, RF-12).

    Args:
        attention (bool): Whether a result needs attention.
        deliveries (Sequence[Delivery]): The deliveries of ``--notify``; empty without it.

    Returns:
        int: :data:`EXIT_DELIVERY` when a delivery failed, else :data:`EXIT_ATTENTION` or
            :data:`EXIT_OK`.
    """
    if any(delivery.outcome is DeliveryOutcome.FAILED for delivery in deliveries):
        return EXIT_DELIVERY
    return EXIT_ATTENTION if attention else EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    context: ssl.SSLContext | None = None,
    bootstrap_url: str = rdap.BOOTSTRAP_URL,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
    github_api_url: str = github.API_URL,
    telegram_api_url: str = telegram.API_URL,
) -> int:
    """
    Run the command line.

    Args:
        argv (Sequence[str] | None): The arguments without the program name; ``None`` reads
            them from :data:`sys.argv`.
        context (ssl.SSLContext | None): The client context of every connection; ``None``
            means the default one. Only the tests pass it (ADR-7).
        bootstrap_url (str): The URL of the IANA bootstrap. Only the tests change it (ADR-7).
        now (datetime | None): The time to count the days left from; ``None`` means the clock.
            Only the tests pass it (ADR-7).
        environ (Mapping[str, str] | None): Where ``--notify`` reads the channels' variables;
            ``None`` means :data:`os.environ`. Only the tests pass it (spec 005, ADR-9).
        github_api_url (str): The root of the GitHub REST API. Only the tests change it.
        telegram_api_url (str): The root of the Telegram Bot API. Only the tests change it.

    Returns:
        int: :data:`EXIT_OK`, :data:`EXIT_ATTENTION`, :data:`EXIT_USAGE`,
            :data:`EXIT_INTERNAL` or :data:`EXIT_DELIVERY`.

    Raises:
        SystemExit: From :mod:`argparse`, for ``--version``, ``--help`` and invalid arguments,
            with code ``2`` for the last.
    """
    _escape_unencodable()
    parser, check = _parsers()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_usage(sys.stderr)
        return EXIT_USAGE

    names = bool(args.host or args.domain)
    if args.config is not None and names:
        # Not a mutually exclusive group: argparse would then refuse --host with --domain too.
        check.error("argument --host/--domain: not allowed with argument --config")
    if args.notify and names:
        # The names carry no [notifications] table, so there is nothing to notify (RF-01).
        check.error("argument --notify: not allowed with argument --host/--domain")
    if args.state is not None and not args.notify:
        check.error("argument --state: only allowed with argument --notify")

    lang: Lang = args.lang or Lang.EN
    # Every text is written through it; it knows the secrets once the settings are read.
    redact: Callable[[str], str] = _identity
    try:
        try:
            config = _load(args)
        except ConfigError as error:
            # The flag wins; the file's lang only when that key itself was valid (RF-08).
            lang = args.lang or error.lang or Lang.EN
            _write_problems(error, lang, args.config, names)
            return EXIT_USAGE

        lang = args.lang or config.lang or Lang.EN
        settings: ChannelSettings | None = None
        if args.notify and config.notifications is not None:
            try:
                settings = read_settings(
                    config.notifications, os.environ if environ is None else environ
                )
            except ConfigError as error:
                _write_problems(error, lang, args.config, names, environment=True)
                return EXIT_USAGE
            redact = Redactor(settings.private_values())

        report = run(config, context=context, bootstrap_url=bootstrap_url, now=now)
        deliveries: tuple[Delivery, ...] = ()
        if settings is not None:

            def warn(line: str) -> None:
                """
                Write a line about the state file, cleaned like every other text (ADR-7).

                Args:
                    line (str): The translated line; its path comes from the command line.
                """
                sys.stderr.write(redact(line) + "\n")

            deliveries = notify(
                report,
                config,
                settings,
                args.state if args.state is not None else Path(DEFAULT_STATE),
                lang=lang,
                warn=warn,
                context=context,
                github_api_url=github_api_url,
                telegram_api_url=telegram_api_url,
            )
        exit_code = _exit_code(report.needs_attention(), deliveries)
        if args.json:
            text = render_json(report, exit_code, deliveries)
        else:
            text = render_text(report, lang, deliveries)
    except Exception:
        # A bug: one line a person can read, and the traceback for the report (ADR-3), which
        # may quote a URL or a header, so it is formatted first and redacted (spec 005, ADR-7).
        sys.stderr.write(translate(lang, "cli.internal_error") + "\n")
        sys.stderr.write(redact(traceback.format_exc()))
        return EXIT_INTERNAL

    _write_report(redact(text))
    return exit_code
