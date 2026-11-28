"""
Tests for the files of the GitHub Action and of its template (spec 006, RF-08 to RF-10, RF-12,
RNF-01, RNF-02, RNF-06; ADR-2, ADR-6, ADR-7).

``action.yml`` and the template's workflow are read with :func:`yaml.safe_load` for their structure
and as text for what the parser drops: the version comment after each pinned ``uses:`` and the
commented channel inputs. The template's ``tlsvigil.toml`` is loaded like any configuration, and
its two READMEs are compared by shape. The bootstrap of the Check step is
taken from the file itself and run in a subprocess, from a directory that holds a planted
``json.py``, so the test runs the very line a runner runs. Isolation: only the repository's files
are read; the subprocess stops at the inputs, before any check, so nothing reaches the network.
When the tests run from a tree without ``action.yml`` (an sdist, which has no ``template/``
either), they are skipped.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from tlsvigil.action import CHANNEL_INPUTS, INPUT_PREFIX, INPUTS, input_variable
from tlsvigil.config import Channel, load_config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The repository root, one level above the tests folder.
_ROOT = Path(__file__).resolve().parents[1]
_ACTION = _ROOT / "action.yml"
_SOURCE = _ROOT / "src"
_DATA = Path(__file__).resolve().parent / "data" / "action"
_TEMPLATE = _ROOT / "template"
_WORKFLOW = _TEMPLATE / ".github" / "workflows" / "tlsvigil.yml"

# Every test below reads action.yml, so the mark is shared.
pytestmark = pytest.mark.skipif(
    not _ACTION.is_file(), reason="action.yml is not in this source tree"
)

# A `uses:` line pinned to a full commit SHA and followed by its version comment (RNF-01): a tag
# or a branch can be moved to other code; a SHA cannot.
_PINNED = re.compile(r"^\s*(?:-\s+)?uses:\s+[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(?:\.\d+)*$")
_USES = re.compile(r"^\s*(?:-\s+)?uses:")

# The Actions the composite runs, in order; GitHub's own only (RNF-01).
_RESTORE = "actions/cache/restore@"
_SAVE = "actions/cache/save@"

# This Action, as the template names it: until the first release it pins a commit of main, then
# the commit of a release and its tag (ADR-7).
_OWN = "ryanvmorais/tlsvigil"
_SELF = re.compile(
    r"^\s*(?:-\s+)?uses:\s+" + re.escape(_OWN) + r"@[0-9a-f]{40} # (?:main|v\d+(?:\.\d+)*)$"
)

# A commented channel input of the template: its name and the secret it reads.
_OFFERED = re.compile(r"^\s*#\s+([a-z-]+): \$\{\{ secrets\.([A-Z_]+) \}\}$", re.MULTILINE)

# An ATX heading; the run of "#" is its level.
_HEADING = re.compile(r"^(#{1,6}) ")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _action() -> dict[str, Any]:
    """
    Read ``action.yml``.

    Returns:
        dict[str, Any]: The parsed file.
    """
    return cast(dict[str, Any], yaml.safe_load(_ACTION.read_text(encoding="utf-8")))


def _steps() -> list[dict[str, Any]]:
    """
    Give the steps of the composite Action.

    Returns:
        list[dict[str, Any]]: The steps, in order.
    """
    return cast(list[dict[str, Any]], _action()["runs"]["steps"])


def _step(prefix: str) -> dict[str, Any]:
    """
    Give the one step that uses an Action.

    Args:
        prefix (str): The start of its ``uses:``, up to the ``@``.

    Returns:
        dict[str, Any]: The step.
    """
    found = [step for step in _steps() if str(step.get("uses", "")).startswith(prefix)]
    assert len(found) == 1, prefix
    return found[0]


def _check_step() -> dict[str, Any]:
    """
    Give the Check step, the one with ``id: run``.

    Returns:
        dict[str, Any]: The step.
    """
    found = [step for step in _steps() if step.get("id") == "run"]
    assert len(found) == 1
    return found[0]


def _bootstrap(source: Path) -> list[str]:
    """
    Build the command line of the Check step's last line, with this test's Python.

    Args:
        source (Path): What ``$TLSVIGIL_SOURCE`` holds on a runner.

    Returns:
        list[str]: The arguments of the ``exec`` line, ``$python`` and ``$TLSVIGIL_SOURCE``
            replaced; the code after ``-c`` is the file's own.
    """
    lines = [line for line in _check_step()["run"].splitlines() if line.startswith("exec ")]
    assert len(lines) == 1
    words = shlex.split(lines[0])
    assert words[:2] == ["exec", "$python"]
    assert words[-1] == "$TLSVIGIL_SOURCE"
    return [sys.executable, *words[2:-1], str(source)]


def _run(argv: list[str], cwd: Path, output: Path) -> subprocess.CompletedProcess[str]:
    """
    Run the bootstrap with an invalid ``notify``, so the module stops at the inputs.

    Args:
        argv (list[str]): The command line, from :func:`_bootstrap`.
        cwd (Path): The working directory, the workspace of a runner.
        output (Path): The file ``GITHUB_OUTPUT`` names.

    Returns:
        subprocess.CompletedProcess[str]: The finished run, its streams captured.
    """
    environ = {name: value for name, value in os.environ.items() if not name.startswith("GITHUB_")}
    environ = {name: value for name, value in environ.items() if not name.startswith(INPUT_PREFIX)}
    environ[input_variable("notify")] = "maybe"
    environ[input_variable("fail-on-attention")] = "false"
    environ["GITHUB_OUTPUT"] = str(output)
    return subprocess.run(
        argv,
        cwd=cwd,
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


# ---------------------------------------------------------------------------
# The metadata (RF-10)
# ---------------------------------------------------------------------------


def test_the_metadata_has_what_the_marketplace_lists() -> None:
    action = _action()
    assert action["name"] == "TLSVigil"
    assert action["description"].strip()
    assert action["author"].strip()
    assert action["branding"] == {"icon": "shield", "color": "blue"}
    assert action["runs"]["using"] == "composite"


def test_the_inputs_are_the_modules_in_order_each_with_a_description() -> None:
    inputs = _action()["inputs"]
    assert tuple(inputs) == INPUTS
    for name, spec in inputs.items():
        assert str(spec.get("description", "")).strip(), name
        assert "default" in spec, name


def test_the_defaults_are_those_of_the_design() -> None:
    inputs = _action()["inputs"]
    assert inputs["config"]["default"] == "tlsvigil.toml"
    assert inputs["notify"]["default"] == "true"
    assert inputs["fail-on-attention"]["default"] == "false"
    assert inputs["github-token"]["default"] == "${{ github.token }}"
    empty = {"lang", *INPUTS[INPUTS.index("telegram-token") :]}
    assert {name for name in INPUTS if inputs[name]["default"] == ""} == empty


def test_the_output_is_the_exit_code_of_the_check_step() -> None:
    outputs = _action()["outputs"]
    assert list(outputs) == ["exit-code"]
    assert outputs["exit-code"]["description"].strip()
    assert outputs["exit-code"]["value"] == "${{ steps.run.outputs.exit-code }}"
    assert _check_step()["id"] == "run"


# ---------------------------------------------------------------------------
# The pins and the shell (RNF-01, RNF-02)
# ---------------------------------------------------------------------------


def test_every_uses_is_pinned_to_a_full_sha_with_its_version() -> None:
    lines = [line for line in _ACTION.read_text(encoding="utf-8").splitlines() if _USES.match(line)]
    # Positive control: the restore and the save are both found, so the check ran on them.
    assert len(lines) == 2
    assert [line for line in lines if not _PINNED.match(line)] == []


def test_only_actions_of_github_are_used() -> None:
    used = [str(step["uses"]) for step in _steps() if "uses" in step]
    assert used and all(uses.startswith("actions/") for uses in used)


def test_no_run_holds_an_expression() -> None:
    runs = [str(step["run"]) for step in _steps() if "run" in step]
    # Positive control: the Check step's script is among them.
    assert len(runs) == 1 and "exec " in runs[0]
    assert [run for run in runs if "${{" in run] == []
    assert all(step.get("shell") == "bash" for step in _steps() if "run" in step)


def test_every_input_reaches_the_check_step_by_its_variable() -> None:
    env = _check_step()["env"]
    expected = {input_variable(name): f"${{{{ inputs.{name} }}}}" for name in INPUTS}
    assert {name: value for name, value in env.items() if name.startswith(INPUT_PREFIX)} == expected
    assert env["TLSVIGIL_SOURCE"] == "${{ github.action_path }}/src"
    assert env["TLSVIGIL_STATE_DIR"] == "${{ runner.temp }}/tlsvigil-state"


# ---------------------------------------------------------------------------
# The state (RF-05; ADR-4)
# ---------------------------------------------------------------------------


def test_the_steps_are_restore_check_save_in_that_order() -> None:
    steps = _steps()
    assert len(steps) == 3
    assert str(steps[0]["uses"]).startswith(_RESTORE)
    assert steps[1]["id"] == "run"
    assert str(steps[2]["uses"]).startswith(_SAVE)


def test_the_restore_runs_with_notify_and_by_the_prefix() -> None:
    restore = _step(_RESTORE)
    assert restore["if"] == "inputs.notify == 'true'"
    assert restore["with"]["restore-keys"] == "tlsvigil-state-"
    assert str(restore["with"]["key"]).startswith("tlsvigil-state-")


def test_the_save_runs_always_and_only_for_a_written_state() -> None:
    save = _step(_SAVE)
    condition = str(save["if"])
    assert condition.startswith("always() && ")
    assert "inputs.notify == 'true'" in condition
    assert "steps.run.outputs.state == 'written'" in condition


def test_the_save_keeps_the_path_and_the_key_of_the_restore() -> None:
    restore, save = _step(_RESTORE)["with"], _step(_SAVE)["with"]
    assert save["path"] == restore["path"] == "${{ runner.temp }}/tlsvigil-state"
    key = "tlsvigil-state-${{ github.run_id }}-${{ github.run_attempt }}"
    assert save["key"] == restore["key"] == key


# ---------------------------------------------------------------------------
# The bootstrap (ADR-2)
# ---------------------------------------------------------------------------


def test_the_bootstrap_ignores_a_json_py_planted_in_the_workspace(tmp_path: Path) -> None:
    """The module runs, not the workspace's ``json.py``: ``-I`` keeps it off ``sys.path``."""
    (tmp_path / "json.py").write_text('raise SystemExit("planted json.py ran")\n', encoding="utf-8")
    output = tmp_path / "output.txt"
    result = _run(_bootstrap(_SOURCE), tmp_path, output)
    assert "planted" not in result.stdout + result.stderr
    assert result.returncode == 2
    assert "::error title=TLSVigil::" in result.stdout
    assert output.read_text(encoding="utf-8") == "exit-code=2\n"


