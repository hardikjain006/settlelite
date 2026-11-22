"""Account statements with running balances."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from ..errors import ValidationError
from ..ledger.balances import BalanceService
from ..ledger.journal import JournalService
from ..ledger.models import Account
from ..money import format_amount


@dataclass(frozen=True)
class StatementLine:
    entry_id: int
    effective_date: date
    kind: str
    memo: str
    amount: int
    balance: int

    def to_dict(self, currency: str) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "effective_date": self.effective_date.isoformat(),
            "kind": self.kind,
            "memo": self.memo,
            "amount_minor": self.amount,
            "balance_minor": self.balance,
            "amount": format_amount(self.amount, currency),
        }


@dataclass(frozen=True)
class Statement:
    account: Account
    start: date | None
    end: date | None
    opening_balance: int
    lines: tuple[StatementLine, ...]

    @property
    def closing_balance(self) -> int:
        return self.lines[-1].balance if self.lines else self.opening_balance

    def to_dict(self) -> dict[str, Any]:
        currency = self.account.currency
        return {
            "account": self.account.code,
            "currency": currency,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "opening_balance_minor": self.opening_balance,
            "closing_balance_minor": self.closing_balance,
            "lines": [line.to_dict(currency) for line in self.lines],
        }


def build_statement(
    journal: JournalService,
    balances: BalanceService,
    account: Account,
    *,
    start: date | None = None,
    end: date | None = None,
) -> Statement:
    """List entries touching ``account`` between ``start`` and ``end``.

    Each line shows the net effect of the entry on the account (signed on
    its normal side) and the running balance after it.  Entries are
    ordered by effective date, then entry id.
    """
    if start is not None and end is not None and end < start:
        raise ValidationError("statement end date precedes start date")
    opening = balances.opening_balance(account, start) if start is not None else 0
    running = opening
    lines: list[StatementLine] = []
    for entry in journal.list(start=start, end=end, account=account):
        net = sum(
            account.type.signed(posting.direction, posting.amount)
            for posting in entry.postings
            if posting.account_id == account.id
        )
        running += net
        lines.append(
            StatementLine(
                entry_id=entry.id,
                effective_date=entry.effective_date,
                kind=entry.kind.value,
                memo=entry.memo,
                amount=net,
                balance=running,
            )
        )
    return Statement(account, start, end, opening, tuple(lines))
