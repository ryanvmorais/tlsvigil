"""
What a channel has to hear: the findings of a run, and the events against what it was told (005).

:func:`findings` turns a :class:`~tlsvigil.runner.Report` and the count of ``error`` runs into one
answer per entry (RF-04): a :class:`Finding`, or a :class:`Mark` that holds, resolves or ends what
a channel was told. :func:`plan` compares those answers with what one channel was told and gives
the events to send and the channel's view after the run (RF-05, RF-06).

Both are pure: no clock, no network and no file, so every rule is a unit test. The reminder days
are fixed (ADR-8); a reminder is the lowest day reached, so a late or skipped run reminds once.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from tlsvigil.output import display_name
from tlsvigil.runner import CertificateItem, DomainItem, Report

__all__ = [
    "PAST_DUE",
    "REMINDER_DAYS",
    "Answer",
    "Event",
    "EventKind",
    "Finding",
    "Mark",
    "Plan",
    "findings",
    "plan",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The days left that remind, counted only below the entry's threshold (ADR-8).
REMINDER_DAYS: Final = (14, 7, 3, 1, 0)

# The reminder of a domain past its date (days left below 0); a certificate past its date is
# `invalid`, so only a domain reaches it.
PAST_DUE: Final = -1

# Every reminder day, the lowest last, so the first one reached from the end is the lowest.
_REMINDERS: Final = (*REMINDER_DAYS, PAST_DUE)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Finding:
    """
    What a channel knows about one entry that needs a person.

    Attributes:
        key (str): ``certificate:<host>:<port>`` or ``domain:<name>``, lowercase; the same entry
            has the same key from run to run.
        name (str): The entry as shown: ``example.com:443``, ``[2001:db8::1]:8443``,
            ``example.com.br``.
        status (str): ``expiring``, ``invalid`` or ``error``.
        reason (str | None): The invalid reason or the error kind; ``None`` for ``expiring``.
        expires_at (datetime | None): Aware, in UTC; set for ``expiring`` only.
        reminder (int | None): The lowest reminder day reached, or ``None``; ``expiring`` only.
    """

    key: str
    name: str
    status: str
    reason: str | None = None
    expires_at: datetime | None = None
    reminder: int | None = None


class Mark(StrEnum):
    """
    An answer of a run that is not a finding (RF-04).

    Attributes:
        HOLD (str): The first ``error`` run in a row: the channel keeps what it was told.
        RESOLVED (str): The entry is ``ok``.
        UNCHECKABLE (str): The entry is ``unknown``: no longer checkable, never resolved.
    """

    HOLD = "hold"
    RESOLVED = "resolved"
    UNCHECKABLE = "uncheckable"


# What a run says about one entry.
type Answer = Finding | Mark


class EventKind(StrEnum):
    """
    What a channel is told about one entry (RF-05, RF-06).

    Attributes:
        NEW (str): A warning the channel was not told about.
        CHANGED (str): Another status, or another reason for ``invalid``.
        REMINDER (str): An ``expiring`` warning that reached a lower reminder day.
        RESOLVED (str): The warning ended: the entry is ``ok``.
        UNCHECKABLE (str): The warning ended: the entry is now ``unknown``.
        REMOVED (str): The warning ended: the entry left the configuration.
    """

    NEW = "new"
    CHANGED = "changed"
    REMINDER = "reminder"
    RESOLVED = "resolved"
    UNCHECKABLE = "uncheckable"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class Event:
    """
    One thing to tell a channel.

    Attributes:
        kind (EventKind): What happened.
        finding (Finding): The current finding; the last one told for the three endings.
        previous (Finding | None): The last one told, for ``CHANGED``; ``None`` otherwise.
    """

    kind: EventKind
    finding: Finding
    previous: Finding | None = None


@dataclass(frozen=True, slots=True)
class Plan:
    """
    What one channel hears in a run, and what it knows afterwards.

    Attributes:
        events (tuple[Event, ...]): In the order of the configuration, removed entries last.
        view (tuple[Finding, ...]): What the channel knows after this run, in the same order;
            the state keeps it once the delivery succeeds.
    """

    events: tuple[Event, ...]
    view: tuple[Finding, ...]


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def _reminder(days_left: int, warn_days: int) -> int | None:
    """
    Give the lowest reminder day an ``expiring`` entry reached (RF-06, ADR-8).

    Args:
        days_left (int): The whole days left; below 0 for a domain past its date.
        warn_days (int): The threshold of the entry: only lower days remind.

    Returns:
        int | None: The lowest day ``d`` with ``d < warn_days`` and ``days_left <= d``, or
            ``None`` when the days left are above every such day.
    """
    # `<=` and not `==`: a run that is late or skipped jumps over a day and still reminds.
    reached = [day for day in _REMINDERS if day < warn_days and days_left <= day]
    return min(reached) if reached else None


def _text(value: StrEnum | None) -> str | None:
    """
    Give the value of a reason or an error kind as a plain string.

    Args:
        value (StrEnum | None): The member of a check, or ``None``.

    Returns:
        str | None: Its value, so a finding read back from the state compares equal and has
            the same type.
    """
    return None if value is None else value.value


def _certificate_key(item: CertificateItem) -> str:
    """
    Give the key of a certificate entry.

    Args:
        item (CertificateItem): The item.

    Returns:
        str: ``certificate:<host>:<port>``, lowercase.
    """
    return f"certificate:{item.entry.host}:{item.entry.port}".lower()


def _domain_key(item: DomainItem) -> str:
    """
    Give the key of a domain entry.

    Args:
        item (DomainItem): The item.

    Returns:
        str: ``domain:<name>``, lowercase.
    """
    return f"domain:{item.entry.domain}".lower()


def _answer(
    key: str,
    name: str,
    status: str,
    reason: str | None,
    expiry: tuple[datetime, int, int] | None,
    error_runs: Mapping[str, int],
) -> tuple[Answer, int]:
    """
    Answer for one entry from its status, the first table of the design (RF-04).

    Args:
        key (str): The key of the entry.
        name (str): The entry as shown.
        status (str): The status of the result.
        reason (str | None): The invalid reason or the error kind, as a string.
        expiry (tuple[datetime, int, int] | None): The date, the days left and the threshold of
            an ``expiring`` result.
        error_runs (Mapping[str, int]): The ``error`` runs in a row before this one, by key.

    Returns:
        tuple[Answer, int]: The answer, and the ``error`` runs in a row after this one.

    Raises:
        ValueError: When an ``expiring`` result has no date, or the status is not one of the
            report's.
    """
    if status == "expiring":
        if expiry is None:
            raise ValueError(f"an expiring result must carry its date: {key}")
        moment, days_left, warn_days = expiry
        reminder = _reminder(days_left, warn_days)
        return Finding(key, name, status, None, moment.astimezone(UTC), reminder), 0
    if status == "invalid":
        return Finding(key, name, status, reason), 0
    if status == "error":
        runs = error_runs.get(key, 0) + 1
        # One failure is often the network of the runner, not the host: only the second
        # `error` in a row is told (requirements, resolved decision 6).
        if runs == 1:
            return Mark.HOLD, runs
        return Finding(key, name, status, reason), runs
    if status == "ok":
        return Mark.RESOLVED, 0
    if status == "unknown":
        return Mark.UNCHECKABLE, 0
    raise ValueError(f"not a status of the report: {status!r}")


def findings(
    report: Report, error_runs: Mapping[str, int]
) -> tuple[dict[str, Answer], dict[str, int]]:
    """
    Give the answer of a run for every entry, and the new count of ``error`` runs (RF-04).

    Args:
        report (Report): What the run found.
        error_runs (Mapping[str, int]): The ``error`` runs in a row before this run, by key; a
            key that is not there counts 0.

    Returns:
        tuple[dict[str, Answer], dict[str, int]]: The answers by key, in the order of the
            report (the certificates first); and the ``error`` runs in a row after this run,
            by key, the entries with 0 left out (an entry no longer configured is dropped too).

    Raises:
        ValueError: When an ``expiring`` result has no date.
    """
    answers: dict[str, Answer] = {}
    runs_after: dict[str, int] = {}

    def keep(key: str, answer: Answer, runs: int) -> None:
        """Keep the answer of an entry, and its count of ``error`` runs when not 0."""
        answers[key] = answer
        if runs:
            runs_after[key] = runs

    for cert in report.certificates:
        key = _certificate_key(cert)
        result = cert.result
        reason = _text(result.reason or result.error_kind)
        expiry = None
        if result.not_after is not None and result.days_left is not None:
            expiry = (result.not_after, result.days_left, cert.entry.warn_days)
        keep(key, *_answer(key, display_name(cert), str(cert.status), reason, expiry, error_runs))

    for domain in report.domains:
        key = _domain_key(domain)
        found = domain.result
        reason = _text(found.reason or found.error_kind)
        expiry = None
        if found.expiration is not None and found.days_left is not None:
            expiry = (found.expiration, found.days_left, domain.entry.warn_days)
        name = domain.entry.domain
        keep(key, *_answer(key, name, str(domain.status), reason, expiry, error_runs))

    return answers, runs_after


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _changed(told: Finding, now: Finding) -> bool:
    """
    Tell whether a finding differs from the one told enough to tell again (RF-05).

    Args:
        told (Finding): The last finding the channel was told.
        now (Finding): The finding of this run.

    Returns:
        bool: ``True`` for another status, or another reason for ``invalid``. The kind of an
            ``error`` does not count: two failures of one host differ from run to run.
    """
    if told.status != now.status:
        return True
    return now.status == "invalid" and told.reason != now.reason


def _reminds(told: Finding, now: Finding) -> bool:
    """
    Tell whether an ``expiring`` finding reached a lower reminder day than the one told (RF-06).

    Args:
        told (Finding): The last finding the channel was told.
        now (Finding): The finding of this run, with the same status.

    Returns:
        bool: ``True`` when ``now`` has a reminder and ``told`` had none or a higher one.
    """
    if now.status != "expiring" or now.reminder is None:
        return False
    return told.reminder is None or now.reminder < told.reminder


# Each mark that ends a warning, and the event that tells the channel so.
_ENDINGS: Final = {Mark.RESOLVED: EventKind.RESOLVED, Mark.UNCHECKABLE: EventKind.UNCHECKABLE}


def plan(told: Mapping[str, Finding], answers: Mapping[str, Answer]) -> Plan:
    """
    Compare what one channel was told with the answers of this run (RF-05, RF-06).

    Args:
        told (Mapping[str, Finding]): What the channel was last told, by key.
        answers (Mapping[str, Answer]): The answers of :func:`findings`, in the order of the
            configuration.

    Returns:
        Plan: The events, in the order of the answers and then the removed entries in the order
            of ``told``; and the view after this run, in the order of the answers.
    """
    events: list[Event] = []
    view: list[Finding] = []

    for key, answer in answers.items():
        before = told.get(key)
        if answer is Mark.HOLD:
            if before is not None:
                view.append(before)
        elif isinstance(answer, Mark):
            if before is not None:
                events.append(Event(_ENDINGS[answer], before))
        else:
            if before is None:
                events.append(Event(EventKind.NEW, answer))
            elif _changed(before, answer):
                events.append(Event(EventKind.CHANGED, answer, before))
            elif _reminds(before, answer):
                events.append(Event(EventKind.REMINDER, answer))
            # The finding of this run is kept even without an event: a reminder that went up
            # (a date pushed later) lets the next lower day remind again.
            view.append(answer)

    # Last, so the entries still configured come first in every message.
    events.extend(
        Event(EventKind.REMOVED, before) for key, before in told.items() if key not in answers
    )

    return Plan(tuple(events), tuple(view))
