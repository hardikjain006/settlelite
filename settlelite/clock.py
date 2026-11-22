"""Clocks and timestamp formatting.

All persisted timestamps are UTC with second precision, written as
``YYYY-MM-DDTHH:MM:SSZ``.  Services receive a :class:`Clock` instead of
calling :func:`datetime.now` so that replays and tests are deterministic.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

from .errors import ValidationError

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Clock(Protocol):
    """Source of the current instant."""

    def now(self) -> datetime:
        """Return the current time as an aware UTC datetime."""
        ...


class SystemClock:
    """Clock backed by the operating system time."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc).replace(microsecond=0)


class FixedClock:
    """Clock that returns a fixed instant until explicitly moved."""

    def __init__(self, instant: datetime | str) -> None:
        self._instant = _coerce(instant)

    def now(self) -> datetime:
        return self._instant

    def set(self, instant: datetime | str) -> None:
        """Move the clock to ``instant``."""
        self._instant = _coerce(instant)

    def advance(self, **delta: float) -> datetime:
        """Move the clock forward by a :class:`timedelta` built from ``delta``."""
        step = timedelta(**delta)
        if step < timedelta(0):
            raise ValueError("a fixed clock cannot move backwards")
        self._instant = self._instant + step
        return self._instant


def _coerce(instant: datetime | str) -> datetime:
    if isinstance(instant, str):
        return parse_timestamp(instant)
    if instant.tzinfo is None:
        raise ValueError("clock instants must be timezone-aware")
    return instant.astimezone(timezone.utc).replace(microsecond=0)


def format_timestamp(instant: datetime) -> str:
    """Format an aware datetime as a canonical UTC timestamp string."""
    if instant.tzinfo is None:
        raise ValueError("cannot format a naive datetime")
    return instant.astimezone(timezone.utc).strftime(_TIMESTAMP_FORMAT)


def parse_timestamp(text: str) -> datetime:
    """Parse an ISO-8601 timestamp with an explicit offset into UTC.

    ``Z`` and numeric offsets are accepted; naive timestamps are rejected
    because their meaning depends on the host time zone.
    """
    if not isinstance(text, str) or not text:
        raise ValidationError("timestamp must be a nonempty string")
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValidationError(f"invalid timestamp: {text!r}") from exc
    if parsed.tzinfo is None:
        raise ValidationError(f"timestamp must include a UTC offset: {text!r}")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def parse_date(text: str) -> date:
    """Parse a strict ``YYYY-MM-DD`` calendar date."""
    if not isinstance(text, str) or not _DATE_RE.match(text):
        raise ValidationError(f"date must be formatted YYYY-MM-DD: {text!r}")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(f"invalid calendar date: {text!r}") from exc


def format_date(value: date) -> str:
    """Format a date as ``YYYY-MM-DD``."""
    return value.isoformat()


def optional_date_text(value: object) -> str | None:
    """``YYYY-MM-DD`` for a date, otherwise ``None`` (used in fingerprints)."""
    return value.isoformat() if isinstance(value, date) else None


def optional_timestamp_text(value: object) -> str | None:
    """Canonical timestamp for an aware datetime, otherwise ``None``."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return format_timestamp(value)
    return None
