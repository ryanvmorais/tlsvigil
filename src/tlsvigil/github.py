"""
The GitHub channel: one issue in the repository of the run, kept up to date (spec 005, RF-08).

:meth:`GitHubChannel.deliver` works in three steps, with four requests at most (RNF-05):

1. **Find the issue.** The one whose number the state keeps, while it is open and by the same
   author; or else the oldest open issue by ``github-actions[bot]`` whose body starts with the
   marker, which only TLSVigil writes (ADR-3).
2. **Tell.** A comment with the events on the issue found, or a new issue when there is none and
   something needs attention. Only these two notify the watchers (ADR-4).
3. **Bring the issue found up to date.** Its title and body rewritten on every run, and the issue
   closed when nothing needs attention any more.

What the channel was told advances as soon as step 2 succeeds, so a failure in step 3 never tells
twice: the next run repeats step 3 whatever its events. Nothing is retried within a run. The token
travels in one header and is never part of a message; the only text of GitHub's that reaches a
message is its ``message`` field, escaped and cut.
"""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from tlsvigil import __version__, https
from tlsvigil.alerts import Plan
from tlsvigil.certificate import create_context
from tlsvigil.config import Channel
from tlsvigil.delivery import Delivery, DeliveryOutcome
from tlsvigil.environment import GitHubSettings
from tlsvigil.i18n import Lang, translate
from tlsvigil.messages import MARKER, comment, issue_body, title
from tlsvigil.output import printable
from tlsvigil.state import ChannelState

