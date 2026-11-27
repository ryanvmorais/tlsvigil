"""
Tests for the classification of a domain result against a warning threshold (spec 003, RF-02).

:func:`~tlsvigil.rdap.classify` is pure, so these tests build the results by hand and need no
server.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tlsvigil.rdap import DomainResult, ErrorKind, Outcome, Status, UnknownReason, classify

# A fixed, aware time: classify() never reads the clock, so the results need no real one.
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
# Any positive threshold works; 30 days is a common warning window for a domain.
THRESHOLD = 30


def dated(days_left: int | None) -> DomainResult:
    """
    Build a dated result with the given days left.

    Args:
        days_left (int | None): The days left; ``None`` builds a result the check never makes.

    Returns:
        DomainResult: A dated result for ``example.com``, checked at ``NOW``.
    """
    expiration = None if days_left is None else NOW + timedelta(days=days_left, hours=1)
    return DomainResult(
        domain="example.com",
        checked_at=NOW,
        outcome=Outcome.DATED,
        server="rdap.example",
        expiration=expiration,
        days_left=days_left,
        registry_name="example.com",
        statuses=("active",),
    )


# One result of each other outcome; their reason and kind do not change the status.
UNDATED = DomainResult(
    domain="example.de",
    checked_at=NOW,
    outcome=Outcome.UNDATED,
    reason=UnknownReason.NO_RDAP_SERVICE,
)
ERROR = DomainResult(
    domain="example.com",
    checked_at=NOW,
    outcome=Outcome.ERROR,
    server="rdap.example",
    error_kind=ErrorKind.NOT_FOUND,
    message="HTTP 404",
)


# ---------------------------------------------------------------------------
# Dated: the boundary of the threshold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days_left", "expected"),
    [
        (THRESHOLD - 1, Status.EXPIRING),
        (THRESHOLD, Status.OK),
        (THRESHOLD + 1, Status.OK),
    ],
    ids=["n-minus-1", "n", "n-plus-1"],
)
def test_a_dated_result_is_expiring_below_the_threshold(days_left: int, expected: Status) -> None:
    assert classify(dated(days_left), THRESHOLD) is expected


@pytest.mark.parametrize(
    ("days_left", "expected"),
    [(-1, Status.EXPIRING), (0, Status.OK), (365, Status.OK)],
    ids=["passed", "last-day", "far"],
)
def test_a_threshold_of_zero_still_warns_for_a_passed_date(
    days_left: int, expected: Status
) -> None:
    """A passed date is negative, so it is ``expiring`` with any threshold (resolved decision 3)."""
    assert classify(dated(days_left), 0) is expected


def test_a_passed_date_is_expiring_with_a_large_threshold_too() -> None:
    assert classify(dated(-400), 365) is Status.EXPIRING


# ---------------------------------------------------------------------------
# Undated and error: whatever the threshold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("threshold", [0, THRESHOLD, 365], ids=["zero", "n", "year"])
def test_an_undated_result_is_unknown_whatever_the_threshold(threshold: int) -> None:
    assert classify(UNDATED, threshold) is Status.UNKNOWN


@pytest.mark.parametrize("threshold", [0, THRESHOLD, 365], ids=["zero", "n", "year"])
def test_an_error_result_is_an_error_whatever_the_threshold(threshold: int) -> None:
    assert classify(ERROR, threshold) is Status.ERROR


@pytest.mark.parametrize(
    "reason", list(UnknownReason), ids=[reason.value for reason in UnknownReason]
)
def test_every_unknown_reason_is_unknown(reason: UnknownReason) -> None:
    result = DomainResult("example.com", NOW, Outcome.UNDATED, reason=reason)

    assert classify(result, THRESHOLD) is Status.UNKNOWN


@pytest.mark.parametrize("kind", list(ErrorKind), ids=[kind.value for kind in ErrorKind])
def test_every_error_kind_is_an_error(kind: ErrorKind) -> None:
    result = DomainResult("example.com", NOW, Outcome.ERROR, error_kind=kind, message="failed")

    assert classify(result, THRESHOLD) is Status.ERROR


# ---------------------------------------------------------------------------
# Refused arguments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "threshold", [-1, 30.0, True, "30", None], ids=["negative", "float", "bool", "str", "none"]
)
@pytest.mark.parametrize("result", [dated(90), UNDATED, ERROR], ids=["dated", "undated", "error"])
def test_a_bad_threshold_is_refused_before_the_outcome_is_read(
    result: DomainResult, threshold: object
) -> None:
    with pytest.raises(ValueError, match="warn_days must be an integer, 0 or more"):
        classify(result, threshold)  # type: ignore[arg-type]  # the type a caller may still pass


def test_a_dated_result_without_days_left_is_refused() -> None:
    with pytest.raises(ValueError, match="must carry its days left"):
        classify(dated(None), THRESHOLD)
