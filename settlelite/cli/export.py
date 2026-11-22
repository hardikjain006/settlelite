"""``export`` command."""

from __future__ import annotations

import argparse
import os

from ..errors import ValidationError
from ..money import get_currency
from ..reports.export import ExportFilter, ExportFormat, export_journal
from .common import CommandContext, Payload, add_runtime_options, date_arg


def register(subparsers: argparse._SubParsersAction) -> None:
    export = subparsers.add_parser("export", help="export journal postings as CSV or JSON lines")
    export.add_argument("--format", default="csv", choices=[fmt.value for fmt in ExportFormat])
    export.add_argument("--from", dest="start", help="first effective date to include")
    export.add_argument("--to", dest="end", help="last effective date to include")
    export.add_argument("--account", help="only postings to this account")
    export.add_argument("--currency", help="only entries in this currency")
    export.add_argument(
        "--output", metavar="PATH", help="write to a file and print a summary instead of rows"
    )
    add_runtime_options(export)
    export.set_defaults(handler=run_export)


def run_export(args: argparse.Namespace, ctx: CommandContext) -> Payload | int:
    app = ctx.app()
    if args.account is not None:
        app.accounts.get(args.account)
    selection = ExportFilter(
        start=date_arg(args.start),
        end=date_arg(args.end),
        account=args.account,
        currency=get_currency(args.currency).code if args.currency else None,
    )
    if args.output is None:
        export_journal(app.db, ctx.stdout, fmt=args.format, selection=selection)
        return 0
    target = os.path.abspath(args.output)
    if not os.path.isdir(os.path.dirname(target)):
        raise ValidationError(f"output directory does not exist: {os.path.dirname(target)}")
    temporary = target + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="") as handle:
        rows = export_journal(app.db, handle, fmt=args.format, selection=selection)
    os.replace(temporary, target)
    return {"format": args.format, "rows": rows, "output": args.output}
