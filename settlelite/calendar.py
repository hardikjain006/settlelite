"""Business-day calendar with configurable weekends and holiday files.

Holiday files are plain text: one ``YYYY-MM-DD`` date per line, optionally
followed by whitespace and a free-form name.  Blank lines and lines starting
with ``#`` are ignored.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from datetime import date, timedelta

from .clock import parse_date
from .errors import CalendarError, ValidationError

WEEKDAY_NAMES: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
"""Three-letter weekday names accepted in configuration."""

DEFAULT_WEEKEND = frozenset({5, 6})


def parse_weekend(names: Iterable[object]) -> frozenset[int]:
    """Convert weekday names such as ``["fri", "sat"]`` to weekday numbers."""
    days: set[int] = set()
    for name in names:
        if not isinstance(name, str) or name.lower() not in WEEKDAY_NAMES:
            raise CalendarError(f"unknown weekday name: {name!r}")
        day = WEEKDAY_NAMES[name.lower()]
        if day in days:
            raise CalendarError(f"weekday listed twice: {name!r}")
        days.add(day)
    if len(days) == 7:
        raise CalendarError("a calendar needs at least one business weekday")
    return frozenset(days)


def parse_holiday_lines(lines: Iterable[str], *, source: str = "<holidays>") -> dict[date, str]:
    """Parse holiday file content into a ``{date: name}`` mapping."""
    holidays: dict[date, str] = {}
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        token = parts[0]
        name = parts[1] if len(parts) > 1 else ""
        try:
            day = parse_date(token)
        except ValidationError as exc:
            raise CalendarError(f"{source}:{number}: {exc.message}") from None
        if day in holidays:
            raise CalendarError(f"{source}:{number}: duplicate holiday {token}")
        holidays[day] = name.strip()
    return holidays


def load_holiday_file(path: str | os.PathLike[str]) -> dict[date, str]:
    """Read and parse a holiday file."""
    try:
        with open(path, encoding="utf-8") as handle:
            return parse_holiday_lines(handle, source=os.fspath(path))
    except OSError as exc:
        raise CalendarError(f"cannot read holiday file {os.fspath(path)}: {exc.strerror}") from exc


class BusinessCalendar:
    """Determines which dates are business days.

    A business day is any date that is neither a weekend day nor a listed
    holiday.  The calendar is immutable; :meth:`with_holidays` returns a new
    instance.
    """

    _MAX_SCAN_DAYS = 3660

    def __init__(
        self,
        weekend: Iterable[int] = DEFAULT_WEEKEND,
        holidays: Mapping[date, str] | Iterable[date] = (),
    ) -> None:
        weekend_days = frozenset(weekend)
        if any(not isinstance(d, int) or not 0 <= d <= 6 for d in weekend_days):
            raise CalendarError("weekend days must be integers from 0 (Mon) to 6 (Sun)")
        if len(weekend_days) == 7:
            raise CalendarError("a calendar needs at least one business weekday")
        self._weekend = weekend_days
        if isinstance(holidays, Mapping):
            self._holidays = dict(holidays)
        else:
            self._holidays = {day: "" for day in holidays}
        for day in self._holidays:
            if not isinstance(day, date):
                raise CalendarError(f"holiday must be a date: {day!r}")

    @property
    def weekend(self) -> frozenset[int]:
        return self._weekend

    @property
    def holidays(self) -> dict[date, str]:
        """A copy of the holiday table."""
        return dict(self._holidays)

    def with_holidays(self, holidays: Mapping[date, str] | Iterable[date]) -> "BusinessCalendar":
        """Return a calendar with additional holidays."""
        merged = dict(self._holidays)
        extra = holidays if isinstance(holidays, Mapping) else {day: "" for day in holidays}
        merged.update(extra)
        return BusinessCalendar(self._weekend, merged)

    def is_weekend(self, day: date) -> bool:
        return day.weekday() in self._weekend

    def is_holiday(self, day: date) -> bool:
        return day in self._holidays

    def holiday_name(self, day: date) -> str | None:
        return self._holidays.get(day)

    def is_business_day(self, day: date) -> bool:
        return not self.is_weekend(day) and not self.is_holiday(day)

    def _step(self, day: date, direction: int) -> date:
        for _ in range(self._MAX_SCAN_DAYS):
            day = day + timedelta(days=direction)
            if self.is_business_day(day):
                return day
        raise CalendarError("no business day found within ten years")

    def next_business_day(self, day: date) -> date:
        """Return the first business day strictly after ``day``."""
        return self._step(day, 1)

    def previous_business_day(self, day: date) -> date:
        """Return the last business day strictly before ``day``."""
        return self._step(day, -1)

    def roll_forward(self, day: date) -> date:
        """Return ``day`` if it is a business day, else the next one."""
        return day if self.is_business_day(day) else self.next_business_day(day)

    def roll_backward(self, day: date) -> date:
        """Return ``day`` if it is a business day, else the previous one."""
        return day if self.is_business_day(day) else self.previous_business_day(day)

    def add_business_days(self, day: date, count: int) -> date:
        """Move ``count`` business days from ``day``.

        ``day`` itself is never counted.  A positive ``count`` moves forward,
        a negative one backward, and zero rolls a non-business day forward.
        """
        if not isinstance(count, int) or isinstance(count, bool):
            raise ValidationError("business day count must be an integer")
        if count == 0:
            return self.roll_forward(day)
        direction = 1 if count > 0 else -1
        for _ in range(abs(count)):
            day = self._step(day, direction)
        return day

    def business_days_between(self, start: date, end: date) -> int:
        """Count business days in the half-open interval ``(start, end]``.

        The result is negative when ``end`` precedes ``start``.
        """
        if end == start:
            return 0
        if end < start:
            return -self.business_days_between(end, start)
        count = 0
        day = start
        while day < end:
            day = day + timedelta(days=1)
            if self.is_business_day(day):
                count += 1
        return count

    def holidays_between(self, start: date, end: date) -> list[tuple[date, str]]:
        """List holidays in the closed interval ``[start, end]`` in date order."""
        return sorted(
            (day, name) for day, name in self._holidays.items() if start <= day <= end
        )
