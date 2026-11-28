"""
The glue of the GitHub Action in ``action.yml`` (spec 006); not a command of its own.

The Action's Check step runs this module with the runner's Python, from the source of the commit
the workflow pinned, and installs nothing (ADR-1). Each input of the step reaches the module as an
environment variable named ``TLSVIGIL_INPUT_<NAME>``, so no value is ever written into the text of
a shell script (RNF-02). :func:`read_inputs` validates them before any check, and
:func:`channel_environment` turns the channel inputs into the variables spec 005 reads (ADR-8).
:func:`step_result` turns the command's exit code into the result of the step: a warning that was
delivered is not a failed run (RF-04).

:func:`main` runs ``tlsvigil check`` in the same process (ADR-3), between ``::stop-commands::``
and its resume, so no line of the output is read as a workflow command, and copies the output into
the run's summary inside a code block no line can close (ADR-5).
"""

from __future__ import annotations

import io
import os
import re
import secrets
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO, cast

from tlsvigil import cli
from tlsvigil.cli import EXIT_ATTENTION, EXIT_OK, EXIT_USAGE
from tlsvigil.i18n import Lang, parse_lang, translate
from tlsvigil.state import DEFAULT_STATE

__all__ = [
    "CHANNEL_INPUTS",
    "INPUTS",
    "INPUT_PREFIX",
    "MAX_SUMMARY_BYTES",
    "SECRET_INPUTS",
    "STATE_DIR",
    "STATE_FILE",
    "Check",
    "InputError",
    "Inputs",
    "channel_environment",
    "input_variable",
    "main",
    "read_inputs",
    "step_result",
    "summary",
]

# ---------------------------------------------------------------------------
# The inputs (RF-02, RF-03; ADR-8)
# ---------------------------------------------------------------------------

# The step passes the input "fail-on-attention" as TLSVIGIL_INPUT_FAIL_ON_ATTENTION. Not the
# runner's own INPUT_* names: a composite Action does not get those, and the prefix keeps these
# names apart from anything the step's env: may already hold.
INPUT_PREFIX: Final = "TLSVIGIL_INPUT_"

# The directory the cache step restores and saves (ADR-4), and the state file of spec 005 in it.
STATE_DIR: Final = "TLSVIGIL_STATE_DIR"
STATE_FILE: Final = "state.json"

# GitHub takes up to 1 MiB of summary per step; half of it leaves room for the heading and the
# fence, and is far more than a report of the largest configuration (RF-06).
MAX_SUMMARY_BYTES: Final = 512 * 1024

# Each channel input and the variable of spec 005, RF-03, it becomes.
CHANNEL_INPUTS: Final[Mapping[str, str]] = {
    "github-token": "GITHUB_TOKEN",
    "telegram-token": "TLSVIGIL_TELEGRAM_TOKEN",
    "telegram-chat-id": "TLSVIGIL_TELEGRAM_CHAT_ID",
    "smtp-username": "TLSVIGIL_SMTP_USERNAME",
    "smtp-password": "TLSVIGIL_SMTP_PASSWORD",
    "smtp-from": "TLSVIGIL_SMTP_FROM",
    "smtp-to": "TLSVIGIL_SMTP_TO",
}

# The inputs whose values the log must hide; the secret store masks its own values already, and
# these cover a token that came from somewhere else (RNF-03).
SECRET_INPUTS: Final = frozenset({"github-token", "telegram-token", "smtp-password"})

# Every input of action.yml, in its order; a test holds action.yml and the READMEs to this tuple.
INPUTS: Final = ("config", "lang", "notify", "fail-on-attention", *CHANNEL_INPUTS)

# The two spellings of a boolean input, read without regard to case like GitHub's own `==`.
_BOOLEANS: Final = {"true": True, "false": False}


@dataclass(frozen=True)
class Inputs:
    """
    The validated inputs of the step (RF-02).

    Attributes:
        config (str | None): The configuration's path, relative to the workspace; ``None`` for
            the command's default, ``tlsvigil.toml``.
        lang (Lang | None): The language of the run; ``None`` for the file's ``lang``.
        notify (bool): Whether the run delivers to the channels and keeps the state.
        fail_on_attention (bool): Whether exit ``1`` fails the step (RF-04).
        secrets (tuple[str, ...]): The non-empty values of :data:`SECRET_INPUTS`, to be masked.
    """

    config: str | None
    lang: Lang | None
    notify: bool
    fail_on_attention: bool
    secrets: tuple[str, ...]


