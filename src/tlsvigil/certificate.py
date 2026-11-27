"""
Certificate check: the result type, the TLS context and the classification (spec 002).

A check opens one verified TLS handshake to ``host:port`` and turns every way it can end into a
:class:`CertificateResult` value: a verified certificate with its days left, an ``invalid`` one
with the reason of the failed verification, or an ``error`` when the host could not be checked
(ADR-1). The warning threshold stays out of the network code: :func:`classify` applies it to a
result afterwards.

The date comes from a verified chain only. A failed verification is itself the finding, and the
check never retries without verification to read the date (RF-03).
"""

from __future__ import annotations

import ipaddress
import math
import ssl
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

__all__ = [
    "CertificateResult",
    "ErrorKind",
    "InvalidReason",
    "Outcome",
    "Status",
    "classify",
    "create_context",
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class Outcome(StrEnum):
    """
    What the handshake gave.

    Attributes:
        VERIFIED (str): The chain and the name verified; the result carries the days left.
        INVALID (str): The verification failed; the result carries the reason.
        ERROR (str): The host could not be checked; the result carries the kind of failure.
    """

    VERIFIED = "verified"
    INVALID = "invalid"
    ERROR = "error"


class Status(StrEnum):
    """
    What :func:`classify` gives for a result and a threshold (RF-02).

    Attributes:
        OK (str): Verified, with the threshold's number of days left or more.
        EXPIRING (str): Verified, with fewer days left than the threshold.
        INVALID (str): The verification failed, whatever the threshold.
        ERROR (str): The host could not be checked, whatever the threshold.
    """

    OK = "ok"
    EXPIRING = "expiring"
    INVALID = "invalid"
    ERROR = "error"


class InvalidReason(StrEnum):
    """
    Why the verification failed, read from the OpenSSL verification code (RF-03, ADR-3).

    Attributes:
        EXPIRED (str): The validity period of a certificate in the chain has ended.
        NOT_YET_VALID (str): The validity period has not started.
        HOSTNAME_MISMATCH (str): The certificate does not name the host that was checked.
        UNTRUSTED (str): The chain does not lead to a trusted root (self-signed, unknown CA,
            missing intermediate).
        OTHER (str): Any other verification code.
    """

    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    HOSTNAME_MISMATCH = "hostname_mismatch"
    UNTRUSTED = "untrusted"
    OTHER = "other"


class ErrorKind(StrEnum):
    """
    Why the host could not be checked (RF-04).

    Attributes:
        DNS (str): The name did not resolve.
        REFUSED (str): The connection was refused.
        TIMEOUT (str): The connection or the handshake did not finish within the timeout.
        TLS (str): The handshake failed for a reason other than the verification.
        OTHER (str): Any other failure.
    """

    DNS = "dns"
    REFUSED = "refused"
    TIMEOUT = "timeout"
    TLS = "tls"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class CertificateResult:
    """
    The outcome of one certificate check.

    The enum values are the strings the JSON output prints, so no mapping is needed there. The
    result is frozen: later stages read it and never change it.

    Attributes:
        host (str): The host that was checked, as given.
        port (int): The TCP port that was checked.
        checked_at (datetime): Aware, in UTC: the time the days left are counted from.
        outcome (Outcome): What the handshake gave.
        not_after (datetime | None): The expiry date, in UTC; ``VERIFIED`` only.
        days_left (int | None): Whole days until ``not_after``, rounded down and never negative;
            ``VERIFIED`` only.
        issuer_organization (str | None): The issuer's first organization name, as read.
        issuer_common_name (str | None): The issuer's first common name, as read.
        reason (InvalidReason | None): Why the verification failed; ``INVALID`` only.
        error_kind (ErrorKind | None): Why the host could not be checked; ``ERROR`` only.
        message (str | None): The library's text, as read; ``INVALID`` and ``ERROR``.
    """

    host: str
    port: int
    checked_at: datetime
    outcome: Outcome
    not_after: datetime | None = None
    days_left: int | None = None
    issuer_organization: str | None = None
    issuer_common_name: str | None = None
    reason: InvalidReason | None = None
    error_kind: ErrorKind | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

# A host is a name or an IP address, never a URL, a path, a bracketed address or user info.
_FORBIDDEN_IN_HOST = frozenset("/[]@")


def _validate(host: str, port: int, timeout: float, now: datetime | None) -> None:
    """
    Refuse bad arguments before any socket exists (RF-06).

    Args:
        host (str): A DNS name or an IP address, without scheme, path, brackets or port.
        port (int): The TCP port, from ``1`` to ``65535``.
        timeout (float): Seconds, a finite number greater than ``0``.
        now (datetime | None): The time to count from; when given, it must be aware.

    Raises:
        ValueError: Naming the bad argument.
    """
    if type(host) is not str or not host:
        raise ValueError("host must be a non-empty string")
    if any(char.isspace() for char in host):
        raise ValueError(f"host must not contain whitespace: {host!r}")
    # "://" is covered by "/": a URL is refused whatever its scheme.
    if any(char in _FORBIDDEN_IN_HOST for char in host):
        raise ValueError(f"host must be a name or an IP address, not a URL or a path: {host!r}")
    if ":" in host:
        # Only an IPv6 address may contain ":"; "example.com:443" passes the port in the host.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise ValueError(
                f"host must not contain a port or a ':' unless it is an IPv6 address: {host!r}"
            ) from None

    # `type(...) is` and not isinstance: bool is a subclass of int, and True is not a port.
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError(f"port must be an integer from 1 to 65535: {port!r}")

    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"timeout must be a finite number of seconds greater than 0: {timeout!r}")

    # A naive time would make the days left depend on the machine's time zone.
    if now is not None and now.utcoffset() is None:
        raise ValueError(f"now must be an aware datetime: {now!r}")


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


