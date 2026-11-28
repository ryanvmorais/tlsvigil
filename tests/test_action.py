"""
Tests for the glue of the GitHub Action (spec 006, RF-01 to RF-06, RNF-02, RNF-03).

The step's environment is a plain mapping handed to the functions, never ``os.environ``, so no
variable of the machine running the tests is read. The run's tests stand a fake in for the
command, or run the real one with its seams against the local TLS server and ``FakeGitHub``, so
nothing here reaches the network.
"""

from __future__ import annotations

import json
import re
import ssl
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
import trustme

import runs
from fake_apis import CHAT_ID, REPOSITORY, TELEGRAM_TOKEN, TOKEN, FakeGitHub
from http_server import LocalHTTPServer
from smtp_server import PASSWORD, USERNAME
from tls_server import LocalServer
from tlsvigil import action, cli
from tlsvigil.action import (
    CHANNEL_INPUTS,
    INPUT_PREFIX,
    INPUTS,
    MAX_SUMMARY_BYTES,
    STATE_DIR,
    STATE_FILE,
    Check,
    InputError,
    Inputs,
    channel_environment,
    input_variable,
    main,
    read_inputs,
    step_result,
    summary,
)
from tlsvigil.cli import (
    EXIT_ATTENTION,
    EXIT_DELIVERY,
    EXIT_INTERNAL,
    EXIT_OK,
    EXIT_USAGE,
)
from tlsvigil.i18n import MESSAGES, Lang

# The inputs as action.yml passes them by default: the two booleans set, the rest empty.
DEFAULTS: Mapping[str, str] = {
    input_variable("config"): "tlsvigil.toml",
    input_variable("lang"): "",
    input_variable("notify"): "true",
    input_variable("fail-on-attention"): "false",
    **{input_variable(name): "" for name in CHANNEL_INPUTS},
}

# A value planted in an invalid input, to be found in no message. No real token prefix, so the
# secret scanning of the repository is not triggered.
PLANTED = "planted-SECRET-v4lue"


def environ(**inputs: str) -> dict[str, str]:
    """
    Build the step's environment from the defaults and some inputs.

    Args:
        **inputs (str): Each input by its name with ``_`` for ``-``, such as
            ``fail_on_attention="true"``.

    Returns:
        dict[str, str]: The environment, as the Check step would see it.
    """
    result = dict(DEFAULTS)
    for name, value in inputs.items():
        result[input_variable(name.replace("_", "-"))] = value
    return result


# ---------------------------------------------------------------------------
# The names of the inputs
# ---------------------------------------------------------------------------


def test_an_input_is_passed_in_a_variable_of_its_own_prefix() -> None:
    assert input_variable("fail-on-attention") == "TLSVIGIL_INPUT_FAIL_ON_ATTENTION"
    assert input_variable("telegram-chat-id") == "TLSVIGIL_INPUT_TELEGRAM_CHAT_ID"


def test_the_inputs_are_the_four_options_then_the_channel_inputs() -> None:
    assert INPUTS == (
        "config",
        "lang",
        "notify",
        "fail-on-attention",
        "github-token",
        "telegram-token",
        "telegram-chat-id",
        "smtp-username",
        "smtp-password",
        "smtp-from",
        "smtp-to",
    )


# ---------------------------------------------------------------------------
# read_inputs (RF-02)
# ---------------------------------------------------------------------------


def test_the_defaults_read_as_the_default_file_notify_on_and_no_failure_on_attention() -> None:
    assert read_inputs(environ()) == Inputs(
        config="tlsvigil.toml",
        lang=None,
        notify=True,
        fail_on_attention=False,
        secrets=(),
    )


def test_an_empty_config_and_lang_leave_the_command_its_own_defaults() -> None:
    inputs = read_inputs(environ(config="  ", lang=""))

    assert inputs.config is None
    assert inputs.lang is None


