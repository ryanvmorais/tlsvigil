"""
Tests for the text and the JSON output (spec 004, RF-06, RF-07, RNF-02, RNF-03, RNF-05).

The reports are built by hand (``reports.py``) with dates counted from a fixed time, so the
expected text is written out in full; no server and no connection is involved.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

import reports
from tlsvigil import __version__, certificate, rdap
from tlsvigil.i18n import Lang
from tlsvigil.output import NAME_WIDTH_CAP, SCHEMA_VERSION, printable, render_json, render_text
from tlsvigil.runner import STATUSES, Report

# The report of the design's example: three need attention, one of each status.
EXAMPLE = reports.report(
    reports.verified("example.com", days_left=9),
    reports.verified("mail.example.com", 465, days_left=61),
    reports.invalid("old.example.com"),
    reports.unreachable("typo.example.com"),
    reports.dated("example.com", days_left=301),
    reports.undated("example.de"),
)

CERTIFICATE_KEYS = [
    "host",
    "port",
    "status",
    "warn_days",
    "checked_at",
    "not_after",
    "days_left",
    "issuer_organization",
    "issuer_common_name",
    "reason",
    "error_kind",
    "message",
]
DOMAIN_KEYS = [
    "domain",
    "status",
    "warn_days",
    "checked_at",
    "expiration",
    "days_left",
    "server",
    "registry_name",
    "statuses",
    "reason",
    "error_kind",
    "message",
]


def _lines(report: Report, lang: Lang = Lang.EN) -> list[str]:
    """
    Give the item lines of the text output, without the blank line and the summary.

    Args:
        report (Report): The report.
        lang (Lang): The language.

    Returns:
        list[str]: One line per item.
    """
    return render_text(report, lang).splitlines()[:-2]


# ---------------------------------------------------------------------------
# printable() (RNF-02, ADR-4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        pytest.param("a\x1b[2Jb", "a\\x1b[2Jb", id="ESC (Cc)"),
        pytest.param("a\rb", "a\\rb", id="carriage return (Cc)"),
        pytest.param("a\nb", "a\\nb", id="line feed (Cc)"),
        pytest.param("a\tb", "a\\tb", id="tab (Cc)"),
        pytest.param("a\u202eb", "a\\u202eb", id="right-to-left override (Cf)"),
        pytest.param("a\u200bb", "a\\u200bb", id="zero-width space (Cf)"),
        pytest.param("a\ud800b", "a\\ud800b", id="lone surrogate (Cs)"),
        pytest.param("a\ue000b", "a\\ue000b", id="private use (Co)"),
        pytest.param("a\u0378b", "a\\u0378b", id="unassigned (Cn)"),
        pytest.param("a\u2028b", "a\\u2028b", id="line separator (Zl)"),
        pytest.param("a\u2029b", "a\\u2029b", id="paragraph separator (Zp)"),
        pytest.param("a\U000e0001b", "a\\U000e0001b", id="language tag (Cf, astral)"),
    ],
)
def test_printable_escapes_what_would_act_on_a_terminal(text: str, shown: str) -> None:
    assert printable(text) == shown
    assert printable(text).isascii()


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("Autoridade Certificadora São Paulo", id="accented"),
        pytest.param("日本の認証局", id="Japanese"),
        pytest.param("a\\x1b b {0} {x}", id="backslash, space and braces"),
        pytest.param("", id="empty"),
    ],
)
def test_printable_leaves_plain_text_as_it_is(text: str) -> None:
    """Control for the test above: a letter, a space or a brace is not escaped."""
    assert printable(text) == text


# ---------------------------------------------------------------------------
# The text output (RF-06)
# ---------------------------------------------------------------------------


def test_the_text_output_in_english() -> None:
    assert render_text(EXAMPLE, Lang.EN) == (
        "EXPIRING  example.com:443       certificate expires in 9 days (2026-12-06)\n"
        "OK        mail.example.com:465  certificate expires in 61 days (2027-01-27)\n"
        "INVALID   old.example.com:443   certificate failed verification: expired\n"
        "ERROR     typo.example.com:443  certificate not checked: the name does not resolve"
        " ([Errno 11001] getaddrinfo failed)\n"
        "OK        example.com           domain expires in 301 days (2027-09-24)\n"
        "UNKNOWN   example.de            domain expiry unknown: the registry has no RDAP service\n"
        "\n"
        "6 checked: 3 need attention.\n"
    )


def test_the_text_output_in_portuguese() -> None:
    assert render_text(EXAMPLE, Lang.PT_BR) == (
        "VENCENDO      example.com:443       certificado vence em 9 dias (2026-12-06)\n"
        "OK            mail.example.com:465  certificado vence em 61 dias (2027-01-27)\n"
        "INVÁLIDO      old.example.com:443   certificado não passou na verificação: vencido\n"
        "ERRO          typo.example.com:443  certificado não verificado: o nome não resolve"
        " ([Errno 11001] getaddrinfo failed)\n"
        "OK            example.com           domínio vence em 301 dias (2027-09-24)\n"
        "DESCONHECIDO  example.de            vencimento do domínio desconhecido:"
        " o registro não tem serviço RDAP\n"
        "\n"
        "6 verificados: 3 precisam de atenção.\n"
    )


@pytest.mark.parametrize(
    ("item", "english", "portuguese"),
    [
        pytest.param(
            reports.dated(days_left=3),
            "domain expires in 3 days (2026-11-30)",
            "domínio vence em 3 dias (2026-11-30)",
            id="expiring domain",
        ),
        pytest.param(
            reports.failed(kind=rdap.ErrorKind.NOT_FOUND, message="HTTP 404"),
            "domain not checked: the registry has no such domain (HTTP 404)",
            "domínio não verificado: o registro não tem esse domínio (HTTP 404)",
            id="domain error",
        ),
        pytest.param(
            reports.undated(reason=rdap.UnknownReason.NO_EXPIRATION),
            "domain expiry unknown: the registry gives no expiry date",
            "vencimento do domínio desconhecido: o registro não informa a data de vencimento",
            id="no expiration",
        ),
        pytest.param(
            reports.invalid(reason=certificate.InvalidReason.HOSTNAME_MISMATCH),
            "certificate failed verification: the certificate is not for this host",
            "certificado não passou na verificação: o certificado não é deste host",
            id="hostname mismatch",
        ),
        pytest.param(
            reports.unreachable(kind=certificate.ErrorKind.TIMEOUT, message=None),
            "certificate not checked: no answer within the timeout",
            "certificado não verificado: sem resposta dentro do timeout",
            id="error without a message",
        ),
        pytest.param(
            reports.unreachable(kind=certificate.ErrorKind.REFUSED, message=""),
            "certificate not checked: the connection was refused",
            "certificado não verificado: a conexão foi recusada",
            id="error with an empty message",
        ),
    ],
)
def test_the_detail_of_each_kind_of_line(item: reports.Item, english: str, portuguese: str) -> None:
    assert _lines(reports.report(item), Lang.EN)[0].endswith("  " + english)
    assert _lines(reports.report(item), Lang.PT_BR)[0].endswith("  " + portuguese)


@pytest.mark.parametrize(
    ("days_left", "english", "portuguese"),
    [
        pytest.param(2, "expires in 2 days (2026-11-29)", "vence em 2 dias (2026-11-29)", id="2"),
        pytest.param(1, "expires in 1 day (2026-11-28)", "vence em 1 dia (2026-11-28)", id="1"),
        pytest.param(0, "expires today (2026-11-27)", "vence hoje (2026-11-27)", id="0"),
        pytest.param(-1, "expired 1 day ago (2026-11-26)", "venceu há 1 dia (2026-11-26)", id="-1"),
        pytest.param(
            -3, "expired 3 days ago (2026-11-24)", "venceu há 3 dias (2026-11-24)", id="-3"
        ),
    ],
)
def test_the_days_of_a_domain(days_left: int, english: str, portuguese: str) -> None:
    report = reports.report(reports.dated(days_left=days_left))

    assert _lines(report, Lang.EN)[0].endswith(f"  domain {english}")
    assert _lines(report, Lang.PT_BR)[0].endswith(f"  domínio {portuguese}")


@pytest.mark.parametrize(
    ("days_left", "english"),
    [
        pytest.param(1, "certificate expires in 1 day (2026-11-28)", id="1"),
        pytest.param(0, "certificate expires today (2026-11-27)", id="0"),
    ],
)
def test_the_days_of_a_certificate(days_left: int, english: str) -> None:
    report = reports.report(reports.verified(days_left=days_left))

    assert _lines(report)[0].endswith("  " + english)


def test_the_date_is_the_utc_date() -> None:
    # 22:30 in São Paulo on the 27th is 01:30 UTC on the 28th: the line shows the 28th.
    item = reports.dated(days_left=0)
    evening = datetime(2026, 11, 27, 22, 30, tzinfo=timezone(timedelta(hours=-3)))
    item = replace(item, result=replace(item.result, expiration=evening))

    assert _lines(reports.report(item))[0].endswith("domain expires today (2026-11-28)")


def test_an_ipv6_host_is_shown_in_brackets() -> None:
    report = reports.report(reports.verified("2001:db8::1", 8443))

    assert _lines(report)[0].split()[1] == "[2001:db8::1]:8443"


def test_the_status_column_is_as_wide_as_the_widest_label_of_the_language() -> None:
    report = reports.report(reports.verified())

    # "UNKNOWN" and "EXPIRING" set the width in English; "DESCONHECIDO" in Portuguese.
    assert _lines(report, Lang.EN)[0].startswith("OK" + " " * 6 + "  example.com:443  ")
    assert _lines(report, Lang.PT_BR)[0].startswith("OK" + " " * 10 + "  example.com:443  ")


def test_the_name_column_is_as_wide_as_the_widest_name() -> None:
    report = reports.report(reports.dated("a.com"), reports.dated("abcdefgh.com"))

    first, second = _lines(report)

    assert first.index("domain") == second.index("domain")
    assert first.index("domain") == len("EXPIRING  ") + len("abcdefgh.com") + 2


def test_a_name_over_the_cap_pushes_its_detail_instead_of_being_cut() -> None:
    long_name = "a" * (NAME_WIDTH_CAP + 5) + ".com"
    report = reports.report(reports.dated("short.com"), reports.dated(long_name))

    short_line, long_line = _lines(report)

    assert f"  {long_name}  domain expires" in long_line
    assert short_line.index("domain") == len("EXPIRING  ") + NAME_WIDTH_CAP + 2
    assert long_line.index("domain") == len("EXPIRING  ") + len(long_name) + 2


@pytest.mark.parametrize(
    ("report", "english", "portuguese"),
    [
        pytest.param(
            reports.report(reports.verified(days_left=9)),
            "1 checked: 1 needs attention.",
            "1 verificado: 1 precisa de atenção.",
            id="one",
        ),
        pytest.param(
            reports.report(reports.verified(), reports.undated()),
            "2 checked: 0 need attention.",
            "2 verificados: 0 precisam de atenção.",
            id="none needs attention",
        ),
        pytest.param(
            Report(reports.NOW, (), ()),
            "0 checked: 0 need attention.",
            "0 verificados: 0 precisam de atenção.",
            id="empty",
        ),
    ],
)
def test_the_summary(report: Report, english: str, portuguese: str) -> None:
    assert render_text(report, Lang.EN).endswith(english + "\n")
    assert render_text(report, Lang.PT_BR).endswith(portuguese + "\n")


def test_a_blank_line_parts_the_items_from_the_summary() -> None:
    one = reports.report(reports.verified())

    assert render_text(one, Lang.EN).endswith("(2027-01-27)\n\n1 checked: 0 need attention.\n")
    # No item, no blank line: the summary alone (a valid configuration always has one entry).
    assert render_text(Report(reports.NOW, (), ()), Lang.EN) == "0 checked: 0 need attention.\n"


def test_an_error_message_with_escapes_stays_on_its_line() -> None:
    hostile = "refused\x1b[2J\r\nINVALID   forged.example:443   \u202eevil"
    report = reports.report(reports.unreachable(message=hostile), reports.verified())

    text = render_text(report, Lang.EN)

    assert len(text.splitlines()) == 2 + 2
    assert "\x1b" not in text and "\r" not in text and "\u202e" not in text
    assert "(refused\\x1b[2J\\r\\nINVALID   forged.example:443   \\u202eevil)" in text


def test_a_name_from_the_configuration_is_escaped_too() -> None:
    report = reports.report(reports.dated("exa\u202emple.com"))

    assert _lines(report)[0].split()[1] == "exa\\u202emple.com"


# ---------------------------------------------------------------------------
# The JSON output (RF-07, RNF-03, RNF-05, ADR-5)
# ---------------------------------------------------------------------------


def test_the_document_carries_the_run() -> None:
    document = json.loads(render_json(EXAMPLE, 1))

    assert list(document) == [
        "schema_version",
        "tlsvigil_version",
        "started_at",
        "exit_code",
        "summary",
        "certificates",
        "domains",
    ]
    assert document["schema_version"] == SCHEMA_VERSION == 1
    assert document["tlsvigil_version"] == __version__
    assert document["started_at"] == "2026-11-27T12:00:00+00:00"
    assert document["exit_code"] == 1
    assert document["summary"] == {
        "total": 6,
        "attention": 3,
        "ok": 2,
        "expiring": 1,
        "invalid": 1,
        "unknown": 1,
        "error": 1,
    }
    assert [item["host"] for item in document["certificates"]] == [
        "example.com",
        "mail.example.com",
        "old.example.com",
        "typo.example.com",
    ]
    assert [item["domain"] for item in document["domains"]] == ["example.com", "example.de"]


def test_the_document_is_indented_ascii_with_a_final_newline() -> None:
    text = render_json(EXAMPLE, 1)

    assert text.startswith('{\n  "schema_version": 1,\n')
    assert text.endswith("}\n") and not text.endswith("\n\n")
    assert text.isascii()


@pytest.mark.parametrize("status", STATUSES)
def test_every_key_is_present_whatever_the_status(status: str) -> None:
    items = {
        **reports.one_of_each(),
        "expiring_domain": reports.dated(days_left=3),
        "error_domain": reports.failed(),
    }
    document = json.loads(render_json(reports.report(*items.values()), 1))

    shown = [*document["certificates"], *document["domains"]]
    of_status = [item for item in shown if item["status"] == status]

    assert of_status
    for item in of_status:
        assert list(item) == (CERTIFICATE_KEYS if "host" in item else DOMAIN_KEYS)


def test_a_verified_certificate_in_the_document() -> None:
    document = json.loads(render_json(reports.report(reports.verified(days_left=9)), 1))

    assert document["certificates"] == [
        {
            "host": "example.com",
            "port": 443,
            "status": "expiring",
            "warn_days": 14,
            "checked_at": "2026-11-27T12:00:00+00:00",
            "not_after": "2026-12-06T12:00:00+00:00",
            "days_left": 9,
            "issuer_organization": "Example Trust",
            "issuer_common_name": "Example R1",
            "reason": None,
            "error_kind": None,
            "message": None,
        }
    ]


def test_a_failed_certificate_in_the_document() -> None:
    document = json.loads(render_json(reports.report(reports.invalid(), reports.unreachable()), 1))

    invalid, error = document["certificates"]
    assert (invalid["status"], invalid["reason"], invalid["error_kind"]) == (
        "invalid",
        "expired",
        None,
    )
    assert invalid["message"] == "certificate verify failed: certificate has expired"
    assert (error["status"], error["reason"], error["error_kind"]) == ("error", None, "dns")
    assert error["message"] == "[Errno 11001] getaddrinfo failed"
    for item in (invalid, error):
        assert item["not_after"] is None
        assert item["days_left"] is None


def test_the_domains_in_the_document() -> None:
    document = json.loads(
        render_json(reports.report(reports.dated(), reports.undated(), reports.failed()), 1)
    )

    dated, undated, failed = document["domains"]
    assert dated == {
        "domain": "example.com",
        "status": "ok",
        "warn_days": 30,
        "checked_at": "2026-11-27T12:00:00+00:00",
        "expiration": "2027-09-24T12:00:00+00:00",
        "days_left": 301,
        "server": "rdap.example.net",
        "registry_name": "EXAMPLE.COM",
        "statuses": ["client transfer prohibited"],
        "reason": None,
        "error_kind": None,
        "message": None,
    }
    assert (undated["status"], undated["reason"], undated["statuses"]) == (
        "unknown",
        "no_rdap_service",
        [],
    )
    assert undated["server"] is None
    assert (failed["status"], failed["error_kind"], failed["expiration"]) == (
        "error",
        "timeout",
        None,
    )


def test_outside_text_is_escaped_by_the_encoder() -> None:
    item = reports.verified(
        issuer_organization="Autoridade Certificadora São Paulo",
        issuer_common_name="\x1b[31mR1\u202e",
    )
    text = render_json(reports.report(item), 0)

    assert text.isascii()
    assert '"issuer_organization": "Autoridade Certificadora S\\u00e3o Paulo"' in text
    assert '"issuer_common_name": "\\u001b[31mR1\\u202e"' in text
    # The document keeps the exact text; only the text output escapes it for a terminal.
    certificate_object = json.loads(text)["certificates"][0]
    assert certificate_object["issuer_common_name"] == "\x1b[31mR1\u202e"


def test_the_exit_code_is_written_as_given() -> None:
    report = reports.report(reports.verified())

    assert json.loads(render_json(report, 0))["exit_code"] == 0
    assert json.loads(render_json(report, 3))["exit_code"] == 3
