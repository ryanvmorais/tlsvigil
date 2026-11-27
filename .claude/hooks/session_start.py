"""
SessionStart hook (project scope): environment check.

When a Claude Code session starts, runs light, non-blocking checks and prints
warnings to stdout, which Claude Code adds to the session context. It never
fails the session start: any error is swallowed.

Checks:
    - ``.venv`` exists and is not older than ``uv.lock`` (otherwise: ``uv sync``).
    - ``core.hooksPath`` points at ``.githooks``, so the pre-commit gate runs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# <root>/.claude/hooks/session_start.py  ->  <root>
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def check_venv() -> list[str]:
    """
    Check that the uv environment exists and follows the lock file.

    Returns:
        list[str]: Zero or one warning.
    """
    venv = PROJECT_ROOT / ".venv"
    lock = PROJECT_ROOT / "uv.lock"
    if not venv.exists():
        return ["[uv] .venv is missing - run: uv sync"]
    if lock.exists() and lock.stat().st_mtime > venv.stat().st_mtime:
        return ["[uv] uv.lock is newer than .venv - run: uv sync"]
    return []


def check_hooks_path() -> list[str]:
    """
    Check that Git runs the versioned pre-commit hook.

    Returns:
        list[str]: Zero or one warning.
    """
    result = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip() != ".githooks":
        return ["[git] pre-commit gate is off - run: git config core.hooksPath .githooks"]
    return []


def main() -> None:
    """Print the environment warnings, if any; never raise."""
    try:
        warnings = check_venv() + check_hooks_path()
    except Exception:  # a diagnostic must never break the session start
        return
    if warnings:
        sys.stdout.write(
            "Environment check (tlsvigil):\n" + "\n".join(f"- {w}" for w in warnings) + "\n"
        )


if __name__ == "__main__":
    main()
