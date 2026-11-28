"""
Tests that the Portuguese README keeps the shape of the English one.

``README.pt-BR.md`` is a translation, and a translation drifts: a section added to ``README.md``
that nobody carries over leaves Brazilian readers with an older tool than the one they installed.
The tests compare structure, never wording: the order of the headings, the rows of the tables,
the cross-links between the two files and the repository files the Portuguese links point at.
Isolation: only the two READMEs and the existence of the files they link are read; when the tests
run from a tree without them (an sdist), they are skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The repository root, one level above the tests folder.
_ROOT = Path(__file__).resolve().parents[1]
# The two files the tests compare; they are skipped when either is missing from the tree.
_ENGLISH = _ROOT / "README.md"
_PORTUGUESE = _ROOT / "README.pt-BR.md"

# A Markdown link or image target: the text between "](" and the closing parenthesis.
_TARGET = re.compile(r"\]\(([^)\s]+)\)")

# An ATX heading; the run of "#" is its level.
_HEADING = re.compile(r"^(#{1,6}) ")

# Both READMEs are needed for every test below, so the mark is shared.
_needs_both = pytest.mark.skipif(
    not (_ENGLISH.is_file() and _PORTUGUESE.is_file()),
    reason="the two READMEs are not in this source tree",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _outside_fences(path: Path) -> list[str]:
    """
    Read a Markdown file and drop the lines inside fenced code blocks.

    Args:
        path (Path): The Markdown file.

    Returns:
        list[str]: The remaining lines, so that a ``#`` comment in a shell block is not a heading.
    """
    kept: list[str] = []
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced:
            kept.append(line)
    return kept


def _levels(path: Path) -> list[int]:
    """
    List the heading levels of a README, in order.

    Args:
        path (Path): The Markdown file.

    Returns:
        list[int]: One level (1 to 6) per heading.
    """
    found = (_HEADING.match(line) for line in _outside_fences(path))
    return [len(m.group(1)) for m in found if m]


def _table_rows(path: Path, first_cell: str) -> int:
    """
    Count the data rows of the table whose header starts with a given cell.

    Args:
        path (Path): The Markdown file.
        first_cell (str): The text of the first header cell (empty for a comparison table).

    Returns:
        int: The rows under the header, not counting the header and its separator.
    """
    lines = _outside_fences(path)
    # An empty first cell is written "| |", not with two spaces.
    header = f"| {first_cell} |" if first_cell else "| |"
    start = next(i for i, line in enumerate(lines) if line.startswith(header))
    rows = 0
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        rows += 1
    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_needs_both
def test_both_readmes_have_the_same_headings_in_the_same_order() -> None:
    assert _levels(_PORTUGUESE) == _levels(_ENGLISH)


@_needs_both
def test_both_readmes_have_the_same_check_rows() -> None:
    assert _table_rows(_PORTUGUESE, "Verificação") == _table_rows(_ENGLISH, "Check")


@_needs_both
def test_both_readmes_have_the_same_comparison_rows() -> None:
    # The comparison table opens with an empty header cell in both languages.
    assert _table_rows(_PORTUGUESE, "") == _table_rows(_ENGLISH, "")


@pytest.mark.parametrize(
    ("portuguese", "english"),
    [
        ("Chave", "Key"),
        ("Opção", "Option"),
        ("Código", "Code"),
        ("Variável", "Variable"),
        ("Entrada", "Input"),
        ("Saída", "Output"),
    ],
    ids=["keys", "options", "exit-codes", "variables", "action-inputs", "action-outputs"],
)
@_needs_both
def test_both_readmes_have_the_same_rows_in_the_command_tables(
    portuguese: str, english: str
) -> None:
    assert _table_rows(_PORTUGUESE, portuguese) == _table_rows(_ENGLISH, english)


@_needs_both
def test_each_readme_links_to_the_other_in_its_first_lines() -> None:
    english = "\n".join(_outside_fences(_ENGLISH)[:4])
    portuguese = "\n".join(_outside_fences(_PORTUGUESE)[:4])

    assert "(README.pt-BR.md)" in english
    assert "(README.md)" in portuguese


@_needs_both
def test_the_portuguese_readme_links_resolve_inside_the_repository() -> None:
    # The Portuguese README is read on GitHub only, so each relative link must name a real path.
    text = _PORTUGUESE.read_text(encoding="utf-8")
    relative = [t for t in _TARGET.findall(text) if not t.startswith(("https://", "#"))]

    missing = [t for t in relative if not (_ROOT / t.split("#")[0]).exists()]

    assert relative
    assert missing == []
