"""Balance report across accounts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from ..ledger.balances import AccountBalance, BalanceService
from ..ledger.models import Account, AccountStatus, AccountType


@dataclass(frozen=True)
class BalanceReportRow:
    balance: AccountBalance

    def to_dict(self) -> dict[str, Any]:
        data = self.balance.to_dict()
        data["name"] = self.balance.account.name
        data["status"] = self.balance.account.status.value
        return data


@dataclass(frozen=True)
class CurrencyTotals:
    """Per-currency totals; ``debits == credits`` for a consistent ledger."""

    currency: str
    debits: int
    credits: int
    by_type: dict[str, int]

    @property
    def balanced(self) -> bool:
        return self.debits == self.credits

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "debits_minor": self.debits,
            "credits_minor": self.credits,
            "balanced": self.balanced,
            "by_type_minor": dict(sorted(self.by_type.items())),
        }


@dataclass(frozen=True)
class BalanceReport:
    as_of: date | None
    rows: tuple[BalanceReportRow, ...]
    totals: tuple[CurrencyTotals, ...]

    @property
    def balanced(self) -> bool:
        return all(total.balanced for total in self.totals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "accounts": [row.to_dict() for row in self.rows],
            "totals": [total.to_dict() for total in self.totals],
            "balanced": self.balanced,
        }


def build_balance_report(
    balances: BalanceService,
    accounts: list[Account],
    *,
    as_of: date | None = None,
    include_zero: bool = True,
    include_closed: bool = False,
) -> BalanceReport:
    """Build a report of ``accounts`` ordered by account code.

    Totals are computed over every selected account, grouped by currency.
    Reserved amounts always reflect holds active now, even when ``as_of``
    selects an earlier ledger date.
    """
    selected = [
        account
        for account in accounts
        if include_closed or account.status is AccountStatus.OPEN
    ]
    rows: list[BalanceReportRow] = []
    totals: dict[str, dict[str, Any]] = {}
    for item in balances.for_accounts(selected, as_of=as_of):
        currency = item.account.currency
        bucket = totals.setdefault(
            currency, {"debits": 0, "credits": 0, "by_type": {t.value: 0 for t in AccountType}}
        )
        bucket["debits"] += item.debits
        bucket["credits"] += item.credits
        bucket["by_type"][item.account.type.value] += item.balance
        if include_zero or item.balance != 0 or item.reserved != 0:
            rows.append(BalanceReportRow(item))
    return BalanceReport(
        as_of=as_of,
        rows=tuple(rows),
        totals=tuple(
            CurrencyTotals(currency, bucket["debits"], bucket["credits"], bucket["by_type"])
            for currency, bucket in sorted(totals.items())
        ),
    )

