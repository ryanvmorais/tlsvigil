"""
The configuration of a run: the TOML file or the names on the command line (spec 004).

:func:`load_config` reads ``tlsvigil.toml`` (or the file given) and :func:`config_from_names`
takes the ``--host`` and ``--domain`` flags; both give one validated :class:`Config` or raise one
:class:`ConfigError` that lists every problem found, each with its place, before any check runs
(RF-05). A rule of a host or a domain is never written here: the configuration calls the
validators of the checks and maps the code of their exception to a sentence (ADR-2).

The ``[notifications]`` table names the channels and the SMTP server (spec 005, RF-02). What is
secret or personal comes from the environment instead (:mod:`tlsvigil.environment`), so a key that
looks like one is a problem that names the variable to use, never repeating the value.

A :class:`Problem` holds a key of the language catalogue, not a sentence, so the command writes it
in the language of the run. The values it repeats are the text of the file, unescaped; the output
escapes them (ADR-4).
"""

from __future__ import annotations

import ipaddress
import math
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final

from tlsvigil.certificate import HostProblem, InvalidHost, validate_host
from tlsvigil.i18n import Lang
from tlsvigil.rdap import InvalidDomain, normalize_domain

__all__ = [
    "DEFAULT_CERTIFICATE_WARN_DAYS",
    "DEFAULT_CONFIG",
    "DEFAULT_DOMAIN_WARN_DAYS",
    "DEFAULT_SMTP_PORTS",
    "DEFAULT_TIMEOUT",
    "MAX_CONFIG_BYTES",
    "Channel",
    "Config",
    "ConfigError",
    "DomainEntry",
    "HostEntry",
    "Notifications",
    "Problem",
    "SmtpSecurity",
    "SmtpServer",
    "config_from_names",
    "load_config",
    "toml_position",
    "validate",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Read from the current directory only: no search in parents, the home or a variable (RF-02).
DEFAULT_CONFIG: Final = "tlsvigil.toml"
# A thousand hosts take about 40 KiB; a wrong path (a log, a disk image) is not read whole (ADR-6).
MAX_CONFIG_BYTES: Final = 256 * 1024
# The timeout default of the two checks (specs 002 and 003), so a flag run and a file agree.
DEFAULT_TIMEOUT: Final = 10.0
# A renewal by automation happens well before 14 days are left; a domain renewal may need a
# payment or a person, so it gets a month (requirements, resolved decision 2).
DEFAULT_CERTIFICATE_WARN_DAYS: Final = 14
DEFAULT_DOMAIN_WARN_DAYS: Final = 30

# The HTTPS port, the one a host written without a port means (ADR-8).
_DEFAULT_PORT: Final = 443
# The keys each table may hold (RF-04); anything else is a problem, so a typo is never ignored
# in silence (requirements, resolved decision 5).
_TOP_KEYS: Final = frozenset({"lang", "timeout", "certificates", "domains", "notifications"})
_CERTIFICATE_KEYS: Final = frozenset({"warn_days", "hosts"})
_DOMAIN_KEYS: Final = frozenset({"warn_days", "names"})
_HOST_ENTRY_KEYS: Final = frozenset({"host", "port", "warn_days"})
_DOMAIN_ENTRY_KEYS: Final = frozenset({"name", "warn_days"})
_NOTIFICATION_KEYS: Final = frozenset({"channels", "smtp"})
_SMTP_KEYS: Final = frozenset({"host", "port", "security"})
# The keys a person may write to put a secret or an address in the file, each with the variables
# that hold it instead (spec 005, RF-02, RF-03), in [notifications] and in [notifications.smtp]
# alike: RF-02 says anywhere in the table. The names are those of tlsvigil.environment (ADR-10);
# tests/test_environment.py checks that each one is read there.
_SECRET_KEYS: Final[Mapping[str, tuple[str, ...]]] = {
    "token": ("GITHUB_TOKEN", "TLSVIGIL_TELEGRAM_TOKEN"),
    "chat_id": ("TLSVIGIL_TELEGRAM_CHAT_ID",),
    "chat": ("TLSVIGIL_TELEGRAM_CHAT_ID",),
    "username": ("TLSVIGIL_SMTP_USERNAME",),
    "user": ("TLSVIGIL_SMTP_USERNAME",),
    "password": ("TLSVIGIL_SMTP_PASSWORD",),
    "from": ("TLSVIGIL_SMTP_FROM",),
    "sender": ("TLSVIGIL_SMTP_FROM",),
    "to": ("TLSVIGIL_SMTP_TO",),
    "recipients": ("TLSVIGIL_SMTP_TO",),
}
# A [notifications.github] or [notifications.telegram] table is where a person would put a
# token; written as anything other than a table, the key is just unknown.
_SECRET_TABLES: Final[Mapping[str, tuple[str, ...]]] = {
    "github": ("GITHUB_TOKEN", "GITHUB_REPOSITORY"),
    "telegram": ("TLSVIGIL_TELEGRAM_TOKEN", "TLSVIGIL_TELEGRAM_CHAT_ID"),
}
# ASCII digits only, at most five: int() would also take "٤٤٣", "+443" and a 5000-digit string.
_PORT_TEXT: Final = re.compile(r"[0-9]{1,5}")
# The suffix tomllib gives every syntax error before Python 3.14 (and still gives after).
_TOML_SUFFIX: Final = re.compile(r" \(at (?:line (\d+), column (\d+)|end of document)\)\Z")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class Channel(StrEnum):
    """
    A way to tell someone about a warning (spec 005, RF-02).

    Attributes:
        GITHUB (str): One issue in the repository the workflow runs in.
        TELEGRAM (str): A message from a bot to a chat.
        SMTP (str): An e-mail through an SMTP server.
    """

    GITHUB = "github"
    TELEGRAM = "telegram"
    SMTP = "smtp"


class SmtpSecurity(StrEnum):
    """
    How the connection to the SMTP server becomes TLS, with no plain-text mode (spec 005, RF-10).

    Attributes:
        STARTTLS (str): A plain connection upgraded by ``STARTTLS``, the default.
        TLS (str): TLS from the first byte.
    """

    STARTTLS = "starttls"
    TLS = "tls"


# The submission ports of RFC 6409 and RFC 8314, one for each way to reach TLS.
DEFAULT_SMTP_PORTS: Final[Mapping[SmtpSecurity, int]] = {
    SmtpSecurity.STARTTLS: 587,
    SmtpSecurity.TLS: 465,
}


@dataclass(frozen=True, slots=True)
class SmtpServer:
    """
    The server of the ``smtp`` channel; who signs in and who gets the e-mail come from the
    environment (spec 005, RF-03).

    Attributes:
        host (str): A name or an address that :func:`~tlsvigil.certificate.validate_host` accepts.
        port (int): The TCP port, by default the one of :data:`DEFAULT_SMTP_PORTS`.
        security (SmtpSecurity): How the connection becomes TLS.
    """

    host: str
    port: int
    security: SmtpSecurity


@dataclass(frozen=True, slots=True)
class Notifications:
    """
    The ``[notifications]`` table (spec 005, RF-02).

    Attributes:
        channels (tuple[Channel, ...]): In the order of the file, none twice; empty when the table
            lists none.
        smtp (SmtpServer | None): The ``[notifications.smtp]`` table, kept even when ``smtp`` is
            not listed, so a channel is turned off by removing it from the list.
    """

    channels: tuple[Channel, ...]
    smtp: SmtpServer | None


@dataclass(frozen=True, slots=True)
class HostEntry:
    """
    A host whose certificate is checked.

    Attributes:
        host (str): The name or the address as given, brackets removed from an IPv6 address.
        port (int): The TCP port, ``443`` unless given.
        warn_days (int): The entry's own threshold, or the table's.
    """

    host: str
    port: int
    warn_days: int


@dataclass(frozen=True, slots=True)
class DomainEntry:
    """
    A domain whose registration is checked.

    Attributes:
        domain (str): The name in the form the check uses: lowercase, no trailing dot (RF-05).
        warn_days (int): The entry's own threshold, or the table's.
    """

    domain: str
    warn_days: int


@dataclass(frozen=True, slots=True)
class Config:
    """
    A validated configuration: everything a run needs (RF-04).

    Attributes:
        lang (Lang | None): The file's language, ``None`` when it does not set one (RF-08).
        timeout (float): Seconds for each TLS connection and each RDAP request.
        hosts (tuple[HostEntry, ...]): In the order of the file or of the flags.
        domains (tuple[DomainEntry, ...]): In the order of the file or of the flags.
        notifications (Notifications | None): The ``[notifications]`` table, ``None`` when the
            file has none or the names come from the flags (spec 005).
    """

    lang: Lang | None
    timeout: float
    hosts: tuple[HostEntry, ...]
    domains: tuple[DomainEntry, ...]
    notifications: Notifications | None = None


@dataclass(frozen=True, slots=True)
class Problem:
    """
    One thing wrong with the configuration, to be written in the language of the run (RF-05).

    Attributes:
        place (str): Where it is, such as ``certificates.hosts[2].port`` or ``--host[1]``
            (entries count from 1); empty for the file itself.
        message (str): A key of :data:`tlsvigil.i18n.MESSAGES`.
        params (Mapping[str, str]): The values the sentence names, as plain text.
    """

    place: str
    message: str
    params: Mapping[str, str] = field(default_factory=dict)


class ConfigError(Exception):
    """
    The configuration cannot be used; no check may run (RF-02, RF-05).

    Attributes:
        problems (tuple[Problem, ...]): Every problem found, in the order of the file.
        lang (Lang | None): The file's ``lang`` when it was valid, for writing the problems.
    """

    def __init__(self, problems: Sequence[Problem], lang: Lang | None = None) -> None:
        """
        Keep every problem and the language they may be written in.

        Args:
            problems (Sequence[Problem]): At least one problem, in order.
            lang (Lang | None): The file's valid ``lang``, if any.
        """
        super().__init__(f"the configuration has {len(problems)} problem(s)")
        self.problems = tuple(problems)
        self.lang = lang


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_config(path: Path) -> Config:
    """
    Read, parse and validate a configuration file (RF-02; ADR-6).

    At most :data:`MAX_CONFIG_BYTES` are read; the bytes are decoded as UTF-8 with an optional
    byte order mark and parsed by :mod:`tomllib`, which runs no code (RNF-04).

    Args:
        path (Path): The file, ``tlsvigil.toml`` or the one given by ``--config``.

    Returns:
        Config: The validated configuration.

    Raises:
        ConfigError: When the file cannot be read, decoded or parsed, or holds any problem.
    """
    shown = str(path)
    try:
        with path.open("rb") as file:
            # One byte over the cap tells a file at the cap from a larger one.
            data = file.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        raise ConfigError([Problem("", "config.missing", {"path": shown})]) from None
    except IsADirectoryError:
        raise ConfigError([Problem("", "config.directory", {"path": shown})]) from None
    except PermissionError:
        # Windows refuses to open a directory with a PermissionError, not IsADirectoryError.
        key = "config.directory" if path.is_dir() else "config.permission"
        raise ConfigError([Problem("", key, {"path": shown})]) from None
    except OSError as error:
        detail = error.strerror or str(error)
        raise ConfigError(
            [Problem("", "config.unreadable", {"path": shown, "detail": detail})]
        ) from None

    if len(data) > MAX_CONFIG_BYTES:
        limit = str(MAX_CONFIG_BYTES // 1024)
        raise ConfigError([Problem("", "config.too_large", {"path": shown, "limit": limit})])

    try:
        # utf-8-sig drops the byte order mark some Windows editors still write (ADR-6).
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ConfigError([Problem("", "config.not_utf8", {"path": shown})]) from None

    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        line, column, detail = toml_position(
            str(error), text, getattr(error, "lineno", None), getattr(error, "colno", None)
        )
        params = {"line": str(line), "column": str(column), "detail": detail}
        raise ConfigError([Problem("", "config.toml_syntax", params)]) from None

    return validate(document)


def toml_position(
    message: str, text: str, lineno: int | None, colno: int | None
) -> tuple[int, int, str]:
    """
    Give the line, the column and the parser's sentence of a TOML syntax error.

    Python 3.14 and newer give the position as ``lineno`` and ``colno``; before, it is only in the
    message, and "end of document" has to be counted from the text. The two give the same answer
    (RNF-06), and taking the attributes as arguments lets both paths run on every version.

    Args:
        message (str): ``str()`` of the :class:`tomllib.TOMLDecodeError`.
        text (str): The text that was parsed.
        lineno (int | None): The error's ``lineno``, ``None`` before Python 3.14.
        colno (int | None): The error's ``colno``, ``None`` before Python 3.14.

    Returns:
        tuple[int, int, str]: The line and the column, counted from 1, and the message without
        its position suffix.
    """
    match = _TOML_SUFFIX.search(message)
    detail = message[: match.start()] if match else message
    if lineno is not None and colno is not None:
        return lineno, colno, detail
    if match and match.group(1):
        return int(match.group(1)), int(match.group(2)), detail
    # "end of document", counted as tomllib 3.14 counts it: the position after the last character.
    end = len(text)
    line = text.count("\n") + 1
    column = end + 1 if line == 1 else end - text.rindex("\n")
    return line, column, detail


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Collector:
    """
    What validation gathers while it walks: the problems and the entries seen so far.

    Attributes:
        problems (list[Problem]): In the order they were found.
        hosts (dict[tuple[str, int], str]): The duplicate key of each host, and its place.
        domains (dict[str, str]): Each normalized domain, and its place.
    """

    problems: list[Problem] = field(default_factory=list)
    hosts: dict[tuple[str, int], str] = field(default_factory=dict)
    domains: dict[str, str] = field(default_factory=dict)

    def add(self, place: str, message: str, **params: str) -> None:
        """
        Record a problem.

        Args:
            place (str): Where it is.
            message (str): The catalogue key.
            **params (str): The values the sentence names.
        """
        self.problems.append(Problem(place, message, params))


class _Refused(Exception):
    """A host string that breaks the syntax of ADR-8; the argument is the catalogue key."""


def validate(document: Mapping[str, object]) -> Config:
    """
    Check a parsed file against the shape of RF-04, collecting every problem (RF-05).

    The keys are walked in the order of the file, so the problems come in that order; validation
    never stops at the first one.

    Args:
        document (Mapping[str, object]): What :func:`tomllib.loads` gave.

    Returns:
        Config: The configuration, when nothing is wrong.

    Raises:
        ConfigError: With every problem found, and the file's ``lang`` when it was valid.
    """
    found = _Collector()
    lang: Lang | None = None
    timeout = DEFAULT_TIMEOUT
    hosts: list[HostEntry] = []
    domains: list[DomainEntry] = []
    notifications: Notifications | None = None
    listed = 0

    for key, value in document.items():
        if key not in _TOP_KEYS:
            found.add(key, "config.unknown_key")
        elif key == "lang":
            lang = _lang(value)
            if lang is None:
                found.add("lang", "config.lang")
        elif key == "timeout":
            seconds = _timeout(value)
            if seconds is None:
                found.add("timeout", "config.timeout")
            else:
                timeout = seconds
        elif key == "certificates":
            listed += _certificates(value, found, hosts)
        elif key == "domains":
            listed += _domains(value, found, domains)
        else:
            notifications = _notifications(value, found)

    # Counted as written, valid or not (a table or a list of the wrong type counts as one): a
    # file whose only entry is wrong has that problem, and "nothing to check" would only add
    # noise.
    if listed == 0:
        found.add("", "config.nothing_to_check")
    if found.problems:
        raise ConfigError(found.problems, lang)
    return Config(lang, timeout, tuple(hosts), tuple(domains), notifications)


def config_from_names(hosts: Sequence[str], domains: Sequence[str]) -> Config:
    """
    Build the configuration of the ``--host`` and ``--domain`` flags (RF-03).

    The names get the defaults of RF-04 and the same validation as the entries of a file, with
    places such as ``--host[1]``.

    Args:
        hosts (Sequence[str]): Each ``--host`` value, ``host``, ``host:port`` or
            ``[IPv6]:port``.
        domains (Sequence[str]): Each ``--domain`` value.

    Returns:
        Config: The configuration, with no language of its own.

    Raises:
        ConfigError: With every problem found.
    """
    found = _Collector()
    host_entries: list[HostEntry] = []
    for index, text in enumerate(hosts, start=1):
        host = _host_string(text, f"--host[{index}]", DEFAULT_CERTIFICATE_WARN_DAYS, found)
        if host is not None:
            host_entries.append(host)
    domain_entries: list[DomainEntry] = []
    for index, name in enumerate(domains, start=1):
        domain = _domain_name(name, f"--domain[{index}]", DEFAULT_DOMAIN_WARN_DAYS, found)
        if domain is not None:
            domain_entries.append(domain)
    if not hosts and not domains:
        found.add("", "config.nothing_to_check")
    if found.problems:
        raise ConfigError(found.problems)
    return Config(None, DEFAULT_TIMEOUT, tuple(host_entries), tuple(domain_entries), None)


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def _lang(value: object) -> Lang | None:
    """
    Read ``lang`` without regard to letter case (RF-08).

    Args:
        value (object): The value of the key.

    Returns:
        Lang | None: The language, or ``None`` when the value is not one of the two.
    """
    if type(value) is not str:
        return None
    return next((lang for lang in Lang if lang.value.lower() == value.lower()), None)


def _timeout(value: object) -> float | None:
    """
    Read ``timeout``: a finite number of seconds greater than ``0``.

    Args:
        value (object): The value of the key.

    Returns:
        float | None: The seconds, or ``None`` for anything else; TOML allows ``inf`` and
        ``nan``, and a ``bool`` is refused although it is an ``int``.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _warn_days(value: object, place: str, found: _Collector) -> int | None:
    """
    Read a ``warn_days``: an integer, ``0`` or more.

    Args:
        value (object): The value of the key.
        place (str): Where it is.
        found (_Collector): Where a problem goes.

    Returns:
        int | None: The threshold, or ``None`` after recording the problem.
    """
    if type(value) is int and value >= 0:
        return value
    found.add(place, "config.warn_days")
    return None


def _table_warn_days(table: Mapping[str, object], default: int) -> int:
    """
    Read a table's ``warn_days`` silently, for the entries to inherit.

    The problem of a bad value is recorded where the key is walked, in the order of the file;
    here a bad value just falls back to the default, since the run will not happen anyway.

    Args:
        table (Mapping[str, object]): The ``[certificates]`` or the ``[domains]`` table.
        default (int): The default threshold of that table.

    Returns:
        int: The table's threshold, or the default.
    """
    value = table.get("warn_days", default)
    return value if type(value) is int and value >= 0 else default


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def _certificates(value: object, found: _Collector, hosts: list[HostEntry]) -> int:
    """
    Walk the ``[certificates]`` table, appending each valid host.

    Args:
        value (object): The value of the key.
        found (_Collector): Where problems go.
        hosts (list[HostEntry]): Where valid entries go.

    Returns:
        int: How many entries the table lists, valid or not.
    """
    if not isinstance(value, dict):
        found.add("certificates", "config.not_a_table")
        return 1
    default = _table_warn_days(value, DEFAULT_CERTIFICATE_WARN_DAYS)
    listed = 0
    for key, item in value.items():
        place = f"certificates.{key}"
        if key not in _CERTIFICATE_KEYS:
            found.add(place, "config.unknown_key")
        elif key == "warn_days":
            _warn_days(item, place, found)
        elif not isinstance(item, list):
            found.add(place, "config.not_a_list")
            listed += 1
        else:
            listed += len(item)
            for index, entry in enumerate(item, start=1):
                host = _host_entry(entry, f"{place}[{index}]", default, found)
                if host is not None:
                    hosts.append(host)
    return listed


def _domains(value: object, found: _Collector, domains: list[DomainEntry]) -> int:
    """
    Walk the ``[domains]`` table, appending each valid domain.

    Args:
        value (object): The value of the key.
        found (_Collector): Where problems go.
        domains (list[DomainEntry]): Where valid entries go.

    Returns:
        int: How many entries the table lists, valid or not.
    """
    if not isinstance(value, dict):
        found.add("domains", "config.not_a_table")
        return 1
    default = _table_warn_days(value, DEFAULT_DOMAIN_WARN_DAYS)
    listed = 0
    for key, item in value.items():
        place = f"domains.{key}"
        if key not in _DOMAIN_KEYS:
            found.add(place, "config.unknown_key")
        elif key == "warn_days":
            _warn_days(item, place, found)
        elif not isinstance(item, list):
            found.add(place, "config.not_a_list")
            listed += 1
        else:
            listed += len(item)
            for index, entry in enumerate(item, start=1):
                domain = _domain_entry(entry, f"{place}[{index}]", default, found)
                if domain is not None:
                    domains.append(domain)
    return listed


def _notifications(value: object, found: _Collector) -> Notifications | None:
    """
    Walk the ``[notifications]`` table (spec 005, RF-02).

    A channel listed without what it needs in the file (``smtp`` without its table) is a problem;
    a table whose channel is not listed is kept, so removing a name from ``channels`` is enough to
    turn a channel off.

    Args:
        value (object): The value of the key.
        found (_Collector): Where problems go.

    Returns:
        Notifications | None: The table, or ``None`` when it is not a table; meaningful only when
        no problem was found.
    """
    if not isinstance(value, dict):
        found.add("notifications", "config.not_a_table")
        return None
    channels: tuple[Channel, ...] = ()
    smtp: SmtpServer | None = None
    smtp_written = False
    for key, item in value.items():
        place = f"notifications.{key}"
        if key not in _NOTIFICATION_KEYS:
            _unknown_key("notifications", key, item, found)
        elif key == "channels":
            channels = _channels(item, place, found)
        else:
            smtp_written = True
            smtp = _smtp_server(item, place, found)
    if Channel.SMTP in channels and not smtp_written:
        found.add("notifications.smtp", "config.smtp_table")
    return Notifications(channels, smtp)


def _channels(value: object, place: str, found: _Collector) -> tuple[Channel, ...]:
    """
    Read ``channels``: a list of known channels, none twice.

    Args:
        value (object): The value of the key.
        place (str): ``notifications.channels``.
        found (_Collector): Where problems go.

    Returns:
        tuple[Channel, ...]: The valid channels, in order.
    """
    if not isinstance(value, list):
        found.add(place, "config.not_a_list")
        return ()
    channels: list[Channel] = []
    seen: dict[Channel, str] = {}
    for index, item in enumerate(value, start=1):
        where = f"{place}[{index}]"
        # Exact values only, as in the documentation: "GitHub" is a typo, not a channel.
        channel = next((member for member in Channel if member.value == item), None)
        if type(item) is not str or channel is None:
            found.add(where, "config.channel")
            continue
        first = seen.setdefault(channel, where)
        if first != where:
            found.add(where, "config.duplicate_channel", first=first)
            continue
        channels.append(channel)
    return tuple(channels)


def _smtp_server(value: object, place: str, found: _Collector) -> SmtpServer | None:
    """
    Read the ``[notifications.smtp]`` table: the server only, never who signs in (RF-03).

    Args:
        value (object): The value of the key.
        place (str): ``notifications.smtp``.
        found (_Collector): Where problems go.

    Returns:
        SmtpServer | None: The server, or ``None`` after recording its problems.
    """
    if not isinstance(value, dict):
        found.add(place, "config.not_a_table")
        return None
    valid = True
    for key, item in value.items():
        if key not in _SMTP_KEYS:
            _unknown_key(place, key, item, found)
            valid = False

    security: SmtpSecurity | None = SmtpSecurity.STARTTLS
    if "security" in value:
        written = value["security"]
        security = next((member for member in SmtpSecurity if member.value == written), None)
        if type(written) is not str or security is None:
            found.add(f"{place}.security", "config.smtp_security")

    port = value.get("port", DEFAULT_SMTP_PORTS[security or SmtpSecurity.STARTTLS])
    # A bool is an int to Python, and TOML has no other way to write a port.
    port_ok = type(port) is int and 1 <= port <= 65535
    if not port_ok:
        found.add(f"{place}.port", f"host.{HostProblem.PORT}")

    if "host" not in value:
        found.add(place, "config.missing_key", key="host")
        return None
    host = value["host"]
    if type(host) is not str:
        found.add(f"{place}.host", "config.not_a_string")
        return None
    try:
        # The port was judged above; a valid stand-in keeps only the rules of the host here.
        validate_host(host, port if port_ok else _DEFAULT_PORT)
    except InvalidHost as error:
        found.add(f"{place}.host", f"host.{error.code}")
        return None
    if not valid or not port_ok or security is None:
        return None
    return SmtpServer(host, port, security)


def _unknown_key(table: str, key: str, value: object, found: _Collector) -> None:
    """
    Record an unknown key of a notifications table, as a secret one when it looks like one.

    The problem of a secret-like key names the variables to use and never the value, which may be
    a real token (RF-02).

    Args:
        table (str): ``notifications`` or ``notifications.smtp``.
        key (str): The key as written.
        value (object): Its value, only to tell a table from anything else.
        found (_Collector): Where the problem goes.
    """
    place = f"{table}.{key}"
    variables = _SECRET_KEYS.get(key)
    if variables is None and table == "notifications" and isinstance(value, dict):
        variables = _SECRET_TABLES.get(key)
    if variables is None:
        found.add(place, "config.unknown_key")
    else:
        found.add(place, "config.secret_key", variables=", ".join(variables))


# ---------------------------------------------------------------------------
# Host entries
# ---------------------------------------------------------------------------


def _host_entry(entry: object, place: str, default: int, found: _Collector) -> HostEntry | None:
    """
    Read one item of ``hosts``: a string, or an inline table with ``host`` (RF-04).

    Args:
        entry (object): The item.
        place (str): Where it is, such as ``certificates.hosts[2]``.
        default (int): The table's threshold.
        found (_Collector): Where problems go.

    Returns:
        HostEntry | None: The entry, or ``None`` after recording its problems.
    """
    if isinstance(entry, str):
        return _host_string(entry, place, default, found)
    if not isinstance(entry, dict):
        found.add(place, "config.bad_host_entry")
        return None

    valid = True
    for key in entry:
        if key not in _HOST_ENTRY_KEYS:
            found.add(f"{place}.{key}", "config.unknown_key")
            valid = False
    warn_days: int | None = default
    if "warn_days" in entry:
        warn_days = _warn_days(entry["warn_days"], f"{place}.warn_days", found)
    if "host" not in entry:
        found.add(place, "config.missing_key", key="host")
        return None
    host = entry["host"]
    if type(host) is not str:
        found.add(f"{place}.host", "config.not_a_string")
        return None
    port = entry.get("port", _DEFAULT_PORT)
    try:
        validate_host(host, port)
    except InvalidHost as error:
        # The port of the table form has its own place; every other rule is about the host.
        where = f"{place}.port" if error.code is HostProblem.PORT else f"{place}.host"
        found.add(where, f"host.{error.code}")
        return None
    # Registered even when the entry has another problem, as a domain is, so a later copy of it
    # is still reported.
    if not _is_new_host(host, port, place, found) or warn_days is None or not valid:
        return None
    return HostEntry(host, port, warn_days)


def _host_string(text: str, place: str, warn_days: int, found: _Collector) -> HostEntry | None:
    """
    Read a host written ``host``, ``host:port`` or ``[IPv6]:port`` (ADR-8).

    Args:
        text (str): The string.
        place (str): Where it is.
        warn_days (int): The threshold the entry inherits.
        found (_Collector): Where problems go.

    Returns:
        HostEntry | None: The entry, or ``None`` after recording its problem.
    """
    try:
        host, port = _split_host(text)
        validate_host(host, port)
    except _Refused as refused:
        found.add(place, str(refused))
        return None
    except InvalidHost as error:
        found.add(place, f"host.{error.code}")
        return None
    if not _is_new_host(host, port, place, found):
        return None
    return HostEntry(host, port, warn_days)


def _split_host(text: str) -> tuple[str, int]:
    """
    Split a host string into the host and the port (ADR-8).

    No ``:`` is a host on port ``443``; one ``:`` separates the two; a leading ``[`` opens an IPv6
    address with an optional ``:port`` after ``]``; two or more ``:`` without brackets are an
    IPv6 address on port ``443``, left to :func:`~tlsvigil.certificate.validate_host` to judge.

    Args:
        text (str): The string as written.

    Returns:
        tuple[str, int]: The host, brackets removed, and the port.

    Raises:
        _Refused: When the brackets are not closed, are followed by something other than
            ``:port``, or hold something other than an IPv6 address.
        InvalidHost: When the port is not a number from ``1`` to ``65535``.
    """
    if text.startswith("["):
        close = text.find("]")
        if close == -1:
            raise _Refused("config.host_syntax")
        host, rest = text[1:close], text[close + 1 :]
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise _Refused("config.brackets") from None
        if not rest:
            return host, _DEFAULT_PORT
        if not rest.startswith(":"):
            raise _Refused("config.host_syntax")
        return host, _port(rest[1:])
    if text.count(":") == 1:
        host, _, port = text.partition(":")
        if not _PORT_TEXT.fullmatch(port):
            # "https://example.com" has one ':' too: the rules of the whole text come first, so a
            # URL is called a URL and not a bad port.
            _refuse_unless_port_in_host(text)
        return host, _port(port)
    return text, _DEFAULT_PORT


def _refuse_unless_port_in_host(text: str) -> None:
    """
    Raise the problem of the whole text, unless it is only the ``:`` of a port.

    Args:
        text (str): A host string with one ``:`` and no valid port after it.

    Raises:
        InvalidHost: For any rule of the whole text other than the port in the host.
    """
    try:
        validate_host(text, _DEFAULT_PORT)
    except InvalidHost as error:
        if error.code is not HostProblem.PORT_IN_HOST:
            raise


def _port(text: str) -> int:
    """
    Read the port of a host string.

    Args:
        text (str): What follows the ``:``.

    Returns:
        int: The port, still to be checked against the range by ``validate_host``.

    Raises:
        InvalidHost: When the text is not one to five ASCII digits.
    """
    if not _PORT_TEXT.fullmatch(text):
        raise InvalidHost(HostProblem.PORT, f"port must be an integer from 1 to 65535: {text!r}")
    return int(text)


def _is_new_host(host: str, port: int, place: str, found: _Collector) -> bool:
    """
    Refuse a host and port already listed (RF-05).

    A name is compared in lowercase without a trailing dot, an address in its compressed form,
    so ``Example.COM.`` and ``example.com`` are one host, and so are two spellings of an IPv6
    address.

    Args:
        host (str): The host, already valid.
        port (int): The port, already valid.
        place (str): Where it is.
        found (_Collector): Where the problem goes, and the hosts seen so far.

    Returns:
        bool: ``True`` the first time, ``False`` after recording the problem of a repeat.
    """
    try:
        name = ipaddress.ip_address(host).compressed
    except ValueError:
        name = host.lower().removesuffix(".")
    first = found.hosts.setdefault((name, port), place)
    if first != place:
        found.add(place, "config.duplicate_host", first=first)
        return False
    return True


# ---------------------------------------------------------------------------
# Domain entries
# ---------------------------------------------------------------------------


def _domain_entry(entry: object, place: str, default: int, found: _Collector) -> DomainEntry | None:
    """
    Read one item of ``names``: a string, or an inline table with ``name`` (RF-04).

    Args:
        entry (object): The item.
        place (str): Where it is, such as ``domains.names[1]``.
        default (int): The table's threshold.
        found (_Collector): Where problems go.

    Returns:
        DomainEntry | None: The entry, or ``None`` after recording its problems.
    """
    if isinstance(entry, str):
        return _domain_name(entry, place, default, found)
    if not isinstance(entry, dict):
        found.add(place, "config.bad_domain_entry")
        return None

    valid = True
    for key in entry:
        if key not in _DOMAIN_ENTRY_KEYS:
            found.add(f"{place}.{key}", "config.unknown_key")
            valid = False
    warn_days: int | None = default
    if "warn_days" in entry:
        warn_days = _warn_days(entry["warn_days"], f"{place}.warn_days", found)
    if "name" not in entry:
        found.add(place, "config.missing_key", key="name")
        return None
    name = entry["name"]
    if type(name) is not str:
        found.add(f"{place}.name", "config.not_a_string")
        return None
    domain = _domain_name(name, f"{place}.name", default, found)
    if domain is None or warn_days is None or not valid:
        return None
    return DomainEntry(domain.domain, warn_days)


def _domain_name(name: str, place: str, warn_days: int, found: _Collector) -> DomainEntry | None:
    """
    Normalize a domain and refuse one already listed (RF-05).

    Args:
        name (str): The domain as written.
        place (str): Where it is.
        warn_days (int): The threshold the entry gets.
        found (_Collector): Where problems go, and the domains seen so far.

    Returns:
        DomainEntry | None: The entry with the normalized name, or ``None`` after recording its
        problem.
    """
    try:
        domain = normalize_domain(name)
    except InvalidDomain as error:
        found.add(place, f"domain.{error.code}")
        return None
    first = found.domains.setdefault(domain, place)
    if first != place:
        found.add(place, "config.duplicate_domain", first=first)
        return None
    return DomainEntry(domain, warn_days)
