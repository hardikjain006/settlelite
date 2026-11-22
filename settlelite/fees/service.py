"""Fee schedule management and fee charging through the ledger."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..audit import AuditLog
from ..clock import Clock, format_timestamp, optional_date_text
from ..errors import CurrencyMismatchError, NotFoundError, ValidationError
from ..jsonutil import canonical_json
from ..ledger.accounts import AccountService
from ..ledger.idempotency import RequestLog, fingerprint, validate_request_id
from ..ledger.journal import JournalService
from ..ledger.models import Direction, EntryKind, JournalEntry, PostingInput, validate_memo
from ..money import RoundingMode, allocate, parse_cents
from ..storage.database import Database
from .compute import FeeQuote, compute_fee
from .models import FeeSchedule, parse_schedule, validate_schedule_name
from .repository import FeeCharge, FeeChargeRepository, FeeScheduleRepository


@dataclass(frozen=True)
class SaveResult:
    schedule: FeeSchedule
    created: bool

    def to_dict(self) -> dict[str, Any]:
        return {"schedule": self.schedule.to_dict(), "created": self.created}


@dataclass(frozen=True)
class ChargeResult:
    """Outcome of a fee charge; ``entry`` is ``None`` when the fee is zero."""

    charge: FeeCharge
    quote: FeeQuote
    entry: JournalEntry | None
    replayed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "charge": self.charge.to_dict(),
            "quote": self.quote.to_dict(),
            "entry": self.entry.to_dict() if self.entry else None,
            "replayed": self.replayed,
        }


class FeeService:
    """Stores fee schedules, quotes fees and posts fee entries."""

    def __init__(
        self,
        db: Database,
        clock: Clock,
        audit: AuditLog,
        accounts: AccountService,
        journal: JournalService,
        *,
        default_rounding: RoundingMode = RoundingMode.HALF_UP,
    ) -> None:
        self.db = db
        self.clock = clock
        self.audit = audit
        self.accounts = accounts
        self.journal = journal
        self.default_rounding = default_rounding
        self.repo = FeeScheduleRepository(db)
        self.charges = FeeChargeRepository(db)
        self.requests = RequestLog(db, clock)

    def save(self, definition: Mapping[str, Any] | FeeSchedule) -> SaveResult:
        """Store a schedule as a new version unless it is unchanged."""
        schedule = (
            definition
            if isinstance(definition, FeeSchedule)
            else parse_schedule(definition, default_rounding=self.default_rounding)
        )
        text = canonical_json(schedule.definition())
        with self.db.transaction():
            latest = self.repo.latest_definition(schedule.name)
            if latest is not None and latest[1] == text:
                stored = self.repo.get(schedule.name, latest[0])
                assert stored is not None
                return SaveResult(stored, created=False)
            version = (latest[0] if latest else 0) + 1
            stored = self.repo.insert(
                schedule,
                version=version,
                definition=text,
                created_at=format_timestamp(self.clock.now()),
            )
            self.audit.record(
                "fee_schedule",
                schedule.name,
                "fee_schedule.saved",
                {"version": version, "currency": schedule.currency},
            )
        return SaveResult(stored, created=True)

    def get(self, name: str, version: int | None = None) -> FeeSchedule:
        validate_schedule_name(name)
        if version is not None and (not isinstance(version, int) or version < 1):
            raise ValidationError("schedule version must be a positive integer")
        schedule = self.repo.get(name, version)
        if schedule is None:
            label = name if version is None else f"{name} v{version}"
            raise NotFoundError(f"unknown fee schedule: {label}", details={"schedule": name})
        return schedule

    def list(self) -> list[FeeSchedule]:
        """Latest version of every schedule, sorted by name."""
        return self.repo.latest_all()

    def history(self, name: str) -> list[FeeSchedule]:
        versions = self.repo.history(validate_schedule_name(name))
        if not versions:
            raise NotFoundError(f"unknown fee schedule: {name}", details={"schedule": name})
        return versions

    def quote(self, name: str, amount: int, *, version: int | None = None) -> FeeQuote:
        return compute_fee(self.get(name, version), amount)

    def charge(
        self,
        *,
        request_id: str,
        schedule: str,
        payer: str,
        amount: int,
        revenue: str | None = None,
        memo: str = "",
        effective_date: date | None = None,
    ) -> ChargeResult:
        """Compute the fee on ``amount`` and post it from ``payer``.

        The fee is credited to ``revenue`` or, when the schedule defines
        splits, divided across the split accounts by weight with exact
        remainder allocation.  Exactly one of the two must apply.  The
        latest schedule version at the time of the first request is used;
        replays return the original result.
        """
        request_id = validate_request_id(request_id)
        parse_cents(amount)
        memo = validate_memo(memo)
        payload = {
            "schedule": schedule,
            "payer": payer,
            "amount_minor": amount,
            "revenue": revenue,
            "memo": memo,
            "effective_date": optional_date_text(effective_date),
        }
        print_ = fingerprint("fee.charge", payload)
        with self.db.transaction():
            prior = self.requests.check(request_id, "fee.charge", print_)
            if prior is not None:
                charge = self.charges.by_id(int(prior.result_id or 0))
                assert charge is not None
                stored = self.get(charge.schedule_name, charge.schedule_version)
                entry = self.journal.get(charge.entry_id) if charge.entry_id else None
                return ChargeResult(charge, compute_fee(stored, amount), entry, replayed=True)
            current = self.get(schedule)
            payer_account = self.accounts.require_open(payer)
            if payer_account.currency != current.currency:
                raise CurrencyMismatchError(
                    f"schedule {current.name} is {current.currency}, "
                    f"payer {payer_account.code} is {payer_account.currency}"
                )
            if revenue is not None and current.splits:
                raise ValidationError(
                    f"schedule {current.name} defines revenue splits; do not pass a revenue account"
                )
            if revenue is None and not current.splits:
                raise ValidationError(
                    f"schedule {current.name} has no revenue splits; a revenue account is required"
                )
            quote = compute_fee(current, amount)
            entry: JournalEntry | None = None
            if quote.total > 0:
                lines = self._lines(
                    current, payer_account.code, payer_account.type.decrease_side, quote.total, revenue
                )
                entry = self.journal.record(
                    request_id=request_id,
                    lines=lines,
                    memo=memo or f"Fee {current.name} v{current.version}",
                    effective_date=effective_date,
                    kind=EntryKind.FEE,
                )
            charge = self.charges.insert(
                request_id=request_id,
                schedule=current,
                payer_account_id=payer_account.id,
                base_amount=amount,
                fee_amount=quote.total,
                entry_id=entry.id if entry else None,
                created_at=format_timestamp(self.clock.now()),
            )
            self.requests.remember(
                request_id, "fee.charge", print_, result_type="fee_charge", result_id=charge.id
            )
            self.audit.record(
                "fee_charge",
                charge.id,
                "fee.charged",
                {
                    "schedule": current.name,
                    "version": current.version,
                    "payer": payer_account.code,
                    "base_amount_minor": amount,
                    "fee_minor": quote.total,
                    "entry_id": charge.entry_id,
                },
            )
        return ChargeResult(charge, quote, entry, replayed=False)

    def _lines(
        self,
        schedule: FeeSchedule,
        payer: str,
        payer_side: Direction,
        total: int,
        revenue: str | None,
    ) -> list[PostingInput]:
        lines = [PostingInput(payer, payer_side, total)]
        if revenue is not None:
            lines.append(PostingInput(revenue, payer_side.opposite, total))
            return lines
        shares = allocate(total, [split.weight for split in schedule.splits])
        for split, share in zip(schedule.splits, shares):
            if share > 0:
                lines.append(PostingInput(split.account, payer_side.opposite, share))
        return lines

    def list_charges(
        self, *, schedule: str | None = None, payer: str | None = None
    ) -> list[FeeCharge]:
        """Recorded charges in creation order."""
        payer_id = self.accounts.get(payer).id if payer is not None else None
        name = validate_schedule_name(schedule) if schedule is not None else None
        return self.charges.list(schedule=name, payer_account_id=payer_id)
