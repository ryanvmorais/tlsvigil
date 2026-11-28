"""
Tests for the configuration: the file, its shape and every problem (spec 004, RF-02 to RF-05).

Each file is written to ``tmp_path`` and read by :func:`~tlsvigil.config.load_config`; the flags
go to :func:`~tlsvigil.config.config_from_names`. Loading opens no connection, so no server is
needed, and the network guard of ``conftest.py`` would fail any test that tried.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tlsvigil import config as config_module
from tlsvigil.config import (
    DEFAULT_CERTIFICATE_WARN_DAYS,
    DEFAULT_DOMAIN_WARN_DAYS,
    DEFAULT_SMTP_PORTS,
    DEFAULT_TIMEOUT,
    MAX_CONFIG_BYTES,
    Channel,
    Config,
    ConfigError,
    DomainEntry,
    HostEntry,
    Notifications,
    Problem,
    SmtpSecurity,
    SmtpServer,
    config_from_names,
    load_config,
    toml_position,
)
from tlsvigil.i18n import MESSAGES, Lang, translate
from tlsvigil.rdap import DomainProblem

# The example of the design's "Interfaces", as a person would write it.
EXAMPLE = """\
# Optional: "en" (default) or "pt-BR".
lang = "pt-BR"
# Optional: seconds for each TLS connection and each RDAP request (default 10).
timeout = 10

[certificates]
# Warn when fewer days than this are left (default 14).
warn_days = 14
hosts = [
  "example.com",                                  # port 443
  "mail.example.com:465",
  "[2001:db8::1]:8443",
  { host = "legacy.example.com", warn_days = 30 },
]

[domains]
# Warn when fewer days than this are left (default 30).
warn_days = 30
names = ["example.com", "example.com.br"]
"""

# The longest domain plus one character, every label valid: only the length refuses it.
NAME_254 = ".".join(["a" * 63] * 3 + ["a" * 58]) + ".com"


def _write(tmp_path: Path, content: str | bytes) -> Path:
    """
    Write a configuration file, byte for byte.

    Args:
        tmp_path (Path): The test's directory.
        content (str | bytes): The text (encoded as UTF-8) or the exact bytes.

    Returns:
        Path: The file.
    """
    path = tmp_path / "tlsvigil.toml"
    # write_bytes, not write_text: Windows would turn "\n" into "\r\n" and move the columns.
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


def _refused(path: Path) -> ConfigError:
    """
    Load a file that must be refused.

    Args:
        path (Path): The file.

    Returns:
        ConfigError: The error, after checking that each of its keys is in the catalogue.
    """
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    for problem in caught.value.problems:
        assert problem.message in MESSAGES, problem
    return caught.value


def _places(error: ConfigError) -> list[tuple[str, str]]:
    """
    Give the place and the key of each problem, in order.

    Args:
        error (ConfigError): The error.

    Returns:
        list[tuple[str, str]]: One pair per problem.
    """
    return [(problem.place, problem.message) for problem in error.problems]


# ---------------------------------------------------------------------------
# A good file
# ---------------------------------------------------------------------------


def test_the_example_of_the_design_loads_to_the_expected_config(tmp_path: Path) -> None:
    assert load_config(_write(tmp_path, EXAMPLE)) == Config(
        lang=Lang.PT_BR,
        timeout=10.0,
        hosts=(
            HostEntry("example.com", 443, 14),
            HostEntry("mail.example.com", 465, 14),
            HostEntry("2001:db8::1", 8443, 14),
            HostEntry("legacy.example.com", 443, 30),
        ),
        domains=(DomainEntry("example.com", 30), DomainEntry("example.com.br", 30)),
    )


def test_a_file_with_hosts_only_gets_every_default(tmp_path: Path) -> None:
    loaded = load_config(_write(tmp_path, 'certificates.hosts = ["example.com"]\n'))
    assert loaded == Config(None, DEFAULT_TIMEOUT, (HostEntry("example.com", 443, 14),), ())
    assert (DEFAULT_TIMEOUT, DEFAULT_CERTIFICATE_WARN_DAYS) == (10.0, 14)


def test_a_file_with_domains_only_gets_the_domain_default(tmp_path: Path) -> None:
    loaded = load_config(_write(tmp_path, 'domains.names = ["example.com"]\n'))
    assert loaded.domains == (DomainEntry("example.com", DEFAULT_DOMAIN_WARN_DAYS),)
    assert loaded.hosts == ()
    assert DEFAULT_DOMAIN_WARN_DAYS == 30


def test_both_entry_forms_and_the_per_entry_threshold(tmp_path: Path) -> None:
    text = """\