def test_a_config_is_kept_as_written_apart_from_the_surrounding_whitespace() -> None:
    assert read_inputs(environ(config=" -sites/prod.toml ")).config == "-sites/prod.toml"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("en", Lang.EN), ("EN", Lang.EN), ("pt-BR", Lang.PT_BR), ("pt-br", Lang.PT_BR)],
)
def test_the_language_is_read_without_regard_to_case(text: str, expected: Lang) -> None:
    assert read_inputs(environ(lang=text)).lang is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("true", True), ("True", True), (" TRUE ", True), ("false", False), ("FALSE", False)],
)
def test_a_boolean_is_true_or_false_without_regard_to_case(text: str, expected: bool) -> None:
    inputs = read_inputs(environ(notify=text, fail_on_attention=text))

    assert inputs.notify is expected
    assert inputs.fail_on_attention is expected


@pytest.mark.parametrize("name", ["notify", "fail-on-attention"])
@pytest.mark.parametrize("text", ["", "yes", "1", "on", PLANTED])
def test_a_boolean_outside_true_and_false_is_a_problem_named_after_its_input(
    name: str, text: str
) -> None:
    env = environ()
    env[input_variable(name)] = text

    with pytest.raises(InputError) as caught:
        read_inputs(env)

    assert caught.value.problems == ((name, "action.input_boolean"),)


@pytest.mark.parametrize("text", ["pt", "es", "english", PLANTED])
def test_an_unknown_language_is_a_problem(text: str) -> None:
    with pytest.raises(InputError) as caught:
        read_inputs(environ(lang=text))

    assert caught.value.problems == (("lang", "action.input_lang"),)
    assert caught.value.lang is None


def test_every_problem_is_found_at_once_in_the_order_of_the_inputs() -> None:
    with pytest.raises(InputError) as caught:
        read_inputs(environ(lang="xx", notify="maybe", fail_on_attention="perhaps"))

    assert caught.value.problems == (
        ("lang", "action.input_lang"),
        ("notify", "action.input_boolean"),
        ("fail-on-attention", "action.input_boolean"),
    )


def test_a_valid_language_is_kept_for_writing_the_other_problems() -> None:
    with pytest.raises(InputError) as caught:
        read_inputs(environ(lang="pt-BR", notify="maybe"))

    assert caught.value.lang is Lang.PT_BR


def test_no_problem_holds_the_value_that_caused_it() -> None:
    with pytest.raises(InputError) as caught:
        read_inputs(environ(lang=PLANTED, notify=PLANTED, fail_on_attention=PLANTED))

    # Positive control: the three problems were found, so the texts below are the real ones.
    assert len(caught.value.problems) == 3
    texts = [str(caught.value)]
    for _, key in caught.value.problems:
        texts.extend(MESSAGES[key].values())
    assert all(PLANTED not in text and PLANTED.lower() not in text for text in texts)


def test_the_secrets_to_mask_are_the_non_empty_secret_inputs_only() -> None:
    inputs = read_inputs(
        environ(
            github_token=" gh-token-value ",
            telegram_token="",
            smtp_password="pass word",
            telegram_chat_id="-100123",
            smtp_from="alerts@example.com",
        )
    )

    assert sorted(inputs.secrets) == ["gh-token-value", "pass word"]


# ---------------------------------------------------------------------------
# channel_environment (RF-03)
# ---------------------------------------------------------------------------


def test_each_channel_input_becomes_the_variable_spec_005_reads() -> None:
    env = environ(**{name.replace("-", "_"): f"value of {name}" for name in CHANNEL_INPUTS})

    result = channel_environment(env)

    assert {variable: result[variable] for variable in CHANNEL_INPUTS.values()} == {
        variable: f"value of {name}" for name, variable in CHANNEL_INPUTS.items()
    }


def test_a_non_empty_input_replaces_a_variable_the_step_inherited() -> None:
    env = environ(telegram_token="from-the-input")
    env["TLSVIGIL_TELEGRAM_TOKEN"] = "from-the-env"

    assert channel_environment(env)["TLSVIGIL_TELEGRAM_TOKEN"] == "from-the-input"


