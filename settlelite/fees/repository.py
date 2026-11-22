"""Versioned storage of fee schedules."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..money import RoundingMode
from ..storage.repository import Repository
from .models import FeeSchedule, parse_schedule


def _schedule_from_row(row: sqlite3.Row) -> FeeSchedule:
    definition = json.loads(row["definition"])
    schedule = parse_schedule(definition, default_rounding=RoundingMode(definition["rounding"]))
    return FeeSchedule(
        name=schedule.name,
        currency=schedule.currency,
        rounding=schedule.rounding,
        components=schedule.components,
        minimum=schedule.minimum,
        maximum=schedule.maximum,
        splits=schedule.splits,
        version=row["version"],
    )


class FeeScheduleRepository(Repository):
    """Each save of a changed schedule creates a new immutable version."""

    def insert(
        self, schedule: FeeSchedule, *, version: int, definition: str, created_at: str
    ) -> FeeSchedule:
        self._insert(
            "INSERT INTO fee_schedules (name, version, currency, definition, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (schedule.name, version, schedule.currency, definition, created_at),
        )
        stored = self.get(schedule.name, version)
        assert stored is not None
        return stored

    def get(self, name: str, version: int | None = None) -> FeeSchedule | None:
        if version is None:
            row = self._one(
                "SELECT * FROM fee_schedules WHERE name = ? ORDER BY version DESC LIMIT 1", (name,)
            )
        else:
            row = self._one(
                "SELECT * FROM fee_schedules WHERE name = ? AND version = ?", (name, version)
            )
        return _schedule_from_row(row) if row else None

    def latest_definition(self, name: str) -> tuple[int, str] | None:
        row = self._one(
            "SELECT version, definition FROM fee_schedules WHERE name = ? "
            "ORDER BY version DESC LIMIT 1",
            (name,),
        )
        return (row["version"], row["definition"]) if row else None

    def latest_all(self) -> list[FeeSchedule]:
        rows = self._all(
            "SELECT f.* FROM fee_schedules f WHERE f.version = "
            "(SELECT MAX(version) FROM fee_schedules g WHERE g.name = f.name) ORDER BY f.name"
        )
        return [_schedule_from_row(row) for row in rows]

    def history(self, name: str) -> list[FeeSchedule]:
        rows = self._all("SELECT * FROM fee_schedules WHERE name = ? ORDER BY version", (name,))
        return [_schedule_from_row(row) for row in rows]


@dataclass(frozen=True)
class FeeCharge:
    """A recorded fee charge, including zero-fee charges that posted nothing."""

    id: int
    request_id: str
    schedule_name: str
    schedule_version: int
    payer: str
    base_amount: int
    fee_amount: int
    entry_id: int | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "schedule": self.schedule_name,
            "version": self.schedule_version,
            "payer": self.payer,
            "base_amount_minor": self.base_amount,
            "fee_minor": self.fee_amount,
            "entry_id": self.entry_id,
            "created_at": self.created_at,
        }


_CHARGE_SELECT = (
    "SELECT c.*, a.code AS payer FROM fee_charges c JOIN accounts a ON a.id = c.payer_account_id"
)


def _charge_from_row(row: sqlite3.Row) -> FeeCharge:
    return FeeCharge(
        id=row["id"],
        request_id=row["request_id"],
        schedule_name=row["schedule_name"],
        schedule_version=row["schedule_version"],
        payer=row["payer"],
        base_amount=row["base_amount"],
        fee_amount=row["fee_amount"],
        entry_id=row["entry_id"],
        created_at=row["created_at"],
    )


class FeeChargeRepository(Repository):
    """Persistence for :class:`FeeCharge`."""

    def insert(
        self,
        *,
        request_id: str,
        schedule: FeeSchedule,
        payer_account_id: int,
        base_amount: int,
        fee_amount: int,
        entry_id: int | None,
        created_at: str,
    ) -> FeeCharge:
        charge_id = self._insert(
            "INSERT INTO fee_charges (request_id, schedule_name, schedule_version, "
            "payer_account_id, base_amount, fee_amount, entry_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request_id,
                schedule.name,
                schedule.version,
                payer_account_id,
                base_amount,
                fee_amount,
                entry_id,
                created_at,
            ),
        )
        charge = self.by_id(charge_id)
        assert charge is not None
        return charge

    def by_id(self, charge_id: int) -> FeeCharge | None:
        row = self._one(f"{_CHARGE_SELECT} WHERE c.id = ?", (charge_id,))
        return _charge_from_row(row) if row else None

    def list(
        self, *, schedule: str | None = None, payer_account_id: int | None = None
    ) -> list[FeeCharge]:
        clauses: list[str] = []
        params: list[Any] = []
        if schedule is not None:
            clauses.append("c.schedule_name = ?")
            params.append(schedule)
        if payer_account_id is not None:
            clauses.append("c.payer_account_id = ?")
            params.append(payer_account_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._all(f"{_CHARGE_SELECT}{where} ORDER BY c.id", params)
        return [_charge_from_row(row) for row in rows]
