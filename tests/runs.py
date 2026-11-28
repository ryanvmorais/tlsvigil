"""
Helpers for the tests that run checks against the local servers (spec 004).

The leaves are verified against the real clock, so a test takes the time once with :func:`now`
and dates both its leaves (:func:`leaf`) and its domain answers (:func:`serve_registry`) from it.
The registry is ``https_server``: a bootstrap that names it for ``com`` and nothing for ``org``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import trustme

from http_server import LocalHTTPServer, Route

DATA: Final = Path(__file__).parent / "data" / "rdap"
BOOTSTRAP_PATH: Final = "/dns.json"
BASE_PATH: Final = "/rdap/"


def now() -> datetime:
    """
    Take the time a test counts from, to the second.

    Returns:
        datetime: The current time in UTC without microseconds, because a certificate stores its
            dates to the second.
    """
    return datetime.now(UTC).replace(microsecond=0)


def leaf(ca: trustme.CA, start: datetime, valid_for: timedelta) -> trustme.LeafCert:
    """
    Issue a leaf for ``localhost`` that expires ``valid_for`` after ``start``.

    Args:
        ca (trustme.CA): The authority the client context trusts.
        start (datetime): The test's time.
        valid_for (timedelta): From ``start`` to the expiry; negative for an expired leaf.

    Returns:
        trustme.LeafCert: The leaf, valid since 30 days before ``start``.
    """
    return ca.issue_cert(
        "localhost", not_before=start - timedelta(days=30), not_after=start + valid_for
    )


def serve_registry(server: LocalHTTPServer, answers: dict[str, datetime]) -> str:
    """
    Serve the bootstrap and one dated answer per domain on ``server``.

    Args:
        server (LocalHTTPServer): The running server.
        answers (dict[str, datetime]): From each domain under ``com`` to its expiration; the
            record is ``example.com.json`` with its name and its events replaced. A domain under
            ``com`` left out gets the server's ``404``.

    Returns:
        str: The URL of the bootstrap.
    """
    bootstrap = {
        "version": "1.0",
        "publication": "2026-11-27T00:00:00Z",
        "services": [[["com"], [server.url(BASE_PATH)]]],
    }
    server.routes[BOOTSTRAP_PATH] = Route(
        200, json.dumps(bootstrap).encode(), {"Content-Type": "application/json"}
    )
    for domain, expires in answers.items():
        record = json.loads((DATA / "example.com.json").read_bytes())
        record["ldhName"] = domain.upper()
        record["events"] = [
            {"eventAction": "expiration", "eventDate": expires.strftime("%Y-%m-%dT%H:%M:%SZ")}
        ]
        server.routes[f"{BASE_PATH}domain/{domain}"] = Route(
            200, json.dumps(record).encode(), {"Content-Type": "application/rdap+json"}
        )
    return server.url(BOOTSTRAP_PATH)
