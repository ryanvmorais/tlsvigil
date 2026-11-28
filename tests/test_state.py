"""
Tests for the state file between runs (spec 005, RF-07, RNF-03, ADR-2).

Every file lives in ``tmp_path``. A refused file is checked twice: the state read is the empty one,
and the notice names a sentence of the catalogue whose fields it fills. The atomic write is
checked by making :func:`os.replace` fail and reading the old file back whole.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tlsvigil.alerts import PAST_DUE, Finding
from tlsvigil.config import Channel
from tlsvigil.i18n import MESSAGES, Lang, translate
from tlsvigil.state import (
    DEFAULT_STATE,
    EMPTY_STATE,
    MAX_STATE_BYTES,
    STATE_VERSION,
    ChannelState,
    Notice,
    State,
    load_state,
    reset_for,
    save_state,
)

EXPIRES = datetime(2026, 12, 20, tzinfo=UTC)
CERT = Finding("certificate:example.com:443", "example.com:443", "invalid", "expired")
DOMAIN = Finding("domain:example.com.br", "example.com.br", "expiring", None, EXPIRES, 14)
PAST = Finding("domain:example.org", "example.org", "expiring", None, EXPIRES, PAST_DUE)
FAILED = Finding("certificate:2001:db8::1:8443", "[2001:db8::1]:8443", "error", "timeout")

STATE = State(
    {"certificate:typo.example.com:443": 1, FAILED.key: 3},
    {
        Channel.GITHUB: ChannelState(
            {CERT.key: CERT, DOMAIN.key: DOMAIN},
            "owner/name",
            12,
            "github-actions[bot]",
        ),
        Channel.TELEGRAM: ChannelState({PAST.key: PAST, FAILED.key: FAILED}),
        Channel.SMTP: ChannelState(),
    },
)

# The document of the design ("The state file"), the base every malformed file is made from.
# `Any`: the tests reach into it and change it at every level.
DOCUMENT: dict[str, Any] = {
    "version": 1,
    "error_runs": {"certificate:example.com:443": 1},
    "channels": {
        "github": {
            "repository": "owner/name",
            "issue": 12,
            "author": "github-actions[bot]",
            "told": {
                "domain:example.com.br": {
                    "name": "example.com.br",
                    "status": "expiring",
                    "reason": None,
                    "expires_at": "2026-12-20T00:00:00+00:00",
                    "reminder": 14,
                },
                "certificate:old.example.com:443": {
                    "name": "old.example.com:443",
                    "status": "invalid",
                    "reason": "expired",
                    "expires_at": None,
                    "reminder": None,
                },
            },
        },
        "telegram": {"told": {}},
    },
}


def _write(tmp_path: Path, data: bytes) -> Path:
    """
    Write a state file.

    Args:
        tmp_path (Path): The test's directory.
        data (bytes): The bytes of the file.

    Returns:
        Path: The file.
    """
    path = tmp_path / DEFAULT_STATE
    path.write_bytes(data)
    return path


def _dump(tmp_path: Path, document: object) -> Path:
    """
    Write a JSON document as a state file.

    Args:
        tmp_path (Path): The test's directory.
        document (object): The document.

    Returns:
        Path: The file.
    """
    return _write(tmp_path, json.dumps(document).encode())


def _refused(path: Path, key: str) -> Notice:
    """
    Read a file that must be refused, and check the state and the notice.

    Args:
        path (Path): The file.
        key (str): The catalogue key expected.

    Returns:
        Notice: The notice, its sentence checked in both languages.
    """
    state, notice = load_state(path)

    assert state == EMPTY_STATE
    assert notice is not None
    assert notice.key == key
    assert notice.params["path"] == str(path)
    for lang in Lang:
        assert str(path) in translate(lang, notice.key, **notice.params)
    return notice


# ---------------------------------------------------------------------------
# The round trip and the file as written
# ---------------------------------------------------------------------------


def test_a_saved_state_reads_back_equal(tmp_path: Path) -> None:
    path = tmp_path / DEFAULT_STATE

    save_state(path, STATE)

    assert load_state(path) == (STATE, None)


def test_the_order_of_the_findings_survives_the_round_trip(tmp_path: Path) -> None:
    path = tmp_path / DEFAULT_STATE

    save_state(path, STATE)
    state, _ = load_state(path)

    assert list(state.channels) == [Channel.GITHUB, Channel.TELEGRAM, Channel.SMTP]
    assert list(state.channels[Channel.GITHUB].told) == [CERT.key, DOMAIN.key]


def test_the_document_of_the_design_reads(tmp_path: Path) -> None:
    state, notice = load_state(_dump(tmp_path, DOCUMENT))

    assert notice is None
    assert state.error_runs == {"certificate:example.com:443": 1}
    github = state.channels[Channel.GITHUB]
    assert (github.repository, github.issue, github.author) == (
        "owner/name",
        12,
        "github-actions[bot]",
    )
    assert github.told["domain:example.com.br"] == Finding(
        "domain:example.com.br", "example.com.br", "expiring", None, EXPIRES, 14
    )
    assert state.channels[Channel.TELEGRAM] == ChannelState()


def test_a_date_with_another_offset_is_read_in_utc(tmp_path: Path) -> None:
    document = copy.deepcopy(DOCUMENT)
    told = document["channels"]["github"]["told"]
    told["domain:example.com.br"]["expires_at"] = "2026-12-19T21:00:00-03:00"

    state, _ = load_state(_dump(tmp_path, document))

    moment = state.channels[Channel.GITHUB].told["domain:example.com.br"].expires_at
    assert moment == EXPIRES
    assert moment.tzinfo is UTC


def test_the_saved_file_is_utf8_json_with_a_final_newline(tmp_path: Path) -> None:
    path = tmp_path / DEFAULT_STATE
    name = Finding("domain:xn--exmpl-hra.com", "exâmpl.com", "invalid", "x")

    save_state(path, State({}, {Channel.SMTP: ChannelState({name.key: name})}))

    data = path.read_bytes()
    assert data.endswith(b"}\n")
    assert "exâmpl.com".encode() in data
    assert json.loads(data)["version"] == STATE_VERSION


def test_a_successful_save_leaves_only_the_state_file(tmp_path: Path) -> None:
    save_state(tmp_path / DEFAULT_STATE, STATE)
    save_state(tmp_path / DEFAULT_STATE, EMPTY_STATE)

    assert [path.name for path in tmp_path.iterdir()] == [DEFAULT_STATE]
    assert load_state(tmp_path / DEFAULT_STATE) == (EMPTY_STATE, None)


def _keys(value: object) -> Iterator[str]:
    """
    Give every key of every object of a JSON document, the keys of the findings excepted.

    Args:
        value (object): The document.

    Yields:
        str: Each key, as many times as it appears.
    """
    if isinstance(value, dict):
        for key, inner in value.items():
            # The entries' keys are data (a host, a domain), not fields.
            if not key.startswith(("certificate:", "domain:")):
                yield key
            yield from _keys(inner)


def test_a_saved_file_holds_no_field_for_a_secret_or_an_address(tmp_path: Path) -> None:
    """The fields are those of the design: a new one (a token, a chat, an address) fails here."""
    path = tmp_path / DEFAULT_STATE

    save_state(path, STATE)

    fields = set(_keys(json.loads(path.read_bytes())))
    assert fields == {
        "version",
        "error_runs",
        "channels",
        "github",
        "telegram",
        "smtp",
        "repository",
        "issue",
        "author",
        "told",
        "name",
        "status",
        "reason",
        "expires_at",
        "reminder",
    }


def test_the_field_walk_sees_a_nested_field() -> None:
    """Control for the test above: a field inside a channel is seen, an entry's key is not."""
    found = set(_keys({"channels": {"telegram": {"chat_id": "1", "told": {"domain:a.b": {}}}}}))

    assert found == {"channels", "telegram", "chat_id", "told"}


