"""
The two outputs of a run: text for a person and JSON for a program (spec 004, RF-06, RF-07).

:func:`render_text` writes one line per item and a summary, in the language of the run; every
value from the outside (a certificate, an RDAP answer, an error message, the configuration) passes
through :func:`printable` first, so no control or format character reaches the terminal (RNF-02,
ADR-4). :func:`render_json` writes one ASCII document whose keys never depend on the status or the
language (RNF-03, RNF-05, ADR-5).
"""

from __future__ import annotations

import json
import unicodedata
from datetime import UTC, datetime
from typing import Final

from tlsvigil import __version__
from tlsvigil.i18n import Lang, plural, translate
from tlsvigil.runner import STATUSES, CertificateItem, DomainItem, Report

__all__ = [
    "NAME_WIDTH_CAP",
    "SCHEMA_VERSION",
    "display_name",
    "printable",
    "render_json",
    "render_text",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The version of the JSON shape: a key is never renamed, removed or retyped within it (RNF-05).
SCHEMA_VERSION: Final = 1

# The name column grows with the longest name up to this width; a longer name pushes its own
# detail to the right instead of being cut, so the name is never shown wrong (RF-06).
NAME_WIDTH_CAP: Final = 40

# The space between two columns.
_GAP: Final = "  "

# Control (Cc), format (Cf: the bidirectional overrides, the zero-width characters), surrogate
# (Cs), private use (Co), unassigned (Cn), and the line and paragraph separators (Zl, Zp): none
# of them may reach a terminal as it is (ADR-4).
_ESCAPED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


def printable(text: str) -> str:
    r"""
    Escape every character of ``text`` that would act on a terminal (RNF-02, ADR-4).

    Args:
        text (str): A value from the outside, such as an issuer or an error message.

    Returns:
        str: The same text on one line, each control, format or separator character replaced
            by its Python escape (``\x1b``, ``\n``, ``\u202e``); the rest unchanged.
    """
    return "".join(
        # unicode_escape gives the short form a person knows (\n, \x1b, \u202e, \U000e0001).
        (
            char.encode("unicode_escape").decode("ascii")
            if unicodedata.category(char) in _ESCAPED_CATEGORIES
            else char
        )
        for char in text
    )


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------


def _utc_date(moment: datetime) -> str:
    """
    Give the UTC date of an aware time in ISO form.

    Args:
        moment (datetime): An aware time.

    Returns:
        str: ``YYYY-MM-DD``.
    """
    return moment.astimezone(UTC).date().isoformat()


def display_name(item: CertificateItem) -> str:
    """
    Give the name of a certificate: ``host:port``, an IPv6 address in brackets.

    The lines of the output and the notifications (spec 005, RF-11) name a host the same way.

    Args:
        item (CertificateItem): The item.

    Returns:
        str: The name, escaped.
    """
    host = item.entry.host
    # The brackets were removed when the entry was read (ADR-8); without them the port would
    # read as one more group of the address.
    shown = f"[{host}]" if ":" in host else host
    return printable(f"{shown}:{item.entry.port}")


def _with_message(detail: str, message: str | None) -> str:
    """
    Add the text of a failure after its kind, in parentheses, when there is one.

    Args:
        detail (str): The translated detail.
        message (str | None): The text as it came, from the library or the server.

    Returns:
        str: The detail, then `` (message)`` escaped.
    """
    return detail if not message else f"{detail} ({printable(message)})"


def _days_detail(lang: Lang, kind: str, days_left: int, expiry: datetime) -> str:
    """
    Write the detail of a dated item: in how many days, or how long ago, and the date.

    Args:
        lang (Lang): The language of the output.
        kind (str): ``certificate`` or ``domain``, the middle of the catalogue key.
        days_left (int): Whole days until the expiry; negative once it has passed.
        expiry (datetime): The expiry, aware.

    Returns:
        str: Such as ``certificate expires in 9 days (2026-12-06)``.
    """
    date = _utc_date(expiry)
    if days_left == 0:
        return translate(lang, f"line.{kind}.today", date=date)
    if days_left < 0:
        # Only a domain gets here: a certificate past its date fails the verification.
        days = plural(lang, "days", -days_left)
        return translate(lang, f"line.{kind}.expired", days=days, date=date)
    days = plural(lang, "days", days_left)
    return translate(lang, f"line.{kind}.expires", days=days, date=date)


def _certificate_detail(item: CertificateItem, lang: Lang) -> str:
    """
    Write the detail of a certificate line (RF-06).

    Args:
        item (CertificateItem): The item.
        lang (Lang): The language of the output.

    Returns:
        str: The days and the date, the reason, or the kind of error and its message.
    """
    result = item.result
    if result.reason is not None:
        reason = translate(lang, f"reason.certificate.{result.reason}")
        return translate(lang, "line.certificate.invalid", reason=reason)
    if result.error_kind is not None:
        kind = translate(lang, f"error.certificate.{result.error_kind}")
        return _with_message(translate(lang, "line.certificate.error", kind=kind), result.message)
    # A verified result carries both; classify() already refused one without its days.
    assert result.days_left is not None and result.not_after is not None
    return _days_detail(lang, "certificate", result.days_left, result.not_after)


def _domain_detail(item: DomainItem, lang: Lang) -> str:
    """
    Write the detail of a domain line (RF-06).

    Args:
        item (DomainItem): The item.
        lang (Lang): The language of the output.

    Returns:
        str: The days and the date, the reason, or the kind of error and its message.
    """
    result = item.result
    if result.reason is not None:
        reason = translate(lang, f"reason.domain.{result.reason}")
        return translate(lang, "line.domain.unknown", reason=reason)
    if result.error_kind is not None:
        kind = translate(lang, f"error.domain.{result.error_kind}")
        return _with_message(translate(lang, "line.domain.error", kind=kind), result.message)
    # A dated result carries both; classify() already refused one without its days.
    assert result.days_left is not None and result.expiration is not None
    return _days_detail(lang, "domain", result.days_left, result.expiration)


def render_text(report: Report, lang: Lang) -> str:
    """
    Write the report for a person: one line per item, then the summary (RF-06).

    Args:
        report (Report): The report of a run.
        lang (Lang): The language of the output.

    Returns:
        str: The lines, the certificates first, a blank line and the summary, ending in a
            newline. Every value from the outside is escaped by :func:`printable`.
    """
    rows = [
        (str(item.status), display_name(item), _certificate_detail(item, lang))
        for item in report.certificates
    ] + [
        (str(item.status), printable(item.entry.domain), _domain_detail(item, lang))
        for item in report.domains
    ]

    # As wide as the widest label of the language, so the columns do not move between runs.
    labels = {status: translate(lang, f"status.{status}") for status in STATUSES}
    status_width = max(len(label) for label in labels.values())
    name_width = min(max((len(name) for _, name, _ in rows), default=0), NAME_WIDTH_CAP)

    lines = [
        f"{labels[status]:<{status_width}}{_GAP}{name:<{name_width}}{_GAP}{detail}"
        for status, name, detail in rows
    ]

    counts = report.counts()
    checked = plural(lang, "checked", counts["total"])
    attention = plural(lang, "attention", counts["attention"])
    summary = translate(lang, "summary", checked=checked, attention=attention)
    # A blank line sets the summary apart from the items; with no item there is nothing to part.
    return "\n".join([*lines, "", summary] if lines else [summary]) + "\n"


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def _iso(moment: datetime | None) -> str | None:
    """
    Give an aware time in ISO 8601 with its UTC offset.

    Args:
        moment (datetime | None): An aware time, or nothing.

    Returns:
        str | None: Such as ``2026-12-06T23:59:59+00:00``; ``None`` for nothing.
    """
    return None if moment is None else moment.astimezone(UTC).isoformat()


def _certificate_object(item: CertificateItem) -> dict[str, object]:
    """
    Build the JSON object of a certificate: every key, whatever the status (ADR-5).

    Args:
        item (CertificateItem): The item.

    Returns:
        dict[str, object]: The fields of the result, the status and the threshold.
    """
    result = item.result
    return {
        "host": result.host,
        "port": result.port,
        "status": item.status.value,
        "warn_days": item.entry.warn_days,
        "checked_at": _iso(result.checked_at),
        "not_after": _iso(result.not_after),
        "days_left": result.days_left,
        "issuer_organization": result.issuer_organization,
        "issuer_common_name": result.issuer_common_name,
        "reason": None if result.reason is None else result.reason.value,
        "error_kind": None if result.error_kind is None else result.error_kind.value,
        "message": result.message,
    }


def _domain_object(item: DomainItem) -> dict[str, object]:
    """
    Build the JSON object of a domain: every key, whatever the status (ADR-5).

    Args:
        item (DomainItem): The item.

    Returns:
        dict[str, object]: The fields of the result, the status and the threshold.
    """
    result = item.result
    return {
        "domain": result.domain,
        "status": item.status.value,
        "warn_days": item.entry.warn_days,
        "checked_at": _iso(result.checked_at),
        "expiration": _iso(result.expiration),
        "days_left": result.days_left,
        "server": result.server,
        "registry_name": result.registry_name,
        "statuses": list(result.statuses),
        "reason": None if result.reason is None else result.reason.value,
        "error_kind": None if result.error_kind is None else result.error_kind.value,
        "message": result.message,
    }


def render_json(report: Report, exit_code: int) -> str:
    """
    Write the report for a program: one JSON document, the same in every language (RF-07).

    Args:
        report (Report): The report of a run.
        exit_code (int): The exit code the command ends with.

    Returns:
        str: The document, pure ASCII (RNF-03), indented by two spaces, ending in a newline.
            The encoder escapes every control character inside a string (RNF-02).
    """
    document = {
        "schema_version": SCHEMA_VERSION,
        "tlsvigil_version": __version__,
        "started_at": _iso(report.started_at),
        "exit_code": exit_code,
        "summary": report.counts(),
        "certificates": [_certificate_object(item) for item in report.certificates],
        "domains": [_domain_object(item) for item in report.domains],
    }
    return json.dumps(document, ensure_ascii=True, indent=2) + "\n"
