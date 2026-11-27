"""
Tests for the reading of the IANA bootstrap and the choice of the longest suffix (spec 003, RF-04).

The parser and :meth:`~tlsvigil.rdap.Bootstrap.base_url_for` are pure, so these tests build the
bootstrap documents in code and need no server.
"""

from __future__ import annotations

import json

import pytest

from tlsvigil.rdap import Bootstrap, _parse_bootstrap

# Hosts under `.example` (RFC 2606): none of them is ever asked.
COM_URL = "https://rdap.example/com/v1/"
BR_URL = "https://rdap.example/br/"
COM_BR_URL = "https://rdap.example/com.br/"


def bootstrap_body(*services: list[list[str]]) -> bytes:
    """
    Build the body of a bootstrap document with the given services.

    Args:
        *services (list[list[str]]): Each a pair ``[entries, urls]``, as in RFC 9224.

    Returns:
        bytes: The JSON document, with the ``version`` and ``publication`` members it carries.
    """
    document = {
        "version": "1.0",
        "publication": "2026-11-26T19:00:01Z",
        "description": "A hand-written bootstrap for the tests.",
        "services": list(services),
    }
    return json.dumps(document).encode()


def parse(*services: list[list[str]]) -> Bootstrap:
    """
    Read a bootstrap document with the given services.

    Args:
        *services (list[list[str]]): Each a pair ``[entries, urls]``.

    Returns:
        Bootstrap: The parsed bootstrap.
    """
    return _parse_bootstrap(bootstrap_body(*services))


# ---------------------------------------------------------------------------
# A valid document
# ---------------------------------------------------------------------------


def test_each_entry_of_a_service_gives_its_base_url() -> None:
    bootstrap = parse([["com", "net"], [COM_URL]], [["br"], [BR_URL]])

    assert bootstrap.services == ((("com",), COM_URL), (("net",), COM_URL), (("br",), BR_URL))
    assert bootstrap.base_url_for("example.com") == COM_URL
    assert bootstrap.base_url_for("example.net") == COM_URL
    assert bootstrap.base_url_for("example.br") == BR_URL


def test_an_entry_is_read_in_lowercase_and_without_a_trailing_dot() -> None:
    bootstrap = parse([["COM", "Com.Br."], [COM_URL]])

    assert bootstrap.services == ((("com",), COM_URL), (("com", "br"), COM_URL))


def test_a_slash_is_appended_to_a_base_url_without_one() -> None:
    bootstrap = parse(
        [["com"], ["https://rdap.example/com/v1"]], [["br"], ["https://rdap.example"]]
    )

    assert bootstrap.base_url_for("example.com") == "https://rdap.example/com/v1/"
    assert bootstrap.base_url_for("example.br") == "https://rdap.example/"


def test_a_base_url_with_a_port_is_kept() -> None:
    bootstrap = parse([["com"], ["https://127.0.0.1:8443/rdap/"]])

    assert bootstrap.base_url_for("example.com") == "https://127.0.0.1:8443/rdap/"


def test_an_empty_list_of_services_is_valid_and_names_no_server() -> None:
    assert parse().base_url_for("example.com") is None


# ---------------------------------------------------------------------------
# The HTTPS filter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://rdap.example/com/",
        "HTTPS://rdap.example/com/",
        "https://user:secret@rdap.example/com/",
        "https://rdap.example/com/?key=value",
        "https://rdap.example/com/?",
        "https://rdap.example/com/#part",
        "https:///com/",
        "https://rdap.example:99999/com/",
        "https://[::1/com/",
        "ftp://rdap.example/com/",
    ],
    ids=[
        "http",
        "uppercase-scheme",
        "user-info",
        "query",
        "empty-query",
        "fragment",
        "no-host",
        "port-out-of-range",
        "bad-bracket",
        "ftp",
    ],
)
def test_a_service_without_a_usable_https_url_is_dropped(url: str) -> None:
    """The control is the second service, which the same document keeps."""
    bootstrap = parse([["com"], [url]], [["br"], [BR_URL]])

    assert bootstrap.base_url_for("example.com") is None
    assert bootstrap.base_url_for("example.br") == BR_URL


def test_the_first_usable_https_url_of_a_service_is_chosen() -> None:
    bootstrap = parse(
        [["com"], ["http://rdap.example/com/", "https://user@rdap.example/", COM_URL, BR_URL]]
    )

    assert bootstrap.base_url_for("example.com") == COM_URL


# ---------------------------------------------------------------------------
# The longest suffix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "services",
    [
        ([["br"], [BR_URL]], [["com.br"], [COM_BR_URL]]),
        ([["com.br"], [COM_BR_URL]], [["br"], [BR_URL]]),
    ],
    ids=["shorter-first", "longer-first"],
)
def test_the_entry_with_the_longest_suffix_wins(services: tuple[list[list[str]], ...]) -> None:
    bootstrap = parse(*services)

    assert bootstrap.base_url_for("example.com.br") == COM_BR_URL
    assert bootstrap.base_url_for("www.example.com.br") == COM_BR_URL
    assert bootstrap.base_url_for("example.net.br") == BR_URL


def test_an_entry_matches_whole_labels_only() -> None:
    """``m.br`` is not a suffix of ``example.com.br``, though ``"m.br"`` ends the string."""
    bootstrap = parse([["m.br"], [COM_BR_URL]], [["br"], [BR_URL]])

    assert bootstrap.base_url_for("example.com.br") == BR_URL
    assert bootstrap.base_url_for("example.m.br") == COM_BR_URL


def test_a_top_level_domain_absent_from_the_bootstrap_names_no_server() -> None:
    bootstrap = parse([["com"], [COM_URL]])

    assert bootstrap.base_url_for("example.de") is None
    assert bootstrap.base_url_for("example.com") == COM_URL  # control


# ---------------------------------------------------------------------------
# A document of the wrong shape is refused as a whole
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b"<html>not json</html>",
        b"",
        b"[" * 300_000,
        b"\xff\xfe\xfa",
        b"[]",
        b'"services"',
        b"{}",
        b'{"services": {}}',
        b'{"services": [["com"]]}',
        b'{"services": [[["com"], ["https://rdap.example/"], []]]}',
        b'{"services": ["com"]}',
        b'{"services": [["com", ["https://rdap.example/"]]]}',
        b'{"services": [[["com"], "https://rdap.example/"]]}',
        b'{"services": [[[1], ["https://rdap.example/"]]]}',
        b'{"services": [[["com"], [null]]]}',
    ],
    ids=[
        "not-json",
        "empty",
        "deeply-nested",
        "not-utf8",
        "list",
        "string",
        "no-services",
        "services-not-list",
        "pair-of-one",
        "pair-of-three",
        "service-not-list",
        "entries-not-list",
        "urls-not-list",
        "entry-not-string",
        "url-not-string",
    ],
)
def test_a_document_of_the_wrong_shape_is_refused_as_a_whole(body: bytes) -> None:
    with pytest.raises(ValueError, match=r"^invalid bootstrap document"):
        _parse_bootstrap(body)


def test_a_malformed_service_after_a_valid_one_still_refuses_the_document() -> None:
    """A damaged bootstrap is an error, never a partial list of services."""
    body = b'{"services": [[["com"], ["https://rdap.example/"]], [["br"], 1]]}'

    with pytest.raises(ValueError, match=r"^invalid bootstrap document"):
        _parse_bootstrap(body)
    # Control: without the damaged service, the same document is read.
    valid = b'{"services": [[["com"], ["https://rdap.example/"]]]}'
    assert _parse_bootstrap(valid).base_url_for("example.com") == "https://rdap.example/"
