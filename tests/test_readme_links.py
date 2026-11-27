"""
Tests that the README links work on GitHub and on PyPI.

``README.md`` uses relative links, which GitHub resolves. PyPI shows the same file as the long
description and resolves nothing, so the build rewrites every relative target to an absolute
GitHub URL with the substitutions declared in ``pyproject.toml``. The tests apply those
substitutions the way the build does and check both ends: each relative link names a real path,
and nothing relative survives the rewrite. Isolation: only ``README.md`` and ``pyproject.toml``
are read; when the tests run from a tree without the README, they are skipped.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The repository root, one level above the tests folder.
_ROOT = Path(__file__).resolve().parents[1]
_README = _ROOT / "README.md"
_PYPROJECT = _ROOT / "pyproject.toml"

# A Markdown link or image target: the text between "](" and the closing parenthesis.
_TARGET = re.compile(r"\]\(([^)\s]+)\)")

# Every test reads the README, so the mark is shared.
_needs_readme = pytest.mark.skipif(
    not _README.is_file(), reason="README.md is not in this source tree"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pypi_readme() -> str:
    """
    Rewrite the README the way the build does for PyPI.

    Returns:
        str: The README after every ``fancy-pypi-readme`` substitution, applied in order.
    """
    with _PYPROJECT.open("rb") as file:
        hook = tomllib.load(file)["tool"]["hatch"]["metadata"]["hooks"]["fancy-pypi-readme"]
    text = _README.read_text(encoding="utf-8")
    for rule in hook["substitutions"]:
        text = re.sub(rule["pattern"], rule["replacement"], text)
    return text


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_needs_readme
def test_readme_relative_links_name_real_paths() -> None:
    relative = [
        t
        for t in _TARGET.findall(_README.read_text(encoding="utf-8"))
        if not t.startswith(("https://", "#"))
    ]

    missing = [t for t in relative if not (_ROOT / t.split("#")[0]).exists()]

    # Guards the check against a regex that stops matching and passes on an empty list.
    assert len(relative) >= 5
    assert missing == []


@_needs_readme
def test_no_relative_link_survives_the_pypi_rewrite() -> None:
    targets = _TARGET.findall(_pypi_readme())

    relative = [t for t in targets if not t.startswith(("https://", "#"))]

    assert targets
    assert relative == []
