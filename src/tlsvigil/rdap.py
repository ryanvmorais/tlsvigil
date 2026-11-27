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

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from urllib.parse import urlsplit

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
        bootstrap_url: str = BOOTSTRAP_URL,
    ) -> None:
        """
        Validate the settings; nothing is fetched until a domain needs the bootstrap.

        Args:
            timeout (float): Seconds for each whole request, a finite number greater than ``0``
                (ADR-3).
            bootstrap_url (str): The ``https://`` URL of the bootstrap; the tests point it at a
                local server.

        Raises:
            ValueError: When the timeout or the URL is bad (RF-07).
        """
        _validate_timeout(timeout)
        _validate_https_url(bootstrap_url)
        self.timeout = timeout
        self.bootstrap_url = bootstrap_url


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
