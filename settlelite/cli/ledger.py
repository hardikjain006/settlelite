"""``post``, ``reverse``, ``entry``, ``balance`` and ``statement`` commands."""

from __future__ import annotations

import argparse

from ..errors import ValidationError
from ..ledger.models import Direction, EntryKind, PostingInput
from ..reports.balances import build_balance_report
from ..reports.statement import build_statement
from .common import (
    CommandContext,
    Payload,
    add_runtime_options,
    amount_arg,
    date_arg,
    positive_int_arg,
    split_assignment,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    post = subparsers.add_parser("post", help="post a balanced journal entry")
    post.add_argument("--request-id", required=True, help="idempotency key for this entry")
    for side in ("debit", "credit"):
        post.add_argument(
            f"--{side}",
            action="append",
            default=[],
            metavar="ACCOUNT=AMOUNT",
            help=f"{side} line (repeatable)",
        )
    post.add_argument("--memo", default="")
    post.add_argument("--date", help="effective date YYYY-MM-DD (default: today)")
    post.add_argument("--minor", action="store_true", help="amounts are integer minor units")
    add_runtime_options(post)
    post.set_defaults(handler=run_post)

    reverse = subparsers.add_parser("reverse", help="reverse a journal entry")
    reverse.add_argument("entry_id")
    reverse.add_argument("--request-id", required=True)
    reverse.add_argument("--memo")
    reverse.add_argument("--date", help="effective date of the reversal")
    add_runtime_options(reverse)
    reverse.set_defaults(handler=run_reverse)

    entry = subparsers.add_parser("entry", help="inspect journal entries")
    entry_sub = entry.add_subparsers(dest="action", required=True, metavar="ACTION")
    show = entry_sub.add_parser("show", help="show one entry")
    show.add_argument("entry_id")
    show.set_defaults(handler=run_entry_show)
    list_ = entry_sub.add_parser("list", help="list entries by effective date")
    list_.add_argument("--from", dest="start")
    list_.add_argument("--to", dest="end")
    list_.add_argument("--account")
    list_.add_argument("--kind", choices=[kind.value for kind in EntryKind])
    list_.add_argument("--limit")
    list_.set_defaults(handler=run_entry_list)
    for parser in (show, list_):
        add_runtime_options(parser)

    balance = subparsers.add_parser("balance", help="account balances and trial balance")
    balance.add_argument("account", nargs="?", help="single account (default: all accounts)")
    balance.add_argument("--as-of", help="include entries effective on or before this date")
    balance.add_argument("--currency", help="restrict the report to one currency")
    balance.add_argument("--nonzero", action="store_true", help="omit zero-balance accounts")
    balance.add_argument("--include-closed", action="store_true")
    balance.add_argument("--trial", action="store_true", help="per-currency debit/credit totals")
    add_runtime_options(balance)
    balance.set_defaults(handler=run_balance)

    statement = subparsers.add_parser("statement", help="account statement with running balance")
    statement.add_argument("account")
    statement.add_argument("--from", dest="start")
    statement.add_argument("--to", dest="end")
    add_runtime_options(statement)
    statement.set_defaults(handler=run_statement)


def _entry_id(value: str) -> int:
    number = positive_int_arg(value, name="entry id")
    assert number is not None
    return number


def run_post(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    specs = [(Direction.DEBIT, spec, "--debit") for spec in args.debit]
    specs += [(Direction.CREDIT, spec, "--credit") for spec in args.credit]
    if not args.debit or not args.credit:
        raise ValidationError("post needs at least one --debit and one --credit")
    lines = []
    for direction, spec, option in specs:
        code, amount_text = split_assignment(spec, option=option)
        account = app.accounts.get(code)
        amount = amount_arg(amount_text, account.currency, minor=args.minor)
        lines.append(PostingInput(code, direction, amount))
    result = app.journal.post(
        request_id=args.request_id,
        lines=lines,
        memo=args.memo,
        effective_date=date_arg(args.date),
    )
    return result.to_dict()


def run_reverse(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    result = ctx.app().journal.reverse(
        request_id=args.request_id,
        entry_id=_entry_id(args.entry_id),
        memo=args.memo,
        effective_date=date_arg(args.date),
    )
    return result.to_dict()


def run_entry_show(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    entry = app.journal.get(_entry_id(args.entry_id))
    reversal = app.journal.reversal_of(entry.id)
    return {"entry": entry.to_dict(), "reversed_by": reversal.id if reversal else None}


def run_entry_list(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    account = app.accounts.get(args.account) if args.account else None
    entries = app.journal.list(
        start=date_arg(args.start),
        end=date_arg(args.end),
        account=account,
        kind=EntryKind(args.kind) if args.kind else None,
        limit=positive_int_arg(args.limit, name="--limit"),
    )
    return {"entries": [entry.to_dict() for entry in entries]}


def run_balance(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    as_of = date_arg(args.as_of)
    if args.trial:
        if args.account:
            raise ValidationError("--trial reports on all accounts; omit the account")
        lines = app.balances.trial_balance(as_of=as_of)
        return {
            "as_of": as_of.isoformat() if as_of else None,
            "trial_balance": [line.to_dict() for line in lines],
            "balanced": all(line.balanced for line in lines),
        }
    if args.account:
        account = app.accounts.get(args.account)
        return {"balance": app.balances.for_account(account, as_of=as_of).to_dict()}
    accounts = app.accounts.list(currency=args.currency)
    report = build_balance_report(
        app.balances,
        accounts,
        as_of=as_of,
        include_zero=not args.nonzero,
        include_closed=args.include_closed,
    )
    return report.to_dict()


def run_statement(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    statement = build_statement(
        app.journal,
        app.balances,
        app.accounts.get(args.account),
        start=date_arg(args.start),
        end=date_arg(args.end),
    )
    return {"statement": statement.to_dict()}
