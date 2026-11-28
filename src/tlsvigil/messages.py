"""
The words of the notifications: the issue, its comments, the Telegram text and the e-mail (005).

This is the only module that writes a notification (RF-11). An entry is told with the sentences
of the text output of spec 004 (the days left and the date, the reason, the kind of error), so the
two never drift apart; the days left are counted from the moment the run started.

Outside text is data (RNF-04, ADR-5): for GitHub every name is a Markdown code span, whose fence
is longer than any run of backticks inside, so GitHub renders no markup, link, mention or
reference in it; for Telegram and e-mail, which are sent as plain text, every name only passes
through :func:`~tlsvigil.output.printable`. The catalogue's own sentences hold no Markdown
character.

The issue body, a comment and a Telegram message are cut at a whole line under their caps,
measured in UTF-16 code units as GitHub and Telegram count them, and end saying how many entries
were left out (RF-11), so a delivery never fails because of its length.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from itertools import accumulate
from typing import Final

from tlsvigil.alerts import Event, EventKind, Finding, Plan
from tlsvigil.i18n import MESSAGES, Lang, plural, translate
from tlsvigil.output import days_detail, printable

__all__ = [
    "GITHUB_CAP",
    "MARKER",
    "TELEGRAM_CAP",
    "code_span",
    "comment",
    "detail",
    "issue_body",
    "mail_body",
    "mail_subject",
    "telegram_text",
    "title",
    "utf16_length",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The first line of TLSVigil's issue: with the author, what lets a run adopt it (ADR-3).
MARKER: Final = "<!-- tlsvigil -->"

# Under GitHub's 65,536 characters for an issue body and a comment, with room to spare.
GITHUB_CAP: Final = 60_000

# Under Telegram's 4,096 characters for a message.
TELEGRAM_CAP: Final = 4_000

# The start of a list item in GitHub's Markdown; written by this module, never by the catalogue.
_ITEM: Final = "- "

# The days left are whole days, floored, as the checks count them (spec 002 and 003).
_ONE_DAY: Final = timedelta(days=1)


# ---------------------------------------------------------------------------
# Escaping and measuring
# ---------------------------------------------------------------------------


def code_span(text: str) -> str:
    """
    Write a text as one Markdown code span, so GitHub shows it as it is (ADR-5).

    Args:
        text (str): A name or any text from the outside.

    Returns:
        str: The text after :func:`~tlsvigil.output.printable` (one line, no control
            character) between two fences of backticks one longer than the longest run inside;
            with a space inside each fence when the text starts or ends with a backtick, or
            starts and ends with a space, which CommonMark strips again.
    """
    content = printable(text)
    longest = run = 0
    for char in content:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    fence = "`" * (longest + 1)
    # A backtick at an edge would join the fence; a space at both edges would be stripped.
    padded = content.startswith("`") or content.endswith("`")
    padded = padded or (content.startswith(" ") and content.endswith(" ")) or not content
    if padded:
        content = f" {content} "
    return f"{fence}{content}{fence}"


def utf16_length(text: str) -> int:
    """
    Measure a text as GitHub and Telegram do: in UTF-16 code units.

    Args:
        text (str): The text.

    Returns:
        int: The number of code units; a character outside the Basic Multilingual Plane, such
            as an emoji, counts two.
    """
    return len(text.encode("utf-16-le")) // 2


def _fit(
    head: Sequence[str],
    items: Sequence[str],
    tail: Sequence[str],
    cap: int,
    more: Callable[[int], str],
) -> str:
    """
    Join the lines of a message, leaving out whole items from the end until it fits (RF-11).

    Args:
        head (Sequence[str]): The lines before the items, always kept.
        items (Sequence[str]): One line per item, in order.
        tail (Sequence[str]): The lines after the items, always kept.
        cap (int): The longest text, in UTF-16 code units.
        more (Callable[[int], str]): The line that says how many items were left out.

    Returns:
        str: The lines joined by newlines: every item when they fit, or the most items that
            fit with the line of ``more`` after them.
    """
    lines = [*head, *items, *tail]
    if utf16_length("\n".join(lines)) <= cap:
        return "\n".join(lines)

    # Each line costs its length and the newline that joins it to the next one.
    fixed = sum(utf16_length(line) + 1 for line in (*head, *tail))
    sizes = [0, *accumulate(utf16_length(line) + 1 for line in items)]
    kept = 0
    for count in range(len(items) - 1, -1, -1):
        last = more(len(items) - count)
        # The joined text has one newline fewer than the lines it joins.
        if fixed + sizes[count] + utf16_length(last) <= cap:
            kept = count
            break
    left_out = more(len(items) - kept)
    return "\n".join([*head, *items[:kept], left_out, *tail])


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------


def _words(lang: Lang, key: str, fallback: str) -> str:
    """
    Give the sentence of a key, or the raw value when the catalogue has none.

    A reason read back from a state file is any string; one TLSVigil never wrote must not stop
    a delivery.

    Args:
        lang (Lang): The language of the run.
        key (str): A key that may be in the catalogue.
        fallback (str): The value to show escaped instead.

    Returns:
        str: The sentence, or ``fallback`` after :func:`~tlsvigil.output.printable`.
    """
    return translate(lang, key) if key in MESSAGES else printable(fallback)


def _kind(finding: Finding) -> str:
    """
    Tell whether a finding is about a certificate or a domain.

    Args:
        finding (Finding): The finding.

    Returns:
        str: ``certificate`` or ``domain``, the middle of the catalogue keys of the output.
    """
    return "certificate" if finding.key.startswith("certificate:") else "domain"


def detail(finding: Finding, now: datetime, lang: Lang) -> str:
    """
    Write what is wrong with an entry, as the text output of spec 004 does (RF-11).

    Args:
        finding (Finding): The finding.
        now (datetime): The moment the run started, aware: the days left count from it.
        lang (Lang): The language of the run.

    Returns:
        str: The days left and the date, the reason of an invalid certificate, or the kind of
            error; TLSVigil's own words, with no outside text.
    """
    kind = _kind(finding)
    reason = finding.reason or ""
    if finding.status == "expiring" and finding.expires_at is not None:
        days_left = (finding.expires_at - now) // _ONE_DAY
        if kind == "certificate":
            # A certificate past its date fails the verification; the check counts no less than 0.
            days_left = max(0, days_left)
        return days_detail(lang, kind, days_left, finding.expires_at)
    if finding.status == "invalid":
        words = _words(lang, f"reason.{kind}.{reason}", reason)
        return translate(lang, "line.certificate.invalid", reason=words)
    words = _words(lang, f"error.{kind}.{reason}", reason)
    return translate(lang, f"line.{kind}.error", kind=words)


def _previous(finding: Finding, lang: Lang) -> str:
    """
    Write what an entry was before a change: its status, with its reason or kind of error.

    Args:
        finding (Finding): The last finding the channel was told.
        lang (Lang): The language of the run.

    Returns:
        str: Such as ``expiring`` or ``invalid: the certificate has expired``.
    """
    status = _words(lang, f"notify.was.{finding.status}", finding.status)
    if finding.reason is None:
        return status
    prefix = "reason" if finding.status == "invalid" else "error"
    reason = _words(lang, f"{prefix}.{_kind(finding)}.{finding.reason}", finding.reason)
    return f"{status}: {reason}"


def _attention(count: int, lang: Lang) -> str:
    """
    Say how many entries need attention.

    Args:
        count (int): The entries of the view.
        lang (Lang): The language of the run.

    Returns:
        str: Such as ``2 entries need attention`` or ``nothing needs attention``.
    """
    if count == 0:
        return translate(lang, "notify.nothing")
    return plural(lang, "notify.entries", count)


def _more(lang: Lang, prefix: str = "") -> Callable[[int], str]:
    """
    Give the line that ends a message cut at its cap.

    Args:
        lang (Lang): The language of the run.
        prefix (str): Written before the sentence, such as the start of a list item.

    Returns:
        Callable[[int], str]: From the number of entries left out to the line.
    """
    return lambda count: prefix + plural(lang, "notify.more", count)


# ---------------------------------------------------------------------------
# The lines of a message
# ---------------------------------------------------------------------------


def _event_line(event: Event, name: str, now: datetime, lang: Lang) -> str:
    """
    Write one event (RF-05, RF-06).

    Args:
        event (Event): The event.
        name (str): The name of the entry, already escaped for the channel.
        now (datetime): The moment the run started.
        lang (Lang): The language of the run.

    Returns:
        str: Such as ``New: example.com:443, certificate expires in 9 days (2026-12-06)``.
    """
    key = f"event.{event.kind}"
    if event.kind in (EventKind.NEW, EventKind.REMINDER):
        return translate(lang, key, name=name, detail=detail(event.finding, now, lang))
    if event.kind is EventKind.CHANGED:
        # A change always carries the finding told before it (alerts.plan).
        assert event.previous is not None
        found = detail(event.finding, now, lang)
        previous = _previous(event.previous, lang)
        return translate(lang, key, name=name, detail=found, previous=previous)
    return translate(lang, key, name=name)


def _view_line(finding: Finding, name: str, now: datetime, lang: Lang) -> str:
    """
    Write one entry of the view: its status, its name and what is wrong.

    Args:
        finding (Finding): The finding.
        name (str): The name of the entry, already escaped for the channel.
        now (datetime): The moment the run started.
        lang (Lang): The language of the run.

    Returns:
        str: Such as ``EXPIRING example.com:443, certificate expires in 9 days (2026-12-06)``.
    """
    status = _words(lang, f"status.{finding.status}", finding.status)
    found = detail(finding, now, lang)
    return translate(lang, "notify.item", status=status, name=name, detail=found)


def _github_events(events: Sequence[Event], now: datetime, lang: Lang) -> list[str]:
    """
    Write the events as list items for GitHub, each name as a code span.

    Args:
        events (Sequence[Event]): The events.
        now (datetime): The moment the run started.
        lang (Lang): The language of the run.

    Returns:
        list[str]: One Markdown list item per event.
    """
    return [
        _ITEM + _event_line(event, code_span(event.finding.name), now, lang) for event in events
    ]


def _plain_events(events: Sequence[Event], now: datetime, lang: Lang) -> list[str]:
    """
    Write the events as plain lines, for Telegram and e-mail.

    Args:
        events (Sequence[Event]): The events.
        now (datetime): The moment the run started.
        lang (Lang): The language of the run.

    Returns:
        list[str]: One line per event, the names escaped by
            :func:`~tlsvigil.output.printable`.
    """
    return [_event_line(event, printable(event.finding.name), now, lang) for event in events]


def _plain_view(view: Sequence[Finding], now: datetime, lang: Lang) -> list[str]:
    """
    Write the view as plain lines, for the e-mail.

    Args:
        view (Sequence[Finding]): What the channel knows after this run.
        now (datetime): The moment the run started.
        lang (Lang): The language of the run.

    Returns:
        list[str]: One line per entry, or one line that says nothing needs attention.
    """
    if not view:
        return [translate(lang, "notify.none")]
    return [_view_line(finding, printable(finding.name), now, lang) for finding in view]


# ---------------------------------------------------------------------------
# The messages
# ---------------------------------------------------------------------------


def title(view: Sequence[Finding], lang: Lang) -> str:
    """
    Write the title of the issue, the first line of a Telegram message and the e-mail subject.

    Args:
        view (Sequence[Finding]): What the channel knows after this run.
        lang (Lang): The language of the run.

    Returns:
        str: Such as ``TLSVigil: 2 entries need attention`` or ``TLSVigil: nothing needs
            attention``; TLSVigil's own words and a count, never outside text.
    """
    return translate(lang, "notify.title", attention=_attention(len(view), lang))


def issue_body(view: Sequence[Finding], now: datetime, lang: Lang) -> str:
    """
    Write the body of the issue: the marker, the entries that need attention, and how it works.

    Args:
        view (Sequence[Finding]): What the channel knows after this run.
        now (datetime): The moment the run started.
        lang (Lang): The language of the run.

    Returns:
        str: Markdown that starts with :data:`MARKER`, one list item per entry with its name as
            a code span, cut at :data:`GITHUB_CAP`.
    """
    head = [MARKER, translate(lang, "notify.intro"), ""]
    items = [_ITEM + _view_line(finding, code_span(finding.name), now, lang) for finding in view]
    if not items:
        head = [MARKER]
        items = [translate(lang, "notify.none")]
    tail = ["", translate(lang, "notify.footer")]
    return _fit(head, items, tail, GITHUB_CAP, _more(lang, _ITEM))


def comment(plan: Plan, now: datetime, lang: Lang) -> str:
    """
    Write the comment that tells the events of a run on the issue (ADR-4).

    Args:
        plan (Plan): What the channel hears in this run; its events are not empty.
        now (datetime): The moment the run started.
        lang (Lang): The language of the run.

    Returns:
        str: One list item per event and the count after this run, cut at :data:`GITHUB_CAP`.
    """
    count = translate(lang, "notify.now", attention=_attention(len(plan.view), lang))
    items = _github_events(plan.events, now, lang)
    return _fit([], items, ["", count], GITHUB_CAP, _more(lang, _ITEM))


def telegram_text(plan: Plan, now: datetime, lang: Lang) -> str:
    """
    Write the Telegram message of a run: the title, then the events, as plain text (RF-09).

    Args:
        plan (Plan): What the channel hears in this run; its events are not empty.
        now (datetime): The moment the run started.
        lang (Lang): The language of the run.

    Returns:
        str: The text, cut at :data:`TELEGRAM_CAP`.
    """
    head = [title(plan.view, lang), ""]
    items = _plain_events(plan.events, now, lang)
    return _fit(head, items, [], TELEGRAM_CAP, _more(lang))


def mail_subject(plan: Plan, lang: Lang) -> str:
    """
    Write the subject of the e-mail of a run (RF-10).

    Args:
        plan (Plan): What the channel hears in this run.
        lang (Lang): The language of the run.

    Returns:
        str: The title: TLSVigil's own words and a count, with no outside text, so nothing can
            break the header.
    """
    return title(plan.view, lang)


def mail_body(plan: Plan, now: datetime, lang: Lang) -> str:
    """
    Write the plain-text body of the e-mail of a run (RF-10).

    Args:
        plan (Plan): What the channel hears in this run; its events are not empty.
        now (datetime): The moment the run started.
        lang (Lang): The language of the run.

    Returns:
        str: The events, a blank line and the view, ending in a newline; not cut, since an
            e-mail has no such limit.
    """
    lines = [*_plain_events(plan.events, now, lang), "", *_plain_view(plan.view, now, lang)]
    return "\n".join(lines) + "\n"
