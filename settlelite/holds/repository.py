"""Row mapping for holds."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..storage.repository import Repository
from .models import Hold, HoldStatus

_SELECT = (
    "SELECT h.*, a.code AS account_code FROM holds h JOIN accounts a ON a.id = h.account_id"
)


def _hold_from_row(row: sqlite3.Row) -> Hold:
    return Hold(
        id=row["id"],
        request_id=row["request_id"],
        account_id=row["account_id"],
        account_code=row["account_code"],
        amount=row["amount"],
        currency=row["currency"],
        status=HoldStatus(row["status"]),
        reference=row["reference"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        resolved_at=row["resolved_at"],
        captured_amount=row["captured_amount"],
        capture_entry_id=row["capture_entry_id"],
    )


class HoldRepository(Repository):
    """Persistence for :class:`Hold`."""

    def insert(
        self,
        *,
        request_id: str,
        account_id: int,
        amount: int,
        currency: str,
        reference: str,
        created_at: str,
        expires_at: str,
    ) -> Hold:
        hold_id = self._insert(
            "INSERT INTO holds (request_id, account_id, amount, currency, reference, "
            "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (request_id, account_id, amount, currency, reference, created_at, expires_at),
        )
        hold = self.by_id(hold_id)
        assert hold is not None
        return hold

    def by_id(self, hold_id: int) -> Hold | None:
        row = self._one(f"{_SELECT} WHERE h.id = ?", (hold_id,))
        return _hold_from_row(row) if row else None

    def by_request_id(self, request_id: str) -> Hold | None:
        row = self._one(f"{_SELECT} WHERE h.request_id = ?", (request_id,))
        return _hold_from_row(row) if row else None

    def list(
        self, *, account_id: int | None = None, status: HoldStatus | None = None
    ) -> list[Hold]:
        clauses: list[str] = []
        params: list[Any] = []
        if account_id is not None:
            clauses.append("h.account_id = ?")
            params.append(account_id)
        if status is not None:
            clauses.append("h.status = ?")
            params.append(status.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return [_hold_from_row(row) for row in self._all(f"{_SELECT}{where} ORDER BY h.id", params)]

    def reserved_total(self, account_id: int, now: str, *, exclude_hold_id: int | None) -> int:
        """Sum of active, unexpired holds on an account."""
        sql = (
            "SELECT SUM(amount) FROM holds WHERE account_id = ? AND status = 'active' "
            "AND expires_at > ?"
        )
        params: list[Any] = [account_id, now]
        if exclude_hold_id is not None:
            sql += " AND id != ?"
            params.append(exclude_hold_id)
        return int(self._scalar(sql, params, 0))

    def has_active(self, account_id: int, now: str) -> bool:
        row = self._one(
            "SELECT 1 FROM holds WHERE account_id = ? AND status = 'active' AND expires_at > ? "
            "LIMIT 1",
            (account_id, now),
        )
        return row is not None

    def due_for_expiry(self, now: str) -> list[Hold]:
        rows = self._all(
            f"{_SELECT} WHERE h.status = 'active' AND h.expires_at < ? ORDER BY h.id", (now,)
        )
        return [_hold_from_row(row) for row in rows]

    def resolve(
        self,
        hold_id: int,
        status: HoldStatus,
        resolved_at: str,
        *,
        captured_amount: int | None = None,
        capture_entry_id: int | None = None,
    ) -> None:
        cursor = self.db.execute(
            "UPDATE holds SET status = ?, resolved_at = ?, captured_amount = ?, "
            "capture_entry_id = ? WHERE id = ? AND status = 'active'",
            (status.value, resolved_at, captured_amount, capture_entry_id, hold_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"hold {hold_id} was not active")
