"""
Domain expiry over RDAP: the result type, the validation, the bootstrap and the answer (spec 003).

A check asks the RDAP server that the IANA bootstrap names for the top-level domain of a name, and
turns every way the attempt can end into a :class:`DomainResult` value: a ``dated`` record with
its expiration and days left, an ``undated`` one with the reason the date is unknown, or an
``error`` with the kind of failure (RF-01, RF-03, RF-05). The warning threshold stays out of the
network code: :func:`classify` applies it to a result afterwards, as in spec 002.

Everything read from an answer is data: the domain name, the statuses and the messages are kept
as plain strings, and nothing in an answer decides which server is asked next (RNF-03).
"""

from __future__ import annotations

import http.client
import json
import math
import re
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from urllib.parse import urljoin, urlsplit

from tlsvigil import __version__
from tlsvigil.certificate import create_context

__all__ = [
    "BOOTSTRAP_URL",
    "MAX_BOOTSTRAP_BYTES",
    "MAX_DOMAIN_BYTES",
    "MAX_REDIRECTS",
    "Bootstrap",
    "DomainResult",
    "ErrorKind",
    "Outcome",
    "RdapClient",
    "Status",
    "UnknownReason",
    "classify",
]

# The IANA bootstrap for DNS (RFC 9224); the only bootstrap the tool reads.
BOOTSTRAP_URL: Final = "https://data.iana.org/rdap/dns.json"
# The bootstrap is about 50 KiB today; 2 MiB leaves room to grow and still bounds a hostile one
# (ADR-4).
MAX_BOOTSTRAP_BYTES: Final = 2 * 1024 * 1024
# A domain record is a few KiB; 512 KiB bounds a hostile one (ADR-4).
MAX_DOMAIN_BYTES: Final = 512 * 1024
# Registries redirect to a canonical path at most once or twice; three bounds a loop (ADR-5).
MAX_REDIRECTS: Final = 3

