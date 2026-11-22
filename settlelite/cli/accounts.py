"""``account`` command."""

from __future__ import annotations

import argparse

from ..ledger.models import AccountType
from .common import CommandContext, Payload, add_runtime_options


def register(subparsers: argparse._SubParsersAction) -> None:
    account = subparsers.add_parser("account", help="open, inspect and close accounts")
    sub = account.add_subparsers(dest="action", required=True, metavar="ACTION")

    open_ = sub.add_parser("open", help="open a new account")
    open_.add_argument("code", help="unique account code, e.g. wallet:alice")
    open_.add_argument("--name", required=True)
    open_.add_argument("--type", required=True, choices=[t.value for t in AccountType])
    open_.add_argument("--currency", help="ISO currency code (default from config)")
    open_.add_argument(
        "--allow-negative", action="store_true", help="permit the balance to go below zero"
    )
    open_.set_defaults(handler=run_open)

    show = sub.add_parser("show", help="show an account and its balance")
    show.add_argument("code")
    show.set_defaults(handler=run_show)

    list_ = sub.add_parser("list", help="list accounts")
    list_.add_argument("--status", choices=["open", "closed"])
    list_.add_argument("--currency")
    list_.add_argument("--type", choices=[t.value for t in AccountType])
    list_.set_defaults(handler=run_list)

    close = sub.add_parser("close", help="close an account with zero balance")
    close.add_argument("code")
    close.set_defaults(handler=run_close)

    reopen = sub.add_parser("reopen", help="reopen a closed account")
    reopen.add_argument("code")
    reopen.set_defaults(handler=run_reopen)

    for parser in (open_, show, list_, close, reopen):
        add_runtime_options(parser)


def run_open(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    account = app.accounts.open(
        args.code,
        args.name,
        args.type,
        args.currency or app.config.default_currency,
        allow_negative=args.allow_negative,
    )
    return {"account": account.to_dict()}


def run_show(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app = ctx.app()
    account = app.accounts.get(args.code)
    return {"account": account.to_dict(), "balance": app.balances.for_account(account).to_dict()}


def run_list(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    accounts = ctx.app().accounts.list(status=args.status, currency=args.currency, type=args.type)
    return {"accounts": [account.to_dict() for account in accounts]}


def run_close(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    return {"account": ctx.app().accounts.close(args.code).to_dict()}


def run_reopen(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    return {"account": ctx.app().accounts.reopen(args.code).to_dict()}
