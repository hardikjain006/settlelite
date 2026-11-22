"""Exact money arithmetic in integer minor units.

Amounts are always Python integers counted in the minor unit of their
currency (cents for USD, yen for JPY, fils for KWD).  Nothing in this module
uses binary floating point: rational intermediate results are carried as
:class:`fractions.Fraction` and rounded once, with an explicit
:class:`RoundingMode`.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction

from .errors import AmountError, CurrencyError, CurrencyMismatchError, ValidationError

BASIS_POINTS = 10_000
"""Number of basis points in one whole (100%)."""

MAX_AMOUNT = 10**15 - 1
"""Largest magnitude accepted for a single stored amount, in minor units."""

_CODE_RE = re.compile(r"^[A-Z]{3}$")
_DECIMAL_RE = re.compile(r"^(-)?(\d+)(?:\.(\d+))?$")


def parse_cents(value: object) -> int:
    """Return ``value`` unchanged if it is a genuine integer amount.

    Booleans, floats, strings and ``None`` are rejected rather than coerced,
    so callers never silently round or reinterpret an amount.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise AmountError("amount_cents must be an integer")
    return value


def require_storable(amount: int, *, name: str = "amount") -> int:
    """Validate that ``amount`` is an integer that fits the storage range."""
    parse_cents(amount)
    if abs(amount) > MAX_AMOUNT:
        raise AmountError(f"{name} exceeds the supported magnitude of {MAX_AMOUNT}")
    return amount


def require_positive(amount: object, *, name: str = "amount") -> int:
    """Validate a strictly positive, storable integer amount."""
    value = require_storable(parse_cents(amount), name=name)
    if value <= 0:
        raise AmountError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Currency:
    """An ISO 4217 currency and the number of digits in its minor unit."""

    code: str
    exponent: int
    name: str

    @property
    def minor_per_major(self) -> int:
        """Number of minor units in one major unit."""
        return 10**self.exponent


_CURRENCY_ROWS: tuple[tuple[str, int, str], ...] = (
    ("AUD", 2, "Australian dollar"),
    ("BHD", 3, "Bahraini dinar"),
    ("BRL", 2, "Brazilian real"),
    ("CAD", 2, "Canadian dollar"),
    ("CHF", 2, "Swiss franc"),
    ("CLP", 0, "Chilean peso"),
    ("CNY", 2, "Renminbi"),
    ("CZK", 2, "Czech koruna"),
    ("DKK", 2, "Danish krone"),
    ("EUR", 2, "Euro"),
    ("GBP", 2, "Pound sterling"),
    ("HKD", 2, "Hong Kong dollar"),
    ("HUF", 2, "Hungarian forint"),
    ("INR", 2, "Indian rupee"),
    ("ISK", 0, "Icelandic krona"),
    ("JOD", 3, "Jordanian dinar"),
    ("JPY", 0, "Japanese yen"),
    ("KRW", 0, "South Korean won"),
    ("KWD", 3, "Kuwaiti dinar"),
    ("MXN", 2, "Mexican peso"),
    ("NOK", 2, "Norwegian krone"),
    ("NZD", 2, "New Zealand dollar"),
    ("OMR", 3, "Omani rial"),
    ("PLN", 2, "Polish zloty"),
    ("SEK", 2, "Swedish krona"),
    ("SGD", 2, "Singapore dollar"),
    ("TND", 3, "Tunisian dinar"),
    ("USD", 2, "United States dollar"),
    ("ZAR", 2, "South African rand"),
)

CURRENCIES: dict[str, Currency] = {
    code: Currency(code, exponent, name) for code, exponent, name in _CURRENCY_ROWS
}
"""Supported currencies keyed by ISO code."""