# Every request sends these and `Host`, nothing else: no cookie, no credential (RF-06).
_REQUEST_HEADERS: Final = {
    "Accept": "application/rdap+json",
    # No compression, so no compression bomb to bound (ADR-4).
    "Accept-Encoding": "identity",
    "User-Agent": f"tlsvigil/{__version__}",
    # One request per connection: nothing is reused between the bootstrap and a domain.
    "Connection": "close",
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class Outcome(StrEnum):
    """
    What the registry gave.

    Attributes:
        DATED (str): The record carries an expiration date; the result carries the days left.
        UNDATED (str): The date is unknown; the result carries the reason.
        ERROR (str): The domain could not be checked; the result carries the kind of failure.
    """

    DATED = "dated"
    UNDATED = "undated"
    ERROR = "error"


class Status(StrEnum):
    """
    What :func:`classify` gives for a result and a threshold (RF-02).

    Attributes:
        OK (str): Dated, with the threshold's number of days left or more.
        EXPIRING (str): Dated, with fewer days left than the threshold, or a passed date.
        UNKNOWN (str): The date is unknown, whatever the threshold.
        ERROR (str): The domain could not be checked, whatever the threshold.
    """

    OK = "ok"
    EXPIRING = "expiring"
    UNKNOWN = "unknown"
    ERROR = "error"


class UnknownReason(StrEnum):
    """
    Why the date of a domain is unknown (RF-03).

    Attributes:
        NO_RDAP_SERVICE (str): The bootstrap names no HTTPS RDAP service for the top-level domain.
        NO_EXPIRATION (str): The record has no ``expiration`` event.
    """

    NO_RDAP_SERVICE = "no_rdap_service"
    NO_EXPIRATION = "no_expiration"


class ErrorKind(StrEnum):
    """
    Why the domain could not be checked (RF-04, RF-05, RF-06).

    Attributes:
        BOOTSTRAP (str): The bootstrap could not be fetched or is not a valid document.
        DNS (str): The name of the RDAP server did not resolve.
        REFUSED (str): The connection was refused.
        TIMEOUT (str): The request did not finish within the timeout.
        TLS (str): The handshake failed, a failed verification of the server included.
        NOT_FOUND (str): The registry answered ``404``: it has no such domain.
        RATE_LIMITED (str): The registry answered ``429``.
        HTTP (str): Any other status outside ``2xx``; the message carries the code.
        REDIRECT (str): A redirect to another scheme, host or port, or past the limit.
        TOO_LARGE (str): The answer is larger than its cap.
        INVALID_RESPONSE (str): The answer is not an RDAP domain record for the name asked for,
            or carries an expiration date that cannot be read.
        OTHER (str): Any other failure.
    """

    BOOTSTRAP = "bootstrap"
    DNS = "dns"
    REFUSED = "refused"
    TIMEOUT = "timeout"
    TLS = "tls"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    HTTP = "http"
    REDIRECT = "redirect"
    TOO_LARGE = "too_large"
    INVALID_RESPONSE = "invalid_response"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class DomainResult:
    """
    The outcome of one domain check.

    The enum values are the strings the JSON output prints, so no mapping is needed there. The
    result is frozen: later stages read it and never change it.

    Attributes:
        domain (str): The name asked for, normalized (RF-07).
        checked_at (datetime): Aware, in UTC: the time the days left are counted from.
        outcome (Outcome): What the registry gave.
        server (str | None): The host name of the RDAP server asked (RF-01).
        expiration (datetime | None): The earliest expiration date, in UTC; ``DATED`` only.
        days_left (int | None): Whole days until ``expiration``, rounded down, negative once the
            date has passed; ``DATED`` only.
        registry_name (str | None): The ``ldhName`` of the record, as the registry returned it.
        statuses (tuple[str, ...]): The ``status`` values of the record, as plain text.
        reason (UnknownReason | None): Why the date is unknown; ``UNDATED`` only.
        error_kind (ErrorKind | None): Why the domain could not be checked; ``ERROR`` only.
        message (str | None): The text of the failure, as plain text; ``ERROR`` only.
    """

    domain: str
    checked_at: datetime
    outcome: Outcome
    server: str | None = None
    expiration: datetime | None = None
    days_left: int | None = None
    registry_name: str | None = None
    statuses: tuple[str, ...] = ()
    reason: UnknownReason | None = None
    error_kind: ErrorKind | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

# One label of a host name (RFC 1123): letters, digits and inner hyphens, 63 characters at most.
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
# The longest name DNS can carry in text form, without the trailing dot.
_MAX_NAME_LENGTH = 253


def _normalize_domain(domain: str) -> str:
    """
    Refuse a name that is not a registered domain, and give it in its canonical form (RF-07).

    Args:
        domain (str): The domain as registered, such as ``example.com.br``; capital letters and
            one trailing dot are accepted.

    Returns:
        str: The name in lowercase, without the trailing dot.

    Raises:
        ValueError: Naming the problem; for letters outside ASCII, pointing to the ``xn--`` form.
    """
    if type(domain) is not str or not domain:
        raise ValueError("domain must be a non-empty string")
    if any(char.isspace() for char in domain):
        raise ValueError(f"domain must not contain whitespace: {domain!r}")
    # "://" is covered by "/": a URL is refused whatever its scheme.
    if "/" in domain:
        raise ValueError(f"domain must be a name, not a URL or a path: {domain!r}")
    # ":" also refuses every IPv6 address.
    if ":" in domain:
        raise ValueError(f"domain must not contain a port or a ':': {domain!r}")
    if "@" in domain:
        raise ValueError(f"domain must not contain user information: {domain!r}")
    if "_" in domain:
        raise ValueError(f"domain must contain letters, digits, hyphens and dots only: {domain!r}")
    if not domain.isascii():
        # The idna codec follows IDNA 2003 and registries IDNA 2008; converting here could ask
        # about another domain (requirements, resolved decision 5).
        raise ValueError(
            f"domain must be given in its ASCII form: write the 'xn--' form of {domain!r}"
        )

    name = domain.lower().removesuffix(".")
    labels = name.split(".")
    if len(labels) < 2:
        raise ValueError(f"domain must have at least two labels: {domain!r}")
    if "" in labels:
        raise ValueError(f"domain must not have an empty label: {domain!r}")
    for label in labels:
        if not _LABEL.fullmatch(label):
            raise ValueError(
                "each label must be 1 to 63 letters, digits or inner hyphens: "
                f"{label!r} in {domain!r}"
            )
    if len(name) > _MAX_NAME_LENGTH:
        raise ValueError(f"domain must be {_MAX_NAME_LENGTH} characters or fewer: {domain!r}")
    # No top-level domain is all digits; this refuses every IPv4 address too.
    if labels[-1].isdigit():
        raise ValueError(f"domain must not end in a numeric label: {domain!r}")
    return name


def _validate_timeout(timeout: float) -> None:
    """
    Refuse a timeout that is not a finite number of seconds greater than ``0`` (RF-07).

    Args:
        timeout (float): The value given.

    Raises:
        ValueError: When the value is not an ``int`` or ``float`` (a ``bool`` included), is not
            finite, or is ``0`` or less.
    """
    # `type(...) in` and not isinstance: bool is a subclass of int, and True is not a timeout.
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"timeout must be a finite number of seconds greater than 0: {timeout!r}")


