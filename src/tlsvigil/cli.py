"""
TLSVigil command line: the ``tlsvigil`` entry point.

Built on :mod:`argparse` so the package keeps its zero-dependency promise (ADR-1).
:func:`main` takes the argument list and returns the exit code instead of exiting,
which lets the tests drive it in-process.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from tlsvigil import __version__

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """
    Build the argument parser for the ``tlsvigil`` command.

    Returns:
        argparse.ArgumentParser: The parser, with ``--version`` wired to the
            installed package version.
    """
    parser = argparse.ArgumentParser(
        prog="tlsvigil",
        description="Warn before a TLS certificate or a domain expires.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"tlsvigil {__version__}",
    )
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the command line.

    Args:
        argv (Sequence[str] | None): The arguments without the program name;
            ``None`` reads them from :data:`sys.argv`.

    Returns:
        int: The process exit code (``0`` on success).

    Raises:
        SystemExit: From :mod:`argparse`, for ``--version``, ``--help`` and
            invalid arguments.
    """
    build_parser().parse_args(argv)
    return 0
