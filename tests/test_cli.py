"""
Tests for the command line entry point.

The CLI is driven in-process through :func:`tlsvigil.cli.main`; ``capsys``
captures what :mod:`argparse` prints, so no subprocess and no network.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tlsvigil import __version__
from tlsvigil.cli import main

# Resolved from this file, not from the working directory, so the test passes wherever pytest runs.
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------


def test_version_prints_the_installed_version_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"tlsvigil {__version__}\n"


def test_version_is_the_package_metadata_not_the_source_tree_fallback() -> None:
    # The fallback only appears when the package is not installed; `uv sync` installs it.
    with PYPROJECT.open("rb") as file:
        declared = tomllib.load(file)["project"]["version"]

    assert __version__ == declared


# ---------------------------------------------------------------------------
# No arguments
# ---------------------------------------------------------------------------


def test_no_arguments_exits_zero() -> None:
    assert main([]) == 0
