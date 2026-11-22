"""Authorization hold records."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..clock import parse_timestamp
from ..errors import ValidationError
from ..money import format_amount


class HoldStatus(enum.Enum):
    """Stored lifecycle state of a hold."""

    ACTIVE = "active"
    CAPTURED = "captured"
    RELEASED = "released"
    EXPIRED = "expired"

    @classmethod
    def parse(cls, value: object) -> "HoldStatus":
        if isinstance(value, HoldStatus):
            return value
        for member in cls:
            if member.value == value:
                return member
        names = ", ".join(member.value for member in cls)
        raise ValidationError(f"hold status must be one of {names}: {value!r}")


@dataclass(frozen=True)
class Hold:
    """Funds reserved on an account until captured, released or expired.

    A stored ``active`` hold whose expiry time has passed no longer reserves
    funds even before it is swept to ``expired``; :meth:`effective_status`
    reports that view.
    """

    id: int
    request_id: str
    account_id: int
    account_code: str
    amount: int
    currency: str
    status: HoldStatus
    reference: str
    created_at: str
    expires_at: str
    resolved_at: str | None
    captured_amount: int | None
    capture_entry_id: int | None

    def is_expired_at(self, now: datetime) -> bool:
        return parse_timestamp(self.expires_at) <= now

    def effective_status(self, now: datetime) -> HoldStatus:
        if self.status is HoldStatus.ACTIVE and self.is_expired_at(now):
            return HoldStatus.EXPIRED
        return self.status

    def is_reserving(self, now: datetime) -> bool:
        return self.effective_status(now) is HoldStatus.ACTIVE

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        status = self.effective_status(now) if now is not None else self.status
        return {
            "id": self.id,
            "request_id": self.request_id,
            "account": self.account_code,
            "amount_minor": self.amount,
            "amount": format_amount(self.amount, self.currency),
            "currency": self.currency,
            "status": status.value,
            "reference": self.reference,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "resolved_at": self.resolved_at,
            "captured_amount_minor": self.captured_amount,
            "capture_entry_id": self.capture_entry_id,
        }
