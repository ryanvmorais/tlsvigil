"""
The state between runs: one versioned JSON file, read with a cap, written atomically (spec 005).

The file keeps how many runs in a row each entry was ``error`` and, per channel, what that channel
was last told (RF-07, ADR-2). :func:`load_state` never fails: a file that is missing, unreadable,
too large, not UTF-8, not JSON, of another version or of a wrong shape anywhere is read as the
empty state, so a lost state repeats a warning and never hides one. :func:`save_state` writes a
temporary file in the same directory and moves it over the old one with :func:`os.replace`, so a
run interrupted while writing leaves the old file or the new one, never a partial file.

The file holds names, statuses and dates only: no token, password, chat or address (RNF-03).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from tlsvigil.alerts import PAST_DUE, REMINDER_DAYS, Finding
from tlsvigil.config import Channel

__all__ = [
    "DEFAULT_STATE",
    "EMPTY_STATE",
    "MAX_STATE_BYTES",
    "STATE_VERSION",
    "ChannelState",
    "Notice",
    "State",
    "load_state",
    "reset_for",
    "save_state",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Raised only when the shape of the file changes; a file of another version reads as empty.
STATE_VERSION: Final = 1

# Next to the workflow's checkout, hidden; `--state PATH` chooses another.
DEFAULT_STATE: Final = ".tlsvigil-state.json"

# Far above any real state (a few hundred bytes per entry), so a cap only stops a file that is
# not TLSVigil's.
MAX_STATE_BYTES: Final = 1024 * 1024

_STATUSES: Final = frozenset({"expiring", "invalid", "error"})
_REMINDERS: Final = frozenset({*REMINDER_DAYS, PAST_DUE})
_KEY_PREFIXES: Final = ("certificate:", "domain:")
_FINDING_KEYS: Final = frozenset({"name", "status", "reason", "expires_at", "reminder"})
_GITHUB_KEYS: Final = frozenset({"told", "repository", "issue", "author"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelState:
    """
    What one channel was last told.

    Attributes:
        told (Mapping[str, Finding]): The findings by key, in the order of the configuration.
        repository (str | None): ``github`` only: the repository the issue belongs to.
        issue (int | None): ``github`` only: the number of TLSVigil's open issue.
        author (str | None): ``github`` only: the login that opened it.
    """

    told: Mapping[str, Finding] = field(default_factory=dict)
    repository: str | None = None
    issue: int | None = None
    author: str | None = None


@dataclass(frozen=True, slots=True)
class State:
    """
    Everything one run hands the next.

    Attributes:
        error_runs (Mapping[str, int]): The ``error`` runs in a row, by key; entries with 0 are
            left out.
        channels (Mapping[Channel, ChannelState]): The part of each channel; a channel without
            one was told nothing.
    """

    error_runs: Mapping[str, int] = field(default_factory=dict)
    channels: Mapping[Channel, ChannelState] = field(default_factory=dict)


# The state of the first run.
EMPTY_STATE: Final = State()


@dataclass(frozen=True, slots=True)
class Notice:
    """
    Why a state file was read as empty: a sentence of the catalogue for the standard error.

    Attributes:
        key (str): ``state.<problem>``.
        params (Mapping[str, str]): The fields of the sentence: ``path``, and ``limit`` for a
            file over the cap.
    """

    key: str
    params: Mapping[str, str]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class _Invalid(Exception):
    """Raised inside the validation when the document deviates anywhere; never escapes."""


def _object(value: object) -> dict[str, object]:
    """
    Check that a JSON value is an object.

    Args:
        value (object): The value.

    Returns:
        dict[str, object]: The same value.

    Raises:
        _Invalid: When it is not an object.
    """
    if not isinstance(value, dict):
        raise _Invalid
    return value


def _optional_text(value: object) -> str | None:
    """
    Check that a JSON value is a non-empty string or ``null``.

    Args:
        value (object): The value.

    Returns:
        str | None: The same value.

    Raises:
        _Invalid: When it is neither.
    """
    if value is None or (isinstance(value, str) and value):
        return value
    raise _Invalid


def _moment(value: object) -> datetime:
    """
    Read an aware ISO 8601 moment.

    Args:
        value (object): The value.

    Returns:
        datetime: The moment, in UTC.

    Raises:
        _Invalid: When it is not a string, not ISO 8601 or not aware.
    """
    if not isinstance(value, str):
        raise _Invalid
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise _Invalid from None
    if moment.utcoffset() is None:
        raise _Invalid
    return moment.astimezone(UTC)


def _finding(key: str, value: object) -> Finding:
    """
    Read one finding a channel was told, checking each field against its status.

    Args:
        key (str): The key it is filed under.
        value (object): The JSON object.

    Returns:
        Finding: The finding.

    Raises:
        _Invalid: When a key is missing or unknown, or a field does not fit the status.
    """
    document = _object(value)
    if set(document) != _FINDING_KEYS or not key.startswith(_KEY_PREFIXES):
        raise _Invalid
    name, status = document["name"], document["status"]
    reason, expires_at, reminder = document["reason"], document["expires_at"], document["reminder"]
    if not isinstance(name, str) or not name or status not in _STATUSES:
        raise _Invalid
    if status == "expiring":
        if reason is not None:
            raise _Invalid
        # `type(...) is int` and not isinstance: bool is a subclass of int.
        if reminder is not None and not (type(reminder) is int and reminder in _REMINDERS):
            raise _Invalid
        return Finding(key, name, "expiring", None, _moment(expires_at), reminder)
    if expires_at is not None or reminder is not None:
        raise _Invalid
    text = _optional_text(reason)
    if text is None:
        raise _Invalid
    return Finding(key, name, str(status), text)


def _told(value: object) -> dict[str, Finding]:
    """
    Read the findings of one channel.

    Args:
        value (object): The JSON object, by key.

    Returns:
        dict[str, Finding]: The findings, in the order of the file.

    Raises:
        _Invalid: When it is not an object or a finding is invalid.
    """
    return {key: _finding(key, entry) for key, entry in _object(value).items()}


def _channel(channel: Channel, value: object) -> ChannelState:
    """
    Read the part of one channel.

    Args:
        channel (Channel): The channel.
        value (object): The JSON object.

    Returns:
        ChannelState: The part.

    Raises:
        _Invalid: When a key is missing or unknown, or a field has a wrong type.
    """
    document = _object(value)
    if channel is not Channel.GITHUB:
        if set(document) != {"told"}:
            raise _Invalid
        return ChannelState(_told(document["told"]))
    if set(document) != _GITHUB_KEYS:
        raise _Invalid
    issue = document["issue"]
    if issue is not None and not (type(issue) is int and issue > 0):
        raise _Invalid
    return ChannelState(
        _told(document["told"]),
        _optional_text(document["repository"]),
        issue,
        _optional_text(document["author"]),
    )


def _state(value: object) -> State:
    """
    Read and validate a whole state document.

    Args:
        value (object): The parsed JSON.

    Returns:
        State: The state.

    Raises:
        _Invalid: When anything deviates; the caller tells the version apart first.
    """
    document = _object(value)
    if set(document) != {"version", "error_runs", "channels"}:
        raise _Invalid
    version = document["version"]
    if type(version) is not int or version != STATE_VERSION:
        raise _Invalid
    error_runs: dict[str, int] = {}
    for key, runs in _object(document["error_runs"]).items():
        if not key.startswith(_KEY_PREFIXES) or type(runs) is not int or runs < 1:
            raise _Invalid
        error_runs[key] = runs
    channels: dict[Channel, ChannelState] = {}
    for name, part in _object(document["channels"]).items():
        try:
            channel = Channel(name)
        except ValueError:
            raise _Invalid from None
        channels[channel] = _channel(channel, part)
    return State(error_runs, channels)


def load_state(path: Path) -> tuple[State, Notice | None]:
    """
    Read the state file, or the empty state when it cannot be used (RF-07; ADR-2).

    At most :data:`MAX_STATE_BYTES` are read, the pattern of the configuration (spec 004, ADR-6).

    Args:
        path (Path): The file, :data:`DEFAULT_STATE` or the one given by ``--state``.

    Returns:
        tuple[State, Notice | None]: The state; and ``None`` when it was read or the file does
            not exist (the first run is silent), or why it was read as empty otherwise.
    """
    shown = str(path)

    def empty(key: str, **params: str) -> tuple[State, Notice]:
        return EMPTY_STATE, Notice(f"state.{key}", {"path": shown, **params})

    try:
        with path.open("rb") as file:
            # One byte over the cap tells a file at the cap from a larger one.
            data = file.read(MAX_STATE_BYTES + 1)
    except FileNotFoundError:
        return EMPTY_STATE, None
    except OSError:
        # A directory, a file without permission (Windows refuses a directory this way too),
        # a device error: the reason is the operating system's, the answer the same.
        return empty("unreadable")

    if len(data) > MAX_STATE_BYTES:
        return empty("too_large", limit=str(MAX_STATE_BYTES // 1024))
    try:
        document = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError:
        return empty("not_utf8")
    except ValueError:
        return empty("invalid")

    version = document.get("version") if isinstance(document, dict) else None
    # A version this release does not know is told apart from a broken file: a newer TLSVigil
    # wrote it, and the next run of this one overwrites it. `True == 1`, so the type counts.
    if isinstance(version, int) and not (type(version) is int and version == STATE_VERSION):
        return empty("version")
    try:
        return _state(document), None
    except _Invalid:
        return empty("invalid")


def reset_for(state: State, channels: Iterable[Channel], repository: str | None) -> State:
    """
    Keep only the parts of the channels listed, and drop a GitHub part of another repository.

    Args:
        state (State): The state as read.
        channels (Iterable[Channel]): The channels the configuration lists.
        repository (str | None): ``GITHUB_REPOSITORY``, ``owner/name``; ``None`` when GitHub is
            not listed.

    Returns:
        State: A channel no longer listed loses its part, and a GitHub part whose repository is
            not ``repository`` is reset, so an issue number is never used in another repository.
    """
    listed = set(channels)
    kept: dict[Channel, ChannelState] = {}
    for channel, part in state.channels.items():
        if channel not in listed:
            continue
        if channel is Channel.GITHUB and part.repository != repository:
            part = ChannelState()
        kept[channel] = part
    return replace(state, channels=kept)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _finding_object(finding: Finding) -> dict[str, object]:
    """
    Give the JSON object of one finding, without its key (the key of the object holds it).

    Args:
        finding (Finding): The finding.

    Returns:
        dict[str, object]: The fields, the date in ISO 8601 with its offset.
    """
    expires_at = None if finding.expires_at is None else finding.expires_at.isoformat()
    return {
        "name": finding.name,
        "status": finding.status,
        "reason": finding.reason,
        "expires_at": expires_at,
        "reminder": finding.reminder,
    }


def _channel_object(channel: Channel, part: ChannelState) -> dict[str, object]:
    """
    Give the JSON object of one channel's part.

    Args:
        channel (Channel): The channel.
        part (ChannelState): Its part.

    Returns:
        dict[str, object]: ``told``, and for GitHub the repository, the issue and its author.
    """
    told = {key: _finding_object(finding) for key, finding in part.told.items()}
    if channel is not Channel.GITHUB:
        return {"told": told}
    return {
        "repository": part.repository,
        "issue": part.issue,
        "author": part.author,
        "told": told,
    }


def save_state(path: Path, state: State) -> None:
    """
    Write the state file atomically (RF-07; ADR-2).

    The JSON goes to a temporary file in the same directory (so the move stays on one file
    system), is flushed to disk and is moved over the old file with :func:`os.replace`.

    Args:
        path (Path): The file.
        state (State): What this run hands the next.

    Raises:
        OSError: When the file cannot be written or moved; the old file is left whole and the
            temporary one is removed.
    """
    document = {
        "version": STATE_VERSION,
        "error_runs": dict(state.error_runs),
        "channels": {
            str(channel): _channel_object(channel, part) for channel, part in state.channels.items()
        },
    }
    data = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    directory = path.parent
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        # A failed write or move leaves no stray file next to the state.
        Path(temporary).unlink(missing_ok=True)
        raise
