"""Ledger domain objects: accounts, journal entries and postings."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..errors import ValidationError
from ..money import format_amount, require_positive

_ACCOUNT_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
MAX_NAME_LENGTH = 120
MAX_MEMO_LENGTH = 500


class Direction(enum.Enum):
    """Side of a posting."""

    DEBIT = "debit"
    CREDIT = "credit"

    @property
    def opposite(self) -> "Direction":
        return Direction.CREDIT if self is Direction.DEBIT else Direction.DEBIT

    @classmethod
    def parse(cls, value: object) -> "Direction":
        if isinstance(value, Direction):
            return value
        for member in cls:
            if member.value == value:
                return member
        raise ValidationError(f"direction must be 'debit' or 'credit': {value!r}")


class AccountType(enum.Enum):
    """Classic account classes and their normal balance side."""

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"

    @property
    def normal_side(self) -> Direction:
        """The side on which postings increase the account balance."""
        if self in (AccountType.ASSET, AccountType.EXPENSE):
            return Direction.DEBIT
        return Direction.CREDIT

    @property
    def decrease_side(self) -> Direction:
        """The side on which postings decrease the account balance."""
        return self.normal_side.opposite

    @classmethod
    def parse(cls, value: object) -> "AccountType":
        if isinstance(value, AccountType):
            return value
        for member in cls:
            if member.value == value:
                return member
        names = ", ".join(member.value for member in cls)
        raise ValidationError(f"account type must be one of {names}: {value!r}")

    def signed(self, direction: Direction, amount: int) -> int:
        """Effect of a posting on a balance of this type."""
        return amount if direction is self.normal_side else -amount


class AccountStatus(enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class EntryKind(enum.Enum):
    """Why a journal entry exists."""

    STANDARD = "standard"
    REVERSAL = "reversal"
    FEE = "fee"
    CAPTURE = "capture"


def validate_account_code(code: object) -> str:
    """Account codes are short lower-case identifiers like ``wallet:alice``."""
    if not isinstance(code, str) or not _ACCOUNT_CODE_RE.match(code):
        raise ValidationError(
            "account code must be 1-64 characters of a-z, 0-9, '_', '.', ':' or '-' "
            f"starting with a letter or digit: {code!r}"
        )
    return code


def validate_name(name: object, *, field_name: str = "name") -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValidationError(f"{field_name} must be a nonempty string")
    if len(name) > MAX_NAME_LENGTH:
        raise ValidationError(f"{field_name} must be at most {MAX_NAME_LENGTH} characters")
    return name.strip()


def validate_memo(memo: object) -> str:
    if not isinstance(memo, str):
        raise ValidationError("memo must be a string")
    if len(memo) > MAX_MEMO_LENGTH:
        raise ValidationError(f"memo must be at most {MAX_MEMO_LENGTH} characters")
    if any(ord(ch) < 32 and ch not in "\t" for ch in memo):
        raise ValidationError("memo must not contain control characters")
    return memo


@dataclass(frozen=True)
class Account:
    """A ledger account holding a single currency."""

    id: int
    code: str
    name: str
    type: AccountType
    currency: str
    allow_negative: bool
    status: AccountStatus
    created_at: str
    closed_at: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status is AccountStatus.OPEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "type": self.type.value,
            "currency": self.currency,
            "allow_negative": self.allow_negative,
            "status": self.status.value,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
        }


@dataclass(frozen=True)
class PostingInput:
    """A requested posting line, before it is validated and stored."""

    account: str
    direction: Direction
    amount: int

    def __post_init__(self) -> None:
        validate_account_code(self.account)
        object.__setattr__(self, "direction", Direction.parse(self.direction))
        require_positive(self.amount)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "direction": self.direction.value,
            "amount_minor": self.amount,
        }


@dataclass(frozen=True)
class Posting:
    """A stored posting line."""

    line_no: int
    account_id: int
    account_code: str
    direction: Direction
    amount: int

    def to_dict(self, currency: str) -> dict[str, Any]:
        return {
            "line": self.line_no,
            "account": self.account_code,
            "direction": self.direction.value,
            "amount_minor": self.amount,
            "amount": format_amount(self.amount, currency),
        }


@dataclass(frozen=True)
class JournalEntry:
    """A balanced, immutable set of postings in one currency."""

    id: int
    request_id: str
    kind: EntryKind
    memo: str
    effective_date: date
    currency: str
    created_at: str
    reverses_entry_id: int | None
    postings: tuple[Posting, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        """Sum of the debit side (equal to the credit side)."""
        return sum(p.amount for p in self.postings if p.direction is Direction.DEBIT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "kind": self.kind.value,
            "memo": self.memo,
            "effective_date": self.effective_date.isoformat(),
            "currency": self.currency,
            "created_at": self.created_at,
            "reverses_entry_id": self.reverses_entry_id,
            "total_minor": self.total,
            "postings": [posting.to_dict(self.currency) for posting in self.postings],
        }