def _validate_https_url(url: str) -> None:
    """
    Refuse a bootstrap URL that is not ``https://`` with a host (RF-06).

    Args:
        url (str): The value given.

    Raises:
        ValueError: When the value is not a string, not HTTPS, or has no host.
    """
    if type(url) is not str:
        raise ValueError(f"bootstrap_url must be a string: {url!r}")
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:  # an unbalanced "[" in the host
        host = None
    if not url.startswith("https://") or not host:
        raise ValueError(f"bootstrap_url must be an https:// URL with a host: {url!r}")


def _validate_now(now: datetime | None) -> None:
    """
    Refuse a naive time to count from (RF-07).

    Args:
        now (datetime | None): The time given; ``None`` means the current time.

    Raises:
        ValueError: When ``now`` is a naive datetime.
    """
    # A naive time would make the days left depend on the machine's time zone.
    if now is not None and now.utcoffset() is None:
        raise ValueError(f"now must be an aware datetime: {now!r}")


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bootstrap:
    """
    The HTTPS RDAP services of the IANA bootstrap, one entry per top-level domain (RF-04).

    Attributes:
        services (tuple[tuple[tuple[str, ...], str], ...]): Pairs of the labels of an entry
            (``("com", "br")`` for ``com.br``) and the HTTPS base URL of its service, ending in
            ``/``. An entry whose service has no usable HTTPS URL is absent.
    """

    services: tuple[tuple[tuple[str, ...], str], ...]

    def base_url_for(self, domain: str) -> str | None:
        """
        Give the base URL of the entry whose labels are the longest suffix of ``domain``.

        Args:
            domain (str): A normalized domain (lowercase, no trailing dot).

        Returns:
            str | None: The HTTPS base URL, or ``None`` when no entry matches (RFC 9224,
                section 4).
        """
        labels = tuple(domain.split("."))
        by_labels = dict(self.services)
        # From the longest suffix to the shortest, so `com.br` wins over `br`.
        for start in range(len(labels)):
            base_url = by_labels.get(labels[start:])
            if base_url is not None:
                return base_url
        return None


# One message for every wrong shape: the caller reports it as `ERROR bootstrap` (RF-04), and a
# JSON error only adds its detail after a colon.
_INVALID_BOOTSTRAP = "invalid bootstrap document"


def _https_base_url(urls: list[str]) -> str | None:
    """
    Give the first URL of a service that is safe to ask, with a ``/`` appended when missing.

    Args:
        urls (list[str]): The URLs of one service of the bootstrap, in order.

    Returns:
        str | None: The first ``https://`` URL with a host and no user information, query or
            fragment, ending in ``/`` (RFC 9224, section 3), or ``None`` when there is none.
    """
    for url in urls:
        try:
            parts = urlsplit(url)
            host = parts.hostname
            _ = parts.port  # a port that is not a number in range raises here
        except ValueError:
            continue
        if not url.startswith("https://") or not host:
            continue
        # A bootstrap is data: a URL that carries credentials or parameters is not used.
        if "@" in parts.netloc or "?" in url or "#" in url:
            continue
        return url if url.endswith("/") else url + "/"
    return None