[certificates]
warn_days = 20
hosts = [
  "a.example.com",
  { host = "b.example.com" },
  { host = "c.example.com", port = 8443 },
  { host = "d.example.com", warn_days = 5 },
]
[domains]
warn_days = 40
names = ["example.com", { name = "example.org" }, { name = "example.net", warn_days = 0 }]
"""
    loaded = load_config(_write(tmp_path, text))
    assert loaded.hosts == (
        HostEntry("a.example.com", 443, 20),
        HostEntry("b.example.com", 443, 20),
        HostEntry("c.example.com", 8443, 20),
        HostEntry("d.example.com", 443, 5),
    )
    assert loaded.domains == (
        DomainEntry("example.com", 40),
        DomainEntry("example.org", 40),
        DomainEntry("example.net", 0),
    )


def test_the_table_threshold_applies_when_written_after_the_list(tmp_path: Path) -> None:
    text = '[certificates]\nhosts = ["example.com"]\nwarn_days = 7\n'
    assert load_config(_write(tmp_path, text)).hosts == (HostEntry("example.com", 443, 7),)


def test_an_array_of_tables_is_the_table_form(tmp_path: Path) -> None:
    text = '[[certificates.hosts]]\nhost = "example.com"\nport = 465\n'
    assert load_config(_write(tmp_path, text)).hosts == (HostEntry("example.com", 465, 14),)


@pytest.mark.parametrize(
    ("written", "host", "port"),
    [
        ("[2001:db8::1]:8443", "2001:db8::1", 8443),
        ("2001:db8::1", "2001:db8::1", 443),
        ("[::1]", "::1", 443),
        ("192.0.2.1:8443", "192.0.2.1", 8443),
        ("example.com:0443", "example.com", 443),
    ],
)
def test_the_host_syntax(tmp_path: Path, written: str, host: str, port: int) -> None:
    text = f'certificates.hosts = ["{written}"]\n'
    assert load_config(_write(tmp_path, text)).hosts == (HostEntry(host, port, 14),)


def test_a_domain_is_given_in_its_normalized_form(tmp_path: Path) -> None:
    loaded = load_config(_write(tmp_path, 'domains.names = ["Example.COM."]\n'))
    assert loaded.domains == (DomainEntry("example.com", 30),)


def test_a_host_keeps_the_spelling_it_was_given(tmp_path: Path) -> None:
    loaded = load_config(_write(tmp_path, 'certificates.hosts = ["Example.COM"]\n'))
    assert loaded.hosts == (HostEntry("Example.COM", 443, 14),)


def test_a_byte_order_mark_is_accepted(tmp_path: Path) -> None:
    content = b"\xef\xbb\xbf" + b'lang = "en"\ncertificates.hosts = ["example.com"]\n'
    assert load_config(_write(tmp_path, content)).lang is Lang.EN


@pytest.mark.parametrize(("written", "lang"), [("en", Lang.EN), ("PT-br", Lang.PT_BR)])
def test_lang_is_read_without_regard_to_case(tmp_path: Path, written: str, lang: Lang) -> None:
    text = f'lang = "{written}"\ncertificates.hosts = ["example.com"]\n'
    assert load_config(_write(tmp_path, text)).lang is lang


@pytest.mark.parametrize(("written", "seconds"), [("3", 3.0), ("2.5", 2.5)])
def test_timeout_takes_an_integer_or_a_float(tmp_path: Path, written: str, seconds: float) -> None:
    text = f'timeout = {written}\ncertificates.hosts = ["example.com"]\n'
    loaded = load_config(_write(tmp_path, text))
    assert loaded.timeout == seconds
    assert type(loaded.timeout) is float


def test_a_file_exactly_at_the_cap_loads(tmp_path: Path) -> None:
    """Control for the cap: the same file one byte longer is refused (the test below)."""
    head = b'certificates.hosts = ["example.com"]\n#'
    content = head + b"x" * (MAX_CONFIG_BYTES - len(head))
    assert len(content) == MAX_CONFIG_BYTES
    assert load_config(_write(tmp_path, content)).hosts == (HostEntry("example.com", 443, 14),)


# ---------------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------------


def test_a_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "absent.toml"
    error = _refused(path)
    assert error.problems == (Problem("", "config.missing", {"path": str(path)}),)
    assert error.lang is None


def test_a_directory(tmp_path: Path) -> None:
    # A real directory: IsADirectoryError on Linux, PermissionError on Windows.
    assert _places(_refused(tmp_path)) == [("", "config.directory")]


def test_a_directory_reported_as_such_by_the_system(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The branch the other system takes: each platform reaches only one of the two for real.
    path = _write(tmp_path, "")

    def refuse(*args: object, **kwargs: object) -> None:
        raise IsADirectoryError(21, "Is a directory")

    monkeypatch.setattr(Path, "open", refuse)
    assert _places(_refused(path)) == [("", "config.directory")]


def test_a_file_without_permission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, "")

    def refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", refuse)
    assert _places(_refused(path)) == [("", "config.permission")]


def test_any_other_read_error_gives_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, "")

    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(Path, "open", refuse)
    error = _refused(path)
    assert error.problems == (
        Problem("", "config.unreadable", {"path": str(path), "detail": "Input/output error"}),
    )


def test_a_file_one_byte_over_the_cap(tmp_path: Path) -> None:
    head = b'certificates.hosts = ["example.com"]\n#'
    path = _write(tmp_path, head + b"x" * (MAX_CONFIG_BYTES + 1 - len(head)))
    error = _refused(path)
    assert error.problems == (Problem("", "config.too_large", {"path": str(path), "limit": "256"}),)


def test_a_file_that_is_not_utf8(tmp_path: Path) -> None:
    content = 'certificates.hosts = ["example.com"]\n# configuração\n'.encode("latin-1")
    assert _places(_refused(_write(tmp_path, content))) == [("", "config.not_utf8")]


@pytest.mark.parametrize(
    ("text", "line", "column"),
    [
        ('lang = "en"\ntimeout = = 3\n', 2, 11),
        ('certificates.hosts = ["example.com"', 1, 36),
        ("[certificates]\nhosts = [\n", 3, 1),
    ],
    ids=["middle", "end-one-line", "end-many-lines"],
)
def test_invalid_toml_gives_the_line_and_the_column(
    tmp_path: Path, text: str, line: int, column: int
) -> None:
    error = _refused(_write(tmp_path, text))
    assert _places(error) == [("", "config.toml_syntax")]
    params = error.problems[0].params
    assert (params["line"], params["column"]) == (str(line), str(column))
    # The parser's own sentence, without the position it already gave.
    assert params["detail"]
    assert "(at " not in params["detail"]


# ---------------------------------------------------------------------------
# The position of a TOML error, on every Python
# ---------------------------------------------------------------------------


def test_the_position_comes_from_the_attributes_when_present() -> None:
    # Python 3.14 and newer: lineno and colno win over the message.
    assert toml_position("Invalid value (at line 9, column 9)", "", 2, 5) == (
        2,
        5,
        "Invalid value",
    )


def test_the_position_comes_from_the_message_before_python_3_14() -> None:
    assert toml_position("Invalid value (at line 2, column 11)", "a\nb", None, None) == (
        2,
        11,
        "Invalid value",
    )


@pytest.mark.parametrize(
    ("text", "line", "column"),
    [("abc", 1, 4), ("a\nbc", 2, 3), ("a\n", 2, 1)],
)
def test_the_end_of_the_document_is_counted_from_the_text(
    text: str, line: int, column: int
) -> None:
    assert toml_position("Unterminated (at end of document)", text, None, None) == (
        line,
        column,
        "Unterminated",
    )


def test_a_message_without_a_position_is_kept_whole() -> None:
    assert toml_position("Something else", "abc", None, None) == (1, 4, "Something else")


# ---------------------------------------------------------------------------
# The shape: one problem per bad file
# ---------------------------------------------------------------------------

_HOST = 'certificates.hosts = ["example.com"]\n'


@pytest.mark.parametrize(
    ("text", "place", "key"),
    [
        # Unknown keys, at each level.
        ("warn_day = 3\n" + _HOST, "warn_day", "config.unknown_key"),
        (_HOST + "[notifications]\nissue = true\n", "notifications.issue", "config.unknown_key"),
        (
            '[certificates]\nwarn_day = 3\nhosts = ["example.com"]\n',
            "certificates.warn_day",
            "config.unknown_key",
        ),
        (
            '[domains]\nname = ["example.com"]\nnames = ["example.com"]\n',
            "domains.name",
            "config.unknown_key",
        ),
        (
            'certificates.hosts = [{ host = "example.com", warn_day = 3 }]\n',
            "certificates.hosts[1].warn_day",
            "config.unknown_key",
        ),
        (
            'domains.names = [{ name = "example.com", port = 443 }]\n',
            "domains.names[1].port",
            "config.unknown_key",
        ),
        # Wrong types.
        ("lang = 1\n" + _HOST, "lang", "config.lang"),
        ('timeout = "10"\n' + _HOST, "timeout", "config.timeout"),
        ("timeout = true\n" + _HOST, "timeout", "config.timeout"),
        ("certificates = 1\n", "certificates", "config.not_a_table"),
        ("domains = []\n" + _HOST, "domains", "config.not_a_table"),
        ('certificates.hosts = "example.com"\n', "certificates.hosts", "config.not_a_list"),
        ('domains.names = "example.com"\n' + _HOST, "domains.names", "config.not_a_list"),
        (
            'certificates.warn_days = "14"\n' + _HOST,
            "certificates.warn_days",
            "config.warn_days",
        ),
        ("domains.warn_days = 1.5\n" + _HOST, "domains.warn_days", "config.warn_days"),
        ("certificates.hosts = [443]\n", "certificates.hosts[1]", "config.bad_host_entry"),
        ("domains.names = [true]\n", "domains.names[1]", "config.bad_domain_entry"),
        (
            "certificates.hosts = [{ host = 443 }]\n",
            "certificates.hosts[1].host",
            "config.not_a_string",
        ),
        ("domains.names = [{ name = 1 }]\n", "domains.names[1].name", "config.not_a_string"),
        (
            'certificates.hosts = [{ host = "example.com", port = "443" }]\n',
            "certificates.hosts[1].port",
            "host.port",
        ),
        (
            'certificates.hosts = [{ host = "example.com", warn_days = "7" }]\n',
            "certificates.hosts[1].warn_days",
            "config.warn_days",
        ),
        (
            'domains.names = [{ name = "example.com", warn_days = -1 }]\n',
            "domains.names[1].warn_days",
            "config.warn_days",
        ),
        # A table entry without its name.
        ("certificates.hosts = [{ port = 443 }]\n", "certificates.hosts[1]", "config.missing_key"),
        ("domains.names = [{ warn_days = 3 }]\n", "domains.names[1]", "config.missing_key"),
        # Out of range.
        ('certificates.hosts = ["example.com:0"]\n', "certificates.hosts[1]", "host.port"),
        ('certificates.hosts = ["example.com:65536"]\n', "certificates.hosts[1]", "host.port"),
        (
            'certificates.hosts = [{ host = "example.com", port = 0 }]\n',
            "certificates.hosts[1].port",
            "host.port",
        ),
        ('certificates.hosts = ["example.com:"]\n', "certificates.hosts[1]", "host.port"),
        ('certificates.hosts = ["example.com:https"]\n', "certificates.hosts[1]", "host.port"),
        ('certificates.hosts = ["example.com:+443"]\n', "certificates.hosts[1]", "host.port"),
        ('certificates.hosts = ["example.com:123456"]\n', "certificates.hosts[1]", "host.port"),
        ("certificates.warn_days = -1\n" + _HOST, "certificates.warn_days", "config.warn_days"),
        ("timeout = 0\n" + _HOST, "timeout", "config.timeout"),
        ("timeout = -1.5\n" + _HOST, "timeout", "config.timeout"),
        ("timeout = inf\n" + _HOST, "timeout", "config.timeout"),
        ("timeout = nan\n" + _HOST, "timeout", "config.timeout"),
        ('lang = "fr"\n' + _HOST, "lang", "config.lang"),
        ('lang = "pt"\n' + _HOST, "lang", "config.lang"),
        # Hosts the certificate check refuses.
        (
            'certificates.hosts = ["https://example.com"]\n',
            "certificates.hosts[1]",
            "host.url_or_path",
        ),
        (
            'certificates.hosts = ["example.com/status"]\n',
            "certificates.hosts[1]",
            "host.url_or_path",
        ),
        ('certificates.hosts = ["example .com"]\n', "certificates.hosts[1]", "host.whitespace"),
        ('certificates.hosts = [""]\n', "certificates.hosts[1]", "host.empty"),
        ('certificates.hosts = [":443"]\n', "certificates.hosts[1]", "host.empty"),
        (
            'certificates.hosts = ["example.com:443:1"]\n',
            "certificates.hosts[1]",
            "host.port_in_host",
        ),
        (
            'certificates.hosts = [{ host = "example.com:465" }]\n',
            "certificates.hosts[1].host",
            "host.port_in_host",
        ),
        (
            'certificates.hosts = [{ host = "[::1]" }]\n',
            "certificates.hosts[1].host",
            "host.url_or_path",
        ),
        # The bracket syntax of ADR-8.
        ('certificates.hosts = ["[::1"]\n', "certificates.hosts[1]", "config.host_syntax"),
        ('certificates.hosts = ["[::1]443"]\n', "certificates.hosts[1]", "config.host_syntax"),
        (
            'certificates.hosts = ["[example.com]:443"]\n',
            "certificates.hosts[1]",
            "config.brackets",
        ),
        ('certificates.hosts = ["[]"]\n', "certificates.hosts[1]", "config.brackets"),
        ('certificates.hosts = ["[::1]:99999"]\n', "certificates.hosts[1]", "host.port"),
        ('certificates.hosts = ["[::1]:https"]\n', "certificates.hosts[1]", "host.port"),
        # One ':' and no port after it: the rules of the whole text come first.
        ('certificates.hosts = ["example .com:x"]\n', "certificates.hosts[1]", "host.whitespace"),
        (
            'certificates.hosts = ["user@example.com:"]\n',
            "certificates.hosts[1]",
            "host.url_or_path",
        ),
        # Nothing to check.
        ("", "", "config.nothing_to_check"),
        ('lang = "en"\n', "", "config.nothing_to_check"),
        ("certificates.hosts = []\ndomains.names = []\n", "", "config.nothing_to_check"),
    ],
)
def test_a_bad_file_names_the_place_and_the_problem(
    tmp_path: Path, text: str, place: str, key: str
) -> None:
    assert _places(_refused(_write(tmp_path, text))) == [(place, key)]


# One domain per rule of spec 003, in the file: each maps to its own sentence (ADR-2).
_BAD_DOMAINS = {
    DomainProblem.EMPTY: "",
    DomainProblem.WHITESPACE: "example .com",
    DomainProblem.URL_OR_PATH: "https://example.com",
    DomainProblem.PORT: "example.com:443",
    DomainProblem.USER_INFO: "user@example.com",
    DomainProblem.UNDERSCORE: "_dmarc.example.com",
    DomainProblem.NOT_ASCII: "exemplo.com.bré",
    DomainProblem.ONE_LABEL: "com",
    DomainProblem.EMPTY_LABEL: "example..com",
    DomainProblem.BAD_LABEL: "-example.com",
    DomainProblem.TOO_LONG: NAME_254,
    DomainProblem.NUMERIC: "192.0.2.1",
}


def test_every_rule_of_a_domain_has_a_bad_example() -> None:
    assert set(_BAD_DOMAINS) == set(DomainProblem)


@pytest.mark.parametrize("code", list(DomainProblem))
def test_each_domain_rule_is_reported_by_its_code(tmp_path: Path, code: DomainProblem) -> None:
    text = f'domains.names = ["{_BAD_DOMAINS[code]}"]\n'
    assert _places(_refused(_write(tmp_path, text))) == [("domains.names[1]", f"domain.{code}")]


def test_a_domain_rule_in_the_table_form_points_at_the_name(tmp_path: Path) -> None:
    text = 'domains.names = [{ name = "example.com/whois" }]\n'
    assert _places(_refused(_write(tmp_path, text))) == [
        ("domains.names[1].name", "domain.url_or_path")
    ]


def test_an_entry_with_a_problem_does_not_add_nothing_to_check(tmp_path: Path) -> None:
    """Control for "nothing to check": a listed entry counts, valid or not."""
    text = 'certificates.hosts = ["https://example.com"]\n'
    assert _places(_refused(_write(tmp_path, text))) == [
        ("certificates.hosts[1]", "host.url_or_path")
    ]


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hosts",
    [
        '"example.com", "Example.COM."',
        '"example.com:465", { host = "EXAMPLE.com", port = 465 }',
        '"[2001:db8::1]:443", "2001:db8:0:0:0:0:0:1"',
    ],
    ids=["case-and-dot", "both-forms", "ipv6-spellings"],
)
def test_a_duplicate_host_names_the_first(tmp_path: Path, hosts: str) -> None:
    error = _refused(_write(tmp_path, f"certificates.hosts = [{hosts}]\n"))
    assert error.problems == (
        Problem(
            "certificates.hosts[2]", "config.duplicate_host", {"first": "certificates.hosts[1]"}
        ),
    )


def test_the_same_host_on_another_port_is_not_a_duplicate(tmp_path: Path) -> None:
    """Control for the test above: the port is part of what is compared."""
    text = 'certificates.hosts = ["example.com", "example.com:465"]\n'
    assert len(load_config(_write(tmp_path, text)).hosts) == 2


def test_a_host_with_another_problem_still_counts_for_duplicates(tmp_path: Path) -> None:
    text = 'certificates.hosts = [{ host = "example.com", warn_days = -1 }, "example.com"]\n'
    assert _places(_refused(_write(tmp_path, text))) == [
        ("certificates.hosts[1].warn_days", "config.warn_days"),
        ("certificates.hosts[2]", "config.duplicate_host"),
    ]


def test_a_duplicate_domain_in_another_case_names_the_first(tmp_path: Path) -> None:
    error = _refused(_write(tmp_path, 'domains.names = ["example.com", "EXAMPLE.com."]\n'))
    assert error.problems == (
        Problem("domains.names[2]", "config.duplicate_domain", {"first": "domains.names[1]"}),
    )


def test_a_domain_and_a_host_of_the_same_name_are_not_duplicates(tmp_path: Path) -> None:
    text = 'certificates.hosts = ["example.com"]\ndomains.names = ["example.com"]\n'
    loaded = load_config(_write(tmp_path, text))
    assert (len(loaded.hosts), len(loaded.domains)) == (1, 1)


# ---------------------------------------------------------------------------
# The [notifications] table (spec 005, RF-02)
# ---------------------------------------------------------------------------


def _notifications(tmp_path: Path, table: str) -> Notifications | None:
    """
    Load a file with one host and the given notifications text.

    Args:
        tmp_path (Path): The test's directory.
        table (str): TOML that follows the host, such as a ``[notifications]`` table.

    Returns:
        Notifications | None: The table that was loaded.
    """
    return load_config(_write(tmp_path, _HOST + table)).notifications


def test_the_example_of_the_design_loads_every_channel(tmp_path: Path) -> None:
    table = """\