def test_an_empty_input_keeps_a_variable_the_step_inherited() -> None:
    env = environ(telegram_token="")
    env["TLSVIGIL_TELEGRAM_TOKEN"] = "from-the-env"

    assert channel_environment(env)["TLSVIGIL_TELEGRAM_TOKEN"] == "from-the-env"


def test_an_empty_input_sets_no_variable() -> None:
    env = environ(telegram_token="  ", smtp_to="ops@example.com")

    result = channel_environment(env)

    # Positive control: a non-empty input of the same kind did set its variable.
    assert result["TLSVIGIL_SMTP_TO"] == "ops@example.com"
    assert "TLSVIGIL_TELEGRAM_TOKEN" not in result


def test_no_input_variable_reaches_the_command_and_the_rest_is_kept() -> None:
    env = environ(github_token="gh-token-value")
    env["GITHUB_REPOSITORY"] = "octo-org/site"
    env["PATH"] = "/usr/bin"

    result = channel_environment(env)

    # Positive control: the input names were there to be removed.
    assert any(key.startswith(INPUT_PREFIX) for key in env)
    assert not any(key.startswith(INPUT_PREFIX) for key in result)
    assert result["GITHUB_REPOSITORY"] == "octo-org/site"
    assert result["PATH"] == "/usr/bin"
    assert result["GITHUB_TOKEN"] == "gh-token-value"


def test_the_step_environment_is_not_changed() -> None:
    env = environ(github_token="gh-token-value")
    before = dict(env)

    channel_environment(env)

    assert env == before


# ---------------------------------------------------------------------------
# step_result (RF-04)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "fail_on_attention", "expected"),
    [
        (0, False, 0),
        (0, True, 0),
        (1, False, 0),
        (1, True, 1),
        (2, False, 2),
        (2, True, 2),
        (3, False, 3),
        (3, True, 3),
        (4, False, 4),
        (4, True, 4),
    ],
)
def test_the_step_fails_when_broken_or_undelivered_and_on_attention_only_when_asked(
    code: int, fail_on_attention: bool, expected: int
) -> None:
    assert step_result(code, fail_on_attention) == expected


# ---------------------------------------------------------------------------
# The run: the harness
# ---------------------------------------------------------------------------

# The resume line of ::stop-commands::, which repeats the token: 32 hexadecimal characters.
STOP = re.compile(r"::stop-commands::([0-9a-f]{32})")


@dataclass(frozen=True, slots=True)
class Step:
    """
    What one run of the Check step left.

    Attributes:
        code (int): The step's exit code.
        out (str): The standard output, which the log of the step shows.
        err (str): The standard error.
        summary (str | None): The summary file as bytes decoded; ``None`` when none was written.
        outputs (str | None): The outputs file, the same way.
    """

    code: int
    out: str
    err: str
    summary: str | None
    outputs: str | None

    def lines(self) -> list[str]:
        """
        Give the lines of the log.

        Returns:
            list[str]: The standard output, line by line.
        """
        return self.out.splitlines()

    def token(self) -> str:
        """
        Give the token of the one ``::stop-commands::`` line.

        Returns:
            str: The token.
        """
        (token,) = (match[1] for line in self.lines() if (match := STOP.fullmatch(line)))
        return token


