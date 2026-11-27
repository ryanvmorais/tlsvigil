"""
PostToolUse hook (project scope): the project's single formatter.

Runs ``ruff check --fix`` and ``black`` on a ``.py`` file right after it is
written or edited, with the tools from the uv-managed ``.venv`` (Windows
``Scripts/python.exe`` or Unix ``bin/python``). Until ``.venv`` exists it does
nothing. It never blocks: it always exits with 0.

Claude Code sends the event as JSON on stdin:
    { "tool_input": { "file_path": "/absolute/path/to/file.ext" } }
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# <root>/.claude/hooks/project_format.py  ->  <root>
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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


def format_python(path: Path) -> None:
    """
    Fix lint and format one Python file, ignoring the tools' output.

    Args:
        path (Path): The file that was just written.
    """
    python = venv_python()
    if python is None:
        return
    # `--` ends the options: the path is always read as a file, never as a flag.
    subprocess.run(
        [str(python), "-m", "ruff", "check", "--fix", "--", str(path)], capture_output=True
    )
    subprocess.run([str(python), "-m", "black", "--", str(path)], capture_output=True)


def main() -> None:
    """Read the hook event from stdin and format the file if it is Python."""
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return

    file_path = (data.get("tool_input") or {}).get("file_path")
    if not file_path:
        return

    path = Path(file_path)
    if path.exists() and path.suffix.lower() == ".py":
        format_python(path)


if __name__ == "__main__":
    main()