[notifications]
channels = ["github", "telegram", "smtp"]

[notifications.smtp]
host = "smtp.example.com"
port = 587
security = "starttls"
"""
    assert _notifications(tmp_path, table) == Notifications(
        (Channel.GITHUB, Channel.TELEGRAM, Channel.SMTP),
        SmtpServer("smtp.example.com", 587, SmtpSecurity.STARTTLS),
    )


def test_a_file_without_the_table_has_no_notifications(tmp_path: Path) -> None:
    assert _notifications(tmp_path, "") is None
    assert config_from_names(["example.com"], []).notifications is None


def test_the_channels_keep_the_order_of_the_file(tmp_path: Path) -> None:
    table = '[notifications]\nchannels = ["telegram", "github"]\n'
    assert _notifications(tmp_path, table) == Notifications(
        (Channel.TELEGRAM, Channel.GITHUB), None
    )


@pytest.mark.parametrize("table", ["[notifications]\n", "[notifications]\nchannels = []\n"])
def test_a_table_without_channels_is_accepted(tmp_path: Path, table: str) -> None:
    """The file is valid; whether a run with no channel may notify is the command's to judge."""
    assert _notifications(tmp_path, table) == Notifications((), None)


@pytest.mark.parametrize(
    ("smtp", "server"),
    [
        ('host = "smtp.example.com"', SmtpServer("smtp.example.com", 587, SmtpSecurity.STARTTLS)),
        (
            'host = "smtp.example.com"\nsecurity = "tls"',
            SmtpServer("smtp.example.com", 465, SmtpSecurity.TLS),
        ),
        (
            'host = "smtp.example.com"\nsecurity = "tls"\nport = 2465',
            SmtpServer("smtp.example.com", 2465, SmtpSecurity.TLS),
        ),
        ('host = "192.0.2.25"\nport = 1', SmtpServer("192.0.2.25", 1, SmtpSecurity.STARTTLS)),
        ('host = "::1"\nport = 65535', SmtpServer("::1", 65535, SmtpSecurity.STARTTLS)),
    ],
    ids=["defaults", "tls-default-port", "tls-own-port", "lowest-port", "highest-port"],
)
def test_the_smtp_table_and_its_defaults(tmp_path: Path, smtp: str, server: SmtpServer) -> None:
    table = f'[notifications]\nchannels = ["smtp"]\n[notifications.smtp]\n{smtp}\n'
    assert _notifications(tmp_path, table) == Notifications((Channel.SMTP,), server)
    assert DEFAULT_SMTP_PORTS == {SmtpSecurity.STARTTLS: 587, SmtpSecurity.TLS: 465}


