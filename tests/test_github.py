"""
Tests for the GitHub channel: the life of the issue against a fake API (spec 005, RF-08).

Every test runs :class:`~tlsvigil.github.GitHubChannel` against :class:`~fake_apis.FakeGitHub` on
the local HTTPS server, so the requests are real HTTPS on ``127.0.0.1`` and ``api.github.com`` is
never resolved. Each delivery checks that every request carried the headers and the token, and
the tests count the requests: four at most on every path (RNF-05).
"""

from __future__ import annotations

import ssl
from datetime import UTC, datetime, timedelta

import pytest

from fake_apis import REPOSITORY, TOKEN, FakeGitHub, Operation, github_answer
from http_server import LocalHTTPServer, Route
from tlsvigil.alerts import Event, EventKind, Finding, Plan
from tlsvigil.certificate import create_context
from tlsvigil.config import Channel
from tlsvigil.delivery import Delivery, DeliveryOutcome
from tlsvigil.environment import GitHubSettings
from tlsvigil.github import BOT_LOGIN, MAX_MESSAGE_CHARS, GitHubChannel
from tlsvigil.i18n import Lang
from tlsvigil.messages import MARKER
from tlsvigil.state import ChannelState

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)

WARN = Finding(
    "certificate:example.com:443", "example.com:443", "expiring", None, NOW + timedelta(days=9)
)
SOONER = Finding(
    "certificate:example.com:443", "example.com:443", "expiring", None, NOW + timedelta(days=6), 7
)
BROKEN = Finding("certificate:example.com:443", "example.com:443", "invalid", "expired")

NEW = Plan((Event(EventKind.NEW, WARN),), (WARN,))
RESOLVED = Plan((Event(EventKind.RESOLVED, WARN),), ())
QUIET = Plan((), (WARN,))

EMPTY = ChannelState(repository=REPOSITORY)


def _told(*found: Finding) -> dict[str, Finding]:
    """
    Give what a channel was told, by key.

    Args:
        *found (Finding): The findings.

    Returns:
        dict[str, Finding]: The same findings, by key.
    """
    return {finding.key: finding for finding in found}


def _kept(number: int, *found: Finding) -> ChannelState:
    """
    Give the part of a channel that keeps TLSVigil's issue.

    Args:
        number (int): The number of the issue.
        *found (Finding): What the channel was told.

    Returns:
        ChannelState: The part, with the bot as the author.
    """
    return ChannelState(_told(*found), REPOSITORY, number, BOT_LOGIN)


@pytest.fixture
def fake() -> FakeGitHub:
    """
    Give an empty fake repository.

    Returns:
        FakeGitHub: No issue yet.
    """
    return FakeGitHub()


@pytest.fixture
def api(fake: FakeGitHub, https_server: LocalHTTPServer) -> str:
    """
    Serve the fake on the local server.

    Args:
        fake (FakeGitHub): The fake.
        https_server (LocalHTTPServer): The running server.

    Returns:
        str: The API root.
    """
    return fake.install(https_server)


@pytest.fixture
def channel(api: str, client_context: ssl.SSLContext) -> GitHubChannel:
    """
    Build the channel with the fake's token, pointed at the fake.

    Args:
        api (str): The API root.
        client_context (ssl.SSLContext): The client context that trusts the test CA.

    Returns:
        GitHubChannel: The channel.
    """
    settings = GitHubSettings(TOKEN, REPOSITORY)
    return GitHubChannel(settings, timeout=5.0, context=client_context, api_url=api)


def _deliver(
    channel: GitHubChannel,
    fake: FakeGitHub,
    plan: Plan,
    part: ChannelState = EMPTY,
    now: datetime = NOW,
) -> tuple[Delivery, ChannelState]:
    """
    Deliver a plan, and check the requests it sent.

    Args:
        channel (GitHubChannel): The channel.
        fake (FakeGitHub): The fake, to read its problems.
        plan (Plan): The plan.
        part (ChannelState): What the channel was told.
        now (datetime): The moment the run started.

    Returns:
        tuple[Delivery, ChannelState]: What the channel gave.
    """
    before = len(fake.operations)
    delivery, state = channel.deliver(plan, part, now, Lang.EN)
    assert fake.problems == []
    assert len(fake.operations) - before <= 4
    return delivery, state


# ---------------------------------------------------------------------------
# The life of the issue
# ---------------------------------------------------------------------------