__all__ = [
    "API_URL",
    "API_VERSION",
    "BOT_LOGIN",
    "MAX_ANSWER_BYTES",
    "MAX_MESSAGE_CHARS",
    "GitHubChannel",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The root of the REST API; the tests give the channel a local server's URL instead.
API_URL: Final = "https://api.github.com"

# The newest version the REST documentation lists; 2022-11-28 stays supported until 2028-03-10.
API_VERSION: Final = "2026-03-10"

# The author of an issue a workflow opens with its own token; only such a run can be it (ADR-3).
BOT_LOGIN: Final = "github-actions[bot]"

# The largest answer read: a page of 100 issues with long bodies fits well inside.
MAX_ANSWER_BYTES: Final = 1024 * 1024

# GitHub's own `message` is cut to this many characters before it reaches a message.
MAX_MESSAGE_CHARS: Final = 200

# The most issues the adoption reads: one page, so the requests stay bounded (RNF-05).
_PAGE_SIZE: Final = 100

# The answers that say an issue is gone: deleted, or moved to another repository.
_GONE: Final = frozenset({404, 410, 301})

# The message of a 403 whose token lacks a permission, as GitHub writes it.
_NOT_ACCESSIBLE: Final = "Resource not accessible by integration"


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


class _Failed(Exception):
    """
    A request whose failure ends the delivery, with the message it ends with.

    Attributes:
        message (str): Why, in the language of the run, as plain text.
    """

    def __init__(self, message: str) -> None:
        """
        Keep the message.

        Args:
            message (str): Why the delivery failed.
        """
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class _Issue:
    """
    The fields of an issue the channel reads.

    Attributes:
        number (int): The number of the issue.
        author (str): The login that opened it.
        open (bool): Whether it is open.
        body (str): Its body; empty when it has none.
        pull_request (bool): Whether it is a pull request, which the issues API lists too.
    """

    number: int
    author: str
    open: bool
    body: str
    pull_request: bool


def _github_message(response: https.Response) -> str | None:
    """
    Read the ``message`` field of an answer, when it has one.

    Args:
        response (https.Response): The answer.

    Returns:
        str | None: The text of the field, or ``None`` when the body is not a JSON object with
            a string ``message``.
    """
    try:
        document = json.loads(response.body)
    except ValueError:
        return None
    message = document.get("message") if isinstance(document, dict) else None
    return message if isinstance(message, str) else None


def _rate_limited(response: https.Response) -> bool:
    """
    Tell whether an answer is GitHub limiting the requests, primary or secondary.

    Args:
        response (https.Response): A ``403`` or ``429`` answer.

    Returns:
        bool: ``True`` with a ``retry-after`` header or ``x-ratelimit-remaining: 0``.
    """
    headers = response.headers
    return "retry-after" in headers or headers.get("x-ratelimit-remaining") == "0"


def _failure(response: https.Response, lang: Lang, *, repository_level: bool, create: bool) -> str:
    """
    Write why an answer ends the delivery, by the table of the design (RF-08).

    Args:
        response (https.Response): An answer the step cannot use.
        lang (Lang): The language of the run.
        repository_level (bool): Whether the request names the repository only (the list and
            the new issue), so a ``404`` means the repository.
        create (bool): Whether the request opens an issue, so a ``410`` means issues are off.

    Returns:
        str: The message, as plain text.
    """
    status = response.status
    message = _github_message(response)
    if status == 401:
        return translate(lang, "github.unauthorized")
    if status == 403 and message == _NOT_ACCESSIBLE:
        return translate(lang, "github.permission")
    # Before the permission header: a limited answer may carry that header too.
    if status in (403, 429) and _rate_limited(response):
        return translate(lang, "github.rate_limited")
    if status == 403 and "x-accepted-github-permissions" in response.headers:
        return translate(lang, "github.permission")
    if status == 404 and repository_level:
        return translate(lang, "github.not_found")
    if status == 410 and create:
        return translate(lang, "github.issues_disabled")
    text = translate(lang, "github.http", status=status)
    if message:
        text = f"{text} ({printable(message)[:MAX_MESSAGE_CHARS]})"
    return text


def _issue(value: object) -> _Issue:
    """
    Read the fields the channel needs from one issue of an answer.

    Args:
        value (object): One issue, as decoded from JSON.

    Returns:
        _Issue: Its fields.

    Raises:
        ValueError: When the issue lacks one of them or has the wrong type.
    """
    if not isinstance(value, dict):
        raise ValueError("an issue is not an object")
    number, state, user, body = (value.get(key) for key in ("number", "state", "user", "body"))
    login = user.get("login") if isinstance(user, dict) else None
    # `type(...) is int` and not isinstance: bool is a subclass of int.
    if type(number) is not int or not isinstance(state, str) or not isinstance(login, str):
        raise ValueError("an issue lacks its number, state or author")
    if body is not None and not isinstance(body, str):
        raise ValueError("the body of an issue is not text")
    return _Issue(number, login, state == "open", body or "", "pull_request" in value)


# ---------------------------------------------------------------------------
# The channel
# ---------------------------------------------------------------------------


class GitHubChannel:
    """
    Deliver a plan to one issue of the repository of the run (RF-08).

    Attributes:
        settings (GitHubSettings): The token and the repository.
        timeout (float): Seconds for each whole request.
        api_url (str): The ``https://`` root of the REST API.
    """

    def __init__(
        self,
        settings: GitHubSettings,
        *,
        timeout: float,
        context: ssl.SSLContext | None = None,
        api_url: str = API_URL,
    ) -> None:
        """
        Keep the settings; nothing is sent until :meth:`deliver`.

        Args:
            settings (GitHubSettings): The token and the repository.
            timeout (float): Seconds for each whole request, the timeout of the configuration.
            context (ssl.SSLContext | None): The client context; ``None`` means
                :func:`~tlsvigil.certificate.create_context`. The tests pass one that also
                trusts their own CA.
            api_url (str): The root of the REST API; the tests point it at a local server.
        """
        self.settings = settings
        self.timeout = timeout
        self.api_url = api_url.rstrip("/")
        self._context = create_context() if context is None else context

    # -----------------------------------------------------------------------
    # Requests
    # -----------------------------------------------------------------------

    def _send(
        self, method: str, path: str, lang: Lang, document: dict[str, object] | None = None
    ) -> https.Response:
        """
        Send one request to the API and give its answer, whatever the status.

        Args:
            method (str): ``GET``, ``POST`` or ``PATCH``.
            path (str): The path under the API root, with its query string.
            lang (Lang): The language of the run, for the message of a failure.
            document (dict[str, object] | None): The JSON body, or ``None`` for none.

        Returns:
            https.Response: The answer.

        Raises:
            _Failed: When the request got no answer (DNS, refused, timeout, TLS, too large).
        """
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "Authorization": f"Bearer {self.settings.token}",
            "User-Agent": f"tlsvigil/{__version__}",
        }
        body = None
        if document is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(document, ensure_ascii=False).encode("utf-8")
        try:
            return https.request(
                method,
                self.api_url + path,
                headers=headers,
                body=body,
                cap=MAX_ANSWER_BYTES,
                deadline=https.deadline_after(self.timeout),
                context=self._context,
            )
        except https.RequestError as error:
            kind = translate(lang, f"request.{error.kind}")
            text = translate(lang, "github.unreachable", kind=kind)
            if error.message:
                text = f"{text} ({printable(error.message)[:MAX_MESSAGE_CHARS]})"
            raise _Failed(text) from None

    def _json(self, response: https.Response, lang: Lang) -> object:
        """
        Decode the body of a ``2xx`` answer.

        Args:
            response (https.Response): The answer.
            lang (Lang): The language of the run.

        Returns:
            object: The decoded document.

        Raises:
            _Failed: When the body is not JSON.
        """
        try:
            return json.loads(response.body)
        except ValueError:
            raise _Failed(translate(lang, "github.invalid")) from None

    def _read_issue(self, response: https.Response, lang: Lang) -> _Issue:
        """
        Read one issue from a ``2xx`` answer.

        Args:
            response (https.Response): The answer.
            lang (Lang): The language of the run.

        Returns:
            _Issue: The issue.

        Raises:
            _Failed: When the answer is not an issue.
        """
        try:
            return _issue(self._json(response, lang))
        except ValueError:
            raise _Failed(translate(lang, "github.invalid")) from None

    # -----------------------------------------------------------------------
    # The three steps
    # -----------------------------------------------------------------------

    def _find(self, part: ChannelState, lang: Lang) -> tuple[int | None, str | None]:
        """
        Step 1: find TLSVigil's open issue, the kept one or an adopted one (ADR-3).

        Args:
            part (ChannelState): What the channel was told, with the kept issue, if any.
            lang (Lang): The language of the run.

        Returns:
            tuple[int | None, str | None]: The number and the author of the issue, or
                ``(None, None)`` when there is none.

        Raises:
            _Failed: When a request fails or GitHub refuses it.
        """
        repository = self.settings.repository
        if part.issue is not None:
            response = self._send("GET", f"/repos/{repository}/issues/{part.issue}", lang)
            if 200 <= response.status < 300:
                issue = self._read_issue(response, lang)
                if issue.open and issue.author == part.author and not issue.pull_request:
                    return issue.number, issue.author
                # Closed by a person, or no longer TLSVigil's: forgotten, and left untouched.
            elif response.status not in _GONE:
                raise _Failed(_failure(response, lang, repository_level=False, create=False))

        path = f"/repos/{repository}/issues?state=open&per_page={_PAGE_SIZE}"
        response = self._send("GET", path, lang)
        if not 200 <= response.status < 300:
            raise _Failed(_failure(response, lang, repository_level=True, create=False))
        document = self._json(response, lang)
        if not isinstance(document, list):
            raise _Failed(translate(lang, "github.invalid"))
        try:
            issues = [_issue(value) for value in document]
        except ValueError:
            raise _Failed(translate(lang, "github.invalid")) from None
        ours = [
            issue
            for issue in issues
            if issue.author == BOT_LOGIN
            and issue.body.startswith(MARKER)
            and not issue.pull_request
            and issue.open
        ]
        if not ours:
            return None, None
        # The oldest: a lost state while two runs raced leaves the first one opened in charge.
        oldest = min(ours, key=lambda issue: issue.number)
        return oldest.number, oldest.author

    def deliver(
        self, plan: Plan, part: ChannelState, now: datetime, lang: Lang
    ) -> tuple[Delivery, ChannelState]:
        """
        Deliver a plan to the issue: find it, tell the events, bring it up to date (RF-08).

        Args:
            plan (Plan): What the channel hears in this run.
            part (ChannelState): What the channel was told, with the kept issue; it belongs to
                the repository of the settings (:func:`~tlsvigil.state.reset_for`).
            now (datetime): The moment the run started, aware; the days left count from it.
            lang (Lang): The language of the run.

        Returns:
            tuple[Delivery, ChannelState]: How the delivery ended, and what the channel knows
                afterwards: ``told`` is the view once step 2 succeeded, or the old one; the
                issue is the one found or opened, and none once it is closed.
        """
        repository = self.settings.repository
        state = replace(part, repository=repository)
        sent = False
        try:
            number, author = self._find(part, lang)
            state = replace(state, issue=number, author=author)

            # Step 2: tell.
            heading = title(plan.view, lang)
            if plan.events and number is not None:
                path = f"/repos/{repository}/issues/{number}/comments"
                response = self._send("POST", path, lang, {"body": comment(plan, now, lang)})
                if not 200 <= response.status < 300:
                    raise _Failed(_failure(response, lang, repository_level=False, create=False))
                sent = True
            elif plan.events and plan.view:
                document: dict[str, object] = {
                    "title": heading,
                    "body": issue_body(plan.view, now, lang),
                }
                response = self._send("POST", f"/repos/{repository}/issues", lang, document)
                if not 200 <= response.status < 300:
                    raise _Failed(_failure(response, lang, repository_level=True, create=True))
                opened = self._read_issue(response, lang)
                state = replace(state, issue=opened.number, author=opened.author)
                sent = True
            # With events, no issue and nothing left to tell about (the warnings ended while a
            # person had the issue closed), nothing is posted.
            state = replace(state, told={finding.key: finding for finding in plan.view})

            # Step 3: bring the issue found up to date; one just opened already is.
            if number is not None:
                update: dict[str, object] = {
                    "title": heading,
                    "body": issue_body(plan.view, now, lang),
                }
                if not plan.view:
                    update |= {"state": "closed", "state_reason": "completed"}
                response = self._send("PATCH", f"/repos/{repository}/issues/{number}", lang, update)
                if not 200 <= response.status < 300:
                    raise _Failed(_failure(response, lang, repository_level=False, create=False))
                if not plan.view:
                    state = replace(state, issue=None, author=None)
        except _Failed as failure:
            delivery = Delivery(
                Channel.GITHUB, DeliveryOutcome.FAILED, failure.message, state.issue
            )
            return delivery, state

        outcome = DeliveryOutcome.SENT if sent else DeliveryOutcome.UNCHANGED
        written = state.issue if state.issue is not None else number
        return Delivery(Channel.GITHUB, outcome, None, written), state