class FakeCheck:
    """
    Stands for the command: records each call, writes what it is given, returns a code.

    Attributes:
        calls (list[tuple[list[str], dict[str, str]]]): The arguments and the environment of
            each call.
    """

    def __init__(
        self,
        code: int = EXIT_OK,
        writes: Sequence[tuple[str, str]] = (),
        *,
        state: bool = False,
        crash: bool = False,
    ) -> None:
        """
        Set what each call does.

        Args:
            code (int): The exit code to return.
            writes (Sequence[tuple[str, str]]): Each text and its stream, ``out`` or ``err``, in
                the order to write them.
            state (bool): Whether to write the file of ``--state=``, as a run with ``--notify``
                does.
            crash (bool): Whether to raise after the writes, as a bug would.
        """
        self.code = code
        self.writes = tuple(writes)
        self.state = state
        self.crash = crash
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv: Sequence[str], environ: Mapping[str, str]) -> int:
        """
        Run as the command would.

        Args:
            argv (Sequence[str]): The arguments.
            environ (Mapping[str, str]): The environment of the channels.

        Returns:
            int: The code given.

        Raises:
            RuntimeError: When ``crash`` was set.
        """
        self.calls.append((list(argv), dict(environ)))
        for stream, text in self.writes:
            (sys.stdout if stream == "out" else sys.stderr).write(text)
        if self.state:
            (path,) = (arg.removeprefix("--state=") for arg in argv if arg.startswith("--state="))
            Path(path).write_text("{}", encoding="utf-8")
        if self.crash:
            raise RuntimeError("a bug in the check")
        return self.code


# The function the step fixture gives: run(environ, check, files=True) -> Step.
RunStep = Callable[..., Step]


def _read(path: Path) -> str | None:
    """
    Read a file the step may have written, with its line ends as they are.

    Args:
        path (Path): The file.

    Returns:
        str | None: Its text, or ``None`` when it does not exist.
    """
    return path.read_bytes().decode("utf-8") if path.exists() else None


@pytest.fixture
def step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> RunStep:
    """
    Give a function that runs the Check step in ``tmp_path``, the runner's files there too.

    Args:
        tmp_path (Path): The working directory of the step.
        capsys (pytest.CaptureFixture[str]): Captures the log.
        monkeypatch (pytest.MonkeyPatch): Changes the directory.

    Returns:
        RunStep: ``run(environ, check, files=True)``; with ``files=False`` the environment has
            neither ``GITHUB_STEP_SUMMARY`` nor ``GITHUB_OUTPUT``, as outside Actions.
    """
    monkeypatch.chdir(tmp_path)
    summary_file, outputs_file = tmp_path / "summary.md", tmp_path / "outputs.txt"

    def run(environ: Mapping[str, str], check: Check | None, *, files: bool = True) -> Step:
        """
        Run the step once.

        Args:
            environ (Mapping[str, str]): The inputs and any other variable.
            check (Check | None): Stands for the command; ``None`` runs the module's own call
                of the real command.
            files (bool): Whether the runner's two files are named.

        Returns:
            Step: What the run left; each file holds this run's text only.
        """
        environment = dict(environ)
        if files:
            environment["GITHUB_STEP_SUMMARY"] = str(summary_file)
            environment["GITHUB_OUTPUT"] = str(outputs_file)
        summary_file.unlink(missing_ok=True)
        outputs_file.unlink(missing_ok=True)
        capsys.readouterr()
        code = main(environment) if check is None else main(environment, check=check)
        captured = capsys.readouterr()
        return Step(code, captured.out, captured.err, _read(summary_file), _read(outputs_file))

    return run


# ---------------------------------------------------------------------------
# The run: the workflow commands (RNF-03; ADR-5)
# ---------------------------------------------------------------------------


def test_the_masks_come_before_the_commands_stop_and_the_resume_comes_last(
    step: RunStep,
) -> None:
    check = FakeCheck(writes=[("out", "the report\n")])
    result = step(
        environ(
            notify="false",
            github_token="token-one",
            telegram_token="token-two",
            telegram_chat_id="-100200300",  # not a secret: never masked
            smtp_password="password-three",
        ),
        check,
    )

    token = result.token()
    assert result.lines() == [
        "::add-mask::token-one",
        "::add-mask::password-three",
        "::add-mask::token-two",
        f"::stop-commands::{token}",
        "the report",
        f"::{token}::",
    ]