def test_a_new_warning_opens_one_issue(channel: GitHubChannel, fake: FakeGitHub) -> None:
    delivery, state = _deliver(channel, fake, NEW)

    assert fake.operations == ["list", "create"]
    assert delivery == Delivery(Channel.GITHUB, DeliveryOutcome.SENT, None, 1)
    assert state == ChannelState(_told(WARN), REPOSITORY, 1, BOT_LOGIN)
    issue = fake.issues[1]
    assert issue.title == "TLSVigil: 1 entry needs attention"
    assert issue.body is not None and issue.body.startswith(MARKER)
    assert "`example.com:443`, certificate expires in 9 days" in issue.body


def test_a_later_change_comments_and_rewrites(channel: GitHubChannel, fake: FakeGitHub) -> None:
    fake.add("old title", MARKER + "\nold body")
    plan = Plan((Event(EventKind.CHANGED, BROKEN, WARN),), (BROKEN,))

    delivery, state = _deliver(channel, fake, plan, _kept(1, WARN))

    assert fake.operations == ["get", "comment", "update"]
    assert delivery.outcome is DeliveryOutcome.SENT
    assert state.told == _told(BROKEN)
    issue = fake.issues[1]
    assert issue.comments == [
        "- Changed: `example.com:443`, certificate failed verification: expired (was expiring)"
        "\n\nNow 1 entry needs attention."
    ]
    assert issue.body is not None and "INVALID `example.com:443`" in issue.body
    assert issue.state == "open"


def test_a_run_with_no_event_rewrites_the_days_with_no_comment(
    channel: GitHubChannel, fake: FakeGitHub
) -> None:
    fake.add("TLSVigil: 1 entry needs attention", MARKER + "\nold")

    delivery, state = _deliver(channel, fake, QUIET, _kept(1, WARN), NOW + timedelta(days=1))

    assert fake.operations == ["get", "update"]
    assert delivery == Delivery(Channel.GITHUB, DeliveryOutcome.UNCHANGED, None, 1)
    assert state == _kept(1, WARN)
    issue = fake.issues[1]
    assert issue.comments == []
    assert issue.body is not None and "expires in 8 days" in issue.body


def test_the_last_warning_resolved_comments_and_closes(
    channel: GitHubChannel, fake: FakeGitHub
) -> None:
    fake.add("TLSVigil: 1 entry needs attention", MARKER)

    delivery, state = _deliver(channel, fake, RESOLVED, _kept(1, WARN))

    assert fake.operations == ["get", "comment", "update"]
    assert delivery == Delivery(Channel.GITHUB, DeliveryOutcome.SENT, None, 1)
    assert state == ChannelState({}, REPOSITORY, None, None)
    issue = fake.issues[1]
    assert issue.comments[0].startswith("- Resolved: `example.com:443`")
    assert (issue.state, issue.state_reason) == ("closed", "completed")
    assert issue.title == "TLSVigil: nothing needs attention"


def test_an_issue_closed_by_a_person_stays_closed_until_the_next_change(
    channel: GitHubChannel, fake: FakeGitHub
) -> None:
    closed = fake.add("TLSVigil: 1 entry needs attention", MARKER, state="closed")

    delivery, state = _deliver(channel, fake, QUIET, _kept(1, WARN))

    assert fake.operations == ["get", "list"]
    assert delivery.outcome is DeliveryOutcome.UNCHANGED
    assert (state.issue, state.told) == (None, _told(WARN))
    assert (closed.state, closed.body, closed.comments) == ("closed", MARKER, [])

    # A reminder is a change: a new issue is opened, and the closed one is still left alone.
    reminder = Plan((Event(EventKind.REMINDER, SOONER),), (SOONER,))
    delivery, state = _deliver(channel, fake, reminder, state)

    assert fake.operations[2:] == ["list", "create"]
    assert (delivery.issue, state.issue) == (2, 2)
    assert (closed.state, closed.body, closed.comments) == ("closed", MARKER, [])


@pytest.mark.parametrize("status", [404, 410, 301])
def test_a_deleted_transferred_or_missing_issue_is_forgotten(
    channel: GitHubChannel, fake: FakeGitHub, status: int
) -> None:
    if status != 404:
        fake.gone[7] = status  # a missing number is a 404 already

    delivery, state = _deliver(channel, fake, NEW, _kept(7, WARN))

    assert fake.operations == ["get", "list", "create"]
    assert (delivery.outcome, state.issue) == (DeliveryOutcome.SENT, 1)