def test_the_planted_json_py_runs_without_isolation(tmp_path: Path) -> None:
    """Positive control: the same line without ``-I`` runs the planted file."""
    (tmp_path / "json.py").write_text('raise SystemExit("planted json.py ran")\n', encoding="utf-8")
    argv = [word for word in _bootstrap(_SOURCE) if word != "-I"]
    result = _run(argv, tmp_path, tmp_path / "output.txt")
    assert "planted json.py ran" in result.stderr
    assert result.returncode == 1


# ---------------------------------------------------------------------------
# The configurations of the CI job (RF-12)
# ---------------------------------------------------------------------------


def test_the_refused_configuration_names_only_a_closed_loopback_port() -> None:
    config = load_config(_DATA / "refused.toml")
    assert [(entry.host, entry.port) for entry in config.hosts] == [("127.0.0.1", 1)]
    assert config.domains == () and config.notifications is None


def test_the_telegram_configuration_turns_on_telegram_only() -> None:
    config = load_config(_DATA / "telegram.toml")
    assert [(entry.host, entry.port) for entry in config.hosts] == [("127.0.0.1", 1)]
    assert config.domains == ()
    assert config.notifications is not None
    assert config.notifications.channels == (Channel.TELEGRAM,)


# ---------------------------------------------------------------------------
# The template's workflow (RF-08, RF-09, RNF-06, RNF-07)
# ---------------------------------------------------------------------------


