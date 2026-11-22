"""Reservation source backed by the holds table."""

from __future__ import annotations

from ..clock import Clock, format_timestamp
from ..storage.database import Database
from .repository import HoldRepository


class HoldReservations:
    """Implements :class:`settlelite.ledger.ReservationSource` using holds.

    Only holds that are active and not yet past their expiry time reserve
    funds, so an overdue hold stops affecting available balance even before
    it is swept.
    """

    def __init__(self, db: Database, clock: Clock) -> None:
        self.repo = HoldRepository(db)
        self.clock = clock

    def _now(self) -> str:
        return format_timestamp(self.clock.now())

    def reserved_amount(self, account_id: int, *, exclude_hold_id: int | None = None) -> int:
        return self.repo.reserved_total(account_id, self._now(), exclude_hold_id=exclude_hold_id)

    def has_active(self, account_id: int) -> bool:
        return self.repo.has_active(account_id, self._now())
