"""
Tests for the reading of an RDAP domain answer (spec 003, RF-01, RF-03, RF-05, RNF-03, RNF-04).

The reading is a pure function of the body, the name asked for, the server and the time, so these
tests feed it the hand-written documents of ``tests/data/rdap/`` (changed in code where a case
needs another date) with a fixed ``now`` and need no server.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tlsvigil.rdap import (
    DomainResult,
    ErrorKind,
    Outcome,
    UnknownReason,
    _read_domain_answer,
)

DATA = Path(__file__).parent / "data" / "rdap"
# A fixed, aware time: the days left never depend on the clock of the machine (RNF-05).
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
# The host the tests pretend to have asked; the reading copies it, it never reads one.
SERVER = "rdap.example"


def document(name: str) -> dict[str, Any]:
    """
    Load one of the hand-written answers.

    Args:
        name (str): The file name in ``tests/data/rdap/``, without ``.json``.

    Returns:
        dict[str, Any]: The parsed document, a fresh copy for each call.
    """
    loaded: dict[str, Any] = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


def with_expiration(doc: dict[str, Any], date: str) -> dict[str, Any]:
    """
    Give ``doc`` one ``expiration`` event with ``date``, keeping its other events.

    Args:
        doc (dict[str, Any]): A domain answer.
        date (str): The ``eventDate`` of the new event.

    Returns:
        dict[str, Any]: The same document, changed.
    """
    others = [event for event in doc["events"] if event["eventAction"] != "expiration"]
    doc["events"] = [*others, {"eventAction": "expiration", "eventDate": date}]
    return doc


def read(body: dict[str, Any] | bytes, domain: str = "example.com") -> DomainResult:
    """
    Read an answer as the check would after asking ``SERVER`` at ``NOW``.

    Args:
        body (dict[str, Any] | bytes): A document, encoded here, or a raw body.
        domain (str): The normalized name asked for.

    Returns:
        DomainResult: What the reading gave.
    """
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return _read_domain_answer(raw, domain=domain, server=SERVER, checked_at=NOW)


def assert_invalid(result: DomainResult, domain: str = "example.com") -> None:
    """
    Assert that ``result`` is an ``invalid_response`` error with no date and a message.

    Args:
        result (DomainResult): The result to check.
        domain (str): The name that was asked for.
    """
    assert result.outcome is Outcome.ERROR
    assert result.error_kind is ErrorKind.INVALID_RESPONSE
    assert (result.domain, result.server, result.checked_at) == (domain, SERVER, NOW)
    assert (result.expiration, result.days_left, result.reason) == (None, None, None)
    assert result.message


# ---------------------------------------------------------------------------
# Dated
# ---------------------------------------------------------------------------


def test_a_dated_br_answer_gives_its_expiration_and_days_left() -> None:
    result = read(document("example.com.br"), "example.com.br")

    assert result == DomainResult(
        domain="example.com.br",
        checked_at=NOW,
        outcome=Outcome.DATED,
        server=SERVER,
        expiration=datetime(2027, 2, 25, 12, 0, tzinfo=UTC),
        days_left=90,
        registry_name="example.com.br",
        statuses=("active",),
    )


def test_a_dated_com_answer_keeps_the_registry_name_as_returned() -> None:
    """Verisign returns the name in capitals; it is compared in lowercase and kept as read."""
    result = read(document("example.com"))

    assert result.outcome is Outcome.DATED
    assert result.expiration == datetime(2027, 8, 13, 4, 0, tzinfo=UTC)
    assert result.days_left == 258
    assert result.registry_name == "EXAMPLE.COM"
    assert result.statuses == (
        "client delete prohibited",
        "client transfer prohibited",
        "client update prohibited",
    )


@pytest.mark.parametrize(
    ("offset", "days_left"),
    [
        (timedelta(days=30, hours=1), 30),
        (timedelta(days=30), 30),
        (timedelta(days=30) - timedelta(seconds=1), 29),
        (timedelta(hours=2), 0),
        (-timedelta(hours=1), -1),
        (-timedelta(days=1), -1),
        (-timedelta(days=1, seconds=1), -2),
    ],
    ids=["n-plus-1h", "n-exactly", "n-minus-1s", "2h", "1h-ago", "1d-ago", "1d-1s-ago"],
)
def test_the_days_left_are_rounded_down(offset: timedelta, days_left: int) -> None:
    """0 in the last 24 hours, -1 in the first 24 hours after the date (RF-01)."""
    expiration = NOW + offset
    doc = with_expiration(document("example.com"), expiration.isoformat())

    result = read(doc)

    assert result.outcome is Outcome.DATED
    assert (result.expiration, result.days_left) == (expiration, days_left)


def test_the_earliest_of_two_expiration_events_wins() -> None:
    result = read(document("two-expirations"))

    assert result.expiration == datetime(2027, 2, 13, 4, 0, tzinfo=UTC)
    assert result.days_left == 77  # 77 days and 16 hours


@pytest.mark.parametrize(
    "date",
    ["2027-02-25t12:00:00z", "2027-02-25T09:00:00-03:00", "2027-02-25T12:00:00.000+00:00"],
    ids=["lowercase-t-and-z", "offset", "fraction"],
)
def test_an_rfc_3339_date_is_read_in_any_of_its_forms(date: str) -> None:
    doc = with_expiration(document("example.com.br"), date)

    result = read(doc, "example.com.br")

    assert result.expiration == datetime(2027, 2, 25, 12, 0, tzinfo=UTC)
    assert result.expiration.utcoffset() == timedelta(0)
    assert result.days_left == 90


@pytest.mark.parametrize(
    "ldh_name", ["example.com.", "Example.Com", "EXAMPLE.COM."], ids=["dot", "mixed", "caps-dot"]
)
def test_the_registry_name_matches_in_any_case_and_with_a_trailing_dot(ldh_name: str) -> None:
    doc = document("example.com")
    doc["ldhName"] = ldh_name

    result = read(doc)

    assert result.outcome is Outcome.DATED
    assert result.registry_name == ldh_name


# ---------------------------------------------------------------------------
# Undated
# ---------------------------------------------------------------------------


def test_an_answer_without_an_expiration_event_is_undated_with_its_details() -> None:
    result = read(document("no-expiration"))

    assert result == DomainResult(
        domain="example.com",
        checked_at=NOW,
        outcome=Outcome.UNDATED,
        server=SERVER,
        registry_name="example.com",
        statuses=("active",),
        reason=UnknownReason.NO_EXPIRATION,
    )


@pytest.mark.parametrize(
    "events",
    [None, {}, "expiration", [], ["expiration", 1, None]],
    ids=["missing", "object", "string", "empty", "items-not-objects"],
)
def test_events_that_hold_no_expiration_event_give_no_expiration(events: object) -> None:
    doc = document("example.com")
    if events is None:
        del doc["events"]
    else:
        doc["events"] = events

    result = read(doc)

    assert result.outcome is Outcome.UNDATED
    assert result.reason is UnknownReason.NO_EXPIRATION


# ---------------------------------------------------------------------------
# Statuses and text as data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (None, ()),
        ("active", ()),
        ({"active": True}, ()),
        (["active", 1, None, ["x"], "client hold"], ("active", "client hold")),
    ],
    ids=["missing", "string", "object", "mixed-items"],
)
def test_only_the_string_items_of_a_status_list_are_kept(
    status: object, expected: tuple[str, ...]
) -> None:
    doc = document("example.com")
    if status is None:
        del doc["status"]
    else:
        doc["status"] = status

    result = read(doc)

    assert result.outcome is Outcome.DATED
    assert result.statuses == expected


def test_markup_and_control_characters_are_kept_as_read() -> None:
    """The reading never interprets text; later specs escape it for each channel (RNF-03)."""
    statuses = ["<script>alert(1)</script>", "line\nbreak\x1b[2J\x00", "javascript:void(0)"]
    doc = document("example.com")
    doc["status"] = statuses
    doc["ldhName"] = "ExAmPlE.CoM"

    result = read(doc)

    assert result.statuses == tuple(statuses)
    assert result.registry_name == "ExAmPlE.CoM"
    assert result.server == SERVER


# ---------------------------------------------------------------------------
# Invalid answers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b"<html><body>Service unavailable</body></html>",
        b"",
        b"\xff\xfe\xfa",
        b"[" * 300_000,
        b'{"objectClassName": "domain", "port43": ' + b"1" * 5000 + b"}",
        b"[]",
        b"null",
    ],
    ids=["html", "empty", "not-utf8", "deeply-nested", "long-number", "list", "null"],
)
def test_a_body_that_is_not_a_json_object_is_an_invalid_response(body: bytes) -> None:
    """A deeply nested body and a number of 5000 digits are caught, never raised (RNF-04)."""
    assert_invalid(read(body))


@pytest.mark.parametrize(
    "class_name", ["entity", "nameserver", "Domain", None], ids=["entity", "ns", "case", "missing"]
)
def test_an_object_that_is_not_a_domain_is_an_invalid_response(class_name: str | None) -> None:
    doc = document("example.com")
    if class_name is None:
        del doc["objectClassName"]
    else:
        doc["objectClassName"] = class_name

    assert_invalid(read(doc))


@pytest.mark.parametrize(
    "ldh_name",
    [None, 1, "com", "www.example.com", "example.com.br", "example.com..", "xample.com"],
    ids=["missing", "int", "parent", "child", "other-tld", "two-dots", "other"],
)
def test_an_answer_about_another_name_is_an_invalid_response(ldh_name: object) -> None:
    """Another domain's date would otherwise be reported as the user's (ADR-8)."""
    doc = document("example.com")
    if ldh_name is None:
        del doc["ldhName"]
    else:
        doc["ldhName"] = ldh_name

    assert_invalid(read(doc))


@pytest.mark.parametrize(
    "date",
    [
        "next year",
        "2027-02-25",
        "2027-02-25T12:00:00",
        "2027-02-30T00:00:00Z",
        "10000-01-01T00:00:00Z",
        "9999-12-31T23:00:00-05:00",
        "0001-01-01T00:00:00+05:00",
        1798200000,
        None,
    ],
    ids=[
        "words",
        "date-only",
        "naive",
        "day-out-of-range",
        "year-out-of-range",
        "past-year-9999-in-utc",
        "before-year-1-in-utc",
        "number",
        "null",
    ],
)
def test_an_expiration_date_that_cannot_be_read_is_an_invalid_response(date: object) -> None:
    doc = document("example.com")
    doc["events"] = [{"eventAction": "expiration", "eventDate": date}]

    assert_invalid(read(doc))


def test_one_unreadable_date_among_two_expiration_events_is_an_invalid_response() -> None:
    """The earliest date cannot be known when one of them cannot be read."""
    doc = document("two-expirations")
    doc["events"][2]["eventDate"] = "soon"

    assert_invalid(read(doc))


def test_an_expiration_event_without_a_date_is_an_invalid_response() -> None:
    doc = document("example.com")
    doc["events"] = [{"eventAction": "expiration"}]

    assert_invalid(read(doc))