def _is_string_list(value: object) -> bool:
    """
    Tell whether ``value`` is a list of strings.

    Args:
        value (object): A value read from a JSON document.

    Returns:
        bool: ``True`` for a list whose items are all strings, the empty list included.
    """
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _parse_bootstrap(body: bytes) -> Bootstrap:
    """
    Read the IANA bootstrap for DNS (RF-04).

    Only ``services`` is read; ``version`` and ``publication`` are not. A document of the wrong
    shape is refused as a whole, so that a damaged bootstrap is an error and never a partial
    list of services.

    Args:
        body (bytes): The body of the answer, already under :data:`MAX_BOOTSTRAP_BYTES`.

    Returns:
        Bootstrap: The entries whose service has a usable HTTPS URL.

    Raises:
        ValueError: ``invalid bootstrap document``, when the body is not JSON or does not have
            the shape of RFC 9224.
    """
    try:
        document = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"{_INVALID_BOOTSTRAP}: {_message(exc)}") from None
    if not isinstance(document, dict) or not isinstance(document.get("services"), list):
        raise ValueError(_INVALID_BOOTSTRAP)

    services: list[tuple[tuple[str, ...], str]] = []
    for service in document["services"]:
        if not isinstance(service, list) or len(service) != 2:
            raise ValueError(_INVALID_BOOTSTRAP)
        entries, urls = service
        if not _is_string_list(entries) or not _is_string_list(urls):
            raise ValueError(_INVALID_BOOTSTRAP)
        base_url = _https_base_url(urls)
        if base_url is None:
            continue  # its top-level domains end as `no_rdap_service` (RF-03)
        for entry in entries:
            services.append((tuple(entry.lower().removesuffix(".").split(".")), base_url))
    return Bootstrap(tuple(services))


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------

_ONE_DAY = timedelta(days=1)


def _message(exc: BaseException) -> str:
    """
    Give the text of ``exc`` as the library wrote it, or its type when the text is empty.

    Args:
        exc (BaseException): The exception that was caught.

    Returns:
        str: A non-empty message.
    """
    return str(exc) or type(exc).__name__


def _parse_event_date(value: object) -> datetime | None:
    """
    Read an ``eventDate`` as an aware date in UTC.

    Args:
        value (object): The ``eventDate`` member of an event.

    Returns:
        datetime | None: The date in UTC, or ``None`` when it is not a string, cannot be read,
            carries no offset, or is out of range.
    """
    if not isinstance(value, str):
        return None
    try:
        # RFC 3339 allows a lowercase `t` and `z`, which fromisoformat does not read.
        parsed = datetime.fromisoformat(value.upper())
        if parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):  # OverflowError: a year 9999 date pushed past by its offset
        return None


