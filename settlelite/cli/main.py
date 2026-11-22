"""Argument parsing and dispatch.

Every command except ``export`` (without ``--output``) and ``validate``
prints exactly one compact JSON object with sorted keys.  Exit status is 0
on success, 2 when the request is rejected (invalid input, unknown records,
rule violations, bad configuration) and 1 when storage fails.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import TextIO

from ..version import __version__
from ..errors import SettleLiteError, StorageError
from . import accounts, admin, export, fees, holds, ledger
from .common import EXIT_FAILURE, EXIT_OK, EXIT_REJECTED, CommandContext


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="settlelite", description="SettleLite payments ledger and settlement back office"
    )
    parser.add_argument("--version", action="version", version=f"settlelite {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    admin.register(subparsers)
    accounts.register(subparsers)
    ledger.register(subparsers)
    holds.register(subparsers)
    fees.register(subparsers)
    export.register(subparsers)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one command and return its exit status."""
    parser = build_parser()
    args = parser.parse_args(argv)
    ctx = CommandContext(
        args,
        stdin=stdin or sys.stdin,
        stdout=stdout or sys.stdout,
        stderr=stderr or sys.stderr,
    )
    try:
        result = args.handler(args, ctx)
    except StorageError as exc:
        ctx.emit({"ok": False, **exc.to_dict()})
        return EXIT_FAILURE
    except SettleLiteError as exc:
        ctx.emit({"ok": False, **exc.to_dict()})
        return EXIT_REJECTED
    finally:
        ctx.close()
    if isinstance(result, int):
        return result
    ctx.emit({"ok": True, **result})
    return EXIT_OK