# ---------------------------------------------------------------------------
# A file that cannot be used reads as empty
# ---------------------------------------------------------------------------


def test_a_missing_file_is_the_first_run_in_silence(tmp_path: Path) -> None:
    assert load_state(tmp_path / DEFAULT_STATE) == (EMPTY_STATE, None)


def test_a_directory_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / DEFAULT_STATE
    path.mkdir()

    _refused(path, "state.unreadable")


@pytest.mark.parametrize(
    "error",
    [PermissionError(13, "Permission denied"), OSError(5, "Input/output error")],
    ids=["permission", "device"],
)
def test_a_file_the_system_refuses_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    path = _dump(tmp_path, DOCUMENT)

    def refuse(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(Path, "open", refuse)
    _refused(path, "state.unreadable")


def test_invalid_utf8_is_refused(tmp_path: Path) -> None:
    _refused(_write(tmp_path, b'{"version": 1, "x": "\xff"}'), "state.not_utf8")


@pytest.mark.parametrize(
    "data", [b"", b"{", b"not json", b"[1,"], ids=["empty", "open", "text", "array"]
)
def test_invalid_json_is_refused(tmp_path: Path, data: bytes) -> None:
    _refused(_write(tmp_path, data), "state.invalid")


@pytest.mark.parametrize("version", [2, 0, True], ids=["newer", "zero", "true"])
def test_an_unknown_version_is_refused_as_such(tmp_path: Path, version: object) -> None:
    _refused(_dump(tmp_path, {**DOCUMENT, "version": version}), "state.version")


@pytest.mark.parametrize("version", ["1", 1.0, None], ids=["string", "float", "null"])
def test_a_version_that_is_not_an_integer_is_invalid(tmp_path: Path, version: object) -> None:
    _refused(_dump(tmp_path, {**DOCUMENT, "version": version}), "state.invalid")


def test_a_file_one_byte_over_the_cap_is_refused_with_the_limit(tmp_path: Path) -> None:
    notice = _refused(_write(tmp_path, b" " * (MAX_STATE_BYTES + 1)), "state.too_large")

    assert notice.params["limit"] == "1024"


def test_a_file_at_the_cap_is_read(tmp_path: Path) -> None:
    """Control for the test above: the cap itself is allowed."""
    data = json.dumps(DOCUMENT).encode()
    path = _write(tmp_path, data + b" " * (MAX_STATE_BYTES - len(data)))

    assert path.stat().st_size == MAX_STATE_BYTES
    state, notice = load_state(path)
    assert notice is None
    assert state.channels[Channel.GITHUB].issue == 12


def _github(document: dict[str, Any]) -> dict[str, Any]:
    """The GitHub part of a document."""
    part: dict[str, Any] = document["channels"]["github"]
    return part


def _expiring(document: dict[str, Any]) -> dict[str, Any]:
    """The ``expiring`` finding of the GitHub part."""
    part: dict[str, Any] = _github(document)["told"]["domain:example.com.br"]
    return part


def _invalid(document: dict[str, Any]) -> dict[str, Any]:
    """The ``invalid`` finding of the GitHub part."""
    part: dict[str, Any] = _github(document)["told"]["certificate:old.example.com:443"]
    return part


Mutation = Callable[[dict[str, Any]], None]

# One deviation per level of the document; each is applied to a copy of DOCUMENT.
MALFORMED: dict[str, Mutation] = {
    "extra top key": lambda d: d.update(extra=1),
    "no error_runs": lambda d: d.pop("error_runs"),
    "error_runs a list": lambda d: d.update(error_runs=[]),
    "error_runs zero": lambda d: d.update(error_runs={"domain:a.b": 0}),
    "error_runs true": lambda d: d.update(error_runs={"domain:a.b": True}),
    "error_runs text": lambda d: d.update(error_runs={"domain:a.b": "2"}),
    "error_runs bad key": lambda d: d.update(error_runs={"a.b": 2}),
    "channels a list": lambda d: d.update(channels=[]),
    "unknown channel": lambda d: d["channels"].update(slack={"told": {}}),
    "channel a list": lambda d: d["channels"].update(telegram=[]),
    "telegram with an issue": lambda d: d["channels"]["telegram"].update(issue=1),
    "telegram without told": lambda d: d["channels"].update(telegram={}),
    "github without author": lambda d: _github(d).pop("author"),
    "github with a chat": lambda d: _github(d).update(chat_id="1"),
    "issue zero": lambda d: _github(d).update(issue=0),
    "issue text": lambda d: _github(d).update(issue="12"),
    "issue true": lambda d: _github(d).update(issue=True),
    "repository empty": lambda d: _github(d).update(repository=""),
    "repository a number": lambda d: _github(d).update(repository=5),
    "author a list": lambda d: _github(d).update(author=[]),
    "told a list": lambda d: _github(d).update(told=[]),
    "told bad key": lambda d: _github(d)["told"].update({"example.com": _expiring(d)}),
    "finding a string": lambda d: _github(d)["told"].update({"domain:a.b": "expiring"}),
    "finding without name": lambda d: _expiring(d).pop("name"),
    "finding extra key": lambda d: _expiring(d).update(days_left=3),
    "name empty": lambda d: _expiring(d).update(name=""),
    "name a number": lambda d: _expiring(d).update(name=3),
    "status ok": lambda d: _expiring(d).update(status="ok"),
    "status unknown": lambda d: _expiring(d).update(status="unknown"),
    "expiring with a reason": lambda d: _expiring(d).update(reason="x"),
    "expiring without a date": lambda d: _expiring(d).update(expires_at=None),
    "expiring naive date": lambda d: _expiring(d).update(expires_at="2026-12-20T00:00:00"),
    "expiring bad date": lambda d: _expiring(d).update(expires_at="tomorrow"),
    "expiring date a number": lambda d: _expiring(d).update(expires_at=20261220),
    "reminder not a day": lambda d: _expiring(d).update(reminder=5),
    "reminder true": lambda d: _expiring(d).update(reminder=True),
    "reminder text": lambda d: _expiring(d).update(reminder="14"),
    "invalid without a reason": lambda d: _invalid(d).update(reason=None),
    "invalid reason empty": lambda d: _invalid(d).update(reason=""),
    "invalid reason a number": lambda d: _invalid(d).update(reason=3),
    "invalid with a date": lambda d: _invalid(d).update(expires_at="2026-12-20T00:00:00+00:00"),
    "invalid with a reminder": lambda d: _invalid(d).update(reminder=14),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_a_wrong_key_or_type_at_any_level_is_invalid(tmp_path: Path, name: str) -> None:
    document = copy.deepcopy(DOCUMENT)
    MALFORMED[name](document)

    _refused(_dump(tmp_path, document), "state.invalid")


@pytest.mark.parametrize(
    "document", [[DOCUMENT], "state", 1, None], ids=["list", "text", "number", "null"]
)
def test_a_document_that_is_not_an_object_is_invalid(tmp_path: Path, document: object) -> None:
    _refused(_dump(tmp_path, document), "state.invalid")


def test_every_mutation_reads_when_undone(tmp_path: Path) -> None:
    """Control for the test above: the base document itself reads, so each refusal is its own."""
    assert load_state(_dump(tmp_path, copy.deepcopy(DOCUMENT)))[1] is None


@pytest.mark.parametrize(
    ("place", "value"),
    [("reminder", None), ("reminder", PAST_DUE), ("reminder", 0)],
    ids=["no reminder", "past due", "day 0"],
)
def test_every_reminder_day_and_none_read(tmp_path: Path, place: str, value: object) -> None:
    document = copy.deepcopy(DOCUMENT)
    _expiring(document)[place] = value

    state, notice = load_state(_dump(tmp_path, document))

    assert notice is None
    assert state.channels[Channel.GITHUB].told["domain:example.com.br"].reminder == value


def test_a_github_part_with_nulls_reads(tmp_path: Path) -> None:
    document = copy.deepcopy(DOCUMENT)
    _github(document).update(repository=None, issue=None, author=None)

    state, notice = load_state(_dump(tmp_path, document))

    assert notice is None
    assert state.channels[Channel.GITHUB].issue is None


def test_every_notice_key_is_in_the_catalogue() -> None:
    keys = {key for key in MESSAGES if key.startswith("state.")}

    assert keys == {
        "state.unreadable",
        "state.too_large",
        "state.not_utf8",
        "state.invalid",
        "state.version",
        "state.unwritable",
    }


# ---------------------------------------------------------------------------
# The atomic write
# ---------------------------------------------------------------------------


def test_a_failed_move_leaves_the_old_file_whole_and_no_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / DEFAULT_STATE
    save_state(path, STATE)
    before = path.read_bytes()
    moves: list[tuple[str, str]] = []

    def refuse(source: str, target: object) -> None:
        moves.append((str(source), str(target)))
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", refuse)

    with pytest.raises(OSError, match="No space"):
        save_state(path, EMPTY_STATE)

    assert len(moves) == 1
    assert moves[0][1] == str(path)
    assert path.read_bytes() == before
    assert [entry.name for entry in tmp_path.iterdir()] == [DEFAULT_STATE]


def test_a_failed_write_leaves_the_old_file_whole_and_no_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / DEFAULT_STATE
    save_state(path, STATE)
    before = path.read_bytes()

    def refuse(descriptor: int) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "fsync", refuse)

    with pytest.raises(OSError, match="Input/output"):
        save_state(path, EMPTY_STATE)

    assert path.read_bytes() == before
    assert [entry.name for entry in tmp_path.iterdir()] == [DEFAULT_STATE]


def test_the_temporary_file_is_in_the_same_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / DEFAULT_STATE
    moves: list[Path] = []
    real = os.replace

    def record(source: str, target: str) -> None:
        moves.append(Path(source))
        real(source, target)

    monkeypatch.setattr(os, "replace", record)

    save_state(path, STATE)

    assert [source.parent for source in moves] == [tmp_path]
    assert moves[0] != path
    assert load_state(path) == (STATE, None)


def test_a_state_in_a_missing_directory_cannot_be_written(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        save_state(tmp_path / "missing" / DEFAULT_STATE, STATE)


# ---------------------------------------------------------------------------
# The parts kept for a run
# ---------------------------------------------------------------------------


def test_a_channel_no_longer_listed_loses_its_part() -> None:
    kept = reset_for(STATE, [Channel.TELEGRAM, Channel.GITHUB], "owner/name")

    assert list(kept.channels) == [Channel.GITHUB, Channel.TELEGRAM]
    assert kept.channels[Channel.GITHUB] == STATE.channels[Channel.GITHUB]
    assert kept.error_runs == STATE.error_runs


@pytest.mark.parametrize(
    "repository", ["owner/other", "Owner/Name", None], ids=["other", "case", "none"]
)
def test_a_github_part_of_another_repository_is_reset(repository: str | None) -> None:
    kept = reset_for(STATE, [Channel.GITHUB, Channel.TELEGRAM], repository)

    assert kept.channels[Channel.GITHUB] == ChannelState()
    assert kept.channels[Channel.TELEGRAM] == STATE.channels[Channel.TELEGRAM]


def test_no_channel_listed_keeps_no_part_and_the_error_count() -> None:
    kept = reset_for(STATE, [], None)

    assert kept == State(STATE.error_runs, {})


def test_a_date_survives_as_an_aware_moment(tmp_path: Path) -> None:
    path = tmp_path / DEFAULT_STATE
    moment = datetime(2027, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=-3)))
    finding = Finding("domain:a.example", "a.example", "expiring", None, moment.astimezone(UTC), 7)

    save_state(path, State({}, {Channel.SMTP: ChannelState({finding.key: finding})}))
    state, _ = load_state(path)

    assert state.channels[Channel.SMTP].told[finding.key].expires_at == moment