def test_an_smtp_table_without_its_channel_is_kept(tmp_path: Path) -> None:
    table = '[notifications]\nchannels = ["github"]\n[notifications.smtp]\nhost = "smtp.example"\n'
    assert _notifications(tmp_path, table) == Notifications(
        (Channel.GITHUB,), SmtpServer("smtp.example", 587, SmtpSecurity.STARTTLS)
    )


_SMTP = '[notifications]\nchannels = ["smtp"]\n[notifications.smtp]\nhost = "smtp.example.com"\n'


@pytest.mark.parametrize(
    ("table", "place", "key"),
    [
        ("notifications = 1\n", "notifications", "config.not_a_table"),
        ('[notifications]\nchannels = "github"\n', "notifications.channels", "config.not_a_list"),
        (
            '[notifications]\nchannels = ["slack"]\n',
            "notifications.channels[1]",
            "config.channel",
        ),
        (
            '[notifications]\nchannels = ["GitHub"]\n',
            "notifications.channels[1]",
            "config.channel",
        ),
        ("[notifications]\nchannels = [1]\n", "notifications.channels[1]", "config.channel"),
        ('[notifications]\nchannels = ["smtp"]\n', "notifications.smtp", "config.smtp_table"),
        (
            '[notifications]\nchannels = ["smtp"]\nsmtp = "smtp.example.com"\n',
            "notifications.smtp",
            "config.not_a_table",
        ),
        (
            '[notifications]\nchannels = ["smtp"]\n[notifications.smtp]\nport = 587\n',
            "notifications.smtp",
            "config.missing_key",
        ),
        (
            "[notifications]\n[notifications.smtp]\nhost = 25\n",
            "notifications.smtp.host",
            "config.not_a_string",
        ),
        (
            '[notifications]\n[notifications.smtp]\nhost = "smtp://smtp.example.com"\n',
            "notifications.smtp.host",
            "host.url_or_path",
        ),
        (
            '[notifications]\n[notifications.smtp]\nhost = "smtp.example.com:587"\n',
            "notifications.smtp.host",
            "host.port_in_host",
        ),
        (_SMTP + "port = 0\n", "notifications.smtp.port", "host.port"),
        (_SMTP + "port = 65536\n", "notifications.smtp.port", "host.port"),
        (_SMTP + 'port = "587"\n', "notifications.smtp.port", "host.port"),
        (_SMTP + "port = true\n", "notifications.smtp.port", "host.port"),
        (_SMTP + 'security = "ssl"\n', "notifications.smtp.security", "config.smtp_security"),
        (_SMTP + 'security = "TLS"\n', "notifications.smtp.security", "config.smtp_security"),
        (_SMTP + "security = 1\n", "notifications.smtp.security", "config.smtp_security"),
        (_SMTP + "tls = true\n", "notifications.smtp.tls", "config.unknown_key"),
        (
            '[notifications]\nchannels = ["github"]\ngithub = true\n',
            "notifications.github",
            "config.unknown_key",
        ),
    ],
)
def test_a_bad_notifications_table_names_the_place_and_the_problem(
    tmp_path: Path, table: str, place: str, key: str
) -> None:
    assert _places(_refused(_write(tmp_path, _HOST + table))) == [(place, key)]


