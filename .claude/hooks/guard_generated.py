"""
PreToolUse hook (project scope): protects generated files.

Blocks a manual Edit/Write on files that a tool generates. Editing one by hand
is always a mistake: the next regeneration overwrites the edit, and a hand-edited
``uv.lock`` no longer matches the resolved dependency graph.

Protected files and how to update them:
    - uv.lock  -> uv lock / uv add / uv remove

On a violation it exits with code 2 and writes the guidance to stderr; Claude
Code hands that back to the agent, which takes the right path instead.
"""

from __future__ import annotations

import json
import sys

# Path suffixes, compared case-insensitively with forward slashes.
PROTECTED = {
    "uv.lock": "use uv lock / uv add / uv remove; never edit uv.lock by hand.",
}


def main() -> None:
    """Read the hook event from stdin and exit with 2 if the target is a generated file."""
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return  # unreadable event: do not block

    file_path = str((data.get("tool_input") or {}).get("file_path", ""))
    if not file_path:
        return

    normalized = file_path.replace("\\", "/").lower()
    for target, guidance in PROTECTED.items():
        if normalized.endswith(target):
            sys.stderr.write(f"Edit blocked: '{target}' is a generated file. {guidance}")
            sys.exit(2)


if __name__ == "__main__":
    main()
