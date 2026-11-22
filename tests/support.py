"""Shared fixtures for the test suite."""

from __future__ import annotations

import io
import json
from datetime import date

from settlelite.app import SettleLite
from settlelite.cli import main
from settlelite.clock import FixedClock
from settlelite.config import Config
from settlelite.ledger import PostingInput

START = "2026-03-02T09:00:00Z"
"""A Monday."""

MONDAY = date(2026, 3, 2)


def make_app(config: Config | None = None, *, now: str = START) -> SettleLite:
    """An in-memory instance with a fixed clock."""
    return SettleLite.in_memory(config=config, clock=FixedClock(now))


def standard_accounts(app: SettleLite, currency: str = "USD") -> None:
    """Open the chart of accounts most tests use."""
    app.accounts.open("cash", "Operating cash", "asset", currency, allow_negative=True)
    app.accounts.open("wallet:alice", "Alice", "liability", currency)
    app.accounts.open("wallet:bob", "Bob", "liability", currency)
    app.accounts.open("merchant:shop", "Shop", "liability", currency)
    app.accounts.open("revenue:fees", "Fee revenue", "revenue", currency)


def debit(account: str, amount: int) -> PostingInput:
    return PostingInput(account, "debit", amount)


def credit(account: str, amount: int) -> PostingInput:
    return PostingInput(account, "credit", amount)


def fund(app: SettleLite, account: str, amount: int, request_id: str | None = None):
    """Deposit ``amount`` from cash into a liability account."""
    return app.journal.post(
        request_id=request_id or f"fund-{account}-{amount}",
        lines=[debit("cash", amount), credit(account, amount)],
        memo="deposit",
    ).entry


def balance(app: SettleLite, code: str) -> int:
    return app.balances.balance(app.accounts.get(code))


def available(app: SettleLite, code: str) -> int:
    return app.balances.available(app.accounts.get(code))


def run_cli(*argv: str, stdin: str = "") -> tuple[int, str]:
    """Run the CLI in-process and return ``(exit status, stdout)``."""
    out = io.StringIO()
    status = main(list(argv), stdin=io.StringIO(stdin), stdout=out, stderr=io.StringIO())
    return status, out.getvalue()


def run_json(*argv: str, stdin: str = "") -> tuple[int, dict]:
    """Run a CLI command that prints exactly one JSON object."""
    status, text = run_cli(*argv, stdin=stdin)
    lines = text.splitlines()
    assert len(lines) == 1, text
    return status, json.loads(lines[0])
