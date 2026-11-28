"""
Tests for ``tlsvigil check`` end to end (spec 004, RF-01 to RF-03, RF-06 to RF-09, RNF-03).

:func:`~tlsvigil.cli.main` runs in-process with the seams of ADR-7: ``client_context`` trusts the
test CA, ``bootstrap_url`` points at ``https_server`` (which names itself for ``com`` and nothing
for ``org``), and ``now`` is the test's time. Each test works in its own ``tmp_path``, so the
default ``tlsvigil.toml`` is the one the test wrote, or none. The tests of the streams and of a
crash replace :func:`~tlsvigil.runner.run` with a report built by hand, so they open nothing.
"""

from __future__ import annotations

import errno
import io
import json
import re
import ssl
import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
import trustme

import reports
import runs
from http_server import LocalHTTPServer
from network_guard import NetworkGuard
from runs import leaf, serve_registry
from tls_server import LocalServer
from tlsvigil import cli
from tlsvigil.cli import EXIT_ATTENTION, EXIT_INTERNAL, EXIT_OK, EXIT_USAGE, main
from tlsvigil.i18n import MESSAGES, Lang
from tlsvigil.runner import Report

Serve = Callable[..., LocalServer]

# A domain under `org`, which the local bootstrap names no server for: `unknown`, exit 0.
NO_RDAP = "example.org"
# The two-space gap between the columns; a detail holds single spaces only.
COLUMNS = re.compile(r" {2,}")
# Built with chr(), so the source holds no escape a tool could turn into the character itself:
# the escape character a hostile key carries, and the backslash its escaped form starts with.
ESC = chr(0x1B)
BACKSLASH = chr(0x5C)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(directory: Path, text: str, name: str = "tlsvigil.toml") -> Path:
    """
    Write a configuration file.

    Args:
        directory (Path): Where to write it.
        text (str): The TOML.
        name (str): The file name.

    Returns:
        Path: The file.
    """
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def _hosts_toml(*ports: int) -> str:
    """
    Write the ``[certificates]`` table of hosts on ``localhost``.

    Args:
        *ports (int): The port of each host, in order.

    Returns:
        str: The table.
    """
    hosts = ", ".join(f'"localhost:{port}"' for port in ports)
    return f"[certificates]\nhosts = [{hosts}]\n"


def _domains_toml(*names: str) -> str:
    """
    Write the ``[domains]`` table.

    Args:
        *names (str): The domains, in order.

    Returns:
        str: The table.
    """
    listed = ", ".join(f'"{name}"' for name in names)
    return f"[domains]\nnames = [{listed}]\n"


def _rows(out: str) -> tuple[list[list[str]], str]:
    """
    Split a text report into its columns and its summary.

    Args:
        out (str): The standard output.

    Returns:
        tuple[list[list[str]], str]: The status, name and detail of each line, and the summary.
    """
    *lines, blank, summary = out.splitlines()
    assert blank == ""
    return [COLUMNS.split(line) for line in lines], summary


def _replace_run(monkeypatch: pytest.MonkeyPatch, report: Report) -> None:
    """
    Make the command use a report built by hand instead of running the checks.

    Args:
        monkeypatch (pytest.MonkeyPatch): Undoes the replacement when the test ends.
        report (Report): The report every run gives.
    """

    def _run(*args: object, **kwargs: object) -> Report:
        return report

    monkeypatch.setattr(cli, "run", _run)


# ---------------------------------------------------------------------------
# The run and the exit codes (RF-01, RF-06, RF-09)
# ---------------------------------------------------------------------------


