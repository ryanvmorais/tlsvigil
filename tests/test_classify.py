"""
Tests for the classification of a certificate result against a warning threshold (spec 002, RF-02).

:func:`~tlsvigil.certificate.classify` is pure, so these tests build the results by hand and need
no server.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tlsvigil.certificate import (
    CertificateResult,
    ErrorKind,
    InvalidReason,
    Outcome,
    Status,
    classify,
)

# A fixed, aware time: classify() never reads the clock, so the results need no real one.
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
# Any positive threshold works; 30 days is a common warning window.
THRESHOLD = 30


def verified(days_left: int | None) -> CertificateResult:
    """
    Build a verified result with the given days left.

    Args:
        days_left (int | None): The days left; ``None`` builds a result the check never makes.

    Returns:
        CertificateResult: A verified result for ``example.test:443``, checked at ``NOW``.
    """
    not_after = None if days_left is None else NOW + timedelta(days=days_left, hours=1)
    return CertificateResult(
        host="example.test",
        port=443,
        checked_at=NOW,
        outcome=Outcome.VERIFIED,
        not_after=not_after,
        days_left=days_left,
    )


# One result of each failed outcome; their reason and kind do not change the status.
INVALID = CertificateResult(
    host="example.test",
    port=443,
    checked_at=NOW,
    outcome=Outcome.INVALID,
    reason=InvalidReason.EXPIRED,
    message="certificate has expired",
)
ERROR = CertificateResult(
    host="example.test",
    port=443,
    checked_at=NOW,
    outcome=Outcome.ERROR,
    error_kind=ErrorKind.REFUSED,
    message="connection refused",
)


# ---------------------------------------------------------------------------
# Verified: the boundary of the threshold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days_left", "expected"),
    [
        (THRESHOLD - 1, Status.EXPIRING),
        (THRESHOLD, Status.OK),
        (THRESHOLD + 1, Status.OK),
    ],
    ids=["one-day-short", "exactly-the-threshold", "one-day-over"],
)
def test_a_verified_result_is_expiring_only_below_the_threshold(
    days_left: int, expected: Status
) -> None:
    assert classify(verified(days_left), THRESHOLD) is expected


def test_a_threshold_of_zero_never_warns_by_date() -> None:
    assert classify(verified(0), 0) is Status.OK


# ---------------------------------------------------------------------------
# Invalid and error: the threshold does not matter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("warn_days", [0, 365])
@pytest.mark.parametrize(
    ("result", "expected"),
    [(INVALID, Status.INVALID), (ERROR, Status.ERROR)],
    ids=["invalid", "error"],
)
def test_invalid_and_error_keep_their_status_whatever_the_threshold(
    result: CertificateResult, expected: Status, warn_days: int
) -> None:
    assert classify(result, warn_days) is expected


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("warn_days", [-1, 1.5, True], ids=["negative", "float", "bool"])
def test_a_threshold_that_is_not_a_non_negative_integer_is_refused(warn_days: object) -> None:
    with pytest.raises(ValueError, match="warn_days"):
        # The wrong types are the point of the test: a threshold can come from a hand-written file.
        classify(verified(THRESHOLD), warn_days)  # type: ignore[arg-type]


def test_a_bad_threshold_is_refused_for_invalid_and_error_results_too() -> None:
    # The threshold is checked first, so a bad configuration fails on every host, not only on
    # the verified ones.
    with pytest.raises(ValueError, match="warn_days"):
        classify(INVALID, -1)


def test_a_verified_result_without_days_left_is_refused() -> None:
    with pytest.raises(ValueError, match="days left"):
        classify(verified(None), THRESHOLD)
