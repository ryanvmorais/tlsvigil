"""
The run of the command: every check of a configuration, classified, in one report (spec 004).

:func:`run` checks the certificates first and then the domains, in the order of the
configuration, and classifies each result against the threshold of its entry. The checks of specs
``002`` and ``003`` never raise for the network, so a run always ends with one item per entry;
:func:`run` adds no handling of its own (RF-01).
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from tlsvigil import certificate, rdap
from tlsvigil.config import Config, DomainEntry, HostEntry

__all__ = ["ATTENTION", "STATUSES", "CertificateItem", "DomainItem", "Report", "run"]


# ---------------------------------------------------------------------------
# Statuses
# ---------------------------------------------------------------------------

# Every status value of the two checks, in the order the summary counts them. The two `Status`
# enums of specs 002 and 003 are different types that share `ok`, `expiring` and `error`, so the
# report works with their string values (design, "Data model").
STATUSES: Final = ("ok", "expiring", "invalid", "unknown", "error")

# The statuses that need a person: any of them makes the exit code 1 (RF-09). `unknown` is not
# one: a registry without RDAP is a limit of the registry, not a problem of the domain.
ATTENTION: Final = frozenset({"expiring", "invalid", "error"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CertificateItem:
    """
    One certificate of the run: the entry, what the check gave, and its status.

    Attributes:
        entry (HostEntry): The host, the port and the threshold, as configured.
        result (certificate.CertificateResult): What the check gave.
        status (certificate.Status): The result classified against ``entry.warn_days``.
    """

    entry: HostEntry
    result: certificate.CertificateResult
    status: certificate.Status


@dataclass(frozen=True, slots=True)
class DomainItem:
    """
    One domain of the run: the entry, what the check gave, and its status.

    Attributes:
        entry (DomainEntry): The domain and the threshold, as configured.
        result (rdap.DomainResult): What the check gave.
        status (rdap.Status): The result classified against ``entry.warn_days``.
    """

    entry: DomainEntry
    result: rdap.DomainResult
    status: rdap.Status


@dataclass(frozen=True, slots=True)
class Report:
    """
    Everything one run found, in the order of the configuration (RF-01).

    Attributes:
        started_at (datetime): Aware, in UTC: when the run started.
        certificates (tuple[CertificateItem, ...]): One item per host.
        domains (tuple[DomainItem, ...]): One item per domain.
    """

    started_at: datetime
    certificates: tuple[CertificateItem, ...]
    domains: tuple[DomainItem, ...]

    def _statuses(self) -> list[str]:
        """
        Give the status of every item, the certificates first.

        Returns:
            list[str]: The status values, in the order of the report.
        """
        return [item.status for item in self.certificates] + [item.status for item in self.domains]

    def counts(self) -> dict[str, int]:
        """
        Count the items of the report (RF-06, RF-07).

        Returns:
            dict[str, int]: ``total``, ``attention`` and one count per value of
                :data:`STATUSES`, zero included, in that order.
        """
        statuses = self._statuses()
        counts = {
            "total": len(statuses),
            "attention": sum(status in ATTENTION for status in statuses),
        }
        for value in STATUSES:
            counts[value] = statuses.count(value)
        return counts

    def needs_attention(self) -> bool:
        """
        Tell whether any item needs a person (RF-09).

        Returns:
            bool: ``True`` when any status is in :data:`ATTENTION`.
        """
        return any(status in ATTENTION for status in self._statuses())


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run(
    config: Config,
    *,
    context: ssl.SSLContext | None = None,
    bootstrap_url: str = rdap.BOOTSTRAP_URL,
    now: datetime | None = None,
) -> Report:
    """
    Check every host and every domain of a configuration, one at a time (RF-01).

    Args:
        config (Config): A validated configuration.
        context (ssl.SSLContext | None): The client context of every connection; ``None`` means
            :func:`~tlsvigil.certificate.create_context`. The tests pass one that also trusts
            their own CA (ADR-7).
        bootstrap_url (str): The ``https://`` URL of the IANA bootstrap; the tests point it at a
            local server.
        now (datetime | None): The time to count the days left from, aware; ``None`` means the
            clock of each check.

    Returns:
        Report: One item per entry, the certificates first, each in the order of the
            configuration.

    Raises:
        ValueError: When ``now`` or ``bootstrap_url`` is bad; the checks raise for bad
            arguments only.
    """
    started_at = datetime.now(UTC) if now is None else now.astimezone(UTC)

    certificates = []
    for host in config.hosts:
        cert_result = certificate.check_certificate(
            host.host, host.port, timeout=config.timeout, now=now, context=context
        )
        cert_status = certificate.classify(cert_result, host.warn_days)
        certificates.append(CertificateItem(host, cert_result, cert_status))

    domains = []
    # Created only when there is a domain: a run without one sends nothing to the IANA (RF-01).
    if config.domains:
        client = rdap.RdapClient(
            timeout=config.timeout, context=context, bootstrap_url=bootstrap_url
        )
        for domain in config.domains:
            domain_result = client.check_domain(domain.domain, now=now)
            domain_status = rdap.classify(domain_result, domain.warn_days)
            domains.append(DomainItem(domain, domain_result, domain_status))

    return Report(started_at, tuple(certificates), tuple(domains))
