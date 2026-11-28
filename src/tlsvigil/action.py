"""
The glue of the GitHub Action in ``action.yml`` (spec 006); not a command of its own.

The Action's Check step runs this module with the runner's Python, from the source of the commit
the workflow pinned, and installs nothing (ADR-1). Each input of the step reaches the module as an
environment variable named ``TLSVIGIL_INPUT_<NAME>``, so no value is ever written into the text of
a shell script (RNF-02). :func:`read_inputs` validates them before any check, and
:func:`channel_environment` turns the channel inputs into the variables spec 005 reads (ADR-8).
:func:`step_result` turns the command's exit code into the result of the step: a warning that was
delivered is not a failed run (RF-04).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from tlsvigil.cli import EXIT_ATTENTION, EXIT_OK
from tlsvigil.i18n import Lang, parse_lang

__all__ = [
    "CHANNEL_INPUTS",
    "INPUTS",
    "INPUT_PREFIX",
    "MAX_SUMMARY_BYTES",
    "SECRET_INPUTS",
    "STATE_DIR",
    "STATE_FILE",
    "InputError",
    "Inputs",
    "channel_environment",
    "input_variable",
    "read_inputs",
    "step_result",
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
