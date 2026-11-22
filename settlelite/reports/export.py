"""Journal export as CSV or JSON lines.

Exports contain one row per posting.  Rows are ordered by effective date,
entry id and line number, so exporting the same ledger twice produces
byte-identical output.
"""

from __future__ import annotations

import csv
import enum
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any, TextIO

from ..errors import ValidationError
from ..jsonutil import canonical_json
from ..money import format_amount
from ..storage.database import Database

EXPORT_COLUMNS: tuple[str, ...] = (
    "entry_id",
    "request_id",
    "kind",
    "effective_date",
    "created_at",
    "line",
    "account",
    "direction",
    "amount_minor",
    "amount",
    "currency",
    "memo",
    "reverses_entry_id",
)


class ExportFormat(enum.Enum):
    CSV = "csv"
    JSONL = "jsonl"

    @classmethod
    def parse(cls, value: object) -> "ExportFormat":
        if isinstance(value, ExportFormat):
            return value
        for member in cls:
            if member.value == value:
                return member
        raise ValidationError(f"export format must be csv or jsonl: {value!r}")


@dataclass(frozen=True)
class ExportFilter:
    """Selects which postings are exported."""

    start: date | None = None
    end: date | None = None
    account: str | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValidationError("export end date precedes start date")


@dataclass(frozen=True)
class ExportRow:
    entry_id: int
    request_id: str
    kind: str
    effective_date: str
    created_at: str
    line: int
    account: str
    direction: str
    amount_minor: int
    currency: str
    memo: str
    reverses_entry_id: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "request_id": self.request_id,
            "kind": self.kind,
            "effective_date": self.effective_date,
            "created_at": self.created_at,
            "line": self.line,
            "account": self.account,
            "direction": self.direction,
            "amount_minor": self.amount_minor,
            "amount": format_amount(self.amount_minor, self.currency),
            "currency": self.currency,
            "memo": self.memo,
            "reverses_entry_id": self.reverses_entry_id,
        }


def iter_export_rows(db: Database, selection: ExportFilter | None = None) -> Iterator[ExportRow]:
    """Yield postings matching ``selection`` in deterministic order."""
    selection = selection or ExportFilter()
    clauses: list[str] = []
    params: list[Any] = []
    if selection.start is not None:
        clauses.append("book_date >= ?")
        params.append(selection.start.isoformat())
    if selection.end is not None:
        clauses.append("book_date <= ?")
        params.append(selection.end.isoformat())
    if selection.account is not None:
        clauses.append("a.code = ?")
        params.append(selection.account)
    if selection.currency is not None:
        clauses.append("e.currency = ?")
        params.append(selection.currency)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        "SELECT e.id AS entry_id, e.request_id, e.kind, e.effective_date, e.created_at, "
        "p.line_no, a.code AS account, p.direction, p.amount, e.currency, e.memo, "
        "e.reverses_entry_id, COALESCE(o.effective_date, e.effective_date) AS book_date "
        "FROM postings p "
        "JOIN journal_entries e ON e.id = p.entry_id "
        "LEFT JOIN journal_entries o ON o.id = e.reverses_entry_id "
        f"JOIN accounts a ON a.id = p.account_id{where} "
        "ORDER BY book_date, e.id, p.line_no"
    )
    for row in db.iterate(sql, params):
        yield ExportRow(
            entry_id=row["entry_id"],
            request_id=row["request_id"],
            kind=row["kind"],
            effective_date=row["effective_date"],
            created_at=row["created_at"],
            line=row["line_no"],
            account=row["account"],
            direction=row["direction"],
            amount_minor=row["amount"],
            currency=row["currency"],
            memo=row["memo"],
            reverses_entry_id=row["reverses_entry_id"],
        )


def _csv_value(value: object) -> str:
    return "" if value is None else str(value)


def write_csv(rows: Iterator[ExportRow], stream: TextIO) -> int:
    """Write rows as CSV with a header line; returns the number of rows."""
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    count = 0
    for row in rows:
        data = row.to_dict()
        writer.writerow([_csv_value(data[column]) for column in EXPORT_COLUMNS])
        count += 1
    return count


def write_jsonl(rows: Iterator[ExportRow], stream: TextIO) -> int:
    """Write one canonical JSON object per row; returns the number of rows."""
    count = 0
    for row in rows:
        stream.write(canonical_json(row.to_dict()) + "\n")
        count += 1
    return count


def export_journal(
    db: Database,
    stream: TextIO,
    *,
    fmt: ExportFormat | str = ExportFormat.CSV,
    selection: ExportFilter | None = None,
) -> int:
    """Export postings to ``stream`` and return the number of rows written."""
    format_value = ExportFormat.parse(fmt)
    rows = iter_export_rows(db, selection)
    if format_value is ExportFormat.CSV:
        return write_csv(rows, stream)
    return write_jsonl(rows, stream)
