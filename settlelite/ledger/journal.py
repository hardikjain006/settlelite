"""Double-entry journal: posting and reversing entries."""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..audit import AuditLog
from ..calendar import BusinessCalendar
from ..clock import Clock, format_timestamp, optional_date_text
from ..errors import (
    AccountStateError,
    ConflictError,
    CurrencyMismatchError,
    InsufficientFundsError,
    NotFoundError,
    UnbalancedEntryError,
    ValidationError,
)
from ..money import MAX_AMOUNT
from ..storage.database import Database
from .balances import BalanceService
from .idempotency import RequestLog, fingerprint, validate_request_id
from .models import (
    Account,
    Direction,
    EntryKind,
    JournalEntry,
    PostingInput,
    validate_memo,
)
from .repository import AccountRepository, JournalRepository

MAX_LINES = 100


class NonBusinessDayPolicy(enum.Enum):
    """What to do with an effective date that is not a business day."""

    ALLOW = "allow"
    REJECT = "reject"
    ROLL_FORWARD = "roll_forward"

    @classmethod
    def parse(cls, value: object) -> "NonBusinessDayPolicy":
        if isinstance(value, NonBusinessDayPolicy):
            return value
        for member in cls:
            if member.value == value:
                return member
        names = ", ".join(member.value for member in cls)
        raise ValidationError(f"non-business-day policy must be one of {names}: {value!r}")


@dataclass(frozen=True)
class PostResult:
    """Outcome of a post or reverse request."""

    entry: JournalEntry
    replayed: bool

    def to_dict(self) -> dict[str, Any]:
        return {"entry": self.entry.to_dict(), "replayed": self.replayed}