def test_only_tlsvigils_issue_is_adopted_and_the_others_are_left_untouched(
    channel: GitHubChannel, fake: FakeGitHub
) -> None:
    by_person = fake.add("TLSVigil: 1 entry needs attention", MARKER, author="someone")
    no_marker = fake.add("TLSVigil: 1 entry needs attention", "written by hand")
    ours = fake.add("TLSVigil: 1 entry needs attention", MARKER + "\nlost state")
    pull = fake.add("TLSVigil: 1 entry needs attention", MARKER, pull_request=True)
    newer = fake.add("TLSVigil: 1 entry needs attention", MARKER)

    delivery, state = _deliver(channel, fake, NEW)

    # The oldest of TLSVigil's own got the comment and the rewrite: the control of the others.
    assert fake.operations == ["list", "comment", "update"]
    assert (delivery.issue, state.issue, state.author) == (ours.number, ours.number, BOT_LOGIN)
    assert len(ours.comments) == 1
    assert ours.body is not None and "expires in 9 days" in ours.body
    for other in (by_person, no_marker, pull, newer):
        assert other.comments == []
        assert other.body in (MARKER, "written by hand")
        assert other.state == "open"


def test_a_kept_number_on_an_issue_by_someone_else_is_forgotten(
    channel: GitHubChannel, fake: FakeGitHub
) -> None:
    # The state names issue 1, but a person opened it, with the marker copied in.
    theirs = fake.add("TLSVigil: 1 entry needs attention", MARKER, author="someone")

    delivery, state = _deliver(channel, fake, NEW, _kept(1, WARN))

    assert fake.operations == ["get", "list", "create"]
    assert (delivery.issue, state.issue) == (2, 2)
    assert (theirs.body, theirs.comments, theirs.state) == (MARKER, [], "open")


def test_a_kept_issue_with_a_body_of_none_is_still_read(
    channel: GitHubChannel, fake: FakeGitHub
) -> None:
    # A person may empty the body; GitHub then sends null.
    fake.add("TLSVigil: 1 entry needs attention", None)

    delivery, _ = _deliver(channel, fake, QUIET, _kept(1, WARN))

    assert delivery.outcome is DeliveryOutcome.UNCHANGED
    assert fake.issues[1].body is not None


def test_the_longest_path_sends_four_requests(channel: GitHubChannel, fake: FakeGitHub) -> None:
    fake.gone[9] = 410
    fake.add("TLSVigil: 1 entry needs attention", MARKER)

    _deliver(channel, fake, NEW, _kept(9, WARN))

    assert fake.operations == ["get", "list", "comment", "update"]


def test_warnings_that_end_while_the_issue_is_closed_post_nothing(
    channel: GitHubChannel, fake: FakeGitHub
) -> None:
    fake.add("TLSVigil: 1 entry needs attention", MARKER, state="closed")

    delivery, state = _deliver(channel, fake, RESOLVED, _kept(1, WARN))

    assert fake.operations == ["get", "list"]
    assert delivery.outcome is DeliveryOutcome.UNCHANGED
    assert state == ChannelState({}, REPOSITORY, None, None)


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------

PERMISSION = "the token cannot write issues: the workflow needs permissions: issues: write"
RATE = "GitHub limits the requests; the next run tries again"


@pytest.mark.parametrize(
    ("operation", "answer", "message"),
    [
        (
            "create",
            github_answer(403, "Resource not accessible by integration"),
            PERMISSION,
        ),
        (
            "create",
            github_answer(403, "Forbidden", {"X-Accepted-GitHub-Permissions": "issues=write"}),
            PERMISSION,
        ),
        ("list", github_answer(403, "API rate limit", {"X-RateLimit-Remaining": "0"}), RATE),
        ("comment", github_answer(429, "slow down", {"Retry-After": "60"}), RATE),
        (
            "create",
            github_answer(
                403,
                "secondary limit",
                {"Retry-After": "60", "X-Accepted-GitHub-Permissions": "issues=write"},
            ),
            RATE,
        ),
        (
            "list",
            github_answer(404, "Not Found"),
            "the repository does not exist or the token cannot see it",
        ),
        (
            "create",
            github_answer(410, "Issues are disabled"),
            "issues are turned off in the repository",
        ),
        (
            "update",
            github_answer(422, "Validation Failed"),
            "GitHub answered HTTP 422 (Validation Failed)",
        ),
        ("get", github_answer(500), "GitHub answered HTTP 500"),
        ("get", github_answer(403, "Forbidden"), "GitHub answered HTTP 403 (Forbidden)"),
        (
            "list",
            Route(200, b"<html>not json</html>"),
            "GitHub sent an answer TLSVigil cannot read",
        ),
        ("create", Route(201, b'{"number": "1"}'), "GitHub sent an answer TLSVigil cannot read"),
        ("list", Route(200, b'{"issues": []}'), "GitHub sent an answer TLSVigil cannot read"),
        ("list", Route(200, b"[1]"), "GitHub sent an answer TLSVigil cannot read"),
        (
            "list",
            Route(200, b'[{"number": 1, "state": "open", "user": {"login": "x"}, "body": 5}]'),
            "GitHub sent an answer TLSVigil cannot read",
        ),
        ("get", Route(503, b"<html>busy</html>"), "GitHub answered HTTP 503"),
    ],
)
def test_each_refusal_fails_the_delivery_with_its_message(
    channel: GitHubChannel, fake: FakeGitHub, operation: Operation, answer: Route, message: str
) -> None:
    # The list and the new issue run without a kept issue; the others with issue 1 kept.
    kept = operation in ("get", "comment", "update")
    part = _kept(1, WARN) if kept else EMPTY
    if kept:
        fake.add("TLSVigil: 1 entry needs attention", MARKER)
    plan = Plan((Event(EventKind.CHANGED, BROKEN, WARN),), (BROKEN,)) if kept else NEW
    fake.answers[operation] = answer

    delivery, state = _deliver(channel, fake, plan, part)

    assert delivery.outcome is DeliveryOutcome.FAILED
    assert delivery.message == message
    assert fake.operations[-1] == operation
    # Told only once the comment or the issue is posted: a failed update still advances.
    assert state.told == (_told(BROKEN) if operation == "update" else part.told)


