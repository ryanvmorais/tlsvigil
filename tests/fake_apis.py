"""
Stateful fakes of the APIs the notification channels call (spec 005, RF-14).

:class:`FakeGitHub` keeps issues and their comments in memory and answers the five requests of the
GitHub channel on a :class:`~http_server.LocalHTTPServer`, through
:meth:`~http_server.Route.handled` routes. It checks the headers and the token of every request,
and a test can tell it to answer one kind of request with a fixed route instead (a refusal, a
rate limit, a gone issue), to see each failure of the channel without a real API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Final, Literal
from urllib.parse import urlsplit

from http_server import LocalHTTPServer, RecordedRequest, Route
from tlsvigil.github import API_VERSION, BOT_LOGIN

__all__ = ["REPOSITORY", "TOKEN", "FakeGitHub", "FakeIssue", "Operation", "github_answer"]

# The repository and the token of the fake; the token is a made-up value, never a real one.
REPOSITORY: Final = "octo-org/watched"
TOKEN: Final = "fake-token-of-the-tests-0123456789"

# The issue numbers that get a route: more than any test opens.
_NUMBERS: Final = range(1, 41)

# The kinds of request the channel sends.
type Operation = Literal["get", "list", "create", "comment", "update"]


def github_answer(
    status: int, message: str | None = None, headers: dict[str, str] | None = None
) -> Route:
    """
    Build a fixed answer as GitHub writes one: a JSON body with its ``message``.

    Args:
        status (int): The status code.
        message (str | None): The ``message`` field, or ``None`` for an empty object.
        headers (dict[str, str] | None): More headers, such as ``x-ratelimit-remaining``.

    Returns:
        Route: The answer.
    """
    document = {} if message is None else {"message": message}
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    return Route(status, json.dumps(document).encode(), all_headers)


@dataclass
class FakeIssue:
    """
    One issue the fake keeps.

    Attributes:
        number (int): Its number.
        title (str): Its title.
        body (str | None): Its body.
        author (str): The login that opened it.
        state (str): ``open`` or ``closed``.
        state_reason (str | None): Why it was closed.
        pull_request (bool): Whether it is a pull request, listed with the issues.
        comments (list[str]): The bodies of its comments, in order.
    """

    number: int
    title: str
    body: str | None
    author: str = BOT_LOGIN
    state: str = "open"
    state_reason: str | None = None
    pull_request: bool = False
    comments: list[str] = field(default_factory=list)

    def document(self) -> dict[str, object]:
        """
        Write the issue as the API does, with the fields the channel reads and a few more.

        Returns:
            dict[str, object]: The JSON object.
        """
        document: dict[str, object] = {
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "state": self.state,
            "state_reason": self.state_reason,
            "user": {"login": self.author, "type": "Bot"},
            "html_url": f"https://github.com/{REPOSITORY}/issues/{self.number}",
        }
        if self.pull_request:
            document["pull_request"] = {"url": "https://api.github.com/pulls"}
        return document


class FakeGitHub:
    """
    The issues of one repository, served as the REST API serves them.

    Attributes:
        issues (dict[int, FakeIssue]): The issues by number, pull requests included.
        gone (dict[int, int]): The numbers that answer ``404``, ``410`` or ``301`` to a ``GET``.
        answers (dict[Operation, Route]): A fixed answer that replaces the fake's own for that
            kind of request; the request is still recorded.
        problems (list[str]): What was wrong with the requests: a missing or wrong header.
        operations (list[Operation]): The kinds of request received, in order.
    """

    def __init__(self) -> None:
        """Start with no issue."""
        self.issues: dict[int, FakeIssue] = {}
        self.gone: dict[int, int] = {}
        self.answers: dict[Operation, Route] = {}
        self.problems: list[str] = []
        self.operations: list[Operation] = []
        self._next = 1

    def add(self, title: str = "an issue", body: str | None = "", **fields: object) -> FakeIssue:
        """
        Add an issue as if someone had opened it.

        Args:
            title (str): Its title.
            body (str | None): Its body.
            **fields (object): Other fields of :class:`FakeIssue`, such as ``author``.

        Returns:
            FakeIssue: The new issue, with the next number.
        """
        # The keyword fields are FakeIssue's own; mypy cannot match them to an object mapping.
        issue = FakeIssue(self._next, title, body, **fields)  # type: ignore[arg-type]
        self.issues[issue.number] = issue
        self._next += 1
        return issue

    def install(self, server: LocalHTTPServer) -> str:
        """
        Answer the API's paths on a local server.

        Args:
            server (LocalHTTPServer): The running server.

        Returns:
            str: The API root to give the channel, the server's own URL.
        """
        base = f"/repos/{REPOSITORY}/issues"
        route = Route.handled(self._handle)
        paths = [base, f"{base}?state=open&per_page=100"]
        for number in _NUMBERS:
            paths += [f"{base}/{number}", f"{base}/{number}/comments"]
        server.routes.update(dict.fromkeys(paths, route))
        return server.url("")

    # -----------------------------------------------------------------------
    # Answers
    # -----------------------------------------------------------------------

    def _check(self, request: RecordedRequest) -> bool:
        """
        Record what is wrong with the headers of a request, and tell whether the token is good.

        Args:
            request (RecordedRequest): The request.

        Returns:
            bool: ``False`` when the token is missing or wrong, so the answer is ``401``.
        """
        expected = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        for name, value in expected.items():
            if request.header(name) != value:
                self.problems.append(f"{request.method} {request.path}: {name}")
        if not (request.header("User-Agent") or "").startswith("tlsvigil/"):
            self.problems.append(f"{request.method} {request.path}: User-Agent")
        if request.body and request.header("Content-Type") != "application/json":
            self.problems.append(f"{request.method} {request.path}: Content-Type")
        return request.header("Authorization") == f"Bearer {TOKEN}"

    def _operation(self, request: RecordedRequest) -> tuple[Operation, int | None]:
        """
        Tell which of the five requests this is.

        Args:
            request (RecordedRequest): The request.

        Returns:
            tuple[Operation, int | None]: The kind, and the issue number it names, if any.

        Raises:
            ValueError: When the request is none of them.
        """
        parts = urlsplit(request.path)
        rest = parts.path.removeprefix(f"/repos/{REPOSITORY}/issues").strip("/").split("/")
        match request.method, rest:
            case "GET", [""] if parts.query == "state=open&per_page=100":
                return "list", None
            case "POST", [""]:
                return "create", None
            case "GET", [number]:
                return "get", int(number)
            case "PATCH", [number]:
                return "update", int(number)
            case "POST", [number, "comments"]:
                return "comment", int(number)
        raise ValueError(f"not a request of the channel: {request.method} {request.path}")

    def _handle(self, request: RecordedRequest) -> Route:
        """
        Answer one request, as the API would.

        Args:
            request (RecordedRequest): The request.

        Returns:
            Route: The answer.
        """
        operation, number = self._operation(request)
        self.operations.append(operation)
        if not self._check(request):
            return github_answer(401, "Bad credentials")
        if operation in self.answers:
            return self.answers[operation]
        document = json.loads(request.body) if request.body else {}
        if operation == "list":
            listed = [issue for issue in self.issues.values() if issue.state == "open"]
            # Newest first, as the API sorts by default.
            listed.sort(key=lambda issue: issue.number, reverse=True)
            body = json.dumps([issue.document() for issue in listed]).encode()
            return Route(200, body, {"Content-Type": "application/json"})
        if operation == "create":
            issue = self.add(document["title"], document["body"])
            return Route(201, json.dumps(issue.document()).encode())
        assert number is not None
        if number in self.gone:
            status = self.gone[number]
            headers = {"Location": f"/repos/elsewhere/moved/issues/{number}"}
            return github_answer(status, "Moved Permanently" if status == 301 else "Gone", headers)
        issue_or_none = self.issues.get(number)
        if issue_or_none is None:
            return github_answer(404, "Not Found")
        issue = issue_or_none
        if operation == "comment":
            issue.comments.append(document["body"])
            return Route(201, json.dumps({"id": len(issue.comments)}).encode())
        if operation == "update":
            issue.title = document.get("title", issue.title)
            issue.body = document.get("body", issue.body)
            issue.state = document.get("state", issue.state)
            issue.state_reason = document.get("state_reason", issue.state_reason)
        return Route(200, json.dumps(issue.document()).encode())