def create_context() -> ssl.SSLContext:
    """
    Build the client context every check uses (RF-05, RNF-02).

    It starts from :func:`ssl.create_default_context` (the system trust store, ``CERT_REQUIRED``,
    ``check_hostname``) and sets three things explicitly, because the defaults of the running
    Python changed in 3.13 and the verdict must be the same on every supported version (ADR-2).

    Returns:
        ssl.SSLContext: A context that requires TLS 1.2 or newer, verifies the chain as Python
            3.12 does, and matches the host against the subject alternative names only.
    """
    context = ssl.create_default_context()
    # The default since 3.10, pinned so it cannot drift (RF-05).
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # 3.13 added both to the defaults; STRICT refuses valid leaves without an Authority Key
    # Identifier, and PARTIAL_CHAIN would trust an intermediate as an anchor (ADR-2).
    context.verify_flags &= ~(ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN)
    # Browsers ignore the subject common name; only the SAN names the host.
    context.hostname_checks_common_name = False
    return context


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(result: CertificateResult, warn_days: int) -> Status:
    """
    Apply a warning threshold to a result (RF-02).

    Args:
        result (CertificateResult): The result of a check.
        warn_days (int): Warn when fewer days than this are left; ``0`` never warns by date.

    Returns:
        Status: ``INVALID`` or ``ERROR`` for those outcomes, whatever the threshold; for a
            verified result, ``EXPIRING`` when ``days_left < warn_days`` and ``OK`` otherwise.

    Raises:
        ValueError: When ``warn_days`` is not an integer or is negative, or when a verified
            result carries no days left.
    """
    # `type(...) is` and not isinstance: bool is a subclass of int.
    if type(warn_days) is not int or warn_days < 0:
        raise ValueError(f"warn_days must be an integer, 0 or more: {warn_days!r}")
    if result.outcome is Outcome.INVALID:
        return Status.INVALID
    if result.outcome is Outcome.ERROR:
        return Status.ERROR
    if result.days_left is None:
        raise ValueError("a verified result must carry its days left")
    return Status.EXPIRING if result.days_left < warn_days else Status.OK