def get_currency(code: object) -> Currency:
    """Look up a supported currency by its upper-case ISO code."""
    if not isinstance(code, str) or not _CODE_RE.match(code):
        raise CurrencyError(f"currency must be a three-letter upper-case code: {code!r}")
    try:
        return CURRENCIES[code]
    except KeyError:
        raise CurrencyError(f"unsupported currency: {code}") from None


class RoundingMode(enum.Enum):
    """Strategies for rounding a rational amount to whole minor units."""

    HALF_UP = "half_up"
    """Round to nearest; ties go away from zero."""

    HALF_EVEN = "half_even"
    """Round to nearest; ties go to the even neighbour (banker's rounding)."""

    DOWN = "down"
    """Truncate toward zero."""

    UP = "up"
    """Round away from zero whenever there is a remainder."""

    @classmethod
    def parse(cls, value: object) -> "RoundingMode":
        """Return the mode named by ``value`` (for example ``"half_even"``)."""
        if isinstance(value, RoundingMode):
            return value
        for mode in cls:
            if mode.value == value:
                return mode
        names = ", ".join(mode.value for mode in cls)
        raise ValidationError(f"rounding mode must be one of {names}: {value!r}")


def round_fraction(value: Fraction | int, mode: RoundingMode) -> int:
    """Round an exact rational ``value`` to an integer using ``mode``."""
    value = Fraction(value)
    negative = value < 0
    magnitude = -value if negative else value
    quotient, remainder = divmod(magnitude.numerator, magnitude.denominator)
    denominator = magnitude.denominator
    if remainder:
        twice = 2 * remainder
        if mode is RoundingMode.UP:
            quotient += 1
        elif mode is RoundingMode.HALF_UP:
            if twice >= denominator:
                quotient += 1
        elif mode is RoundingMode.HALF_EVEN:
            if twice > denominator or (twice == denominator and quotient % 2 == 1):
                quotient += 1
        elif mode is not RoundingMode.DOWN:
            raise ValidationError(f"unsupported rounding mode: {mode!r}")
    return -quotient if negative else quotient


def divide(numerator: int, denominator: int, mode: RoundingMode) -> int:
    """Divide two integers and round the exact quotient using ``mode``."""
    if denominator == 0:
        raise ZeroDivisionError("division of an amount by zero")
    return round_fraction(Fraction(numerator, denominator), mode)


def apply_basis_points(amount: int, bps: int, mode: RoundingMode) -> int:
    """Return ``amount * bps / 10000`` rounded with ``mode``."""
    parse_cents(amount)
    parse_cents(bps)
    return round_fraction(Fraction(amount * bps, BASIS_POINTS), mode)


def allocate(amount: int, weights: Sequence[int]) -> list[int]:
    """Split ``amount`` proportionally to integer ``weights`` without loss.

    Uses the largest-remainder method: every share is first truncated toward
    zero, then the leftover minor units go one at a time to the shares with
    the largest fractional remainders.  Ties are broken by position, so the
    result is deterministic.  The returned shares always sum to ``amount``.
    """
    parse_cents(amount)
    if not weights:
        raise ValidationError("allocation requires at least one weight")
    for weight in weights:
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 0:
            raise ValidationError("allocation weights must be nonnegative integers")
    total = sum(weights)
    if total == 0:
        raise ValidationError("allocation weights must not all be zero")
    sign = -1 if amount < 0 else 1
    magnitude = abs(amount)
    shares: list[int] = []
    remainders: list[tuple[int, int]] = []
    for index, weight in enumerate(weights):
        share, remainder = divmod(magnitude * weight, total)
        shares.append(share)
        remainders.append((remainder, index))
    leftover = magnitude - sum(shares)
    remainders.sort(key=lambda item: (-item[0], item[1]))
    for _, index in remainders[:leftover]:
        shares[index] += 1
    return [sign * share for share in shares]


def split_evenly(amount: int, parts: int) -> list[int]:
    """Split ``amount`` into ``parts`` shares that differ by at most one unit."""
    if not isinstance(parts, int) or isinstance(parts, bool) or parts <= 0:
        raise ValidationError("parts must be a positive integer")
    return allocate(amount, [1] * parts)


