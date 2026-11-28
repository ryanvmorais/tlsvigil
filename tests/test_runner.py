"""
Tests for the run of a configuration (spec 004, RF-01, RF-09).

The run goes against the local servers of specs ``002`` and ``003``: TLS servers on the loopback
interface with leaves made during the test, and ``https_server`` holding a bootstrap that names
itself for ``com`` and nothing for ``org`` (``runs.py``). The leaves are verified against the real
clock, so each test takes the time once and dates both the leaves and the domain answers from it.
The counts are tested on reports built by hand (``reports.py``).
"""

from __future__ import annotations

import ssl
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest
import trustme

import reports
import runs
from http_server import LocalHTTPServer
from runs import BOOTSTRAP_PATH, leaf, serve_registry
from tls_server import LocalServer
from tlsvigil.config import Config, DomainEntry, HostEntry
from tlsvigil.runner import ATTENTION, STATUSES, Report, run

Serve = Callable[..., LocalServer]

# Long enough for a loaded CI machine; nothing in these tests waits for it.
TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(hosts: tuple[HostEntry, ...] = (), domains: tuple[DomainEntry, ...] = ()) -> Config:
    """
    Build a configuration without a file.

    Args:
        hosts (tuple[HostEntry, ...]): The hosts.
        domains (tuple[DomainEntry, ...]): The domains.

    Returns:
        Config: The configuration, with the test timeout.
    """
    return Config(lang=None, timeout=TIMEOUT, hosts=hosts, domains=domains)


# ---------------------------------------------------------------------------
# The run against the local servers (RF-01)
# ---------------------------------------------------------------------------


def test_the_items_keep_the_order_of_the_configuration(
    ca: trustme.CA,
    serve: Serve,
    client_context: ssl.SSLContext,
    https_server: LocalHTTPServer,
) -> None:
    now = runs.now()
    expired = serve(leaf(ca, now, timedelta(days=-1)))
    valid = serve(leaf(ca, now, timedelta(days=90)))
    bootstrap_url = serve_registry(https_server, {"example.com": now + timedelta(days=300)})
    config = _config(
        hosts=(HostEntry("localhost", expired.port, 14), HostEntry("localhost", valid.port, 14)),
        domains=(DomainEntry("example.org", 30), DomainEntry("example.com", 30)),
    )

    report = run(config, context=client_context, bootstrap_url=bootstrap_url, now=now)

    assert report.started_at == now
    assert [(item.result.port, item.status) for item in report.certificates] == [
        (expired.port, "invalid"),
        (valid.port, "ok"),
    ]
    assert [(item.result.domain, item.status) for item in report.domains] == [
        ("example.org", "unknown"),
        ("example.com", "ok"),
    ]
    assert [item.entry for item in report.certificates] == list(config.hosts)
    assert [item.entry for item in report.domains] == list(config.domains)
    assert report.counts() == {
        "total": 4,
        "attention": 1,
        "ok": 2,
        "expiring": 0,
        "invalid": 1,
        "unknown": 1,
        "error": 0,
    }
    assert report.needs_attention()


def test_each_entry_is_classified_against_its_own_threshold(
    ca: trustme.CA,
    serve: Serve,
    client_context: ssl.SSLContext,
    https_server: LocalHTTPServer,
) -> None:
    now = runs.now()
    server = serve(leaf(ca, now, timedelta(days=30, hours=1)))  # 30 days left
    bootstrap_url = serve_registry(
        https_server,
        {
            "first.com": now + timedelta(days=30, hours=1),
            "second.com": now + timedelta(days=30, hours=1),
        },
    )
    config = _config(
        hosts=(HostEntry("localhost", server.port, 30), HostEntry("localhost", server.port, 31)),
        domains=(DomainEntry("first.com", 30), DomainEntry("second.com", 31)),
    )

    report = run(config, context=client_context, bootstrap_url=bootstrap_url, now=now)

    assert [item.result.days_left for item in report.certificates] == [30, 30]
    assert [item.status for item in report.certificates] == ["ok", "expiring"]
    assert [item.result.days_left for item in report.domains] == [30, 30]
    assert [item.status for item in report.domains] == ["ok", "expiring"]


