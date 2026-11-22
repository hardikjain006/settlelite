"""Exception hierarchy shared by every SettleLite component.

Every error raised deliberately by the library derives from
:class:`SettleLiteError` and carries a stable, machine-readable ``code``.
The command line interface reports that code verbatim, so codes are part of
the public contract while messages are not.
"""

from __future__ import annotations

from collections.abc import Mapping


class SettleLiteError(Exception):
    """Base class for all errors raised by SettleLite."""

    code = "error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details: dict[str, object] = dict(details or {})

    def to_dict(self) -> dict[str, object]:
        """Return the error as a JSON-serialisable mapping."""
        payload: dict[str, object] = {"error": self.code, "detail": self.message}
        if self.details:
            payload["context"] = dict(self.details)
        return payload


class ValidationError(SettleLiteError, ValueError):
    """Input does not satisfy a syntactic or semantic rule."""

    code = "invalid_input"


class CurrencyError(ValidationError):
    """A currency code is malformed or not in the currency table."""

    code = "invalid_currency"


class CurrencyMismatchError(ValidationError):
    """Two amounts or accounts with different currencies were combined."""

    code = "currency_mismatch"


class AmountError(ValidationError):
    """A monetary amount is malformed or out of range."""

    code = "invalid_amount"


class UnbalancedEntryError(ValidationError):
    """A journal entry's debits and credits do not match."""

    code = "unbalanced_entry"


class CalendarError(ValidationError):
    """A business calendar or holiday file is invalid."""

    code = "invalid_calendar"


class FeeScheduleError(ValidationError):
    """A fee schedule definition is invalid."""

    code = "invalid_fee_schedule"


class NotFoundError(SettleLiteError, LookupError):
    """A referenced record does not exist."""

    code = "not_found"


class ConflictError(SettleLiteError):
    """The request is well formed but conflicts with current state."""

    code = "conflict"


class IdempotencyError(ConflictError):
    """A request id was reused with a different payload."""

    code = "idempotency_conflict"


class InsufficientFundsError(ConflictError):
    """An operation would take an account below its available balance."""

    code = "insufficient_funds"


class AccountStateError(ConflictError):
    """An account is closed or cannot change state as requested."""

    code = "account_state"


class HoldStateError(ConflictError):
    """A hold is not in a state that permits the requested transition."""

    code = "hold_state"


class ConfigError(SettleLiteError):
    """A configuration file is missing, malformed, or inconsistent."""

    code = "invalid_config"


class StorageError(SettleLiteError):
    """The database could not be opened or used."""

    code = "storage_error"


class SchemaError(StorageError):
    """The database schema is missing or not at the expected version."""

    code = "schema_out_of_date"


class MigrationError(StorageError):
    """A schema migration could not be applied or verified."""

    code = "migration_failed"


class AuditIntegrityError(SettleLiteError):
    """The audit log hash chain does not verify."""

    code = "audit_integrity"