class InputError(Exception):
    """
    One or more inputs are outside their form; no check may run (RF-02).

    Attributes:
        problems (tuple[tuple[str, str], ...]): Each problem as the input's name and a key of
            :data:`tlsvigil.i18n.MESSAGES`, in the order of :data:`INPUTS`; never the value.
        lang (Lang | None): The ``lang`` input when it was valid, for writing the problems.
    """

    def __init__(self, problems: Sequence[tuple[str, str]], lang: Lang | None) -> None:
        """
        Keep every problem and the language they may be written in.

        Args:
            problems (Sequence[tuple[str, str]]): The name and the catalogue key of each problem.
            lang (Lang | None): The ``lang`` input when it was valid.
        """
        super().__init__(", ".join(name for name, _ in problems))
        self.problems = tuple(problems)
        self.lang = lang


def input_variable(name: str) -> str:
    """
    Give the environment variable the step passes an input in.

    Args:
        name (str): The input's name in ``action.yml``, such as ``fail-on-attention``.

    Returns:
        str: The variable, such as ``TLSVIGIL_INPUT_FAIL_ON_ATTENTION``.
    """
    return INPUT_PREFIX + name.upper().replace("-", "_")


def _value(environ: Mapping[str, str], name: str) -> str:
    """
    Read one input, stripped; a missing variable reads as an empty input.

    Args:
        environ (Mapping[str, str]): The step's environment.
        name (str): The input's name.

    Returns:
        str: The value without surrounding whitespace.
    """
    return environ.get(input_variable(name), "").strip()


def read_inputs(environ: Mapping[str, str]) -> Inputs:
    """
    Read and validate every input, before any check (RF-02).

    The channel inputs are not validated here: spec 005, RF-03, validates the variables they
    become, with messages that name the variable.

    Args:
        environ (Mapping[str, str]): The step's environment.

    Returns:
        Inputs: The validated inputs.

    Raises:
        InputError: With every input outside its form.
    """
    problems: list[tuple[str, str]] = []

    config = _value(environ, "config") or None

    lang_text = _value(environ, "lang")
    lang = parse_lang(lang_text) if lang_text else None
    if lang_text and lang is None:
        problems.append(("lang", "action.input_lang"))

    flags: dict[str, bool] = {}
    for name in ("notify", "fail-on-attention"):
        # An empty boolean is a problem too: action.yml gives both a default, so an empty value
        # was written on purpose and its meaning would be a guess.
        flag = _BOOLEANS.get(_value(environ, name).lower())
        if flag is None:
            problems.append((name, "action.input_boolean"))
        else:
            flags[name] = flag

    if problems:
        raise InputError(problems, lang)

    secrets = tuple(value for name in sorted(SECRET_INPUTS) if (value := _value(environ, name)))
    return Inputs(
        config=config,
        lang=lang,
        notify=flags["notify"],
        fail_on_attention=flags["fail-on-attention"],
        secrets=secrets,
    )


def channel_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """
    Build the environment the command reads its channels' settings from (RF-03; ADR-8).

    Args:
        environ (Mapping[str, str]): The step's environment.

    Returns:
        dict[str, str]: A copy of ``environ`` without any ``TLSVIGIL_INPUT_*`` name, where each
            non-empty channel input sets its variable over an inherited one; an empty input sets
            nothing, so a variable the step's ``env:`` set is kept.
    """
    result = {key: value for key, value in environ.items() if not key.startswith(INPUT_PREFIX)}
    for name, variable in CHANNEL_INPUTS.items():
        if value := _value(environ, name):
            result[variable] = value
    return result


# ---------------------------------------------------------------------------
# The result of the step (RF-04)
# ---------------------------------------------------------------------------


def step_result(code: int, fail_on_attention: bool) -> int:
    """
    Turn the command's exit code into the exit code of the step.

    Args:
        code (int): The exit code of ``tlsvigil check``.
        fail_on_attention (bool): The ``fail-on-attention`` input.

    Returns:
        int: ``0`` for ``0``, and for ``1`` unless ``fail_on_attention``: the warning is the
            issue or the message, not a red run. Any other code is returned as it is, so the
            step fails and the log keeps the command's own code.
    """
    if code == EXIT_OK or (code == EXIT_ATTENTION and not fail_on_attention):
        return EXIT_OK
    return code