def test_a_run_without_domains_sends_nothing_to_the_bootstrap(
    ca: trustme.CA,
    serve: Serve,
    client_context: ssl.SSLContext,
    https_server: LocalHTTPServer,
) -> None:
    now = runs.now()
    server = serve(leaf(ca, now, timedelta(days=90)))
    bootstrap_url = serve_registry(https_server, {})

    report = run(
        _config(hosts=(HostEntry("localhost", server.port, 14),)),
        context=client_context,
        bootstrap_url=bootstrap_url,
        now=now,
    )

    assert [item.status for item in report.certificates] == ["ok"]
    assert report.domains == ()
    assert https_server.requests == []


def test_a_run_with_one_domain_fetches_the_bootstrap_once(
    client_context: ssl.SSLContext, https_server: LocalHTTPServer
) -> None:
    """Control for the test above: the same server records the bootstrap when a domain needs it."""
    now = runs.now()
    bootstrap_url = serve_registry(https_server, {})

    report = run(
        _config(domains=(DomainEntry("example.org", 30),)),
        context=client_context,
        bootstrap_url=bootstrap_url,
        now=now,
    )

    assert [item.status for item in report.domains] == ["unknown"]
    assert [request.path for request in https_server.requests] == [BOOTSTRAP_PATH]


def test_the_run_takes_the_clock_when_no_time_is_given() -> None:
    before = datetime.now(UTC)
    report = run(_config())
    after = datetime.now(UTC)

    assert before <= report.started_at <= after
    assert report.started_at.tzinfo is UTC
    assert report.certificates == () and report.domains == ()


def test_the_start_is_kept_in_utc() -> None:
    sao_paulo = reports.NOW.astimezone(timezone(timedelta(hours=-3)))

    report = run(_config(), now=sao_paulo)

    assert report.started_at == reports.NOW
    assert report.started_at.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# The counts and the attention (RF-09)
# ---------------------------------------------------------------------------


def test_attention_is_expiring_invalid_and_error() -> None:
    assert {"expiring", "invalid", "error"} == ATTENTION
    assert set(STATUSES) > ATTENTION


@pytest.mark.parametrize("status", STATUSES)
def test_a_report_of_one_item_counts_its_status(status: str) -> None:
    item = reports.one_of_each()[status]
    report = reports.report(item)

    counts = report.counts()

    assert item.status == status
    assert counts["total"] == 1
    assert counts[status] == 1
    assert sum(counts[value] for value in STATUSES) == 1
    assert counts["attention"] == (1 if status in ATTENTION else 0)
    assert report.needs_attention() is (status in ATTENTION)


@pytest.mark.parametrize("status", ["expiring", "error"])
def test_a_domain_that_needs_attention_counts_too(status: str) -> None:
    item = reports.dated(days_left=3) if status == "expiring" else reports.failed()
    report = reports.report(reports.verified(), item)

    assert item.status == status
    assert report.counts()["attention"] == 1
    assert report.needs_attention()


def test_a_report_with_every_status_counts_each_once() -> None:
    report = reports.report(*reports.one_of_each().values())

    assert report.counts() == {
        "total": 5,
        "attention": 3,
        "ok": 1,
        "expiring": 1,
        "invalid": 1,
        "unknown": 1,
        "error": 1,
    }
    assert list(report.counts()) == ["total", "attention", *STATUSES]


def test_an_empty_report_needs_no_attention() -> None:
    report = Report(reports.NOW, (), ())

    assert report.counts() == dict.fromkeys(["total", "attention", *STATUSES], 0)
    assert not report.needs_attention()
