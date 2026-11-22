"""Application facade that wires the services together.

:class:`SettleLite` owns one database connection and exposes each service
as an attribute::

    with SettleLite.open("ledger.db", create=True) as app:
        app.accounts.open("cash", "Operating cash", "asset", "USD", allow_negative=True)
"""

from __future__ import annotations

import os

from .audit import AuditLog
from .calendar import BusinessCalendar
from .clock import Clock, SystemClock
from .config import Config, default_config
from .errors import ConfigError
from .fees.service import FeeService
from .holds.reservations import HoldReservations
from .holds.service import HoldService
from .ledger.accounts import AccountService
from .ledger.balances import BalanceService
from .ledger.journal import JournalService
from .storage.database import Database


class SettleLite:
    """All SettleLite services bound to one database."""

    def __init__(
        self,
        db: Database,
        *,
        config: Config | None = None,
        clock: Clock | None = None,
        calendar: BusinessCalendar | None = None,
        actor: str = "system",
    ) -> None:
        self.db = db
        self.config = config or default_config()
        self.clock = clock or SystemClock()
        self.calendar = calendar or self.config.build_calendar()
        self.audit = AuditLog(db, self.clock, actor=actor)
        self.reservations = HoldReservations(db, self.clock)
        self.balances = BalanceService(db, self.reservations)
        self.accounts = AccountService(
            db, self.clock, self.audit, balances=self.balances, reservations=self.reservations
        )
        self.journal = JournalService(
            db,
            self.clock,
            self.audit,
            self.balances,
            calendar=self.calendar,
            non_business_day=self.config.posting.non_business_day,
        )
        self.holds = HoldService(
            db,
            self.clock,
            self.audit,
            self.accounts,
            self.journal,
            self.balances,
            default_expiry=self.config.holds.default_expiry,
            max_expiry=self.config.holds.max_expiry,
        )
        self.fees = FeeService(
            db,
            self.clock,
            self.audit,
            self.accounts,
            self.journal,
            default_rounding=self.config.rounding,
        )

    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str] | None = None,
        *,
        config: Config | None = None,
        clock: Clock | None = None,
        create: bool = False,
        actor: str = "system",
    ) -> "SettleLite":
        """Open an initialised database.

        With ``create`` the file is created if needed and migrated to the
        latest schema; otherwise the schema must already be current.
        """
        config = config or default_config()
        target = path if path is not None else config.database
        if target is None:
            raise ConfigError("no database configured; pass --db or set SETTLELITE_DB")
        db = Database.open(target, create=create)
        try:
            if create:
                db.migrate(clock=clock)
            db.require_current_schema()
            return cls(db, config=config, clock=clock, actor=actor)
        except BaseException:
            db.close()
            raise

    @classmethod
    def in_memory(
        cls, *, config: Config | None = None, clock: Clock | None = None
    ) -> "SettleLite":
        """A fully migrated private in-memory instance."""
        return cls(Database.memory(clock=clock), config=config, clock=clock)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "SettleLite":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