def test_a_secret_on_several_lines_is_masked_line_by_line(step: RunStep) -> None:
    result = step(environ(notify="false", smtp_password="first\n\nsecond"), FakeCheck())

    # The empty line gets no mask: an empty mask would hide nothing.
    assert result.lines()[:3] == [
        "::add-mask::first",
        "::add-mask::second",
        f"::stop-commands::{result.token()}",
    ]


def test_the_tee_gives_the_descriptor_of_the_real_stream(tmp_path: Path) -> None:
    with open(tmp_path / "stream.txt", "w", encoding="utf-8") as stream:
        tee = action._Tee(stream, action._Copy(MAX_SUMMARY_BYTES))

        assert tee.fileno() == stream.fileno()


def test_the_commands_resume_after_a_crash_of_the_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out, err = sys.stdout, sys.stderr
    check = FakeCheck(writes=[("out", "half a report\n")], crash=True)

    with pytest.raises(RuntimeError, match="a bug in the check"):
        main(environ(notify="false"), check=check)

    lines = capsys.readouterr().out.splitlines()
    (token,) = (match[1] for line in lines if (match := STOP.fullmatch(line)))
    assert lines[-2:] == ["half a report", f"::{token}::"]
    assert (sys.stdout, sys.stderr) == (out, err)


def test_each_run_stops_the_commands_with_a_token_of_its_own(step: RunStep) -> None:
    first = step(environ(notify="false"), FakeCheck())
    second = step(environ(notify="false"), FakeCheck())

    assert first.token() != second.token()


# ---------------------------------------------------------------------------
# The run: the summary (RF-06; ADR-5)
# ---------------------------------------------------------------------------


def test_the_output_reaches_the_log_and_the_summary_in_the_order_written(
    step: RunStep,
) -> None:
    check = FakeCheck(writes=[("out", "one\n"), ("err", "two\n"), ("out", "three\n")])
    result = step(environ(notify="false"), check)

    assert "one\nthree\n" in result.out
    assert result.err == "two\n"
    assert result.summary == "### TLSVigil\n\n```\none\ntwo\nthree\n```\n"


def test_outside_text_sits_in_a_fence_that_no_line_can_close(step: RunStep) -> None:
    text = "`````\n<img src=x onerror=alert(1)> @octocat [a link](https://example.com)\n"
    result = step(environ(notify="false"), FakeCheck(writes=[("out", text)]))

    fence = "``````"  # six: one more than the run of five
    assert result.summary == f"### TLSVigil\n\n{fence}\n{text}{fence}\n"


@pytest.mark.parametrize(
    ("text", "fence"),
    [
        pytest.param("no backtick\n", 3, id="none"),
        pytest.param("a `` b\n", 3, id="two"),
        pytest.param("```\n", 4, id="three"),
        pytest.param("a ` b ```` c `` d\n", 5, id="the-longest-of-several"),
    ],
)
def test_the_fence_is_one_backtick_longer_than_the_longest_run_and_three_at_least(
    text: str, fence: int
) -> None:
    lines = summary(text, False, Lang.EN).splitlines()

    assert lines[2] == lines[-1] == "`" * fence


def test_a_text_without_a_final_line_end_still_closes_its_block() -> None:
    assert summary("last line", False, Lang.EN) == "### TLSVigil\n\n```\nlast line\n```\n"


@pytest.mark.parametrize(
    ("lang", "note"),
    [
        pytest.param(
            "",
            "[The summary was cut here; the whole output is in the log of the step.]",
            id="en",
        ),
        pytest.param(
            "pt-BR",
            "[O resumo foi cortado aqui; a saída inteira está no log do passo.]",
            id="pt-BR",
        ),
    ],
)
def test_a_summary_over_the_cap_ends_at_a_whole_line_with_the_note(
    step: RunStep, monkeypatch: pytest.MonkeyPatch, lang: str, note: str
) -> None:
    monkeypatch.setattr(action, "MAX_SUMMARY_BYTES", 20)
    check = FakeCheck(writes=[("out", "line one\nline two\nline three\n")])
    result = step(environ(notify="false", lang=lang), check)

    assert result.summary == f"### TLSVigil\n\n```\nline one\nline two\n{note}\n```\n"
    assert result.out.count("line three") == 1  # the log keeps the whole output


