"""Placing, capturing, releasing and expiring authorization holds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ..audit import AuditLog
from ..clock import Clock, format_timestamp, optional_timestamp_text
from ..errors import (
    CurrencyMismatchError,
    HoldStateError,
    InsufficientFundsError,
    NotFoundError,
    ValidationError,
)
from ..ledger.accounts import AccountService
from ..ledger.balances import BalanceService
from ..ledger.idempotency import RequestLog, fingerprint, validate_request_id
from ..ledger.journal import JournalService, NonBusinessDayPolicy
from ..ledger.models import Account, EntryKind, JournalEntry, PostingInput, validate_memo
from ..money import require_positive
from ..storage.database import Database
from .models import Hold, HoldStatus
from .repository import HoldRepository

DEFAULT_EXPIRY = timedelta(hours=168)
MAX_EXPIRY = timedelta(hours=720)
MAX_REFERENCE_LENGTH = 120


@dataclass(frozen=True)
class HoldResult:
    hold: Hold
    replayed: bool

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        return {"hold": self.hold.to_dict(now), "replayed": self.replayed}


@dataclass(frozen=True)
class CaptureResult:
    hold: Hold
    entry: JournalEntry
    replayed: bool

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        return {
            "hold": self.hold.to_dict(now),
            "entry": self.entry.to_dict(),
            "replayed": self.replayed,
        }


def _validate_reference(reference: object) -> str:
    if not isinstance(reference, str):
        raise ValidationError("reference must be a string")
    if len(reference) > MAX_REFERENCE_LENGTH:
        raise ValidationError(f"reference must be at most {MAX_REFERENCE_LENGTH} characters")
    if any(not ch.isprintable() for ch in reference):
        raise ValidationError("reference must be printable")
    return reference


class HoldService:
    """Lifecycle of authorization holds.

    A hold reserves part of an account's balance: while active it reduces
    the account's available balance, so the ledger refuses postings that
    would spend reserved funds.  Capturing a hold posts a journal entry that
    moves the captured amount to a destination account and frees any
    remainder; releasing or expiring a hold frees the whole amount.
    """

    def __init__(
        self,
        db: Database,
        clock: Clock,
        audit: AuditLog,
        accounts: AccountService,
        balances: BalanceService,
        *,
        non_business_day: NonBusinessDayPolicy | str = NonBusinessDayPolicy.ALLOW,
        default_expiry: timedelta = DEFAULT_EXPIRY,
        max_expiry: timedelta = MAX_EXPIRY,
    ) -> None:
        if default_expiry <= timedelta(0) or max_expiry <= timedelta(0):
            raise ValidationError("hold expiry durations must be positive")
        if default_expiry > max_expiry:
            raise ValidationError("default hold expiry exceeds the maximum expiry")
        self.db = db
        self.clock = clock
        self.audit = audit
        self.accounts = accounts
        self.balances = balances
        self.journal = JournalService(
            db, clock, audit, balances, non_business_day=non_business_day
        )
        self.default_expiry = default_expiry
        self.max_expiry = max_expiry
        self.repo = HoldRepository(db)
        self.requests = RequestLog(db, clock)

    def _expiry(
        self, now: datetime, expires_at: datetime | None, expires_in: timedelta | None
    ) -> datetime:
        if expires_at is not None and expires_in is not None:
            raise ValidationError("give either expires_at or expires_in, not both")
        if expires_in is not None:
            if not isinstance(expires_in, timedelta) or expires_in <= timedelta(0):
                raise ValidationError("expires_in must be a positive duration")
            expiry = now + expires_in
        elif expires_at is not None:
            if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
                raise ValidationError("expires_at must be a timezone-aware datetime")
            expiry = expires_at.replace(microsecond=0)
        else:
            expiry = now + self.default_expiry
        if expiry <= now:
            raise ValidationError("hold expiry must be in the future")
        if expiry - now > self.max_expiry:
            hours = int(self.max_expiry.total_seconds() // 3600)
            raise ValidationError(f"hold expiry may be at most {hours} hours ahead")
        return expiry

    def place(
        self,
        *,
        request_id: str,
        account: str,
        amount: int,
        expires_at: datetime | None = None,
        expires_in: timedelta | None = None,
        reference: str = "",
    ) -> HoldResult:
        """Reserve ``amount`` on ``account``; idempotent by ``request_id``."""
        request_id = validate_request_id(request_id)
        amount = require_positive(amount)
        reference = _validate_reference(reference)
        payload = {
            "account": account,
            "amount_minor": amount,
            "expires_at": optional_timestamp_text(expires_at),
            "expires_in_seconds": (
                int(expires_in.total_seconds()) if isinstance(expires_in, timedelta) else None
            ),
            "reference": reference,
        }
        print_ = fingerprint("hold.place", payload)
        with self.db.transaction():
            holder = self.accounts.require_open(account)
            now = self.clock.now()
            expiry = self._expiry(now, expires_at, expires_in)
            self._require_available(holder, amount)
            prior = self.requests.check(request_id, "hold.place", print_)
            if prior is not None:
                return HoldResult(self.get(int(prior.result_id or 0)), replayed=True)
            hold = self.repo.insert(
                request_id=request_id,
                account_id=holder.id,
                amount=amount,
                currency=holder.currency,
                reference=reference,
                created_at=format_timestamp(now),
                expires_at=format_timestamp(expiry),
            )
            self.audit.record(
                "hold",
                hold.id,
                "hold.placed",
                {
                    "account": holder.code,
                    "amount_minor": amount,
                    "expires_at": hold.expires_at,
                    "request_id": request_id,
                },
            )
            self.requests.remember(
                request_id, "hold.place", print_, result_type="hold", result_id=hold.id
            )
        return HoldResult(hold, replayed=False)

    def _require_available(self, holder: Account, amount: int) -> None:
        if holder.allow_negative:
            return
        available = self.balances.available(holder)
        if available < amount:
            raise InsufficientFundsError(
                f"insufficient available funds in {holder.code}",
                details={
                    "account": holder.code,
                    "available_minor": available,
                    "required_minor": amount,
                },
            )

    def _require_active(self, hold: Hold) -> None:
        state = hold.effective_status(self.clock.now())
        if state is HoldStatus.ACTIVE:
            return
        if hold.status is HoldStatus.ACTIVE:
            raise HoldStateError(f"hold {hold.id} has expired", details={"hold_id": hold.id})
        raise HoldStateError(
            f"hold {hold.id} is already {hold.status.value}",
            details={"hold_id": hold.id, "status": hold.status.value},
        )

    def capture(
        self,
        *,
        request_id: str,
        hold_id: int,
        destination: str,
        amount: int | None = None,
        memo: str = "",
        effective_date: date | None = None,
    ) -> CaptureResult:
        """Capture all or part of a hold into ``destination``.

        A hold is captured at most once; any uncaptured remainder is freed.
        The capture entry decreases the holder's balance and increases the
        destination's by the captured amount.
        """
        request_id = validate_request_id(request_id)
        if amount is not None:
            amount = require_positive(amount)
        memo = validate_memo(memo)
        value_date = self.journal.resolve_effective_date(effective_date)
        payload = {
            "hold_id": hold_id,
            "destination": destination,
            "amount_minor": amount,
            "memo": memo,
            "effective_date": value_date.isoformat(),
        }
        print_ = fingerprint("hold.capture", payload)
        with self.db.transaction():
            prior = self.requests.check(request_id, "hold.capture", print_)
            if prior is not None:
                hold = self.get(int(prior.result_id or 0))
                entry = self.journal.get(int(hold.capture_entry_id or 0))
                return CaptureResult(hold, entry, replayed=True)
            hold = self.get(hold_id)
            self._require_active(hold)
            captured = hold.amount if amount is None else amount
            if captured > hold.amount:
                raise ValidationError(
                    f"capture amount {captured} exceeds hold amount {hold.amount}",
                    details={"hold_id": hold.id, "hold_amount_minor": hold.amount},
                )
            holder = self.accounts.require_open(hold.account_code)
            target = self.accounts.require_open(destination)
            if target.currency != holder.currency:
                raise CurrencyMismatchError(
                    f"destination {target.code} is {target.currency}, hold is {holder.currency}"
                )
            if target.id == holder.id:
                raise ValidationError("capture destination must differ from the held account")
            side = holder.type.decrease_side
            entry = self.journal.record(
                request_id=request_id,
                lines=[
                    PostingInput(holder.code, side, captured),
                    PostingInput(target.code, side.opposite, captured),
                ],
                memo=memo or f"Capture of hold {hold.id}",
                effective_date=value_date,
                kind=EntryKind.CAPTURE,
                released_hold_id=hold.id,
            )
            now_text = format_timestamp(self.clock.now())
            self.repo.resolve(
                hold.id,
                HoldStatus.CAPTURED,
                now_text,
                captured_amount=captured,
                capture_entry_id=entry.id,
            )
            self.audit.record(
                "hold",
                hold.id,
                "hold.captured",
                {
                    "captured_minor": captured,
                    "released_minor": hold.amount - captured,
                    "entry_id": entry.id,
                    "destination": target.code,
                },
            )
            self.requests.remember(
                request_id, "hold.capture", print_, result_type="hold", result_id=hold.id
            )
            return CaptureResult(self.get(hold.id), entry, replayed=False)

    def release(self, hold_id: int, *, reason: str = "") -> Hold:
        """Free the full amount of an active hold."""
        reason = _validate_reference(reason)
        with self.db.transaction():
            hold = self.get(hold_id)
            self._require_active(hold)
            self.repo.resolve(hold.id, HoldStatus.RELEASED, format_timestamp(self.clock.now()))
            self.audit.record(
                "hold", hold.id, "hold.released", {"amount_minor": hold.amount, "reason": reason}
            )
            return self.get(hold.id)

    def expire_due(self) -> list[Hold]:
        """Mark every active hold past its expiry time as expired."""
        now_text = format_timestamp(self.clock.now())
        expired: list[Hold] = []
        with self.db.transaction():
            for hold in self.repo.due_for_expiry(now_text):
                self.repo.resolve(hold.id, HoldStatus.EXPIRED, now_text)
                self.audit.record(
                    "hold",
                    hold.id,
                    "hold.expired",
                    {"amount_minor": hold.amount, "expires_at": hold.expires_at},
                )
                expired.append(self.get(hold.id))
        return expired

    def get(self, hold_id: int) -> Hold:
        if not isinstance(hold_id, int) or isinstance(hold_id, bool):
            raise ValidationError("hold id must be an integer")
        hold = self.repo.by_id(hold_id)
        if hold is None:
            raise NotFoundError(f"unknown hold: {hold_id}", details={"hold_id": hold_id})
        return hold

    def list(
        self, *, account: str | None = None, status: HoldStatus | str | None = None
    ) -> list[Hold]:
        """Holds in id order, filtered by account and stored status."""
        account_id = self.accounts.get(account).id if account is not None else None
        status_value = HoldStatus.parse(status) if status is not None else None
        return self.repo.list(account_id=account_id, status=status_value)
