"""``hold`` command."""

from __future__ import annotations

import argparse
from datetime import timedelta

from ..holds.models import HoldStatus
from .common import (
    CommandContext,
    Payload,
    add_runtime_options,
    amount_arg,
    date_arg,
    positive_int_arg,
    timestamp_arg,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    hold = subparsers.add_parser("hold", help="authorization holds")
    sub = hold.add_subparsers(dest="action", required=True, metavar="ACTION")

    place = sub.add_parser("place", help="reserve funds on an account")
    place.add_argument("--request-id", required=True)
    place.add_argument("--account", required=True)
    place.add_argument("--amount", required=True)
    expiry = place.add_mutually_exclusive_group()
    expiry.add_argument("--expires-in", metavar="HOURS", help="hours until the hold expires")
    expiry.add_argument("--expires-at", metavar="TIMESTAMP", help="absolute expiry time")
    place.add_argument("--reference", default="")
    place.add_argument("--minor", action="store_true", help="amount is integer minor units")
    place.set_defaults(handler=run_place)

    capture = sub.add_parser("capture", help="capture an active hold")
    capture.add_argument("hold_id")
    capture.add_argument("--request-id", required=True)
    capture.add_argument("--to", dest="destination", required=True, metavar="ACCOUNT")
    capture.add_argument("--amount", help="partial capture amount (default: full hold)")
    capture.add_argument("--memo", default="")
    capture.add_argument("--date", help="effective date of the capture entry")
    capture.add_argument("--minor", action="store_true", help="amount is integer minor units")
    capture.set_defaults(handler=run_capture)

    release = sub.add_parser("release", help="release an active hold")
    release.add_argument("hold_id")
    release.add_argument("--reason", default="")
    release.set_defaults(handler=run_release)

    show = sub.add_parser("show", help="show one hold")
    show.add_argument("hold_id")
    show.set_defaults(handler=run_show)

    list_ = sub.add_parser("list", help="list holds")
    list_.add_argument("--account")
    list_.add_argument("--status", choices=[status.value for status in HoldStatus])
    list_.set_defaults(handler=run_list)

    expire = sub.add_parser("expire", help="mark overdue holds as expired")
    expire.set_defaults(handler=run_expire)

    for parser in (place, capture, release, show, list_, expire):
        add_runtime_options(parser)


def _hold_id(value: str) -> int:
    number = positive_int_arg(value, name="hold id")
    assert number is not None
    return number


def run_place(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    account = app.accounts.get(args.account)
    hours = positive_int_arg(args.expires_in, name="--expires-in")
    result = app.holds.place(
        request_id=args.request_id,
        account=account.code,
        amount=amount_arg(args.amount, account.currency, minor=args.minor),
        expires_in=timedelta(hours=hours) if hours else None,
        expires_at=timestamp_arg(args.expires_at),
        reference=args.reference,
    )
    return result.to_dict(app.clock.now())


def run_capture(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    hold = app.holds.get(_hold_id(args.hold_id))
    amount = (
        amount_arg(args.amount, hold.currency, minor=args.minor) if args.amount is not None else None
    )
    result = app.holds.capture(
        request_id=args.request_id,
        hold_id=hold.id,
        destination=args.destination,
        amount=amount,
        memo=args.memo,
        effective_date=date_arg(args.date),
    )
    return result.to_dict(app.clock.now())


def run_release(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    hold = app.holds.release(_hold_id(args.hold_id), reason=args.reason)
    return {"hold": hold.to_dict(app.clock.now())}


def run_show(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    return {"hold": app.holds.get(_hold_id(args.hold_id)).to_dict(app.clock.now())}


def run_list(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    now = app.clock.now()
    holds = app.holds.list(account=args.account, status=args.status)
    return {"holds": [hold.to_dict(now) for hold in holds]}


def run_expire(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    expired = app.holds.expire_due()
    return {"expired": [hold.to_dict() for hold in expired]}
