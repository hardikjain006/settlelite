"""Interface through which the ledger learns about reserved funds.

The ledger does not know how reservations are made; it only asks a
:class:`ReservationSource` how much of an account's balance is spoken for.
The holds package provides the production implementation.
"""

from __future__ import annotations

from typing import Protocol


class ReservationSource(Protocol):
    """Reports funds reserved against an account."""

    def reserved_amount(self, account_id: int, *, exclude_hold_id: int | None = None) -> int:
        """Total currently reserved on ``account_id`` in minor units."""
        ...

    def has_active(self, account_id: int) -> bool:
        """Whether any reservation is still in force on the account."""
        ...


class NoReservations:
    """A reservation source for ledgers that do not use holds."""

    def reserved_amount(self, account_id: int, *, exclude_hold_id: int | None = None) -> int:
        return 0

    def has_active(self, account_id: int) -> bool:
        return False