def test_a_file_with_every_kind_of_result_prints_them_in_order_and_exits_one(
    ca: trustme.CA,
    serve: Serve,
    client_context: ssl.SSLContext,
    https_server: LocalHTTPServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = runs.now()
    valid = serve(leaf(ca, now, timedelta(days=90)))
    expired = serve(leaf(ca, now, timedelta(days=-1)))
    bootstrap_url = serve_registry(https_server, {"example.com": now + timedelta(days=300)})
    _write_config(
        tmp_path,
        "timeout = 5\n"
        + _hosts_toml(valid.port, expired.port)
        + _domains_toml(NO_RDAP, "example.com"),
    )
    monkeypatch.chdir(tmp_path)

    code = main(["check"], context=client_context, bootstrap_url=bootstrap_url, now=now)

    captured = capsys.readouterr()
    rows, summary = _rows(captured.out)
    in_90 = (now + timedelta(days=90)).date().isoformat()
    in_300 = (now + timedelta(days=300)).date().isoformat()
    assert rows == [
        ["OK", f"localhost:{valid.port}", f"certificate expires in 90 days ({in_90})"],
        ["INVALID", f"localhost:{expired.port}", "certificate failed verification: expired"],
        ["UNKNOWN", NO_RDAP, "domain expiry unknown: the registry has no RDAP service"],
        ["OK", "example.com", f"domain expires in 300 days ({in_300})"],
    ]
    assert summary == "4 checked: 1 needs attention."
    assert captured.err == ""
    assert code == EXIT_ATTENTION


def test_a_file_where_everything_is_fine_exits_zero(
    ca: trustme.CA,
    serve: Serve,
    client_context: ssl.SSLContext,
    https_server: LocalHTTPServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = runs.now()
    valid = serve(leaf(ca, now, timedelta(days=90)))
    bootstrap_url = serve_registry(https_server, {"example.com": now + timedelta(days=300)})
    _write_config(tmp_path, _hosts_toml(valid.port) + _domains_toml("example.com"))
    monkeypatch.chdir(tmp_path)

    code = main(["check"], context=client_context, bootstrap_url=bootstrap_url, now=now)

    rows, summary = _rows(capsys.readouterr().out)
    assert [row[0] for row in rows] == ["OK", "OK"]
    assert summary == "2 checked: 0 need attention."
    assert code == EXIT_OK


def test_an_unknown_domain_alone_exits_zero(
    client_context: ssl.SSLContext,
    https_server: LocalHTTPServer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bootstrap_url = serve_registry(https_server, {})

    code = main(
        ["check", "--domain", NO_RDAP],
        context=client_context,
        bootstrap_url=bootstrap_url,
        now=reports.NOW,
    )

    rows, _ = _rows(capsys.readouterr().out)
    assert [row[0] for row in rows] == ["UNKNOWN"]
    assert code == EXIT_OK


def test_an_error_alone_exits_one(
    client_context: ssl.SSLContext,
    https_server: LocalHTTPServer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Under `com` with no answer: the local registry answers 404, a domain it does not have.
    bootstrap_url = serve_registry(https_server, {})

    code = main(
        ["check", "--domain", "missing.com"],
        context=client_context,
        bootstrap_url=bootstrap_url,
        now=reports.NOW,
    )

    rows, summary = _rows(capsys.readouterr().out)
    assert rows[0][:2] == ["ERROR", "missing.com"]
    assert rows[0][2].startswith("domain not checked: the registry has no such domain")
    assert summary == "1 checked: 1 needs attention."
    assert code == EXIT_ATTENTION


def test_json_goes_to_the_standard_output_alone(
    client_context: ssl.SSLContext,
    https_server: LocalHTTPServer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bootstrap_url = serve_registry(https_server, {})

    code = main(
        ["check", "--domain", NO_RDAP, "--domain", "missing.com", "--json"],
        context=client_context,
        bootstrap_url=bootstrap_url,
        now=reports.NOW,
    )

    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert captured.err == ""
    assert captured.out.isascii()
    assert captured.out.endswith("}\n")
    assert document["exit_code"] == code == EXIT_ATTENTION
    assert [item["status"] for item in document["domains"]] == ["unknown", "error"]
    assert document["certificates"] == []
    assert document["summary"]["total"] == 2


# ---------------------------------------------------------------------------
# The language (RF-08)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("file_lang", "flag", "expected"),
    [
        pytest.param("en", ["--lang", "pt-BR"], "pt", id="the-flag-over-the-file"),
        pytest.param("pt-BR", ["--lang", "en"], "en", id="the-flag-over-the-file-in-english"),
        pytest.param("pt-BR", [], "pt", id="the-file-alone"),
        pytest.param(None, [], "en", id="the-default"),
        pytest.param(None, ["--lang", "PT-br"], "pt", id="the-flag-in-any-case"),
    ],
)
def test_the_language_is_the_flag_then_the_file_then_english(
    file_lang: str | None,
    flag: list[str],
    expected: str,
    client_context: ssl.SSLContext,
    https_server: LocalHTTPServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bootstrap_url = serve_registry(https_server, {})
    head = f'lang = "{file_lang}"\n' if file_lang is not None else ""
    _write_config(tmp_path, head + _domains_toml(NO_RDAP))
    monkeypatch.chdir(tmp_path)

    code = main(
        ["check", *flag], context=client_context, bootstrap_url=bootstrap_url, now=reports.NOW
    )

    rows, summary = _rows(capsys.readouterr().out)
    if expected == "pt":
        assert rows == [
            [
                "DESCONHECIDO",
                NO_RDAP,
                "vencimento do domínio desconhecido: o registro não tem serviço RDAP",
            ]
        ]
        assert summary == "1 verificado: 0 precisam de atenção."
    else:
        assert rows[0][0] == "UNKNOWN"
        assert summary == "1 checked: 0 need attention."
    assert code == EXIT_OK


def test_an_unknown_language_flag_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["check", "--domain", NO_RDAP, "--lang", "fr"])

    captured = capsys.readouterr()
    assert exit_info.value.code == EXIT_USAGE
    assert captured.out == ""
    assert "invalid language 'fr'" in captured.err


# ---------------------------------------------------------------------------
# The names on the command line (RF-03)
# ---------------------------------------------------------------------------


def test_the_names_replace_an_invalid_file_in_the_directory(
    client_context: ssl.SSLContext,
    https_server: LocalHTTPServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bootstrap_url = serve_registry(https_server, {})
    _write_config(tmp_path, "this is not TOML\n")
    monkeypatch.chdir(tmp_path)

    code = main(
        ["check", "--domain", NO_RDAP],
        context=client_context,
        bootstrap_url=bootstrap_url,
        now=reports.NOW,
    )

    captured = capsys.readouterr()
    assert captured.err == ""
    assert code == EXIT_OK


def test_the_same_directory_without_names_reads_the_invalid_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Control for the test above: the file it ignores is read, and refused, without the names."""
    _write_config(tmp_path, "this is not TOML\n")
    monkeypatch.chdir(tmp_path)

    code = main(["check"])

    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert captured.out == ""
    assert "invalid TOML at line 1, column" in captured.err
    # The hint belongs to a missing default file only.
    assert "--host HOST[:PORT]" not in captured.err


def test_a_bad_name_is_a_configuration_error(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["check", "--host", "https://example.com", "--domain", "bad domain"])

    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "Nothing was checked: the names given with --host and --domain cannot be used.",
        "  --host[1]: the host must be a name or an IP address, not a URL or a path",
        "  --domain[1]: the domain must not contain whitespace",
    ]


@pytest.mark.parametrize("names", [["--host", "example.com"], ["--domain", "example.com"]])
def test_config_with_a_name_is_a_usage_error(
    names: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["check", "--config", "tlsvigil.toml", *names])

    captured = capsys.readouterr()
    assert exit_info.value.code == EXIT_USAGE
    assert captured.out == ""
    assert captured.err.startswith("usage: tlsvigil check")
    assert "not allowed with argument --config" in captured.err


def test_a_host_and_a_domain_go_together(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Control for the test above: --config clashes with the names, not a name with another."""
    _replace_run(monkeypatch, reports.report(reports.verified(), reports.dated()))

    code = main(["check", "--host", "example.com", "--domain", "example.com"])

    assert code == EXIT_OK
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# Configuration errors (RF-02, RF-05, RF-08)
# ---------------------------------------------------------------------------


def test_a_missing_default_file_shows_how_to_name_a_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    code = main(["check"])

    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert captured.out == ""
    assert captured.err.splitlines() == [
        "Nothing was checked: the configuration tlsvigil.toml cannot be used.",
        "  the file tlsvigil.toml does not exist",
        "Name what to check with --host HOST[:PORT] or --domain NAME, "
        "or write a tlsvigil.toml file in this directory.",
    ]


def test_a_missing_file_given_by_hand_has_no_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "other.toml"

    code = main(["check", "--config", str(missing)])

    err = capsys.readouterr().err
    assert code == EXIT_USAGE
    assert err.splitlines() == [
        f"Nothing was checked: the configuration {missing} cannot be used.",
        f"  the file {missing} does not exist",
    ]


@pytest.mark.parametrize(
    ("head", "flag", "heading"),
    [
        pytest.param('lang = "pt-BR"\n', [], "Nada foi verificado", id="the-file-lang"),
        pytest.param('lang = "pt-BR"\n', ["--lang", "en"], "Nothing was checked", id="the-flag"),
        pytest.param('lang = "fr"\n', [], "Nothing was checked", id="an-invalid-lang"),
    ],
)
def test_configuration_errors_are_written_in_the_language_of_the_run(
    head: str,
    flag: list[str],
    heading: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write_config(tmp_path, head + "timeout = 0\n" + _domains_toml(NO_RDAP), "my.toml")

    code = main(["check", "--config", str(path), *flag])

    lines = capsys.readouterr().err.splitlines()
    assert code == EXIT_USAGE
    assert lines[0].startswith(heading)
    assert str(path) in lines[0]
    expected = "o timeout deve ser" if heading == "Nada foi verificado" else "the timeout must be"
    assert any(line.startswith(f"  timeout: {expected}") for line in lines)


def test_a_configuration_error_with_json_leaves_the_output_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(tmp_path, "timeout = 0\n" + _domains_toml(NO_RDAP))
    monkeypatch.chdir(tmp_path)

    code = main(["check", "--json"])

    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert captured.out == ""
    assert captured.err.startswith("Nothing was checked: the configuration tlsvigil.toml")


def test_the_text_of_the_file_is_escaped_in_a_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A key that clears the screen: TOML writes it with an escape, the parser gives the character.
    _write_config(tmp_path, f'"{BACKSLASH}u001b[2J" = 1\n' + _domains_toml(NO_RDAP))
    monkeypatch.chdir(tmp_path)

    code = main(["check"])

    err = capsys.readouterr().err
    assert code == EXIT_USAGE
    assert ESC not in err
    assert f"  {BACKSLASH}x1b[2J: unknown key" in err


def test_an_invalid_file_opens_no_connection(
    ca: trustme.CA,
    serve: Serve,
    client_context: ssl.SSLContext,
    https_server: LocalHTTPServer,
    network_guard: NetworkGuard,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = runs.now()
    server = serve(leaf(ca, now, timedelta(days=90)))
    bootstrap_url = serve_registry(https_server, {})
    _write_config(tmp_path, "unknown = 1\n" + _hosts_toml(server.port) + _domains_toml("a.com"))
    monkeypatch.chdir(tmp_path)

    code = main(["check"], context=client_context, bootstrap_url=bootstrap_url, now=now)

    assert code == EXIT_USAGE
    assert network_guard.connects == []
    assert https_server.requests == []


def test_the_same_file_fixed_opens_the_connections(
    ca: trustme.CA,
    serve: Serve,
    client_context: ssl.SSLContext,
    https_server: LocalHTTPServer,
    network_guard: NetworkGuard,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control for the test above: the guard and the registry do see a run's connections."""
    now = runs.now()
    server = serve(leaf(ca, now, timedelta(days=90)))
    bootstrap_url = serve_registry(https_server, {})
    _write_config(tmp_path, _hosts_toml(server.port) + _domains_toml("a.com"))
    monkeypatch.chdir(tmp_path)

    code = main(["check"], context=client_context, bootstrap_url=bootstrap_url, now=now)

    assert code == EXIT_ATTENTION  # a.com is not in the registry: an error
    assert network_guard.connects != []
    assert [request.path for request in https_server.requests] == [
        runs.BOOTSTRAP_PATH,
        f"{runs.BASE_PATH}domain/a.com",
    ]


# ---------------------------------------------------------------------------
# A bug (RF-09; ADR-3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "sentence"),
    [
        pytest.param([], "tlsvigil: internal error; this is a bug", id="en"),
        pytest.param(["--lang", "pt-BR"], "tlsvigil: erro interno; isto é um defeito", id="pt-BR"),
    ],
)
def test_a_crash_exits_three_with_the_traceback(
    flag: list[str],
    sentence: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _crash(*args: object, **kwargs: object) -> Report:
        raise RuntimeError("a bug in the run")

    monkeypatch.setattr(cli, "run", _crash)

    code = main(["check", "--domain", NO_RDAP, *flag])

    captured = capsys.readouterr()
    assert code == EXIT_INTERNAL
    assert captured.out == ""
    assert captured.err.startswith(sentence)
    assert "Traceback (most recent call last):" in captured.err
    assert captured.err.rstrip().endswith("RuntimeError: a bug in the run")


# ---------------------------------------------------------------------------
# The streams (RNF-03)
# ---------------------------------------------------------------------------


def test_a_character_the_output_cannot_encode_is_escaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = "名前解決に失敗しました"
    _replace_run(monkeypatch, reports.report(reports.unreachable(message=message)))
    # A redirected output on Windows: a legacy code page with no Japanese in it.
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="\n")
    monkeypatch.setattr(sys, "stdout", stream)

    code = main(["check", "--domain", NO_RDAP])

    stream.flush()
    written = stream.buffer.getvalue().decode("cp1252")
    escaped = message.encode("cp1252", "backslashreplace").decode("cp1252")
    assert escaped.startswith(f"{BACKSLASH}u")
    assert f"({escaped})" in written
    assert written.endswith("1 checked: 1 needs attention.\n")
    assert code == EXIT_ATTENTION


def test_a_stream_without_reconfigure_is_left_as_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _replace_run(monkeypatch, reports.report(reports.verified()))
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)

    code = main(["check", "--domain", NO_RDAP])

    assert code == EXIT_OK
    assert stream.getvalue().endswith("1 checked: 0 need attention.\n")


class _ClosedPipe(io.StringIO):
    """A standard output whose reader has gone, as in ``tlsvigil check | head -1``."""

    def __init__(self, fd: int) -> None:
        """
        Stand in front of a real descriptor, which the command points at ``os.devnull``.

        Args:
            fd (int): A descriptor the test opened.
        """
        super().__init__()
        self._fd = fd

    def write(self, text: str, /) -> int:
        """
        Fail as a closed pipe does.

        Args:
            text (str): Ignored.

        Raises:
            BrokenPipeError: Always.
        """
        raise BrokenPipeError(errno.EPIPE, "Broken pipe")

    def fileno(self) -> int:
        """
        Give the descriptor behind the stream.

        Returns:
            int: The descriptor of the test.
        """
        return self._fd


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        pytest.param(reports.verified(), EXIT_OK, id="ok"),
        pytest.param(reports.invalid(), EXIT_ATTENTION, id="attention"),
    ],
)
def test_a_closed_pipe_keeps_the_exit_code(
    item: reports.Item,
    expected: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _replace_run(monkeypatch, reports.report(item))
    target = tmp_path / "stdout"
    with target.open("wb") as file:
        monkeypatch.setattr(sys, "stdout", _ClosedPipe(file.fileno()))

        code = main(["check", "--domain", NO_RDAP])

        # The descriptor now points at os.devnull: what is written there is gone.
        file.write(b"after the pipe closed")
    assert code == expected
    assert target.read_bytes() == b""
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


def test_every_key_the_command_writes_is_in_the_catalogue() -> None:
    source = Path(cli.__file__).read_text(encoding="utf-8")
    keys = set(re.findall(r'"(cli\.[a-z_]+)"', source))
    assert keys == {
        "cli.file_heading",
        "cli.names_heading",
        "cli.environment_heading",
        "cli.missing_hint",
        "cli.internal_error",
    }
    assert keys <= set(MESSAGES)
    assert all(set(MESSAGES[key]) == set(Lang) for key in keys)