def _workflow() -> dict[Any, Any]:
    """
    Read the template's workflow.

    Returns:
        dict[Any, Any]: The parsed file; YAML 1.1 reads the key ``on`` as ``True``, so the keys
            are not all strings.
    """
    return cast(dict[Any, Any], yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8")))


def _triggers() -> dict[str, Any]:
    """
    Give the triggers of the template's workflow.

    Returns:
        dict[str, Any]: The ``on:`` mapping, by event name.
    """
    workflow = _workflow()
    return cast(dict[str, Any], workflow.get("on", workflow.get(True)))


def _template_steps() -> list[dict[str, Any]]:
    """
    Give the steps of the template's one job.

    Returns:
        list[dict[str, Any]]: The steps, in order.
    """
    jobs = _workflow()["jobs"]
    assert list(jobs) == ["check"]
    return cast(list[dict[str, Any]], jobs["check"]["steps"])


def test_every_uses_of_the_template_is_pinned_to_a_full_sha() -> None:
    lines = [
        line for line in _WORKFLOW.read_text(encoding="utf-8").splitlines() if _USES.match(line)
    ]
    # Positive control: the checkout and the Action are both found, so the check ran on them.
    assert len(lines) == 2
    assert [line for line in lines if not (_PINNED.match(line) or _SELF.match(line))] == []


def test_the_template_checks_out_without_credentials_and_runs_this_action() -> None:
    steps = _template_steps()
    assert [str(step["uses"]).split("@")[0] for step in steps] == ["actions/checkout", _OWN]
    assert steps[0]["with"] == {"persist-credentials": False}
    assert all(str(step.get("name", "")).strip() for step in steps)
    assert [step for step in steps if "run" in step] == []


def test_the_template_runs_daily_by_hand_and_on_a_change_of_the_configuration() -> None:
    triggers = _triggers()
    assert set(triggers) == {"schedule", "workflow_dispatch", "push"}
    assert triggers["push"] == {"branches": ["main"], "paths": ["tlsvigil.toml"]}
    crons = [str(entry["cron"]) for entry in triggers["schedule"]]
    assert len(crons) == 1
    minute, hour, *days = crons[0].split()
    # Once a day, at a minute off the top of the hour, when the scheduler is busiest.
    assert minute.isdigit() and int(minute) != 0 and hour.isdigit()
    assert days == ["*", "*", "*"]


def test_the_template_never_runs_on_a_pull_request() -> None:
    assert {"pull_request", "pull_request_target"} & set(_triggers()) == set()


def test_the_template_asks_for_two_permissions_only() -> None:
    assert _workflow()["permissions"] == {"contents": "read", "issues": "write"}
    assert "permissions" not in _workflow()["jobs"]["check"]


def test_the_template_queues_a_second_run_instead_of_cancelling() -> None:
    assert _workflow()["concurrency"] == {"group": "tlsvigil", "cancel-in-progress": False}


def test_the_template_job_has_a_timeout() -> None:
    timeout = _workflow()["jobs"]["check"]["timeout-minutes"]
    assert isinstance(timeout, int) and 0 < timeout <= 30


def test_the_template_offers_every_channel_input_commented() -> None:
    """The commented ``with:`` lists the channel inputs of ``action.yml``, in order."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    offered = [(match.group(1), match.group(2)) for match in _OFFERED.finditer(text)]
    # Each secret is named after the variable the input becomes (spec 005, RF-03), the default
    # token excepted: the Action passes the workflow's own.
    expected = [
        (name, variable) for name, variable in CHANNEL_INPUTS.items() if name != "github-token"
    ]
    assert offered == expected
    assert {name for name, _ in expected} <= set(_action()["inputs"])


# ---------------------------------------------------------------------------
# The template's configuration and READMEs (RF-08)
# ---------------------------------------------------------------------------


def _headings(path: Path) -> list[int]:
    """
    List the heading levels of a Markdown file, outside fenced code blocks.

    Args:
        path (Path): The Markdown file.

    Returns:
        list[int]: One level (1 to 6) per heading, in order.
    """
    levels: list[int] = []
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and (match := _HEADING.match(line)):
            levels.append(len(match.group(1)))
    return levels


def _table_lines(path: Path) -> int:
    """
    Count the table lines of a Markdown file.

    Args:
        path (Path): The Markdown file.

    Returns:
        int: The lines that start with ``|``, header and separator included.
    """
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("|"))


def test_the_template_configuration_loads_with_the_github_channel() -> None:
    config = load_config(_TEMPLATE / "tlsvigil.toml")
    assert [(entry.host, entry.port) for entry in config.hosts] == [("example.com", 443)]
    assert [entry.domain for entry in config.domains] == ["example.com"]
    assert config.notifications is not None
    assert config.notifications.channels == (Channel.GITHUB,)


def test_both_template_readmes_have_the_same_headings() -> None:
    english = _headings(_TEMPLATE / "README.md")
    # Positive control: the title and the four sections are found.
    assert len(english) == 5
    assert _headings(_TEMPLATE / "README.pt-BR.md") == english


def test_both_template_readmes_have_the_same_table() -> None:
    english = _table_lines(_TEMPLATE / "README.md")
    assert english > 2
    assert _table_lines(_TEMPLATE / "README.pt-BR.md") == english
