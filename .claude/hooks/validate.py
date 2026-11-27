"""
Stop hook (project scope): validates the whole project at the end of a turn.

When Claude finishes a turn, runs ruff and mypy with the ``.venv`` Python. On a
failure it writes the errors to stderr and exits with code 2; Claude Code hands
that back to the agent, which fixes them before finishing.

pytest stays out on purpose: it can be legitimately red in the middle of a
feature. The pre-commit hook and the CI run it.

Robustness:
    - Validates only when a ``.py`` file under ``src/``, ``tests/`` or
      ``.claude/hooks/`` changed (per ``git status``).
    - Without ``.venv`` it does not block: an environment problem must not
      stall the agent.
    - Honors ``stop_hook_active``: after one retry that still fails, it lets
      the turn end, so it never loops.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# <root>/.claude/hooks/validate.py  ->  <root>
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# The folders ruff and mypy cover (see [tool.mypy] files); a change elsewhere skips the check.
WATCHED = ("src/", "tests/", ".claude/hooks/")


def venv_python() -> Path | None:
    """
    Find the Python interpreter of the project's ``.venv``.

    Returns:
        Path | None: The interpreter path, or ``None`` while ``.venv`` does not exist.
    """
    for relative in ("Scripts/python.exe", "bin/python"):
        candidate = PROJECT_ROOT / ".venv" / relative
        if candidate.exists():
            return candidate
    return None


def has_python_changes() -> bool:
    """
    Tell whether a watched ``.py`` file differs from ``HEAD``.

    Returns:
        bool: ``True`` when one changed, or when git cannot answer (validate to be safe).
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return True

    for line in out.splitlines():
        name = line[3:].strip().strip('"')
        if name.endswith(".py") and name.startswith(WATCHED):
            return True
    return False


def run(python: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """
    Run one tool as ``python -m <tool>`` from the project root.

    Args:
        python (Path): The ``.venv`` interpreter.
        args (list[str]): The module name followed by its arguments.

    Returns:
        subprocess.CompletedProcess[str]: The finished process, output captured.
    """
    return subprocess.run(
        [str(python), "-m", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def main() -> None:
    """Read the hook event from stdin, run ruff and mypy, and exit with 2 on failure."""
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}

    python = venv_python()
    if python is None or not has_python_changes():
        return

    problems: list[str] = []
    ruff = run(python, ["ruff", "check", "."])
    if ruff.returncode != 0:
        problems.append("RUFF:\n" + ruff.stdout + ruff.stderr)
    mypy = run(python, ["mypy"])
    if mypy.returncode != 0:
        problems.append("MYPY:\n" + mypy.stdout + mypy.stderr)

    if not problems:
        return

    report = "\n\n".join(problems)
    if data.get("stop_hook_active"):
        sys.stderr.write("Warning: quality checks still fail after the fix:\n\n" + report)
        return
    sys.stderr.write("Quality checks failed. Fix them before finishing:\n\n" + report)
    sys.exit(2)


if __name__ == "__main__":
    main()