# ---------------------------------------------------------------------------
# The copy of the output (RF-06; ADR-3)
# ---------------------------------------------------------------------------


class _Copy:
    """
    The output of the run, both streams in the order written, up to a size in bytes.

    Attributes:
        cut (bool): Whether some of the output did not fit.
    """

    def __init__(self, limit: int) -> None:
        """
        Start an empty copy.

        Args:
            limit (int): The most bytes the copy keeps, counted in UTF-8.
        """
        self._parts: list[str] = []
        self._room = limit
        self.cut = False

    def keep(self, text: str) -> None:
        """
        Add a text, or the part of it that fits; nothing is added once the copy is full.

        Args:
            text (str): What a stream was given.
        """
        data = text.encode("utf-8", "replace")
        if len(data) <= self._room:
            self._parts.append(text)
            self._room -= len(data)
            return
        # The part that fits; a character split at the edge is dropped, and text() drops the
        # rest of that line anyway.
        self._parts.append(data[: self._room].decode("utf-8", "ignore"))
        self._room = 0
        self.cut = True

    def text(self) -> str:
        """
        Give the copy; a cut one ends at the last whole line that fit.

        Returns:
            str: The output as it was written.
        """
        whole = "".join(self._parts)
        if self.cut:
            whole = whole[: whole.rfind("\n") + 1]
        return whole


class _Tee(io.TextIOBase):
    """A standard stream that writes to the real one and keeps a copy (ADR-3)."""

    def __init__(self, stream: TextIO, copy: _Copy) -> None:
        """
        Wrap a stream.

        Args:
            stream (TextIO): The real stream, which the log of the step reads.
            copy (_Copy): The copy both streams share, so it keeps their order.
        """
        super().__init__()
        self._stream = stream
        self._copy = copy

    def write(self, text: str, /) -> int:
        """
        Write a text to the real stream and keep it in the copy.

        Args:
            text (str): The text.

        Returns:
            int: Its length, as a stream gives it.
        """
        self._copy.keep(text)
        self._stream.write(text)
        return len(text)

    def flush(self) -> None:
        """Flush the real stream."""
        self._stream.flush()

    def fileno(self) -> int:
        """
        Give the real stream's descriptor, which the command points at the null device when the
        reader has gone.

        Returns:
            int: The descriptor.
        """
        return self._stream.fileno()

    def close(self) -> None:
        """
        Leave the real stream as it is: the tee does not own it.

        The collector closes the tee long after the run; the default close would flush the real
        stream then, which may be closed by that time.
        """


# The longest run of backticks a text holds decides the fence around it.
_BACKTICKS: Final = re.compile("`+")


