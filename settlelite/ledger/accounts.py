"""Opening, closing and looking up ledger accounts."""

from __future__ import annotations

from ..audit import AuditLog
from ..clock import Clock, format_timestamp
from ..errors import AccountStateError, ConflictError, NotFoundError, ValidationError
from ..money import get_currency
from ..storage.database import Database
from .balances import BalanceService
from .models import (
    Account,
    AccountStatus,
    AccountType,
    validate_account_code,
    validate_name,
)
from .repository import AccountRepository
from .reservations import NoReservations, ReservationSource


class AccountService:
    """Lifecycle operations on accounts."""

    def __init__(
        self,
        db: Database,
        clock: Clock,
        audit: AuditLog,
        *,
        balances: BalanceService | None = None,
        reservations: ReservationSource | None = None,
    ) -> None:
        self.db = db
        self.clock = clock
        self.audit = audit
        self.repo = AccountRepository(db)
        self.reservations: ReservationSource = reservations or NoReservations()
        self.balances = balances or BalanceService(db, self.reservations)

    def open(
        self,
        code: str,
        name: str,
        type: AccountType | str,
        currency: str,
        *,
        allow_negative: bool = False,
    ) -> Account:
        """Create a new open account; codes are unique and never reused."""
        code = validate_account_code(code)
        name = validate_name(name)
        account_type = AccountType.parse(type)
        currency = get_currency(currency).code
        if not isinstance(allow_negative, bool):
            raise ValidationError("allow_negative must be a boolean")
        with self.db.transaction():
            if self.repo.by_code(code) is not None:
                raise ConflictError(f"account already exists: {code}", details={"account": code})
            account = self.repo.insert(
                code=code,
                name=name,
                type=account_type,
                currency=currency,
                allow_negative=allow_negative,
                created_at=format_timestamp(self.clock.now()),
            )
            self.audit.record(
                "account",
                account.code,
                "account.opened",
                {
                    "name": account.name,
                    "type": account.type.value,
                    "currency": account.currency,
                    "allow_negative": account.allow_negative,
                },
            )
        return account

    def get(self, code: str) -> Account:
        account = self.repo.by_code(validate_account_code(code))
        if account is None:
            raise NotFoundError(f"unknown account: {code}", details={"account": code})
        return account

    def get_by_id(self, account_id: int) -> Account:
        account = self.repo.by_id(account_id)
        if account is None:
            raise NotFoundError(f"unknown account id: {account_id}")
        return account

    def require_open(self, code: str) -> Account:
        account = self.get(code)
        if not account.is_open:
            raise AccountStateError(f"account is closed: {code}", details={"account": code})
        return account

    def list(
        self,
        *,
        status: AccountStatus | str | None = None,
        currency: str | None = None,
        type: AccountType | str | None = None,
    ) -> list[Account]:
        """Accounts sorted by code, optionally filtered."""
        status_value = AccountStatus(status) if isinstance(status, str) else status
        type_value = AccountType.parse(type) if type is not None else None
        currency_value = get_currency(currency).code if currency is not None else None
        return self.repo.list(status=status_value, currency=currency_value, type=type_value)

    def close(self, code: str) -> Account:
        """Close an account with a zero balance and no active holds."""
        with self.db.transaction():
            account = self.get(code)
            if not account.is_open:
                raise AccountStateError(f"account is already closed: {code}")
            balance = self.balances.balance(account)
            if balance != 0:
                raise AccountStateError(
                    f"account {code} has a nonzero balance",
                    details={"account": code, "balance_minor": balance},
                )
            if self.reservations.has_active(account.id):
                raise AccountStateError(f"account {code} has active holds")
            self.repo.mark_closed(account.id, format_timestamp(self.clock.now()))
            self.audit.record("account", code, "account.closed", {})
            return self.get(code)

    def reopen(self, code: str) -> Account:
        """Reopen a previously closed account."""
        with self.db.transaction():
            account = self.get(code)
            if account.is_open:
                raise AccountStateError(f"account is already open: {code}")
            self.repo.mark_open(account.id)
            self.audit.record("account", code, "account.reopened", {})
            return self.get(code)
