"""
Tests for the words of the notifications (spec 005, RF-11, RNF-04, ADR-5).

The messages are pure text built from findings and events, so every test builds them by hand and
reads the result: the sentences in both languages, the days left counted from the start of the
run, the escape of outside text (a code span for GitHub, plain text for the others) and the caps
at a whole line.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from tlsvigil.alerts import Event, EventKind, Finding, Plan
from tlsvigil.i18n import MESSAGES, Lang
from tlsvigil.messages import (
    GITHUB_CAP,
    MARKER,
    TELEGRAM_CAP,
    code_span,
    comment,
    detail,
    issue_body,
    mail_body,
    mail_subject,
    telegram_text,
    title,
    utf16_length,
)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def _expiring(name: str = "example.com:443", days: int = 9, **fields: object) -> Finding:
    """
    Build an ``expiring`` finding of a certificate.

    Args:
        name (str): The name as shown; the key is built from it.
        days (int): The days left from :data:`NOW`.
        **fields (object): Other fields, such as ``reminder``.

    Returns:
        Finding: The finding, expiring at the same hour as :data:`NOW`.
    """
    expires = NOW + timedelta(days=days)
    key = f"certificate:{name}"
    # The keyword fields are Finding's own; mypy cannot match them to an object mapping.
    return Finding(key, name, "expiring", None, expires, **fields)  # type: ignore[arg-type]


INVALID = Finding("certificate:example.com:443", "example.com:443", "invalid", "expired")
DOMAIN_ERROR = Finding("domain:example.com.br", "example.com.br", "error", "timeout")
DOMAIN_SOON = Finding(
    "domain:example.com.br", "example.com.br", "expiring", None, NOW + timedelta(days=3), 3
)

# Every character that can start markup, a mention or an entity in GitHub's Markdown.
_MARKDOWN = re.compile(r"[`*_\[\]<>#|~\\&@!]")

# The catalogue keys whose sentences reach a notification.
_NOTIFICATION_PREFIXES = ("notify.", "event.", "line.", "reason.", "error.", "status.", "days.")


# ---------------------------------------------------------------------------
# The events, in both languages
# ---------------------------------------------------------------------------

EVENTS = {
    EventKind.NEW: Event(EventKind.NEW, _expiring()),
    EventKind.CHANGED: Event(EventKind.CHANGED, INVALID, _expiring()),
    EventKind.REMINDER: Event(EventKind.REMINDER, DOMAIN_SOON),
    EventKind.RESOLVED: Event(EventKind.RESOLVED, INVALID),
    EventKind.UNCHECKABLE: Event(EventKind.UNCHECKABLE, DOMAIN_ERROR),
    EventKind.REMOVED: Event(EventKind.REMOVED, _expiring()),
}


@pytest.mark.parametrize(
    ("kind", "english", "portuguese"),
    [
        (
            EventKind.NEW,
            "New: example.com:443, certificate expires in 9 days (2026-12-06)",
            "Novo: example.com:443, certificado vence em 9 dias (2026-12-06)",
        ),
        (
            EventKind.CHANGED,
            "Changed: example.com:443, certificate failed verification: " "expired (was expiring)",
            "Mudou: example.com:443, certificado não passou na verificação: "
            "vencido (antes: vencendo)",
        ),
        (
            EventKind.REMINDER,
            "Reminder: example.com.br, domain expires in 3 days (2026-11-30)",
            "Lembrete: example.com.br, domínio vence em 3 dias (2026-11-30)",
        ),
        (EventKind.RESOLVED, "Resolved: example.com:443", "Resolvido: example.com:443"),
        (
            EventKind.UNCHECKABLE,
            "Ended without being resolved: example.com.br can no longer be checked",
            "Encerrado sem solução: example.com.br não pode mais ser verificado",
        ),
        (
            EventKind.REMOVED,
            "Ended without being resolved: example.com:443 is no longer in the configuration",
            "Encerrado sem solução: example.com:443 não está mais na configuração",
        ),
    ],
)
def test_each_event_reads_in_both_languages(kind: EventKind, english: str, portuguese: str) -> None:
    plan = Plan((EVENTS[kind],), ())
    assert telegram_text(plan, NOW, Lang.EN).splitlines()[2] == english
    assert telegram_text(plan, NOW, Lang.PT_BR).splitlines()[2] == portuguese


def test_a_change_names_the_reason_it_had_before() -> None:
    before = Finding("certificate:a.example:443", "a.example:443", "invalid", "untrusted")
    now = Finding("certificate:a.example:443", "a.example:443", "invalid", "hostname_mismatch")
    text = mail_body(Plan((Event(EventKind.CHANGED, now, before),), (now,)), NOW, Lang.EN)
    assert text.splitlines()[0] == (
        "Changed: a.example:443, certificate failed verification: "
        "the certificate is not for this host "
        "(was invalid: the chain does not lead to a trusted authority)"
    )


def test_an_error_names_its_kind_without_a_message() -> None:
    assert detail(DOMAIN_ERROR, NOW, Lang.EN) == (
        "domain not checked: the RDAP server did not answer within the timeout"
    )


def test_a_reason_the_catalogue_does_not_know_is_shown_as_it_is() -> None:
    # A state file can hold any string; one TLSVigil never wrote must not stop the delivery.
    odd = Finding("certificate:a.example:443", "a.example:443", "invalid", "new_reason\x1b")
    assert detail(odd, NOW, Lang.EN) == "certificate failed verification: new_reason\\x1b"


# ---------------------------------------------------------------------------
# The issue and the days left
# ---------------------------------------------------------------------------


def test_the_issue_body_lists_the_view_with_the_days_from_the_start_of_the_run() -> None:
    view = (_expiring(days=9), DOMAIN_SOON)
    body = issue_body(view, NOW + timedelta(days=2), Lang.EN)
    assert body.splitlines() == [
        MARKER,
        "TLSVigil found these entries in need of attention on its last run:",
        "",
        "- EXPIRING `example.com:443`, certificate expires in 7 days (2026-12-06)",
        "- EXPIRING `example.com.br`, domain expires in 1 day (2026-11-30)",
        "",
        "TLSVigil rewrites this issue on every run and closes it when nothing needs attention. "
        "Closing it by hand stops the updates until something changes.",
    ]


def test_the_issue_body_starts_with_the_marker_in_portuguese_too() -> None:
    body = issue_body((INVALID,), NOW, Lang.PT_BR)
    assert body.startswith(MARKER + "\n")
    assert "- INVÁLIDO `example.com:443`, certificado não passou na verificação" in body


def test_an_empty_view_says_nothing_needs_attention() -> None:
    body = issue_body((), NOW, Lang.EN)
    assert body.splitlines()[:2] == [MARKER, "Nothing needs attention."]
    assert title((), Lang.EN) == "TLSVigil: nothing needs attention"
    assert title((), Lang.PT_BR) == "TLSVigil: nada precisa de atenção"


@pytest.mark.parametrize(
    ("count", "english", "portuguese"),
    [
        (1, "TLSVigil: 1 entry needs attention", "TLSVigil: 1 entrada precisa de atenção"),
        (2, "TLSVigil: 2 entries need attention", "TLSVigil: 2 entradas precisam de atenção"),
    ],
)
def test_the_title_counts_the_view(count: int, english: str, portuguese: str) -> None:
    view = tuple(_expiring(f"host{index}.example:443") for index in range(count))
    assert title(view, Lang.EN) == english
    assert title(view, Lang.PT_BR) == portuguese


def test_a_certificate_past_its_date_counts_zero_days_like_the_check() -> None:
    late = _expiring(days=0)
    assert (
        detail(late, NOW + timedelta(hours=1), Lang.EN) == "certificate expires today (2026-11-27)"
    )


def test_a_domain_past_its_date_counts_the_days_since() -> None:
    late = Finding("domain:a.example", "a.example", "expiring", None, NOW - timedelta(days=2), -1)
    assert detail(late, NOW, Lang.EN) == "domain expired 2 days ago (2026-11-25)"


def test_the_comment_lists_the_events_and_the_count_after_the_run() -> None:
    plan = Plan((EVENTS[EventKind.NEW], EVENTS[EventKind.RESOLVED]), (_expiring(),))
    assert comment(plan, NOW, Lang.EN).splitlines() == [
        "- New: `example.com:443`, certificate expires in 9 days (2026-12-06)",
        "- Resolved: `example.com:443`",
        "",
        "Now 1 entry needs attention.",
    ]
    assert comment(Plan((EVENTS[EventKind.RESOLVED],), ()), NOW, Lang.PT_BR).endswith(
        "Agora nada precisa de atenção."
    )


def test_the_mail_body_has_the_events_then_the_view_in_plain_text() -> None:
    plan = Plan((EVENTS[EventKind.NEW],), (_expiring(),))
    assert mail_body(plan, NOW, Lang.EN) == (
        "New: example.com:443, certificate expires in 9 days (2026-12-06)\n"
        "\n"
        "EXPIRING example.com:443, certificate expires in 9 days (2026-12-06)\n"
    )
    closing = mail_body(Plan((EVENTS[EventKind.RESOLVED],), ()), NOW, Lang.PT_BR)
    assert closing.splitlines()[-1] == "Nada precisa de atenção."


# ---------------------------------------------------------------------------
# Outside text
# ---------------------------------------------------------------------------

HOSTILE = "**x** <b>y</b> [z](https://e.example) @user #1 www.e.example `a` ``b`` \x1b\nend"


def test_outside_text_reaches_github_inside_one_code_span() -> None:
    finding = Finding("certificate:hostile", HOSTILE, "invalid", "expired")
    plan = Plan((Event(EventKind.NEW, finding),), (finding,))
    span = code_span(HOSTILE)
    # The longest run inside is two backticks, so the fence is three; no line break survives.
    assert span.startswith("``` ") is False
    assert span.startswith("```") and span.endswith("```")
    assert span[3:-3] == HOSTILE.replace("\x1b", "\\x1b").replace("\n", "\\n")
    assert f"- New: {span}, certificate" in comment(plan, NOW, Lang.EN)
    assert f"- INVALID {span}, certificate" in issue_body((finding,), NOW, Lang.EN)


def test_outside_text_reaches_telegram_and_the_mail_as_plain_text() -> None:
    finding = Finding("certificate:hostile", HOSTILE, "invalid", "expired")
    plan = Plan((Event(EventKind.NEW, finding),), (finding,))
    plain = HOSTILE.replace("\x1b", "\\x1b").replace("\n", "\\n")
    assert f"New: {plain}, certificate" in telegram_text(plan, NOW, Lang.EN)
    assert f"New: {plain}, certificate" in mail_body(plan, NOW, Lang.EN)
    assert "\x1b" not in mail_body(plan, NOW, Lang.EN)


@pytest.mark.parametrize(
    ("text", "span"),
    [
        ("plain", "`plain`"),
        ("a`b", "``a`b``"),
        ("a``b`c", "```a``b`c```"),
        ("`edge", "`` `edge ``"),
        ("edge`", "`` edge` ``"),
        (" both ", "`  both  `"),
        (" left", "` left`"),
        ("", "`  `"),
    ],
)
def test_the_code_span_fence_is_longer_than_any_run_inside(text: str, span: str) -> None:
    assert code_span(text) == span


def test_the_subject_holds_no_outside_text() -> None:
    finding = Finding("certificate:hostile", HOSTILE, "invalid", "expired")
    subject = mail_subject(Plan((Event(EventKind.NEW, finding),), (finding,)), Lang.EN)
    assert subject == "TLSVigil: 1 entry needs attention"


@pytest.mark.parametrize(
    "key", sorted(key for key in MESSAGES if key.startswith(_NOTIFICATION_PREFIXES))
)
def test_no_sentence_of_a_notification_holds_a_markdown_character(key: str) -> None:
    for lang in Lang:
        assert not _MARKDOWN.search(MESSAGES[key][lang]), (key, lang)


def test_the_markdown_check_sees_a_markdown_character() -> None:
    # The control of the test above: the pattern finds what it is meant to find.
    assert all(_MARKDOWN.search(char) for char in "`*_[]<>#|~\\&@!")


# ---------------------------------------------------------------------------
# The caps
# ---------------------------------------------------------------------------


def _many(count: int, length: int = 80) -> tuple[Finding, ...]:
    """
    Build many findings with long names.

    Args:
        count (int): How many.
        length (int): The length of each name.

    Returns:
        tuple[Finding, ...]: Invalid certificates with distinct names.
    """
    return tuple(
        Finding(f"certificate:{index}", f"{index:05d}".ljust(length, "a"), "invalid", "expired")
        for index in range(count)
    )


def _telegram_of(count: int) -> str:
    """
    Write the Telegram message of a run with many new warnings.

    Args:
        count (int): How many.

    Returns:
        str: The message.
    """
    view = _many(count)
    return telegram_text(Plan(tuple(Event(EventKind.NEW, f) for f in view), view), NOW, Lang.EN)


def test_a_telegram_message_with_one_entry_more_than_fits_is_cut_at_a_whole_line() -> None:
    # The most entries that fit whole, found by adding one at a time.
    fits = 1
    while "... and" not in _telegram_of(fits + 1):
        fits += 1
    assert utf16_length(_telegram_of(fits)) <= TELEGRAM_CAP

    cut = _telegram_of(fits + 1)
    assert utf16_length(cut) <= TELEGRAM_CAP
    lines = cut.splitlines()
    assert all(line.startswith("New: ") for line in lines[2:-1])
    # The last line counts what was left out, so nothing goes missing without a word.
    more = re.fullmatch(r"\.\.\. and (\d+) more", lines[-1])
    assert more is not None
    assert len(lines[2:-1]) + int(more[1]) == fits + 1
    # As many entries as fit are kept: the room left is less than one more line.
    assert utf16_length(cut) > TELEGRAM_CAP - (utf16_length(lines[2]) + 1)


def test_an_issue_body_over_the_cap_ends_with_the_count_left_out() -> None:
    view = _many(1000)
    body = issue_body(view, NOW, Lang.PT_BR)
    assert utf16_length(body) <= GITHUB_CAP
    lines = body.splitlines()
    items = [line for line in lines if line.startswith("- INVÁLIDO")]
    more = next(line for line in lines if line.startswith("- ... e mais "))
    assert len(items) + int(more.rsplit(" ", 1)[1]) == 1000
    # The footer is kept after the cut.
    assert lines[-1].startswith("O TLSVigil reescreve")


def test_a_comment_over_the_cap_is_cut_and_keeps_the_count() -> None:
    view = _many(1000)
    text = comment(Plan(tuple(Event(EventKind.NEW, f) for f in view), view), NOW, Lang.EN)
    assert utf16_length(text) <= GITHUB_CAP
    assert text.endswith("Now 1000 entries need attention.")


def test_the_cap_counts_utf16_code_units() -> None:
    # An emoji is one character in Python and two code units for GitHub and Telegram.
    assert utf16_length("a\U0001f512") == 3
    wide = tuple(
        Finding(f"certificate:{i}", f"{i:04d}" + "\U0001f512" * 60, "invalid", "expired")
        for i in range(200)
    )
    text = telegram_text(Plan(tuple(Event(EventKind.NEW, f) for f in wide), wide), NOW, Lang.EN)
    assert len(text) < utf16_length(text) <= TELEGRAM_CAP