def _read_domain_answer(
    body: bytes, *, domain: str, server: str, checked_at: datetime
) -> DomainResult:
    """
    Turn the body of a domain answer into a result (RF-01, RF-03, RF-05; ADR-8).

    Args:
        body (bytes): The body of the answer, already under :data:`MAX_DOMAIN_BYTES`.
        domain (str): The normalized name asked for.
        server (str): The host of the URL that was asked, never a name read from the answer.
        checked_at (datetime): Aware, in UTC: the time the days left are counted from.

    Returns:
        DomainResult: ``DATED`` with the earliest expiration and the days left, ``UNDATED`` with
            ``no_expiration``, or ``ERROR`` with ``invalid_response``. It never raises.
    """

    def invalid(message: str) -> DomainResult:
        """Build the result of an answer that is not a usable domain record."""
        return DomainResult(
            domain,
            checked_at,
            Outcome.ERROR,
            server=server,
            error_kind=ErrorKind.INVALID_RESPONSE,
            message=message,
        )

    try:
        document = json.loads(body)
    except (ValueError, RecursionError) as exc:  # RecursionError: a deeply nested body (RNF-04)
        return invalid(f"the answer is not JSON: {_message(exc)}")
    if not isinstance(document, dict) or document.get("objectClassName") != "domain":
        return invalid("the answer is not an RDAP domain object")

    # The answer must be about the name asked for, or it would give another domain's date.
    registry_name = document.get("ldhName")
    if not isinstance(registry_name, str):
        return invalid("the answer carries no ldhName")
    if registry_name.lower().removesuffix(".") != domain:
        return invalid("the answer names another domain")

    # A missing or malformed `status` or `events` is read as empty, not as a broken answer:
    # RFC 9083 makes both optional.
    status = document.get("status")
    status_items = status if isinstance(status, list) else []
    statuses = tuple(item for item in status_items if isinstance(item, str))

    events = document.get("events")
    event_items = events if isinstance(events, list) else []
    expiration_dates = [
        event.get("eventDate")
        for event in event_items
        if isinstance(event, dict) and event.get("eventAction") == "expiration"
    ]
    if not expiration_dates:
        return DomainResult(
            domain,
            checked_at,
            Outcome.UNDATED,
            server=server,
            registry_name=registry_name,
            statuses=statuses,
            reason=UnknownReason.NO_EXPIRATION,
        )

    parsed: list[datetime] = []
    for value in expiration_dates:
        date = _parse_event_date(value)
        if date is None:
            return invalid("the answer carries an expiration date that cannot be read")
        parsed.append(date)
    # The earliest wins: an early warning costs a look, a late one costs the domain.
    expiration = min(parsed)
    return DomainResult(
        domain,
        checked_at,
        Outcome.DATED,
        server=server,
        expiration=expiration,
        # Rounded down: 0 in the last 24 hours, -1 in the first 24 hours after the date (RF-01).
        days_left=(expiration - checked_at) // _ONE_DAY,
        registry_name=registry_name,
        statuses=statuses,
    )


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------


class _RequestFailed(Exception):
    """
    A request that ended without a usable body, with the kind the result will carry.

    Attributes:
        kind (ErrorKind): The kind of failure (the table of "The request" in the design).
        message (str): The text of the failure, as plain text.
    """

    def __init__(self, kind: ErrorKind, message: str) -> None:
        """
        Keep the kind and the message.

        Args:
            kind (ErrorKind): The kind of failure.
            message (str): The text of the failure.
        """
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True, slots=True)
class _Redirect:
    """
    A redirect answer, before the check of where it points.

    Attributes:
        location (str | None): The ``Location`` header, or ``None`` when it sent none.
    """

    location: str | None


# The statuses that carry a `Location` to follow (RFC 9110, section 15.4).
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
# A bounded read, so a body past the cap is refused after one chunk more at most (ADR-4).
_READ_SIZE: Final = 64 * 1024
# Named so that a URL with the port written out and one without compare as the same origin.
_HTTPS_PORT: Final = 443


def _origin(url: str) -> tuple[str, str, int]:
    """
    Give the scheme, the host and the port a URL reaches.

    Args:
        url (str): An absolute URL.

    Returns:
        tuple[str, str, int]: The scheme and the host in lowercase (the host ``""`` when the URL
            has none) and the port, ``443`` when the URL names none.

    Raises:
        ValueError: When the port is not a number in range or the host has an unbalanced ``[``.
    """
    parts = urlsplit(url)
    port = _HTTPS_PORT if parts.port is None else parts.port  # not `or`: port 0 is not 443
    return parts.scheme.lower(), (parts.hostname or ""), port


def _request_target(url: str) -> str:
    """
    Give the path and the query of a URL, as the request line sends them.

    Args:
        url (str): An absolute URL.

    Returns:
        str: The path (``/`` when empty) and, when there is one, ``?`` and the query; the
            fragment is never sent.
    """
    parts = urlsplit(url)
    target = parts.path or "/"
    return f"{target}?{parts.query}" if parts.query else target


def _too_large(cap: int) -> _RequestFailed:
    """
    Give the failure of an answer over its cap.

    Args:
        cap (int): The cap, in bytes.

    Returns:
        _RequestFailed: ``too_large``, with the cap in KiB in the message.
    """
    return _RequestFailed(ErrorKind.TOO_LARGE, f"the answer is larger than {cap // 1024} KiB")


