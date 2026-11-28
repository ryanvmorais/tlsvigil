"""
Reports built by hand, for the tests of the output and of the counts (spec 004).

Each helper builds one item the way :func:`~tlsvigil.runner.run` would: the result of a check,
classified against the entry's threshold by the check's own ``classify``. Nothing here opens a
connection, and every date counts from the fixed :data:`NOW`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tlsvigil import certificate, rdap
from tlsvigil.config import DomainEntry, HostEntry
from tlsvigil.runner import CertificateItem, DomainItem, Report

# A fixed, aware time: the dates of the text never depend on the clock of the machine.
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)

Item = CertificateItem | DomainItem


def verified(
    host: str = "example.com",
    port: int = 443,
    *,
    days_left: int = 61,
    warn_days: int = 14,
    issuer_organization: str | None = "Example Trust",
    issuer_common_name: str | None = "Example R1",
) -> CertificateItem:
    """
    Build a certificate that passed the verification, ``ok`` or ``expiring`` by its threshold.

    Args:
        host (str): The host.
        port (int): The port.
        days_left (int): Whole days until the expiry, ``0`` or more.
        warn_days (int): The threshold of the entry.
        issuer_organization (str | None): The issuer's organization, as read.
        issuer_common_name (str | None): The issuer's common name, as read.

    Returns:
        CertificateItem: The item.
    """
    result = certificate.CertificateResult(
        host=host,
        port=port,
        checked_at=NOW,
        outcome=certificate.Outcome.VERIFIED,
        not_after=NOW + timedelta(days=days_left),
        days_left=days_left,
        issuer_organization=issuer_organization,
        issuer_common_name=issuer_common_name,
    )
    entry = HostEntry(host, port, warn_days)
    return CertificateItem(entry, result, certificate.classify(result, warn_days))


def invalid(
    host: str = "old.example.com",
    port: int = 443,
    *,
    reason: certificate.InvalidReason = certificate.InvalidReason.EXPIRED,
    message: str = "certificate verify failed: certificate has expired",
) -> CertificateItem:
    """
    Build a certificate that failed the verification.

    Args:
        host (str): The host.
        port (int): The port.
        reason (certificate.InvalidReason): Why it failed.
        message (str): The library's text.

    Returns:
        CertificateItem: The item, ``invalid``.
    """
    result = certificate.CertificateResult(
        host=host,
        port=port,
        checked_at=NOW,
        outcome=certificate.Outcome.INVALID,
        issuer_organization="Example Trust",
        issuer_common_name="Example R1",
        reason=reason,
        message=message,
    )
    entry = HostEntry(host, port, 14)
    return CertificateItem(entry, result, certificate.classify(result, 14))


def unreachable(
    host: str = "typo.example.com",
    port: int = 443,
    *,
    kind: certificate.ErrorKind = certificate.ErrorKind.DNS,
    message: str | None = "[Errno 11001] getaddrinfo failed",
) -> CertificateItem:
    """
    Build a certificate that could not be checked.

    Args:
        host (str): The host.
        port (int): The port.
        kind (certificate.ErrorKind): Why the host could not be checked.
        message (str | None): The library's text.

    Returns:
        CertificateItem: The item, ``error``.
    """
    result = certificate.CertificateResult(
        host=host,
        port=port,
        checked_at=NOW,
        outcome=certificate.Outcome.ERROR,
        error_kind=kind,
        message=message,
    )
    entry = HostEntry(host, port, 14)
    return CertificateItem(entry, result, certificate.classify(result, 14))


def dated(
    domain: str = "example.com",
    *,
    days_left: int = 301,
    warn_days: int = 30,
    statuses: tuple[str, ...] = ("client transfer prohibited",),
) -> DomainItem:
    """
    Build a domain with an expiration date, ``ok`` or ``expiring`` by its threshold.

    Args:
        domain (str): The domain.
        days_left (int): Whole days until the expiration; negative once it has passed.
        warn_days (int): The threshold of the entry.
        statuses (tuple[str, ...]): The statuses of the record.

    Returns:
        DomainItem: The item.
    """
    result = rdap.DomainResult(
        domain=domain,
        checked_at=NOW,
        outcome=rdap.Outcome.DATED,
        server="rdap.example.net",
        expiration=NOW + timedelta(days=days_left),
        days_left=days_left,
        registry_name=domain.upper(),
        statuses=statuses,
    )
    entry = DomainEntry(domain, warn_days)
    return DomainItem(entry, result, rdap.classify(result, warn_days))


def undated(
    domain: str = "example.de",
    *,
    reason: rdap.UnknownReason = rdap.UnknownReason.NO_RDAP_SERVICE,
) -> DomainItem:
    """
    Build a domain whose date is unknown.

    Args:
        domain (str): The domain.
        reason (rdap.UnknownReason): Why the date is unknown.

    Returns:
        DomainItem: The item, ``unknown``.
    """
    result = rdap.DomainResult(
        domain=domain, checked_at=NOW, outcome=rdap.Outcome.UNDATED, reason=reason
    )
    entry = DomainEntry(domain, 30)
    return DomainItem(entry, result, rdap.classify(result, 30))


def failed(
    domain: str = "example.net",
    *,
    kind: rdap.ErrorKind = rdap.ErrorKind.TIMEOUT,
    message: str = "the request took longer than 10 s",
) -> DomainItem:
    """
    Build a domain that could not be checked.

    Args:
        domain (str): The domain.
        kind (rdap.ErrorKind): Why it could not be checked.
        message (str): The text of the failure.

    Returns:
        DomainItem: The item, ``error``.
    """
    result = rdap.DomainResult(
        domain=domain,
        checked_at=NOW,
        outcome=rdap.Outcome.ERROR,
        server="rdap.example.net",
        error_kind=kind,
        message=message,
    )
    entry = DomainEntry(domain, 30)
    return DomainItem(entry, result, rdap.classify(result, 30))


def report(*items: Item) -> Report:
    """
    Put items in a report, the certificates and the domains each in the order given.

    Args:
        *items (Item): The items.

    Returns:
        Report: The report, started at :data:`NOW`.
    """
    certificates = tuple(item for item in items if isinstance(item, CertificateItem))
    domains = tuple(item for item in items if isinstance(item, DomainItem))
    return Report(NOW, certificates, domains)


def one_of_each() -> dict[str, Item]:
    """
    Give one item per status of the two checks.

    Returns:
        dict[str, Item]: From each status value to an item that has it; ``expiring`` and
            ``error`` are certificates, the domain versions are built where a test needs them.
    """
    return {
        "ok": verified(),
        "expiring": verified(days_left=9),
        "invalid": invalid(),
        "unknown": undated(),
        "error": unreachable(),
    }
