"""Balance queries over the journal."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..money import format_amount
from ..storage.database import Database
from .models import Account, Direction
from .repository import AccountRepository, JournalRepository
from .reservations import NoReservations, ReservationSource


@dataclass(frozen=True)
class AccountBalance:
    """Balance of one account.

    ``balance`` is signed on the account's normal side: a positive balance
    on a liability means the ledger owes money to the account holder.
    ``available`` subtracts funds reserved by active holds.
    """

    account: Account
    debits: int
    credits: int
    balance: int
    reserved: int
    as_of: date | None = None

    @property
    def available(self) -> int:
        return self.balance - self.reserved

    def to_dict(self) -> dict[str, Any]:
        currency = self.account.currency
        return {
            "account": self.account.code,
            "type": self.account.type.value,
            "currency": currency,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "debits_minor": self.debits,
            "credits_minor": self.credits,
            "balance_minor": self.balance,
            "reserved_minor": self.reserved,
            "available_minor": self.available,
            "balance": format_amount(self.balance, currency),
            "available": format_amount(self.available, currency),
        }


@dataclass(frozen=True)
class TrialBalanceLine:
    """Total debits and credits for one currency."""

    currency: str
    debits: int
    credits: int

    @property
    def balanced(self) -> bool:
        return self.debits == self.credits

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "debits_minor": self.debits,
            "credits_minor": self.credits,
            "balanced": self.balanced,
        }


def signed_balance(account: Account, debits: int, credits: int) -> int:
    """Net balance on the account's normal side."""
    net = debits - credits
    return net if account.type.normal_side is Direction.DEBIT else -net


class BalanceService:
    """Computes balances from postings."""

    def __init__(self, db: Database, reservations: ReservationSource | None = None) -> None:
        self.db = db
        self.accounts = AccountRepository(db)
        self.journal = JournalRepository(db)
        self.reservations: ReservationSource = reservations or NoReservations()

    def balance(self, account: Account, *, as_of: date | None = None) -> int:
        debits, credits = self.journal.totals_for_account(account.id, as_of=as_of)
        return signed_balance(account, debits, credits)

    def opening_balance(self, account: Account, start: date) -> int:
        """Balance from entries effective strictly before ``start``."""
        debits, credits = self.journal.totals_for_account(account.id, before=start)
        return signed_balance(account, debits, credits)

    def for_account(self, account: Account, *, as_of: date | None = None) -> AccountBalance:
        debits, credits = self.journal.totals_for_account(account.id, as_of=as_of)
        return AccountBalance(
            account=account,
            debits=debits,
            credits=credits,
            balance=signed_balance(account, debits, credits),
            reserved=self.reservations.reserved_amount(account.id),
            as_of=as_of,
        )

    def for_accounts(
        self, accounts: Iterable[Account], *, as_of: date | None = None
    ) -> list[AccountBalance]:
        totals = self.journal.totals_by_account(as_of=as_of)
        result = []
        for account in sorted(accounts, key=lambda item: item.code):
            debits, credits = totals.get(account.id, (0, 0))
            result.append(
                AccountBalance(
                    account=account,
                    debits=debits,
                    credits=credits,
                    balance=signed_balance(account, debits, credits),
                    reserved=self.reservations.reserved_amount(account.id),
                    as_of=as_of,
                )
            )
        return result

    def available(self, account: Account, *, exclude_hold_id: int | None = None) -> int:
        """Current balance minus reservations."""
        reserved = self.reservations.reserved_amount(account.id, exclude_hold_id=exclude_hold_id)
        return self.balance(account) - reserved

    def trial_balance(self, *, as_of: date | None = None) -> list[TrialBalanceLine]:
        """Debit and credit totals per currency, sorted by currency code."""
        params: list[Any] = []
        where = ""
        if as_of is not None:
            where = " WHERE COALESCE(r.effective_date, e.effective_date) <= ?"
            params.append(as_of.isoformat())
        rows = self.db.query_all(
            "SELECT e.currency, p.direction, SUM(p.amount) AS total FROM postings p "
            "JOIN journal_entries e ON e.id = p.entry_id "
            f"LEFT JOIN journal_entries r ON r.id = e.reverses_entry_id{where} "
            "GROUP BY e.currency, p.direction ORDER BY e.currency",
            params,
        )
        totals: dict[str, list[int]] = {}
        for row in rows:
            pair = totals.setdefault(row["currency"], [0, 0])
            pair[0 if row["direction"] == "debit" else 1] = int(row["total"])
        return [
            TrialBalanceLine(currency, pair[0], pair[1])
            for currency, pair in sorted(totals.items())
        ]