class JournalService:
    """Validates and records journal entries.

    Every entry must have at least one debit and one credit line, all in the
    currency shared by its accounts, with equal debit and credit totals.
    Accounts that do not allow negative balances may not be taken below
    their available balance (ledger balance minus active reservations).
    """

    def __init__(
        self,
        db: Database,
        clock: Clock,
        audit: AuditLog,
        balances: BalanceService,
        *,
        calendar: BusinessCalendar | None = None,
        non_business_day: NonBusinessDayPolicy | str = NonBusinessDayPolicy.ALLOW,
    ) -> None:
        self.db = db
        self.clock = clock
        self.audit = audit
        self.balances = balances
        self.calendar = calendar or BusinessCalendar()
        self.non_business_day = NonBusinessDayPolicy.parse(non_business_day)
        self.accounts = AccountRepository(db)
        self.repo = JournalRepository(db)
        self.requests = RequestLog(db, clock)

    def today(self) -> date:
        return self.clock.now().date()

    def resolve_effective_date(self, requested: date | None) -> date:
        """Apply the default date and the non-business-day policy."""
        if requested is not None and not isinstance(requested, date):
            raise ValidationError("effective_date must be a date")
        day = requested or self.today()
        if self.calendar.is_business_day(day):
            return day
        if self.non_business_day is NonBusinessDayPolicy.REJECT:
            raise ValidationError(
                f"effective date {day.isoformat()} is not a business day",
                details={"effective_date": day.isoformat()},
            )
        if self.non_business_day is NonBusinessDayPolicy.ROLL_FORWARD:
            return self.calendar.roll_forward(day)
        return day

    def _resolve_lines(
        self, lines: Sequence[PostingInput]
    ) -> tuple[str, list[tuple[Account, Direction, int]]]:
        if not isinstance(lines, Sequence) or isinstance(lines, (str, bytes)):
            raise ValidationError("postings must be a sequence of posting lines")
        if len(lines) < 2:
            raise UnbalancedEntryError("an entry needs at least two postings")
        if len(lines) > MAX_LINES:
            raise ValidationError(f"an entry may have at most {MAX_LINES} postings")
        for line in lines:
            if not isinstance(line, PostingInput):
                raise ValidationError("each posting must be a PostingInput")
        accounts = self.accounts.by_codes([line.account for line in lines])
        resolved: list[tuple[Account, Direction, int]] = []
        currencies: set[str] = set()
        debits = credits = 0
        for line in lines:
            account = accounts.get(line.account)
            if account is None:
                raise NotFoundError(
                    f"unknown account: {line.account}", details={"account": line.account}
                )
            if not account.is_open:
                raise AccountStateError(
                    f"account is closed: {account.code}", details={"account": account.code}
                )
            currencies.add(account.currency)
            if line.direction is Direction.DEBIT:
                debits += line.amount
            else:
                credits += line.amount
            resolved.append((account, line.direction, line.amount))
        if len(currencies) > 1:
            raise CurrencyMismatchError(
                "all postings in an entry must use the same currency",
                details={"currencies": sorted(currencies)},
            )
        if debits == 0 or credits == 0:
            raise UnbalancedEntryError("an entry needs at least one debit and one credit")
        if debits != credits:
            raise UnbalancedEntryError(
                f"debits ({debits}) do not equal credits ({credits})",
                details={"debits_minor": debits, "credits_minor": credits},
            )
        if debits > MAX_AMOUNT:
            raise ValidationError(f"entry total exceeds the supported magnitude of {MAX_AMOUNT}")
        return currencies.pop(), resolved

    def _check_funds(
        self,
        lines: Sequence[tuple[Account, Direction, int]],
        *,
        released_hold_id: int | None,
    ) -> None:
        deltas: dict[int, int] = {}
        by_id: dict[int, Account] = {}
        for account, direction, amount in lines:
            by_id[account.id] = account
            deltas[account.id] = deltas.get(account.id, 0) + account.type.signed(direction, amount)
        for account_id in sorted(deltas):
            delta = deltas[account_id]
            account = by_id[account_id]
            if delta >= 0 or account.allow_negative:
                continue
            available = self.balances.available(account, exclude_hold_id=released_hold_id)
            if available + delta < 0:
                raise InsufficientFundsError(
                    f"insufficient available funds in {account.code}",
                    details={
                        "account": account.code,
                        "available_minor": available,
                        "required_minor": -delta,
                    },
                )

    def record(
        self,
        *,
        request_id: str,
        lines: Sequence[PostingInput],
        memo: str = "",
        effective_date: date | None = None,
        kind: EntryKind = EntryKind.STANDARD,
        reverses_entry_id: int | None = None,
        released_hold_id: int | None = None,
    ) -> JournalEntry:
        """Validate and store an entry without request-log bookkeeping.

        This is the building block used by :meth:`post`, :meth:`reverse`
        and by other services (fees, holds) that keep their own request log
        entries.  It joins the caller's transaction when one is open.
        ``released_hold_id`` names a hold consumed by this entry, whose
        reservation therefore does not count against available funds.
        """
        validate_request_id(request_id)
        memo = validate_memo(memo)
        with self.db.transaction():
            if self.repo.by_request_id(request_id) is not None:
                raise ConflictError(f"an entry already exists for request_id {request_id!r}")
            currency, resolved = self._resolve_lines(lines)
            day = self.resolve_effective_date(effective_date)
            self._check_funds(resolved, released_hold_id=released_hold_id)
            entry = self.repo.insert(
                request_id=request_id,
                kind=kind,
                memo=memo,
                effective_date=day,
                currency=currency,
                created_at=format_timestamp(self.clock.now()),
                reverses_entry_id=reverses_entry_id,
                lines=resolved,
            )
            self.audit.record(
                "entry",
                entry.id,
                "entry.posted",
                {
                    "request_id": request_id,
                    "kind": kind.value,
                    "currency": currency,
                    "total_minor": entry.total,
                    "effective_date": day.isoformat(),
                },
            )
        return entry

    def post(
        self,
        *,
        request_id: str,
        lines: Sequence[PostingInput],
        memo: str = "",
        effective_date: date | None = None,
    ) -> PostResult:
        """Post a standard entry, idempotently by ``request_id``."""
        request_id = validate_request_id(request_id)
        if (
            not isinstance(lines, Sequence)
            or isinstance(lines, (str, bytes))
            or not all(isinstance(line, PostingInput) for line in lines)
        ):
            raise ValidationError("postings must be a sequence of PostingInput lines")
        payload = {
            "lines": [line.to_dict() for line in lines],
            "memo": memo,
            "effective_date": optional_date_text(effective_date),
        }
        print_ = fingerprint("post", payload)
        with self.db.transaction():
            prior = self.requests.check(request_id, "post", print_)
            if prior is not None:
                return PostResult(self.get(int(prior.result_id or 0)), replayed=True)
            entry = self.record(
                request_id=request_id, lines=lines, memo=memo, effective_date=effective_date
            )
            self.requests.remember(
                request_id, "post", print_, result_type="entry", result_id=entry.id
            )
        return PostResult(entry, replayed=False)

    def reverse(
        self,
        *,
        request_id: str,
        entry_id: int,
        memo: str | None = None,
        effective_date: date | None = None,
    ) -> PostResult:
        """Post an entry that exactly offsets ``entry_id``.

        Each entry can be reversed once, and reversal entries themselves
        cannot be reversed.  The reversal is subject to the same funds
        checks as any other entry.
        """
        request_id = validate_request_id(request_id)
        if not isinstance(entry_id, int) or isinstance(entry_id, bool) or entry_id <= 0:
            raise ValidationError("entry_id must be a positive integer")
        payload = {
            "memo": memo,
            "effective_date": optional_date_text(effective_date),
        }
        print_ = fingerprint("reverse", payload)
        with self.db.transaction():
            prior = self.requests.check(request_id, "reverse", print_)
            if prior is not None:
                return PostResult(self.get(int(prior.result_id or 0)), replayed=True)
            original = self.get(entry_id)
            if original.kind is EntryKind.REVERSAL:
                raise ConflictError(f"entry {entry_id} is a reversal and cannot be reversed")
            existing = self.repo.reversal_of(entry_id)
            if existing is not None:
                raise ConflictError(
                    f"entry {entry_id} was already reversed by entry {existing.id}",
                    details={"reversal_entry_id": existing.id},
                )
            lines = [
                PostingInput(posting.account_code, posting.direction.opposite, posting.amount)
                for posting in original.postings
            ]
            entry = self.record(
                request_id=request_id,
                lines=lines,
                memo=memo if memo is not None else f"Reversal of entry {entry_id}",
                effective_date=effective_date,
                kind=EntryKind.REVERSAL,
                reverses_entry_id=entry_id,
            )
            self.audit.record(
                "entry", entry_id, "entry.reversed", {"reversal_entry_id": entry.id}
            )
            self.requests.remember(
                request_id, "reverse", print_, result_type="entry", result_id=entry.id
            )
        return PostResult(entry, replayed=False)

    def get(self, entry_id: int) -> JournalEntry:
        entry = self.repo.by_id(entry_id)
        if entry is None:
            raise NotFoundError(
                f"unknown journal entry: {entry_id}", details={"entry_id": entry_id}
            )
        return entry

    def find_by_request(self, request_id: str) -> JournalEntry | None:
        return self.repo.by_request_id(request_id)

    def reversal_of(self, entry_id: int) -> JournalEntry | None:
        return self.repo.reversal_of(entry_id)

    def list(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        account: Account | None = None,
        kind: EntryKind | None = None,
        limit: int | None = None,
    ) -> list[JournalEntry]:
        if start is not None and end is not None and end < start:
            raise ValidationError("end date precedes start date")
        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise ValidationError("limit must be a positive integer")
        return self.repo.list(
            start=start,
            end=end,
            account_id=account.id if account else None,
            kind=kind,
            limit=limit,
        )