def _status_failure(status: int) -> _RequestFailed:
    """
    Give the failure of a status outside ``2xx`` that is not a redirect (RF-05).

    Args:
        status (int): The status code.

    Returns:
        _RequestFailed: ``not_found`` for ``404``, ``rate_limited`` for ``429`` and ``http``
            otherwise, each with the code in the message.
    """
    if status == 404:
        return _RequestFailed(ErrorKind.NOT_FOUND, "HTTP 404: the registry has no such domain")
    if status == 429:
        # Not retried: a retry would double the load on a registry that is already limiting
        # requests (requirements, resolved decision 7).
        return _RequestFailed(ErrorKind.RATE_LIMITED, "HTTP 429: the registry limits requests")
    return _RequestFailed(ErrorKind.HTTP, f"HTTP {status}")


def _follow(url: str, location: str | None) -> str:
    """
    Resolve a redirect, and refuse it unless it stays on the same origin (ADR-5).

    Args:
        url (str): The URL that answered with the redirect.
        location (str | None): Its ``Location`` header, or ``None`` when it sent none.

    Returns:
        str: The URL to ask next.

    Raises:
        _RequestFailed: ``redirect``, for a missing ``Location`` or another scheme, host or port.
    """
    if location is None:
        raise _RequestFailed(ErrorKind.REDIRECT, "a redirect without a Location")
    target = urljoin(url, location)
    try:
        same_origin = _origin(target) == _origin(url)
    except ValueError:  # a port that is not a number in range, an unbalanced "["
        same_origin = False
    if not same_origin:
        # The answer is data: its Location never decides which host is asked (RNF-03).
        raise _RequestFailed(ErrorKind.REDIRECT, "a redirect to another scheme, host or port")
    return target


def _read_body(
    response: http.client.HTTPResponse,
    sock: socket.socket,
    cap: int,
    remaining: Callable[[], float],
) -> bytes:
    """
    Read a body in chunks, within the cap and the deadline (ADR-3, ADR-4).

    Args:
        response (http.client.HTTPResponse): The answer, its headers read.
        sock (socket.socket): The socket under the answer; its timeout is set to the time left
            before each read.
        cap (int): The largest body accepted, in bytes.
        remaining (Callable[[], float]): Gives the seconds left, or raises once the deadline
            has passed.

    Returns:
        bytes: The whole body.

    Raises:
        _RequestFailed: ``too_large`` past the cap, ``timeout`` once the deadline has passed.
    """
    chunks: list[bytes] = []
    total = 0
    # The answer closes itself, and the socket with it, at the end of the body: at its
    # Content-Length, its last chunk, or the close of the connection. An empty read always ends
    # it, and the deadline bounds the loop in any case.
    while not response.isclosed():
        # A byte now and then resets a socket timeout; the deadline bounds the sum (ADR-3).
        sock.settimeout(remaining())
        chunk = response.read1(_READ_SIZE)
        total += len(chunk)
        if total > cap:
            raise _too_large(cap)
        chunks.append(chunk)
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


