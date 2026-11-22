"""Row mapping for accounts and journal entries."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date
from typing import Any

from ..clock import parse_date
from ..storage.repository import Repository
from .models import (
    Account,
    AccountStatus,
    AccountType,
    Direction,
    EntryKind,
    JournalEntry,
    Posting,
)


def _account_from_row(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        type=AccountType(row["type"]),
        currency=row["currency"],
        allow_negative=bool(row["allow_negative"]),
        status=AccountStatus(row["status"]),
        created_at=row["created_at"],
        closed_at=row["closed_at"],
    )


class AccountRepository(Repository):
    """Persistence for :class:`Account`."""

    def insert(
        self,
        *,
        code: str,
        name: str,
        type: AccountType,
        currency: str,
        allow_negative: bool,
        created_at: str,
    ) -> Account:
        account_id = self._insert(
            "INSERT INTO accounts (code, name, type, currency, allow_negative, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (code, name, type.value, currency, int(allow_negative), created_at),
        )
        account = self.by_id(account_id)
        assert account is not None
        return account

    def by_id(self, account_id: int) -> Account | None:
        row = self._one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        return _account_from_row(row) if row else None

    def by_code(self, code: str) -> Account | None:
        row = self._one("SELECT * FROM accounts WHERE code = ?", (code,))
        return _account_from_row(row) if row else None

    def by_codes(self, codes: Sequence[str]) -> dict[str, Account]:
        unique = sorted(set(codes))
        if not unique:
            return {}
        marks = ", ".join("?" for _ in unique)
        rows = self._all(f"SELECT * FROM accounts WHERE code IN ({marks})", unique)
        return {row["code"]: _account_from_row(row) for row in rows}

    def list(
        self,
        *,
        status: AccountStatus | None = None,
        currency: str | None = None,
        type: AccountType | None = None,
    ) -> list[Account]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if currency is not None:
            clauses.append("currency = ?")
            params.append(currency)
        if type is not None:
            clauses.append("type = ?")
            params.append(type.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._all(f"SELECT * FROM accounts{where} ORDER BY code", params)
        return [_account_from_row(row) for row in rows]

    def mark_closed(self, account_id: int, closed_at: str) -> None:
        self.db.execute(
            "UPDATE accounts SET status = 'closed', closed_at = ? WHERE id = ?",
            (closed_at, account_id),
        )

    def mark_open(self, account_id: int) -> None:
        self.db.execute(
            "UPDATE accounts SET status = 'open', closed_at = NULL WHERE id = ?", (account_id,)
        )


class JournalRepository(Repository):
    """Persistence for :class:`JournalEntry` and its postings."""

    def insert(
        self,
        *,
        request_id: str,
        kind: EntryKind,
        memo: str,
        effective_date: date,
        currency: str,
        created_at: str,
        reverses_entry_id: int | None,
        lines: Sequence[tuple[Account, Direction, int]],
    ) -> JournalEntry:
        entry_id = self._insert(
            "INSERT INTO journal_entries (request_id, kind, memo, effective_date, currency, "
            "created_at, reverses_entry_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                request_id,
                kind.value,
                memo,
                effective_date.isoformat(),
                currency,
                created_at,
                reverses_entry_id,
            ),
        )
        for line_no, (account, direction, amount) in enumerate(lines, start=1):
            self.db.execute(
                "INSERT INTO postings (entry_id, line_no, account_id, direction, amount) "
                "VALUES (?, ?, ?, ?, ?)",
                (entry_id, line_no, account.id, direction.value, amount),
            )
        entry = self.by_id(entry_id)
        assert entry is not None
        return entry

    def _postings(self, entry_ids: Sequence[int]) -> dict[int, list[Posting]]:
        result: dict[int, list[Posting]] = {entry_id: [] for entry_id in entry_ids}
        if not entry_ids:
            return result
        marks = ", ".join("?" for _ in entry_ids)
        rows = self._all(
            "SELECT p.entry_id, p.line_no, p.account_id, a.code, p.direction, p.amount "
            "FROM postings p JOIN accounts a ON a.id = p.account_id "
            f"WHERE p.entry_id IN ({marks}) ORDER BY p.entry_id, p.line_no",
            list(entry_ids),
        )
        for row in rows:
            result[row["entry_id"]].append(
                Posting(
                    line_no=row["line_no"],
                    account_id=row["account_id"],
                    account_code=row["code"],
                    direction=Direction(row["direction"]),
                    amount=row["amount"],
                )
            )
        return result

    def _entries_from_rows(self, rows: Sequence[sqlite3.Row]) -> list[JournalEntry]:
        postings = self._postings([row["id"] for row in rows])
        return [
            JournalEntry(
                id=row["id"],
                request_id=row["request_id"],
                kind=EntryKind(row["kind"]),
                memo=row["memo"],
                effective_date=parse_date(row["effective_date"]),
                currency=row["currency"],
                created_at=row["created_at"],
                reverses_entry_id=row["reverses_entry_id"],
                postings=tuple(postings[row["id"]]),
            )
            for row in rows
        ]

    def by_id(self, entry_id: int) -> JournalEntry | None:
        rows = self._all("SELECT * FROM journal_entries WHERE id = ?", (entry_id,))
        entries = self._entries_from_rows(rows)
        return entries[0] if entries else None

    def by_request_id(self, request_id: str) -> JournalEntry | None:
        rows = self._all("SELECT * FROM journal_entries WHERE request_id = ?", (request_id,))
        entries = self._entries_from_rows(rows)
        return entries[0] if entries else None

    def reversal_of(self, entry_id: int) -> JournalEntry | None:
        rows = self._all(
            "SELECT * FROM journal_entries WHERE reverses_entry_id = ?", (entry_id,)
        )
        entries = self._entries_from_rows(rows)
        return entries[0] if entries else None

    def list(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        account_id: int | None = None,
        kind: EntryKind | None = None,
        limit: int | None = None,
    ) -> list[JournalEntry]:
        """Entries ordered by effective date, then id."""
        clauses: list[str] = []
        params: list[Any] = []
        if start is not None:
            clauses.append("e.effective_date >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("e.effective_date <= ?")
            params.append(end.isoformat())
        if kind is not None:
            clauses.append("e.kind = ?")
            params.append(kind.value)
        if account_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM postings p WHERE p.entry_id = e.id AND p.account_id = ?)"
            )
            params.append(account_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT e.* FROM journal_entries e{where} ORDER BY e.effective_date, e.id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return self._entries_from_rows(self._all(sql, params))

    def totals_for_account(
        self, account_id: int, *, as_of: date | None = None, before: date | None = None
    ) -> tuple[int, int]:
        """Return ``(debits, credits)`` posted to an account.

        ``as_of`` includes entries effective on that date; ``before``
        excludes them.
        """
        clauses = ["p.account_id = ?"]
        params: list[Any] = [account_id]
        if as_of is not None:
            clauses.append("e.effective_date <= ?")
            params.append(as_of.isoformat())
        if before is not None:
            clauses.append("e.effective_date <= ?")
            params.append(before.isoformat())
        rows = self._all(
            "SELECT p.direction, SUM(p.amount) AS total FROM postings p "
            "JOIN journal_entries e ON e.id = p.entry_id "
            f"WHERE {' AND '.join(clauses)} GROUP BY p.direction",
            params,
        )
        totals = {row["direction"]: int(row["total"]) for row in rows}
        return totals.get("debit", 0), totals.get("credit", 0)

    def totals_by_account(self, *, as_of: date | None = None) -> dict[int, tuple[int, int]]:
        params: list[Any] = []
        where = ""
        if as_of is not None:
            where = " WHERE e.effective_date <= ?"
            params.append(as_of.isoformat())
        rows = self._all(
            "SELECT p.account_id, p.direction, SUM(p.amount) AS total FROM postings p "
            f"JOIN journal_entries e ON e.id = p.entry_id{where} "
            "GROUP BY p.account_id, p.direction",
            params,
        )
        result: dict[int, list[int]] = {}
        for row in rows:
            pair = result.setdefault(row["account_id"], [0, 0])
            pair[0 if row["direction"] == "debit" else 1] = int(row["total"])
        return {account_id: (pair[0], pair[1]) for account_id, pair in result.items()}

    def count_postings(self, account_id: int) -> int:
        count = self._scalar("SELECT COUNT(*) FROM postings WHERE account_id = ?", (account_id,), 0)
        return int(count)
