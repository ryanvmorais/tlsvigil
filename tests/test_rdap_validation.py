"""
Tests for the validation of a domain, a timeout, the bootstrap URL and ``now`` (spec 003, RF-07).

The validation runs before any request, so these tests call the validating functions of
:mod:`tlsvigil.rdap` and the constructor of :class:`~tlsvigil.rdap.RdapClient` directly and need
no server.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from tlsvigil.rdap import (
    BOOTSTRAP_URL,
    DomainProblem,
    InvalidDomain,
    RdapClient,
    _validate_now,
    normalize_domain,
)

# The longest name accepted and one character more; every label stays valid, so only the length
# of the whole name can refuse the second.
NAME_253 = ".".join(["a" * 63] * 3 + ["a" * 57]) + ".com"
NAME_254 = ".".join(["a" * 63] * 3 + ["a" * 58]) + ".com"

# ---------------------------------------------------------------------------
# The domain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("domain", "message", "code"),
    [
        ("", "non-empty string", DomainProblem.EMPTY),
        ("https://example.com", "not a URL or a path", DomainProblem.URL_OR_PATH),
        ("example.com/path", "not a URL or a path", DomainProblem.URL_OR_PATH),
        ("example.com:443", "port", DomainProblem.PORT),
        ("example .com", "whitespace", DomainProblem.WHITESPACE),
        ("example.com\n", "whitespace", DomainProblem.WHITESPACE),
        ("user@example.com", "user information", DomainProblem.USER_INFO),
        ("_dmarc.example.com", "letters, digits, hyphens and dots only", DomainProblem.UNDERSCORE),
        ("exa*mple.com", "1 to 63 letters", DomainProblem.BAD_LABEL),
        ("-example.com", "1 to 63 letters", DomainProblem.BAD_LABEL),
        ("example-.com", "1 to 63 letters", DomainProblem.BAD_LABEL),
        ("com", "at least two labels", DomainProblem.ONE_LABEL),
        ("com.", "at least two labels", DomainProblem.ONE_LABEL),
        ("example..com", "empty label", DomainProblem.EMPTY_LABEL),
        (".example.com", "empty label", DomainProblem.EMPTY_LABEL),
        ("example.com..", "empty label", DomainProblem.EMPTY_LABEL),
        ("a" * 64 + ".com", "1 to 63 letters", DomainProblem.BAD_LABEL),
        (NAME_254, "253 characters or fewer", DomainProblem.TOO_LONG),
        ("192.0.2.1", "numeric label", DomainProblem.NUMERIC),
        ("2001:db8::1", "port", DomainProblem.PORT),
    ],
    ids=[
        "empty",
        "url",
        "path",
        "port",
        "space",
        "newline",
        "user-info",
        "underscore",
        "symbol",
        "leading-hyphen",
        "trailing-hyphen",
        "one-label",
        "one-label-dot",
        "empty-label",
        "leading-dot",
        "two-trailing-dots",
        "label-64",
        "name-254",
        "ipv4",
        "ipv6",
    ],
)
def test_a_bad_domain_is_refused_with_the_problem_named(
    domain: str, message: str, code: DomainProblem
) -> None:
    """The message names the problem, and the code names the rule (spec 004, ADR-2)."""
    with pytest.raises(ValueError, match=message) as raised:
        normalize_domain(domain)

    assert isinstance(raised.value, InvalidDomain)
    assert raised.value.code is code


@pytest.mark.parametrize("domain", [None, 1, b"example.com"], ids=["none", "int", "bytes"])
def test_a_domain_that_is_not_a_string_is_refused(domain: object) -> None:
    with pytest.raises(ValueError, match="non-empty string") as raised:
        normalize_domain(domain)  # type: ignore[arg-type]  # the type a caller may still pass

    assert isinstance(raised.value, InvalidDomain)
    assert raised.value.code is DomainProblem.EMPTY


def test_a_name_with_letters_outside_ascii_points_to_the_xn_form() -> None:
    with pytest.raises(ValueError, match=re.escape("'xn--' form of 'pão.com.br'")) as raised:
        normalize_domain("pão.com.br")

    assert isinstance(raised.value, InvalidDomain)
    assert raised.value.code is DomainProblem.NOT_ASCII


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("Example.COM.", "example.com"),
        ("example.com.br", "example.com.br"),
        ("xn--po-gia.com.br", "xn--po-gia.com.br"),
        ("a" * 63 + ".com", "a" * 63 + ".com"),
        (NAME_253, NAME_253),
        ("1.example.com", "1.example.com"),
    ],
    ids=["case-and-dot", "plain", "xn", "label-63", "name-253", "numeric-inner-label"],
)
def test_a_valid_domain_is_given_in_lowercase_without_the_trailing_dot(
    domain: str, expected: str
) -> None:
    """The control of the table above: the limits themselves are accepted."""
    assert normalize_domain(domain) == expected


def test_the_length_cases_sit_on_the_limit() -> None:
    """Guards the two tables above: off by one, the limit would not be tested."""
    assert (len(NAME_253), len(NAME_254)) == (253, 254)


# ---------------------------------------------------------------------------
# The client's settings and now
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "timeout",
    [0, -1, float("inf"), float("nan"), True, "10", None],
    ids=["zero", "negative", "inf", "nan", "bool", "str", "none"],
)
def test_a_bad_timeout_is_refused(timeout: object) -> None:
    with pytest.raises(ValueError, match="timeout must be a finite number"):
        RdapClient(timeout=timeout)  # type: ignore[arg-type]  # the type a caller may still pass


@pytest.mark.parametrize(
    "url",
    [
        "http://data.iana.org/rdap/dns.json",
        "https://",
        "https:///rdap/dns.json",
        "data.iana.org/rdap/dns.json",
        "https://[::1/dns.json",
        123,
    ],
    ids=["http", "no-host", "empty-host", "no-scheme", "bad-bracket", "int"],
)
def test_a_bootstrap_url_that_is_not_https_with_a_host_is_refused(url: object) -> None:
    with pytest.raises(ValueError, match="bootstrap_url"):
        RdapClient(bootstrap_url=url)  # type: ignore[arg-type]  # the type a caller may still pass


def test_the_defaults_and_valid_settings_are_accepted() -> None:
    """The control of the two tables above."""
    default = RdapClient()
    assert (default.timeout, default.bootstrap_url) == (10.0, BOOTSTRAP_URL)
    local = RdapClient(timeout=0.3, bootstrap_url="https://127.0.0.1:8443/rdap/dns.json")
    assert (local.timeout, local.bootstrap_url) == (0.3, "https://127.0.0.1:8443/rdap/dns.json")
    assert RdapClient(timeout=5).timeout == 5


def test_a_naive_now_is_refused() -> None:
    with pytest.raises(ValueError, match="aware datetime"):
        _validate_now(datetime(2026, 11, 27, 12, 0))


@pytest.mark.parametrize(
    "now", [None, datetime(2026, 11, 27, 12, 0, tzinfo=UTC)], ids=["none", "aware"]
)
def test_an_aware_or_absent_now_is_accepted(now: datetime | None) -> None:
    """The control of the test above."""
    _validate_now(now)