def test_a_summary_that_fills_the_cap_exactly_is_whole(
    step: RunStep, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(action, "MAX_SUMMARY_BYTES", len("line one\nline two\n"))
    result = step(environ(notify="false"), FakeCheck(writes=[("out", "line one\nline two\n")]))

    assert result.summary == "### TLSVigil\n\n```\nline one\nline two\n```\n"


def test_the_cap_counts_bytes_and_drops_a_character_split_at_the_edge(
    step: RunStep, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Each "ãã\n" is three characters and five bytes: by characters both would fit in eight,
    # by bytes the second is cut after three, inside its second "ã".
    monkeypatch.setattr(action, "MAX_SUMMARY_BYTES", 8)
    check = FakeCheck(writes=[("out", "ãã\n"), ("out", "ãã\n")])
    result = step(environ(notify="false"), check)

    note = "[The summary was cut here; the whole output is in the log of the step.]"
    assert result.summary == f"### TLSVigil\n\n```\nãã\n{note}\n```\n"


def test_without_the_runner_variables_no_file_is_written(step: RunStep, tmp_path: Path) -> None:
    check = FakeCheck(writes=[("out", "the report\n")])
    result = step(environ(notify="false"), check, files=False)

    assert len(check.calls) == 1
    assert "the report" in result.out
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# The run: the outputs and the result (RF-04, RF-05)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "fail_on_attention", "expected"),
    [
        pytest.param(EXIT_OK, "false", EXIT_OK, id="fine"),
        pytest.param(EXIT_ATTENTION, "false", EXIT_OK, id="attention"),
        pytest.param(EXIT_ATTENTION, "true", EXIT_ATTENTION, id="attention-fails"),
        pytest.param(EXIT_USAGE, "false", EXIT_USAGE, id="usage"),
        pytest.param(EXIT_INTERNAL, "false", EXIT_INTERNAL, id="bug"),
        pytest.param(EXIT_DELIVERY, "false", EXIT_DELIVERY, id="undelivered"),
    ],
)
def test_the_command_code_is_an_output_whatever_the_result_of_the_step(
    step: RunStep, code: int, fail_on_attention: str, expected: int
) -> None:
    check = FakeCheck(code)
    result = step(environ(notify="false", fail_on_attention=fail_on_attention), check)

    assert result.code == expected
    assert result.outputs == f"exit-code={code}\n"


def test_the_state_is_written_as_an_output_when_the_command_wrote_its_file(
    step: RunStep, tmp_path: Path
) -> None:
    state_dir = tmp_path / "temp" / "tlsvigil-state"
    result = step(environ() | {STATE_DIR: str(state_dir)}, FakeCheck(state=True))

    assert (state_dir / STATE_FILE).is_file()
    assert result.outputs == "exit-code=0\nstate=written\n"


def test_no_state_output_when_the_command_wrote_no_file(step: RunStep, tmp_path: Path) -> None:
    state_dir = tmp_path / "temp" / "tlsvigil-state"
    check = FakeCheck(EXIT_USAGE)
    result = step(environ() | {STATE_DIR: str(state_dir)}, check)

    assert len(check.calls) == 1
    assert state_dir.is_dir()  # made for the first run, which has no cache to restore
    assert not (state_dir / STATE_FILE).exists()
    assert result.outputs == "exit-code=2\n"


# ---------------------------------------------------------------------------
# The run: the arguments and the environment of the command (RF-01 to RF-03; RNF-02)
# ---------------------------------------------------------------------------


def test_with_notify_the_command_gets_notify_and_the_state_file_of_the_cache(
    step: RunStep, tmp_path: Path
) -> None:
    state_dir = tmp_path / "temp" / "tlsvigil-state"
    check = FakeCheck()
    step(environ(lang="PT-br") | {STATE_DIR: str(state_dir)}, check)

    ((argv, _),) = check.calls
    assert argv == [
        "check",
        "--config=tlsvigil.toml",
        "--lang=pt-BR",
        "--notify",
        f"--state={state_dir / STATE_FILE}",
    ]


def test_without_the_state_directory_the_command_default_file_is_used(
    step: RunStep,
) -> None:
    check = FakeCheck()
    step(environ(), check)

    ((argv, _),) = check.calls
    assert argv[-2:] == ["--notify", "--state=.tlsvigil-state.json"]


def test_without_notify_and_with_empty_inputs_the_command_gets_check_alone(
    step: RunStep,
) -> None:
    check = FakeCheck()
    step(environ(config="", notify="false"), check)

    ((argv, _),) = check.calls
    assert argv == ["check"]


def test_a_value_that_starts_with_a_dash_is_joined_to_its_option(step: RunStep) -> None:
    check = FakeCheck()
    step(environ(config="--json", notify="false"), check)

    ((argv, _),) = check.calls
    assert argv == ["check", "--config=--json"]


def test_a_config_that_starts_with_a_dash_reaches_the_command_as_a_value(
    step: RunStep,
) -> None:
    result = step(environ(config="--json", notify="false"), None)

    # The command looked for a file named --json: a missing file, not a flag and not a usage
    # error of argparse, which would have raised SystemExit.
    assert result.outputs == "exit-code=2\n"
    assert result.err.splitlines() == [
        "Nothing was checked: the configuration --json cannot be used.",
        "  the file --json does not exist",
    ]


def test_the_command_reads_the_channels_from_the_inputs_and_no_input_variable(
    step: RunStep,
) -> None:
    check = FakeCheck()
    step(environ(notify="false", github_token="from-the-input") | {"PATH": "kept"}, check)

    ((_, environment),) = check.calls
    assert environment["GITHUB_TOKEN"] == "from-the-input"
    assert environment["PATH"] == "kept"
    assert [name for name in environment if name.startswith(INPUT_PREFIX)] == []


def test_an_invalid_input_stops_the_step_before_the_command(step: RunStep) -> None:
    check = FakeCheck()
    result = step(environ(notify="maybe", lang="pt-BR", fail_on_attention=PLANTED), check)

    assert check.calls == []
    assert result.code == EXIT_USAGE
    assert result.lines() == [
        "::error title=TLSVigil::a entrada notify deve ser true ou false",
        "::error title=TLSVigil::a entrada fail-on-attention deve ser true ou false",
    ]
    assert result.outputs == "exit-code=2\n"
    assert result.summary is None


# ---------------------------------------------------------------------------
# The whole run, with the command's seams (RF-01, RF-05; RNF-03)
# ---------------------------------------------------------------------------


@dataclass
class Scheduled:
    """
    A repository whose workflow runs the step: one host under its threshold, the GitHub channel.

    Attributes:
        github (FakeGitHub): The issues of the repository.
        environ (dict[str, str]): The step's environment, the defaults of action.yml and the
            token.
        state (Path): The state file in the directory the cache restores.
        run (Callable[[], Step]): Runs the step once with the real command.
    """

    github: FakeGitHub
    environ: dict[str, str]
    state: Path
    run: Callable[[], Step]


@pytest.fixture
def scheduled(
    ca: trustme.CA,
    serve: Callable[..., LocalServer],
    client_context: ssl.SSLContext,
    https_server: LocalHTTPServer,
    tmp_path: Path,
    step: RunStep,
) -> Iterator[Scheduled]:
    """
    Give the repository, with a host whose certificate has 9 days left (a warning).

    Args:
        ca (trustme.CA): Issues the host's certificate.
        serve (Callable[..., LocalServer]): Starts the host.
        client_context (ssl.SSLContext): Trusts the test CA.
        https_server (LocalHTTPServer): Serves the fake GitHub API.
        tmp_path (Path): The workspace, with ``tlsvigil.toml``.
        step (RunStep): Runs the step.

    Yields:
        Scheduled: The repository; every request to the API had the right headers.
    """
    now = runs.now()
    server = serve(runs.leaf(ca, now, timedelta(days=9)))
    github = FakeGitHub()
    api = github.install(https_server)
    (tmp_path / "tlsvigil.toml").write_text(
        f'[certificates]\nhosts = ["localhost:{server.port}"]\n\n'
        '[notifications]\nchannels = ["github"]\n',
        encoding="utf-8",
    )
    state_dir = tmp_path / "runner-temp" / "tlsvigil-state"
    environment = environ(github_token=TOKEN) | {
        "GITHUB_REPOSITORY": REPOSITORY,
        STATE_DIR: str(state_dir),
    }

    def check(argv: Sequence[str], environ: Mapping[str, str]) -> int:
        return cli.main(argv, context=client_context, now=now, environ=environ, github_api_url=api)

    yield Scheduled(github, environment, state_dir / STATE_FILE, lambda: step(environment, check))
    assert github.problems == []


def test_a_first_run_opens_the_issue_and_a_second_run_with_its_state_adds_nothing(
    scheduled: Scheduled,
) -> None:
    first = scheduled.run()
    second = scheduled.run()

    assert first.code == second.code == EXIT_OK  # a warning delivered is not a failed run
    assert first.outputs == second.outputs == "exit-code=1\nstate=written\n"
    assert "github: sent (issue #1)" in first.lines()
    assert "github: nothing new (issue #1)" in second.lines()
    assert first.summary is not None and "github: sent (issue #1)" in first.summary
    assert list(scheduled.github.issues) == [1]
    assert scheduled.github.issues[1].comments == []
    saved = json.loads(scheduled.state.read_text(encoding="utf-8"))
    assert saved["channels"]["github"]["issue"] == 1


def test_a_planted_invalid_state_file_is_read_as_empty(scheduled: Scheduled) -> None:
    scheduled.state.parent.mkdir(parents=True)
    scheduled.state.write_text('{"version": 1, "channels": "planted"}', encoding="utf-8")

    result = scheduled.run()

    assert "github: sent (issue #1)" in result.lines()
    saved = json.loads(scheduled.state.read_text(encoding="utf-8"))
    assert saved["channels"]["github"]["issue"] == 1


def test_a_secret_in_every_input_reaches_no_output_summary_or_file(
    scheduled: Scheduled,
) -> None:
    values = {
        "telegram_token": TELEGRAM_TOKEN,
        "telegram_chat_id": CHAT_ID,
        "smtp_username": USERNAME,
        "smtp_password": PASSWORD,
        "smtp_from": "tlsvigil@example.com",
        "smtp_to": "first@example.org, second@example.org",
    }
    for name, value in values.items():
        scheduled.environ[input_variable(name.replace("_", "-"))] = value

    result = scheduled.run()

    # The token was used: the fake checks it on every request, and the issue was opened.
    assert list(scheduled.github.issues) == [1]
    private = [TOKEN, *values.values(), "first@example.org", "second@example.org"]
    forms = {form for value in private for form in (value, quote(value, safe=""))}
    masks = [line for line in result.lines() if line.startswith("::add-mask::")]
    # Control: the masks do hold the three secrets, so the search below finds what it seeks.
    assert masks == [
        f"::add-mask::{TOKEN}",
        f"::add-mask::{PASSWORD}",
        f"::add-mask::{TELEGRAM_TOKEN}",
    ]
    log = "\n".join(line for line in result.lines() if line not in masks)
    texts = [
        log,
        result.err,
        result.summary,
        result.outputs,
        scheduled.state.read_text("utf-8"),
    ]
    for text in texts:
        assert text is not None
        for form in forms:
            assert form not in text
