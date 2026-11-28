"""
Tests for the findings of a run and the events of a channel (spec 005, RF-04 to RF-06, ADR-8).

:func:`~tlsvigil.alerts.findings` and :func:`~tlsvigil.alerts.plan` are pure, so every test builds
a report by hand (``tests/reports.py``) and reads the answers and the plan: no clock, no file and
no network. A run of several days is a loop of reports, each told the view of the one before.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import timedelta

import pytest

from reports import NOW, dated, failed, invalid, report, undated, unreachable, verified
from tlsvigil import certificate, rdap
from tlsvigil.alerts import (
    PAST_DUE,
    REMINDER_DAYS,
    Answer,
    Event,
    EventKind,
    Finding,
    Mark,
    Plan,
    findings,
    plan,
)
from tlsvigil.runner import CertificateItem, DomainItem

Item = CertificateItem | DomainItem

CERT_KEY = "certificate:example.com:443"
DOMAIN_KEY = "domain:example.com"


def _told(view: tuple[Finding, ...]) -> dict[str, Finding]:
    """
    Give what a channel was told, as the state keeps it, from the view of a plan.

    Args:
        view (tuple[Finding, ...]): The view of the run before.

    Returns:
        dict[str, Finding]: The same findings, by key.
    """
    return {finding.key: finding for finding in view}


def _step(
    told: Mapping[str, Finding], *items: Item, error_runs: Mapping[str, int] | None = None
) -> tuple[Plan, dict[str, int]]:
    """
    Run the findings and the plan of one channel for one report.

    Args:
        told (Mapping[str, Finding]): What the channel was told, by key.
        *items (Item): The items of the report.
        error_runs (Mapping[str, int] | None): The ``error`` runs before; ``None`` means none.

    Returns:
        tuple[Plan, dict[str, int]]: The plan of the channel and the ``error`` runs after.
    """
    answers, runs = findings(report(*items), error_runs or {})
    return plan(told, answers), runs


def _kinds(result: Plan) -> list[tuple[EventKind, str]]:
    """
    Give the kind and the key of every event, in order.

    Args:
        result (Plan): A plan.

    Returns:
        list[tuple[EventKind, str]]: One pair per event.
    """
    return [(event.kind, event.finding.key) for event in result.events]


def _expiring(
    key: str = CERT_KEY, name: str = "example.com:443", reminder: int | None = None
) -> Finding:
    """
    Build an ``expiring`` finding as a channel was told it.

    Args:
        key (str): The key of the entry.
        name (str): The entry as shown.
        reminder (int | None): The lowest reminder day told.

    Returns:
        Finding: The finding, dated 9 days after :data:`~reports.NOW`.
    """
    return Finding(key, name, "expiring", None, NOW + timedelta(days=9), reminder)


# ---------------------------------------------------------------------------
# The answers of a run (the first table of the design)
# ---------------------------------------------------------------------------


def test_an_expiring_certificate_is_a_finding_with_its_date_and_reminder() -> None:
    answers, runs = findings(report(verified(days_left=7, warn_days=14)), {CERT_KEY: 3})

    assert answers == {
        CERT_KEY: Finding(CERT_KEY, "example.com:443", "expiring", None, NOW + timedelta(days=7), 7)
    }
    assert runs == {}


def test_an_invalid_certificate_is_a_finding_with_its_reason_as_text() -> None:
    answers, runs = findings(report(invalid("example.com")), {})

    assert answers == {CERT_KEY: Finding(CERT_KEY, "example.com:443", "invalid", "expired")}
    # The answer is a `Finding | Mark` union; the line above shows it is a Finding.
    assert type(answers[CERT_KEY].reason) is str  # type: ignore[union-attr]
    assert runs == {}


def test_the_first_error_holds_and_counts_one() -> None:
    answers, runs = findings(report(unreachable("example.com")), {})

    assert answers == {CERT_KEY: Mark.HOLD}
    assert runs == {CERT_KEY: 1}


@pytest.mark.parametrize("before", [1, 2], ids=["second", "third"])
def test_an_error_after_an_error_is_a_finding_and_counts_on(before: int) -> None:
    answers, runs = findings(report(unreachable("example.com")), {CERT_KEY: before})

    assert answers == {CERT_KEY: Finding(CERT_KEY, "example.com:443", "error", "dns")}
    assert runs == {CERT_KEY: before + 1}


@pytest.mark.parametrize(
    ("item", "key", "mark"),
    [
        (verified(days_left=61), CERT_KEY, Mark.RESOLVED),
        (dated(days_left=301), DOMAIN_KEY, Mark.RESOLVED),
        (undated("example.com"), DOMAIN_KEY, Mark.UNCHECKABLE),
    ],
    ids=["ok certificate", "ok domain", "unknown domain"],
)
def test_ok_and_unknown_are_marks_and_reset_the_error_count(
    item: Item, key: str, mark: Mark
) -> None:
    answers, runs = findings(report(item), {key: 4})

    assert answers == {key: mark}
    assert runs == {}


def test_a_domain_error_counts_like_a_certificate_error() -> None:
    first, runs = findings(report(failed("example.com")), {})
    second, runs = findings(report(failed("example.com")), runs)

    assert first == {DOMAIN_KEY: Mark.HOLD}
    assert second == {DOMAIN_KEY: Finding(DOMAIN_KEY, "example.com", "error", "timeout")}
    assert runs == {DOMAIN_KEY: 2}


def test_keys_are_lowercase_and_names_are_as_shown_in_the_order_of_the_report() -> None:
    answers, _ = findings(
        report(
            dated("Example.ORG", days_left=-3),
            invalid("2001:db8::1", 8443),
            invalid("Old.Example.com"),
        ),
        {},
    )

    assert list(answers) == [
        "certificate:2001:db8::1:8443",
        "certificate:old.example.com:443",
        "domain:example.org",
    ]
    names = [answer.name for answer in answers.values() if isinstance(answer, Finding)]
    assert names == ["[2001:db8::1]:8443", "Old.Example.com:443", "Example.ORG"]


def test_an_entry_no_longer_configured_leaves_the_error_count() -> None:
    _, runs = findings(report(verified()), {"certificate:gone.example.com:443": 5})

    assert runs == {}


# ---------------------------------------------------------------------------
# The reminder day reached (RF-06, ADR-8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days_left", "warn_days", "reminder"),
    [
        (20, 30, None),
        (15, 30, None),
        (14, 30, 14),
        (8, 30, 14),
        (7, 30, 7),
        (3, 30, 3),
        (2, 30, 3),
        (1, 30, 1),
        (0, 30, 0),
        (5, 6, None),
        (3, 6, 3),
        (13, 14, None),
        (7, 14, 7),
        (14, 15, 14),
        (0, 1, 0),
    ],
)
def test_the_reminder_is_the_lowest_day_reached_below_the_threshold(
    days_left: int, warn_days: int, reminder: int | None
) -> None:
    answers, _ = findings(report(verified(days_left=days_left, warn_days=warn_days)), {})

    finding = answers[CERT_KEY]
    assert isinstance(finding, Finding)
    assert finding.reminder == reminder


def test_the_reminder_days_are_those_of_the_requirements() -> None:
    assert REMINDER_DAYS == (14, 7, 3, 1, 0)
    assert PAST_DUE == -1


@pytest.mark.parametrize(
    ("days_left", "warn_days", "reminder"),
    [(-1, 30, PAST_DUE), (-40, 30, PAST_DUE), (-1, 0, PAST_DUE), (0, 30, 0)],
    ids=["one day past", "long past", "threshold 0", "the day itself"],
)
def test_a_domain_past_its_date_reaches_past_due(
    days_left: int, warn_days: int, reminder: int
) -> None:
    answers, _ = findings(report(dated(days_left=days_left, warn_days=warn_days)), {})

    finding = answers[DOMAIN_KEY]
    assert isinstance(finding, Finding)
    assert finding.reminder == reminder


def test_a_threshold_of_0_never_warns_a_certificate_and_warns_a_domain_only_past_its_date() -> None:
    answers, _ = findings(
        report(verified(days_left=0, warn_days=0), dated(days_left=0, warn_days=0)), {}
    )
    past, _ = findings(report(dated(days_left=-1, warn_days=0)), {})

    assert answers == {CERT_KEY: Mark.RESOLVED, DOMAIN_KEY: Mark.RESOLVED}
    assert isinstance(past[DOMAIN_KEY], Finding)


# ---------------------------------------------------------------------------
# The events of a channel (the second table of the design)
# ---------------------------------------------------------------------------


def test_a_finding_nobody_was_told_is_new() -> None:
    result, _ = _step({}, invalid("example.com"))

    assert result.events == (Event(EventKind.NEW, result.view[0]),)
    assert result.view == (Finding(CERT_KEY, "example.com:443", "invalid", "expired"),)


@pytest.mark.parametrize(
    ("before", "item"),
    [
        (_expiring(reminder=14), invalid("example.com")),
        (Finding(CERT_KEY, "example.com:443", "error", "dns"), invalid("example.com")),
        (
            Finding(CERT_KEY, "example.com:443", "invalid", "untrusted"),
            invalid("example.com", reason=certificate.InvalidReason.EXPIRED),
        ),
        (
            Finding(CERT_KEY, "example.com:443", "invalid", "expired"),
            verified(days_left=2, warn_days=14),
        ),
    ],
    ids=[
        "expiring to invalid",
        "error to invalid",
        "another invalid reason",
        "invalid to expiring",
    ],
)
def test_another_status_or_another_invalid_reason_is_a_change(before: Finding, item: Item) -> None:
    result, _ = _step({CERT_KEY: before}, item)

    assert result.events == (Event(EventKind.CHANGED, result.view[0], before),)
    assert result.view[0] != before


def test_a_change_of_error_kind_is_not_a_change_and_the_new_kind_is_kept() -> None:
    before = Finding(CERT_KEY, "example.com:443", "error", "dns")

    result, _ = _step(
        {CERT_KEY: before},
        unreachable("example.com", kind=certificate.ErrorKind.TIMEOUT),
        error_runs={CERT_KEY: 2},
    )

    assert result.events == ()
    assert result.view == (Finding(CERT_KEY, "example.com:443", "error", "timeout"),)


def test_the_same_invalid_reason_is_no_event() -> None:
    before = Finding(CERT_KEY, "example.com:443", "invalid", "expired")

    result, _ = _step({CERT_KEY: before}, invalid("example.com"))

    assert result.events == ()
    assert result.view == (before,)


def test_a_lower_reminder_reminds() -> None:
    result, _ = _step({CERT_KEY: _expiring(reminder=14)}, verified(days_left=7, warn_days=30))

    assert _kinds(result) == [(EventKind.REMINDER, CERT_KEY)]
    assert result.events[0].previous is None
    assert result.view[0].reminder == 7


def test_a_first_reminder_after_none_reminds() -> None:
    result, _ = _step({CERT_KEY: _expiring(reminder=None)}, verified(days_left=14, warn_days=30))

    assert _kinds(result) == [(EventKind.REMINDER, CERT_KEY)]


@pytest.mark.parametrize(
    ("told", "days_left", "reminder"),
    [(7, 6, 7), (7, 10, 14), (14, 20, None)],
    ids=["same", "higher", "above every day"],
)
def test_the_same_or_a_higher_reminder_is_no_event_and_is_kept(
    told: int, days_left: int, reminder: int | None
) -> None:
    result, _ = _step(
        {CERT_KEY: _expiring(reminder=told)}, verified(days_left=days_left, warn_days=30)
    )

    assert result.events == ()
    assert result.view[0].reminder == reminder


@pytest.mark.parametrize(
    ("item", "kind"),
    [
        (verified(days_left=61), EventKind.RESOLVED),
        (dated("example.com", days_left=301), EventKind.RESOLVED),
        (undated("example.com"), EventKind.UNCHECKABLE),
    ],
    ids=["certificate ok", "domain ok", "domain unknown"],
)
def test_an_ending_names_the_finding_told_and_empties_the_view(item: Item, kind: EventKind) -> None:
    key = CERT_KEY if isinstance(item, CertificateItem) else DOMAIN_KEY
    before = _expiring(key=key, reminder=7)

    result, _ = _step({key: before}, item)

    assert result.events == (Event(kind, before),)
    assert result.view == ()


def test_an_entry_removed_from_the_configuration_is_named_last() -> None:
    gone = Finding("certificate:gone.example.com:443", "gone.example.com:443", "invalid", "expired")
    kept = _expiring(reminder=14)

    result, _ = _step(
        {gone.key: gone, CERT_KEY: kept},
        verified(days_left=13, warn_days=30),
        dated("example.org", days_left=5),
    )

    assert _kinds(result) == [(EventKind.NEW, "domain:example.org"), (EventKind.REMOVED, gone.key)]
    assert result.events[-1] == Event(EventKind.REMOVED, gone)
    assert [finding.key for finding in result.view] == [CERT_KEY, "domain:example.org"]


def test_removed_entries_keep_the_order_they_were_told_in() -> None:
    told = {
        f"domain:{name}": Finding(f"domain:{name}", name, "invalid", "x")
        for name in ("b.example", "a.example")
    }

    result, _ = _step(told, verified())

    assert _kinds(result) == [
        (EventKind.REMOVED, "domain:b.example"),
        (EventKind.REMOVED, "domain:a.example"),
    ]


@pytest.mark.parametrize(
    "item",
    [verified(days_left=61), undated("example.com"), unreachable("example.com")],
    ids=["resolved", "uncheckable", "hold"],
)
def test_nothing_told_and_nothing_to_tell_is_no_event(item: Item) -> None:
    result, _ = _step({}, item)

    assert result == Plan((), ())


def test_nothing_told_and_an_empty_report_is_no_event() -> None:
    assert plan({}, {}) == Plan((), ())


# ---------------------------------------------------------------------------
# Several runs (RF-04, RF-06)
# ---------------------------------------------------------------------------


def test_a_first_error_keeps_the_finding_told_and_a_second_tells_the_change() -> None:
    before = _expiring(reminder=7)

    first, runs = _step({CERT_KEY: before}, unreachable("example.com"))
    second, runs = _step(_told(first.view), unreachable("example.com"), error_runs=runs)

    assert first == Plan((), (before,))
    assert _kinds(second) == [(EventKind.CHANGED, CERT_KEY)]
    assert second.events[0].previous == before
    assert runs == {CERT_KEY: 2}


def test_a_first_error_with_nothing_told_is_silent_and_a_second_is_new() -> None:
    first, runs = _step({}, unreachable("example.com"))
    second, runs = _step(_told(first.view), unreachable("example.com"), error_runs=runs)

    assert first == Plan((), ())
    assert _kinds(second) == [(EventKind.NEW, CERT_KEY)]


def test_an_ok_between_two_errors_holds_the_second_again() -> None:
    first, runs = _step({}, unreachable("example.com"))
    middle, runs = _step(_told(first.view), verified(), error_runs=runs)
    last, runs = _step(_told(middle.view), unreachable("example.com"), error_runs=runs)

    assert (first.events, middle.events, last.events) == ((), (), ())
    assert runs == {CERT_KEY: 1}


def test_a_warning_that_starts_at_five_days_sends_no_reminder_for_7() -> None:
    told: dict[str, Finding] = {}
    kinds = []
    for days_left in (5, 4, 3):
        result, _ = _step(told, verified(days_left=days_left, warn_days=30))
        kinds.append([event.kind for event in result.events])
        told = _told(result.view)

    assert kinds == [[EventKind.NEW], [], [EventKind.REMINDER]]
    assert told[CERT_KEY].reminder == 3


def test_a_jump_from_eight_to_two_days_sends_one_reminder_for_3() -> None:
    first, _ = _step({}, verified(days_left=8, warn_days=30))
    second, _ = _step(_told(first.view), verified(days_left=2, warn_days=30))

    assert first.view[0].reminder == 14
    assert _kinds(second) == [(EventKind.REMINDER, CERT_KEY)]
    assert second.view[0].reminder == 3


def test_a_jump_to_two_days_with_a_threshold_of_4_reminds_for_3() -> None:
    first, _ = _step({}, verified(days_left=3, warn_days=4))
    assert first.view[0].reminder == 3

    told = {CERT_KEY: _expiring(reminder=None)}
    second, _ = _step(told, verified(days_left=2, warn_days=4))

    assert _kinds(second) == [(EventKind.REMINDER, CERT_KEY)]
    assert second.view[0].reminder == 3


def test_a_domain_past_its_date_reminds_once_at_past_due() -> None:
    told: dict[str, Finding] = {}
    kinds = []
    for days_left in (0, -1, -2):
        result, _ = _step(told, dated(days_left=days_left, warn_days=30))
        kinds.append([event.kind for event in result.events])
        told = _told(result.view)

    assert kinds == [[EventKind.NEW], [EventKind.REMINDER], []]
    assert told[DOMAIN_KEY].reminder == PAST_DUE


def test_a_reminder_that_goes_up_and_down_again_reminds_at_the_next_lower_day() -> None:
    told: dict[str, Finding] = {}
    seen = []
    # 3 days left, then a renewal pushed the date to 10 days, still inside 30, then it falls.
    for days_left in (3, 10, 8, 7, 3):
        result, _ = _step(told, verified(days_left=days_left, warn_days=30))
        seen.append(([event.kind for event in result.events], result.view[0].reminder))
        told = _told(result.view)

    assert seen == [
        ([EventKind.NEW], 3),
        ([], 14),
        ([], 14),
        ([EventKind.REMINDER], 7),
        ([EventKind.REMINDER], 3),
    ]


def test_a_domain_error_of_another_kind_is_no_event_and_ends_when_ok() -> None:
    before = Finding(DOMAIN_KEY, "example.com", "error", "timeout")

    result, runs = _step(
        {DOMAIN_KEY: before},
        failed("example.com", kind=rdap.ErrorKind.NOT_FOUND),
        error_runs={DOMAIN_KEY: 3},
    )
    ended, _ = _step(_told(result.view), dated(), error_runs=runs)

    assert result.events == ()
    assert result.view[0].reason == "not_found"
    assert _kinds(ended) == [(EventKind.RESOLVED, DOMAIN_KEY)]


def test_findings_refuses_a_status_it_does_not_know() -> None:
    item = verified()
    # Not a member of `certificate.Status`, on purpose: the guard is what is tested.
    odd = CertificateItem(item.entry, item.result, "odd")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="odd"):
        findings(report(odd), {})


def test_findings_refuses_an_expiring_result_without_its_date() -> None:
    item = verified(days_left=3)
    undated_result = dataclasses.replace(item.result, not_after=None)
    odd = CertificateItem(item.entry, undated_result, item.status)

    with pytest.raises(ValueError, match="carry its date"):
        findings(report(odd), {})


def test_answer_is_a_finding_or_a_mark() -> None:
    answers: dict[str, Answer] = {CERT_KEY: Mark.HOLD, DOMAIN_KEY: _expiring(DOMAIN_KEY)}

    result = plan({}, answers)

    assert _kinds(result) == [(EventKind.NEW, DOMAIN_KEY)]
