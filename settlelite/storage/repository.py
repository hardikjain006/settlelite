"""Base class for repositories that map rows to domain objects."""

from __future__ import annotations

import sqlite3
from typing import Any

from .database import Database, Params


class Repository:
    """Thin helper around :class:`Database` shared by concrete repositories.

    Repositories hold no state of their own; they translate between rows
    and dataclasses and never open transactions themselves.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def _one(self, sql: str, params: Params = ()) -> sqlite3.Row | None:
        return self.db.query_one(sql, params)

    def _all(self, sql: str, params: Params = ()) -> list[sqlite3.Row]:
        return self.db.query_all(sql, params)

    def _insert(self, sql: str, params: Params = ()) -> int:
        cursor = self.db.execute(sql, params)
        rowid = cursor.lastrowid
        if rowid is None:
            raise RuntimeError("insert did not produce a row id")
        return int(rowid)

    def _scalar(self, sql: str, params: Params = (), default: Any = None) -> Any:
        row = self.db.query_one(sql, params)
        if row is None or row[0] is None:
            return default
        return row[0]
