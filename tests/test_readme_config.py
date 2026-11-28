"""
Tests that the example configuration of both READMEs is a file the command accepts.

The ``toml`` block of each README is what a new user copies first, so a key renamed in the code
and left in the README would greet that user with a configuration error. Each block is written to
a file and read with :func:`tlsvigil.config.load_config`, the same function ``tlsvigil check``
calls, and the two READMEs must list the same entries (only ``lang`` differs between them).
Isolation: only the two READMEs are read and the files go to ``tmp_path``; no check runs, so
nothing reaches the network. When the tests run from a tree without the READMEs (an sdist), they
are skipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tlsvigil.config import Config, load_config
from tlsvigil.i18n import Lang

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The repository root, one level above the tests folder.
_ROOT = Path(__file__).resolve().parents[1]
_ENGLISH = _ROOT / "README.md"
_PORTUGUESE = _ROOT / "README.pt-BR.md"

# Both READMEs are needed for every test below, so the mark is shared.
_needs_both = pytest.mark.skipif(
    not (_ENGLISH.is_file() and _PORTUGUESE.is_file()),
    reason="the two READMEs are not in this source tree",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _toml_block(readme: Path) -> str:
    """
    Return the one ``toml`` fenced block of a README.

    Args:
        readme (Path): The Markdown file.

    Returns:
        str: The lines between the opening ```` ```toml ```` and the closing fence.
    """
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in readme.read_text(encoding="utf-8").splitlines():
        if current is None and line == "```toml":
            current = []
        elif current is not None and line.startswith("```"):
            blocks.append(current)
            current = None
        elif current is not None:
            current.append(line)
    # One example per README: a second block would leave the reader unsure which one to copy.
    assert len(blocks) == 1, f"{readme.name} has {len(blocks)} toml blocks"
    return "\n".join(blocks[0]) + "\n"


def _load(readme: Path, tmp_path: Path) -> Config:
    """
    Write the example of a README to a file and load it as ``tlsvigil check`` would.

    Args:
        readme (Path): The Markdown file.
        tmp_path (Path): The test's temporary folder.

    Returns:
        Config: The validated configuration; a problem raises ``ConfigError`` and fails the test.
    """
    path = tmp_path / f"{readme.stem}.toml"
    # Bytes, so the file has the README's line endings on every platform.
    path.write_bytes(_toml_block(readme).encode("utf-8"))
    return load_config(path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_needs_both
def test_the_english_example_loads_with_its_hosts_and_domains(tmp_path: Path) -> None:
    config = _load(_ENGLISH, tmp_path)

    assert config.lang is Lang.EN
    assert [(h.host, h.port, h.warn_days) for h in config.hosts] == [
        ("example.com", 443, 14),
        ("mail.example.com", 465, 14),
        ("2001:db8::1", 8443, 14),
        ("legacy.example.com", 443, 30),
    ]
    assert [(d.domain, d.warn_days) for d in config.domains] == [
        ("example.com", 30),
        ("example.com.br", 30),
    ]


@_needs_both
def test_the_portuguese_example_loads_in_portuguese(tmp_path: Path) -> None:
    config = _load(_PORTUGUESE, tmp_path)

    assert config.lang is Lang.PT_BR


@_needs_both
def test_both_examples_hold_the_same_entries(tmp_path: Path) -> None:
    english = _load(_ENGLISH, tmp_path)
    portuguese = _load(_PORTUGUESE, tmp_path)

    assert portuguese.hosts == english.hosts
    assert portuguese.domains == english.domains
    assert portuguese.timeout == english.timeout
