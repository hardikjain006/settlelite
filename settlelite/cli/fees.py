"""``fees`` command."""

from __future__ import annotations

import argparse

from ..errors import FeeScheduleError
from ..jsonutil import loads_object
from .common import (
    CommandContext,
    Payload,
    add_runtime_options,
    amount_arg,
    date_arg,
    positive_int_arg,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    fees = subparsers.add_parser("fees", help="fee schedules and fee charges")
    sub = fees.add_subparsers(dest="action", required=True, metavar="ACTION")

    set_ = sub.add_parser("set", help="store a fee schedule from a JSON file")
    set_.add_argument("--file", required=True, help="schedule JSON file, or - for stdin")
    set_.set_defaults(handler=run_set)

    show = sub.add_parser("show", help="show a fee schedule")
    show.add_argument("name")
    show.add_argument("--version")
    show.set_defaults(handler=run_show)

    list_ = sub.add_parser("list", help="list the latest version of every schedule")
    list_.set_defaults(handler=run_list)

    history = sub.add_parser("history", help="list every version of a schedule")
    history.add_argument("name")
    history.set_defaults(handler=run_history)

    quote = sub.add_parser("quote", help="compute a fee without posting it")
    quote.add_argument("name")
    quote.add_argument("amount")
    quote.add_argument("--version")
    quote.add_argument("--minor", action="store_true", help="amount is integer minor units")
    quote.set_defaults(handler=run_quote)

    charge = sub.add_parser("charge", help="compute and post a fee")
    charge.add_argument("name")
    charge.add_argument("--request-id", required=True)
    charge.add_argument("--payer", required=True, metavar="ACCOUNT")
    charge.add_argument("--amount", required=True, help="base amount the fee is computed on")
    charge.add_argument("--revenue", metavar="ACCOUNT", help="revenue account (if no splits)")
    charge.add_argument("--memo", default="")
    charge.add_argument("--date", help="effective date of the fee entry")
    charge.add_argument("--minor", action="store_true", help="amount is integer minor units")
    charge.set_defaults(handler=run_charge)

    charges = sub.add_parser("charges", help="list recorded fee charges")
    charges.add_argument("--schedule")
    charges.add_argument("--payer", metavar="ACCOUNT")
    charges.set_defaults(handler=run_charges)

    for parser in (set_, show, list_, history, quote, charge, charges):
        add_runtime_options(parser)


def run_set(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    if args.file == "-":
        text = ctx.stdin.read()
    else:
        try:
            with open(args.file, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            raise FeeScheduleError(f"cannot read {args.file}: {exc.strerror}") from None
    try:
        data = loads_object(text, what="fee schedule")
    except ValueError as exc:
        raise FeeScheduleError(str(exc)) from None
    return ctx.app().fees.save(data).to_dict()


def run_show(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    version = positive_int_arg(args.version, name="--version")
    return {"schedule": ctx.app().fees.get(args.name, version).to_dict()}


def run_list(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    return {"schedules": [schedule.to_dict() for schedule in ctx.app().fees.list()]}


def run_history(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    return {"versions": [schedule.to_dict() for schedule in ctx.app().fees.history(args.name)]}


def run_quote(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    version = positive_int_arg(args.version, name="--version")
    schedule = app.fees.get(args.name, version)
    amount = amount_arg(args.amount, schedule.currency, minor=args.minor)
    return {"quote": app.fees.quote(args.name, amount, version=version).to_dict()}


def run_charge(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    payer = app.accounts.get(args.payer)
    result = app.fees.charge(
        request_id=args.request_id,
        schedule=args.name,
        payer=payer.code,
        amount=amount_arg(args.amount, payer.currency, minor=args.minor),
        revenue=args.revenue,
        memo=args.memo,
        effective_date=date_arg(args.date),
    )
    return result.to_dict()


def run_charges(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    charges = ctx.app().fees.list_charges(schedule=args.schedule, payer=args.payer)
    return {"charges": [charge.to_dict() for charge in charges]}