def test_a_channel_listed_twice_names_the_first(tmp_path: Path) -> None:
    table = '[notifications]\nchannels = ["github", "telegram", "github"]\n'
    error = _refused(_write(tmp_path, _HOST + table))
    assert error.problems == (
        Problem(
            "notifications.channels[3]",
            "config.duplicate_channel",
            {"first": "notifications.channels[1]"},
        ),
    )


# A value no message may repeat: it stands for a real token or password.
_SECRET = "ghp_DoNotRepeatThisValue1234"


@pytest.mark.parametrize(
    ("table", "place", "variables"),
    [
        (
            f'[notifications]\ntoken = "{_SECRET}"\n',
            "notifications.token",
            "GITHUB_TOKEN, TLSVIGIL_TELEGRAM_TOKEN",
        ),
        (
            f'[notifications.github]\ntoken = "{_SECRET}"\n',
            "notifications.github",
            "GITHUB_TOKEN, GITHUB_REPOSITORY",
        ),
        (
            f'[notifications.telegram]\ntoken = "{_SECRET}"\n',
            "notifications.telegram",
            "TLSVIGIL_TELEGRAM_TOKEN, TLSVIGIL_TELEGRAM_CHAT_ID",
        ),
        (
            f'[notifications]\nchat_id = "{_SECRET}"\n',
            "notifications.chat_id",
            "TLSVIGIL_TELEGRAM_CHAT_ID",
        ),
        (
            f'[notifications]\nchat = "{_SECRET}"\n',
            "notifications.chat",
            "TLSVIGIL_TELEGRAM_CHAT_ID",
        ),
        (
            f'{_SMTP}username = "{_SECRET}"\n',
            "notifications.smtp.username",
            "TLSVIGIL_SMTP_USERNAME",
        ),
        (f'{_SMTP}user = "{_SECRET}"\n', "notifications.smtp.user", "TLSVIGIL_SMTP_USERNAME"),
        (
            f'{_SMTP}password = "{_SECRET}"\n',
            "notifications.smtp.password",
            "TLSVIGIL_SMTP_PASSWORD",
        ),
        (f'{_SMTP}from = "{_SECRET}"\n', "notifications.smtp.from", "TLSVIGIL_SMTP_FROM"),
        (f'{_SMTP}sender = "{_SECRET}"\n', "notifications.smtp.sender", "TLSVIGIL_SMTP_FROM"),
        (f'{_SMTP}to = ["{_SECRET}"]\n', "notifications.smtp.to", "TLSVIGIL_SMTP_TO"),
        (
            f'{_SMTP}recipients = "{_SECRET}"\n',
            "notifications.smtp.recipients",
            "TLSVIGIL_SMTP_TO",
        ),
        # Anywhere in the table (RF-02): each key at the other level too.
        (
            f'[notifications]\npassword = "{_SECRET}"\n',
            "notifications.password",
            "TLSVIGIL_SMTP_PASSWORD",
        ),
        (
            f'{_SMTP}token = "{_SECRET}"\n',
            "notifications.smtp.token",
            "GITHUB_TOKEN, TLSVIGIL_TELEGRAM_TOKEN",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and "\n" not in value else None,
)
def test_a_secret_like_key_names_its_variables_and_never_its_value(
    tmp_path: Path, table: str, place: str, variables: str
) -> None:
    error = _refused(_write(tmp_path, _HOST + table))
    assert error.problems == (Problem(place, "config.secret_key", {"variables": variables}),)
    for lang in Lang:
        sentence = translate(lang, "config.secret_key", **error.problems[0].params)
        assert variables in sentence
        assert _SECRET not in sentence


def test_the_problems_of_the_table_come_with_those_of_the_hosts_in_order(
    tmp_path: Path,
) -> None:
    text = """\
certificates.hosts = ["https://example.com"]
[notifications]
channels = ["github", "fax", "smtp"]
password = "hunter2"
[notifications.smtp]
host = "smtp.example.com"
port = 0
[domains]
names = ["com"]
"""
    assert _places(_refused(_write(tmp_path, text))) == [
        ("certificates.hosts[1]", "host.url_or_path"),
        ("notifications.channels[2]", "config.channel"),
        ("notifications.password", "config.secret_key"),
        ("notifications.smtp.port", "host.port"),
        ("domains.names[1]", "domain.one_label"),
    ]


# ---------------------------------------------------------------------------
# Every problem, in order
# ---------------------------------------------------------------------------


def test_five_problems_in_one_file_are_reported_in_order(tmp_path: Path) -> None:
    text = """\
lang = "fr"
timeout = 0
[certificates]
warn_day = 14
hosts = ["https://example.com", "example.com", "example.com"]
"""
    assert _places(_refused(_write(tmp_path, text))) == [
        ("lang", "config.lang"),
        ("timeout", "config.timeout"),
        ("certificates.warn_day", "config.unknown_key"),
        ("certificates.hosts[1]", "host.url_or_path"),
        ("certificates.hosts[3]", "config.duplicate_host"),
    ]


def test_a_valid_lang_is_kept_for_the_messages(tmp_path: Path) -> None:
    error = _refused(_write(tmp_path, 'lang = "pt-BR"\ntimeout = 0\n' + _HOST))
    assert error.lang is Lang.PT_BR


def test_an_invalid_lang_leaves_the_messages_without_one(tmp_path: Path) -> None:
    error = _refused(_write(tmp_path, 'lang = "fr"\n' + _HOST))
    assert error.lang is None


def test_the_error_message_counts_the_problems(tmp_path: Path) -> None:
    error = _refused(_write(tmp_path, 'lang = "fr"\ntimeout = 0\n' + _HOST))
    assert str(error) == "the configuration has 2 problem(s)"


# ---------------------------------------------------------------------------
# Names on the command line
# ---------------------------------------------------------------------------


def test_names_get_the_defaults_in_the_order_given() -> None:
    loaded = config_from_names(
        ["example.com", "mail.example.com:465", "[::1]:8443"], ["Example.COM.br", "example.org"]
    )
    assert loaded == Config(
        lang=None,
        timeout=DEFAULT_TIMEOUT,
        hosts=(
            HostEntry("example.com", 443, 14),
            HostEntry("mail.example.com", 465, 14),
            HostEntry("::1", 8443, 14),
        ),
        domains=(DomainEntry("example.com.br", 30), DomainEntry("example.org", 30)),
    )


def test_hosts_alone_and_domains_alone() -> None:
    assert config_from_names(["example.com"], []).domains == ()
    assert config_from_names([], ["example.com"]).hosts == ()


def test_bad_names_are_reported_with_their_flag_place() -> None:
    with pytest.raises(ConfigError) as caught:
        config_from_names(
            ["example.com", "https://example.com", "EXAMPLE.com", "[::1"],
            ["example.com", "com", "Example.com."],
        )
    assert _places(caught.value) == [
        ("--host[2]", "host.url_or_path"),
        ("--host[3]", "config.duplicate_host"),
        ("--host[4]", "config.host_syntax"),
        ("--domain[2]", "domain.one_label"),
        ("--domain[3]", "config.duplicate_domain"),
    ]
    assert caught.value.problems[1].params == {"first": "--host[1]"}
    assert caught.value.lang is None


def test_no_names_is_nothing_to_check() -> None:
    with pytest.raises(ConfigError) as caught:
        config_from_names([], [])
    assert _places(caught.value) == [("", "config.nothing_to_check")]


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


def test_every_key_the_module_writes_is_in_the_catalogue() -> None:
    source = Path(config_module.__file__).read_text(encoding="utf-8")
    keys = set(re.findall(r'"(config\.[a-z_0-9]+)"', source))
    assert len(keys) >= 20  # the regular expression found them
    assert keys <= set(MESSAGES)