def summary(text: str, cut: bool, lang: Lang) -> str:
    """
    Write the summary of the run: a heading and the output in one fenced code block (ADR-5).

    The fence is one backtick longer than the longest run of backticks in the block, and never
    shorter than three, so no line can close it (CommonMark, "Fenced code blocks"), and nothing
    inside is read as Markdown, HTML, a link or a mention.

    Args:
        text (str): The copy of the output.
        cut (bool): Whether the copy was cut; the block then ends with ``action.summary_cut``.
        lang (Lang): The language of that line.

    Returns:
        str: The Markdown to append to the summary file.
    """
    body = text if not text or text.endswith("\n") else text + "\n"
    if cut:
        body += translate(lang, "action.summary_cut") + "\n"
    longest = max((len(run) for run in _BACKTICKS.findall(body)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"### TLSVigil\n\n{fence}\n{body}{fence}\n"


# ---------------------------------------------------------------------------
# The run (RF-01, RF-04 to RF-06; ADR-3, ADR-5)
# ---------------------------------------------------------------------------

# The command as the module calls it: the arguments and the environment of the channels in, the
# exit code out. The tests pass one with the command's own seams (spec 004, ADR-7).
Check = Callable[[Sequence[str], Mapping[str, str]], int]


def _check(argv: Sequence[str], environ: Mapping[str, str]) -> int:
    """
    Run ``tlsvigil check`` in this process (ADR-3).

    Args:
        argv (Sequence[str]): The arguments, ``check`` first.
        environ (Mapping[str, str]): Where the channels read their variables.

    Returns:
        int: The command's exit code.
    """
    return cli.main(argv, environ=environ)


def _command(line: str) -> None:
    """
    Print one workflow command and flush it, so it reaches the log before what follows.

    Args:
        line (str): The command, such as ``::add-mask::value``.
    """
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _append(environ: Mapping[str, str], variable: str, text: str) -> None:
    """
    Append a text to the file a variable of the runner names; nothing when it is not set.

    Args:
        environ (Mapping[str, str]): The step's environment.
        variable (str): ``GITHUB_STEP_SUMMARY`` or ``GITHUB_OUTPUT``.
        text (str): What to append.
    """
    if path := environ.get(variable):
        # newline="" writes "\n" as it is, on Windows too.
        with open(path, "a", encoding="utf-8", errors="replace", newline="") as file:
            file.write(text)


def _state_path(environ: Mapping[str, str]) -> Path:
    """
    Give the state file of the run (RF-05; ADR-4).

    Args:
        environ (Mapping[str, str]): The step's environment.

    Returns:
        Path: ``state.json`` in the directory the cache restores and saves; outside Actions,
            where that variable is not set, the command's own default file.
    """
    if directory := environ.get(STATE_DIR):
        return Path(directory) / STATE_FILE
    return Path(DEFAULT_STATE)


def _arguments(inputs: Inputs, state: Path | None) -> list[str]:
    """
    Build the command's arguments as a list, never as the text of a shell script (RNF-02).

    Args:
        inputs (Inputs): The validated inputs.
        state (Path | None): The state file with ``notify``; ``None`` without it.

    Returns:
        list[str]: The arguments, ``check`` first. Each value is joined to its option by ``=``,
            so a path that starts with ``-`` stays a value.
    """
    argv = ["check"]
    if inputs.config is not None:
        argv.append(f"--config={inputs.config}")
    if inputs.lang is not None:
        argv.append(f"--lang={inputs.lang}")
    if state is not None:
        argv += ["--notify", f"--state={state}"]
    return argv


def main(environ: Mapping[str, str] | None = None, *, check: Check = _check) -> int:
    """
    Run the Check step of the Action.

    Args:
        environ (Mapping[str, str] | None): The step's environment; ``None`` means
            :data:`os.environ`. Only the tests pass it.
        check (Check): Runs the command. Only the tests pass it, with the command's seams.

    Returns:
        int: The exit code of the step (RF-04): ``0`` when it succeeds, otherwise the command's
            code, or ``2`` for an invalid input.
    """
    environ = os.environ if environ is None else environ
    try:
        inputs = read_inputs(environ)
    except InputError as error:
        lang = error.lang or Lang.EN
        for name, key in error.problems:
            # The name and the sentence are the module's own; the value is never written.
            _command(f"::error title=TLSVigil::{translate(lang, key, name=name)}")
        _append(environ, "GITHUB_OUTPUT", f"exit-code={EXIT_USAGE}\n")
        return EXIT_USAGE

    # Before the commands stop: a mask inside the fenced part would be ignored. A mask covers
    # one line, so a value with several gets one per line.
    for secret in inputs.secrets:
        for line in secret.splitlines():
            if line:
                _command(f"::add-mask::{line}")
    # A fresh token, so no text of the run can know it and turn the commands back on (ADR-5).
    token = secrets.token_hex(16)
    _command(f"::stop-commands::{token}")

    copy = _Copy(MAX_SUMMARY_BYTES)
    out, err = sys.stdout, sys.stderr
    state: Path | None = None
    try:
        if inputs.notify:
            state = _state_path(environ)
            # The first run has no cache to restore, so no directory either.
            state.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = cast(TextIO, _Tee(out, copy))
        sys.stderr = cast(TextIO, _Tee(err, copy))
        code = check(_arguments(inputs, state), channel_environment(environ))
    finally:
        sys.stdout, sys.stderr = out, err
        err.flush()
        _command(f"::{token}::")

    lang = inputs.lang or Lang.EN
    _append(environ, "GITHUB_STEP_SUMMARY", summary(copy.text(), copy.cut, lang))
    outputs = f"exit-code={code}\n"
    if state is not None and state.is_file():
        outputs += "state=written\n"
    _append(environ, "GITHUB_OUTPUT", outputs)
    return step_result(code, inputs.fail_on_attention)
