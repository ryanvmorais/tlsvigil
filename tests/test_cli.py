"""
Tests for the command line entry point.

The CLI is driven in-process through :func:`tlsvigil.cli.main`; ``capsys``
captures what :mod:`argparse` prints, so no subprocess and no network.
``python -m tlsvigil`` runs through :mod:`runpy` in the same process, and the
version fallback is reached by patching the metadata lookup and reloading the
package, which is reloaded again afterwards.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import runpy
import sys
import tomllib
from pathlib import Path

import pytest

import tlsvigil
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


def test_version_falls_back_without_install(monkeypatch: pytest.MonkeyPatch) -> None:
    # A source tree with no installed distribution must still import (RF-01).
    def _not_installed(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _not_installed)
    try:
        assert importlib.reload(tlsvigil).__version__ == "0.0.0.dev0"
    finally:
        # Put the real lookup back and reload, so later tests see the installed version.
        monkeypatch.undo()
        importlib.reload(tlsvigil)

    assert tlsvigil.__version__ == __version__


def test_python_m_prints_the_same_version_line(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["tlsvigil", "--version"])
    # runpy warns when the module it runs was already imported; start from a clean slate.
    monkeypatch.delitem(sys.modules, "tlsvigil.__main__", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("tlsvigil", run_name="__main__")

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"tlsvigil {__version__}\n"


# ---------------------------------------------------------------------------
# No arguments and invalid arguments
# ---------------------------------------------------------------------------


def test_no_arguments_exits_zero() -> None:
    assert main([]) == 0


def test_unknown_flag_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--nope"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 2
    assert captured.out == ""
    assert captured.err.startswith("usage: tlsvigil")