def test_a_refused_token_fails_the_delivery(
    api: str, client_context: ssl.SSLContext, fake: FakeGitHub
) -> None:
    settings = GitHubSettings("not-the-right-token", REPOSITORY)
    channel = GitHubChannel(settings, timeout=5.0, context=client_context, api_url=api)

    delivery, state = channel.deliver(NEW, EMPTY, NOW, Lang.EN)

    assert (delivery.outcome, delivery.message) == (
        DeliveryOutcome.FAILED,
        "GitHub refused the token",
    )
    assert fake.operations == ["list"]
    assert state.told == {}


def test_githubs_message_is_escaped_and_cut(channel: GitHubChannel, fake: FakeGitHub) -> None:
    fake.answers["list"] = github_answer(500, "\x1b[31m" + "x" * 300 + "\n@user")

    delivery, _ = _deliver(channel, fake, NEW)

    assert delivery.message is not None
    expected = ("\\x1b[31m" + "x" * 300)[:MAX_MESSAGE_CHARS]
    assert delivery.message == f"GitHub answered HTTP 500 ({expected})"
    assert "\x1b" not in delivery.message


def test_an_api_that_cannot_be_reached_fails_the_delivery(api: str, fake: FakeGitHub) -> None:
    # A context without the test CA: the handshake fails, and nothing is retried.
    settings = GitHubSettings(TOKEN, REPOSITORY)
    channel = GitHubChannel(settings, timeout=5.0, context=create_context(), api_url=api)

    delivery, _ = channel.deliver(NEW, EMPTY, NOW, Lang.PT_BR)

    assert delivery.outcome is DeliveryOutcome.FAILED
    assert delivery.message is not None
    assert delivery.message.startswith("não foi possível falar com a API do GitHub: a conexão TLS")
    assert fake.operations == []


def test_the_token_travels_only_in_the_authorization_header(
    channel: GitHubChannel, fake: FakeGitHub, https_server: LocalHTTPServer
) -> None:
    fake.add("TLSVigil: 1 entry needs attention", MARKER)

    delivery, state = _deliver(channel, fake, RESOLVED, _kept(1, WARN))

    assert len(https_server.requests) == 3
    for request in https_server.requests:
        assert request.header("Authorization") == f"Bearer {TOKEN}"
        others = [value for name, value in request.headers if name.lower() != "authorization"]
        assert not any(TOKEN in value for value in others)
        assert TOKEN not in request.path
        assert TOKEN.encode() not in request.body
    assert TOKEN not in repr((delivery, state))


def test_a_failed_update_is_repaired_by_the_next_run(
    channel: GitHubChannel, fake: FakeGitHub
) -> None:
    issue = fake.add("TLSVigil: 1 entry needs attention", MARKER)
    fake.answers["update"] = github_answer(502, "Bad Gateway")

    delivery, state = _deliver(channel, fake, RESOLVED, _kept(1, WARN))

    # The comment was posted, so the channel was told: the next run has no event left.
    assert delivery.outcome is DeliveryOutcome.FAILED
    assert (state.told, state.issue) == ({}, 1)
    assert issue.state == "open"
    assert len(issue.comments) == 1

    del fake.answers["update"]
    delivery, state = _deliver(channel, fake, Plan((), ()), state)

    assert fake.operations[3:] == ["get", "update"]
    assert delivery.outcome is DeliveryOutcome.UNCHANGED
    assert (issue.state, issue.state_reason, state.issue) == ("closed", "completed", None)
    assert len(issue.comments) == 1
