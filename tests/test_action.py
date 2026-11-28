"""
Tests for the glue of the GitHub Action (spec 006, RF-02 to RF-04).

The step's environment is a plain mapping handed to the functions, never ``os.environ``, so no
variable of the machine running the tests is read, and nothing here reaches the network.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from tlsvigil.action import (
    CHANNEL_INPUTS,
    INPUT_PREFIX,
    INPUTS,
    InputError,
    Inputs,
    channel_environment,
    input_variable,
    read_inputs,
    step_result,
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