def parse_amount(text: object, currency: str | Currency) -> int:
    """Parse a decimal string in major units into minor units.

    ``"12.5"`` in USD becomes ``1250``.  Exponent notation, thousands
    separators, a leading ``+`` and more fractional digits than the currency
    allows are all rejected.
    """
    info = currency if isinstance(currency, Currency) else get_currency(currency)
    if not isinstance(text, str):
        raise AmountError(f"amount must be a decimal string: {text!r}")
    match = _DECIMAL_RE.match(text.strip())
    if not match:
        raise AmountError(f"invalid amount: {text!r}")
    sign, whole, fraction = match.groups()
    fraction = fraction or ""
    if len(fraction) > info.exponent:
        raise AmountError(
            f"{info.code} amounts allow at most {info.exponent} decimal places: {text!r}"
        )
    minor = int(whole) * info.minor_per_major + int(fraction.ljust(info.exponent, "0") or "0")
    value = -minor if sign else minor
    return require_storable(value)


def format_amount(minor: int, currency: str | Currency) -> str:
    """Format minor units as a plain decimal string in major units."""
    info = currency if isinstance(currency, Currency) else get_currency(currency)
    parse_cents(minor)
    sign = "-" if minor < 0 else ""
    whole, fraction = divmod(abs(minor), info.minor_per_major)
    if info.exponent == 0:
        return f"{sign}{whole}"
    return f"{sign}{whole}.{fraction:0{info.exponent}d}"


@dataclass(frozen=True, order=False)
class Money:
    """An integer amount of minor units tagged with its currency.

    Arithmetic is only defined between values of the same currency; mixing
    currencies raises :class:`~settlelite.errors.CurrencyMismatchError`.
    """

    amount: int
    currency: str

    def __post_init__(self) -> None:
        parse_cents(self.amount)
        get_currency(self.currency)

    @classmethod
    def zero(cls, currency: str) -> "Money":
        """Return zero in ``currency``."""
        return cls(0, currency)

    @classmethod
    def parse(cls, text: str, currency: str) -> "Money":
        """Parse a decimal major-unit string, e.g. ``Money.parse("1.25", "USD")``."""
        return cls(parse_amount(text, currency), currency)

    def _check(self, other: object) -> "Money":
        if not isinstance(other, Money):
            raise TypeError(f"expected Money, got {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatchError(
                f"cannot combine {self.currency} with {other.currency}"
            )
        return other

    def __add__(self, other: "Money") -> "Money":
        return Money(self.amount + self._check(other).amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        return Money(self.amount - self._check(other).amount, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.amount, self.currency)

    def __lt__(self, other: "Money") -> bool:
        return self.amount < self._check(other).amount

    def __le__(self, other: "Money") -> bool:
        return self.amount <= self._check(other).amount

    def __gt__(self, other: "Money") -> bool:
        return self.amount > self._check(other).amount

    def __ge__(self, other: "Money") -> bool:
        return self.amount >= self._check(other).amount

    def is_zero(self) -> bool:
        return self.amount == 0

    def allocate(self, weights: Sequence[int]) -> list["Money"]:
        """Split this value by ``weights``; see :func:`allocate`."""
        return [Money(share, self.currency) for share in allocate(self.amount, weights)]

    def format(self) -> str:
        """Return the amount as a decimal string in major units."""
        return format_amount(self.amount, self.currency)

    def to_dict(self) -> dict[str, object]:
        return {"amount_minor": self.amount, "currency": self.currency}

    def __str__(self) -> str:
        return f"{self.format()} {self.currency}"


def sum_money(values: Iterable[Money], currency: str) -> Money:
    """Add ``values`` starting from zero in ``currency``."""
    total = Money.zero(currency)
    for value in values:
        total = total + value
    return total