class RdapClient:
    """
    Check domains over RDAP for one run, sharing one bootstrap (ADR-1).

    Attributes:
        timeout (float): Seconds for each whole request, a finite number greater than ``0``.
        bootstrap_url (str): The ``https://`` URL of the bootstrap.
    """

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        context: ssl.SSLContext | None = None,
        bootstrap_url: str = BOOTSTRAP_URL,
    ) -> None:
        """
        Validate the settings; nothing is fetched until a domain needs the bootstrap.

        Args:
            timeout (float): Seconds for each whole request, a finite number greater than ``0``
                (ADR-3).
            context (ssl.SSLContext | None): The client context of every request; ``None``
                means :func:`~tlsvigil.certificate.create_context` (ADR-6). The tests pass one
                that also trusts their own CA.
            bootstrap_url (str): The ``https://`` URL of the bootstrap; the tests point it at a
                local server.

        Raises:
            ValueError: When the timeout or the URL is bad (RF-07).
        """
        _validate_timeout(timeout)
        _validate_https_url(bootstrap_url)
        self.timeout = timeout
        self.bootstrap_url = bootstrap_url
        self._context = create_context() if context is None else context
        # The bootstrap once fetched, or the failure it led to; None until a domain needs it.
        self._bootstrap: Bootstrap | _RequestFailed | None = None

    def check_domain(self, domain: str, *, now: datetime | None = None) -> DomainResult:
        """
        Ask the registry of ``domain`` for its record and report what it gave (RF-01 to RF-05).

        Every way a check can end comes back as a value: the method raises only for bad
        arguments. The bootstrap is fetched on the first call that needs it and reused by the
        later ones, its failure included; then one request is sent for the domain, and nothing
        is retried.

        Args:
            domain (str): The domain as registered, such as ``example.com.br`` (RF-07).
            now (datetime | None): The time to count the days left from, aware; ``None`` means
                the current time.

        Returns:
            DomainResult: ``DATED`` with the expiration and the days left, ``UNDATED`` with the
                reason the date is unknown, or ``ERROR`` with the kind of failure.

        Raises:
            ValueError: When the domain or ``now`` is bad; no request is sent then.
        """
        name = _normalize_domain(domain)
        _validate_now(now)
        checked_at = datetime.now(UTC) if now is None else now.astimezone(UTC)

        bootstrap = self._get_bootstrap()
        if isinstance(bootstrap, _RequestFailed):
            return DomainResult(
                name,
                checked_at,
                Outcome.ERROR,
                error_kind=ErrorKind.BOOTSTRAP,
                message=bootstrap.message,
            )
        base_url = bootstrap.base_url_for(name)
        if base_url is None:
            return DomainResult(
                name, checked_at, Outcome.UNDATED, reason=UnknownReason.NO_RDAP_SERVICE
            )

        # The host of the URL asked, never a name read from an answer (RNF-03).
        server = _origin(base_url)[1]
        try:
            body = self._get(f"{base_url}domain/{name}", MAX_DOMAIN_BYTES)
        except _RequestFailed as failure:
            return DomainResult(
                name,
                checked_at,
                Outcome.ERROR,
                server=server,
                error_kind=failure.kind,
                message=failure.message,
            )
        return _read_domain_answer(body, domain=name, server=server, checked_at=checked_at)

    def _get_bootstrap(self) -> Bootstrap | _RequestFailed:
        """
        Give the bootstrap, fetching it on the first call; a failure is kept like a success.

        Returns:
            Bootstrap | _RequestFailed: The parsed bootstrap, or the failure of its fetch with
                the kind ``bootstrap`` and the message of the failure (RF-04).
        """
        if self._bootstrap is None:
            try:
                body = self._get(self.bootstrap_url, MAX_BOOTSTRAP_BYTES)
                self._bootstrap = _parse_bootstrap(body)
            except _RequestFailed as failure:
                self._bootstrap = _RequestFailed(ErrorKind.BOOTSTRAP, failure.message)
            except ValueError as exc:  # the document is not a bootstrap
                self._bootstrap = _RequestFailed(ErrorKind.BOOTSTRAP, str(exc))
        return self._bootstrap

    def _get(self, url: str, cap: int) -> bytes:
        """
        Send one bounded ``GET`` and give the body of its ``2xx`` answer (RF-05, RF-06).

        One deadline covers the whole request, its redirects included (ADR-3). A redirect is
        followed only on the same scheme, host and port, :data:`MAX_REDIRECTS` times at most
        (ADR-5), and the body is refused past ``cap`` (ADR-4). No proxy variable is read:
        :mod:`http.client` looks at none (ADR-2).

        Args:
            url (str): An ``https://`` URL with a host.
            cap (int): The largest body accepted, in bytes.

        Returns:
            bytes: The body of the answer.

        Raises:
            _RequestFailed: For every way the request can fail, with its kind and message.
        """
        deadline = time.monotonic() + self.timeout

        def remaining() -> float:
            """Give the seconds left before the deadline, or fail once it has passed."""
            left = deadline - time.monotonic()
            if left <= 0:
                raise _RequestFailed(
                    ErrorKind.TIMEOUT, f"the request took longer than {self.timeout} s"
                )
            return left

        for _ in range(1 + MAX_REDIRECTS):
            try:
                answer = self._get_once(url, cap, remaining)
            except _RequestFailed:
                raise
            except socket.gaierror as exc:
                raise _RequestFailed(ErrorKind.DNS, _message(exc)) from None
            except ConnectionRefusedError as exc:
                raise _RequestFailed(ErrorKind.REFUSED, _message(exc)) from None
            except TimeoutError as exc:
                raise _RequestFailed(ErrorKind.TIMEOUT, _message(exc)) from None
            except ssl.SSLError as exc:  # before OSError, of which it is a subclass
                raise _RequestFailed(ErrorKind.TLS, _message(exc)) from None
            except (http.client.HTTPException, OSError, ValueError) as exc:
                # ValueError: a host or a path that http.client cannot encode (deviation 7).
                raise _RequestFailed(ErrorKind.OTHER, _message(exc)) from None
            if isinstance(answer, bytes):
                return answer
            url = _follow(url, answer.location)
        raise _RequestFailed(ErrorKind.REDIRECT, f"more than {MAX_REDIRECTS} redirects")

    def _get_once(self, url: str, cap: int, remaining: Callable[[], float]) -> bytes | _Redirect:
        """
        Send one ``GET`` over one connection, closed on every path.

        Args:
            url (str): The URL to ask.
            cap (int): The largest body accepted, in bytes.
            remaining (Callable[[], float]): Gives the seconds left, or raises once the deadline
                has passed.

        Returns:
            bytes | _Redirect: The body of a ``2xx`` answer, or the redirect it answered with.

        Raises:
            _RequestFailed: For a status outside ``2xx`` that is not a redirect, a body over the
                cap, or the deadline passed.
            OSError: From the connection, the handshake or a read; ``_get`` maps it.
            http.client.HTTPException: From the status line or the headers; ``_get`` maps it.
        """
        _, host, port = _origin(url)
        connection = http.client.HTTPSConnection(
            host, port, timeout=remaining(), context=self._context
        )
        try:
            connection.request("GET", _request_target(url), headers=_REQUEST_HEADERS)
            # Kept, because http.client hands the socket to the answer and forgets it.
            sock = connection.sock
            if sock is None:  # pragma: no cover - request() always leaves a connected socket
                raise http.client.NotConnected("no connection after the request")
            sock.settimeout(remaining())
            with connection.getresponse() as response:
                if response.status in _REDIRECT_STATUSES:
                    return _Redirect(response.getheader("Location"))
                if not 200 <= response.status < 300:
                    raise _status_failure(response.status)
                # Refused before the body is read (ADR-4).
                if response.length is not None and response.length > cap:
                    raise _too_large(cap)
                return _read_body(response, sock, cap, remaining)
        finally:
            connection.close()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(result: DomainResult, warn_days: int) -> Status:
    """
    Apply a warning threshold to a result (RF-02).

    Args:
        result (DomainResult): The result of a check.
        warn_days (int): Warn when fewer days than this are left; ``0`` still warns for a passed
            date.

    Returns:
        Status: ``UNKNOWN`` or ``ERROR`` for those outcomes, whatever the threshold; for a dated
            result, ``EXPIRING`` when ``days_left < warn_days`` and ``OK`` otherwise.

    Raises:
        ValueError: When ``warn_days`` is not an integer or is negative, or when a dated result
            carries no days left.
    """
    # `type(...) is` and not isinstance: bool is a subclass of int.
    if type(warn_days) is not int or warn_days < 0:
        raise ValueError(f"warn_days must be an integer, 0 or more: {warn_days!r}")
    if result.outcome is Outcome.UNDATED:
        return Status.UNKNOWN
    if result.outcome is Outcome.ERROR:
        return Status.ERROR
    if result.days_left is None:
        raise ValueError("a dated result must carry its days left")
    return Status.EXPIRING if result.days_left < warn_days else Status.OK
