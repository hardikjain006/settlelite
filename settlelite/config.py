"""Configuration file loading and validation.

Configuration is a JSON object.  Every member is optional::

    {
      "database": "data/ledger.db",
      "default_currency": "USD",
      "rounding": "half_even",
      "calendar": {
        "weekend": ["sat", "sun"],
        "holiday_files": ["holidays/us-2026.txt"],
        "holidays": ["2026-12-24"]
      },
      "posting": {"non_business_day": "roll_forward"},
      "holds": {"default_expiry_hours": 168, "max_expiry_hours": 720}
    }

Relative paths are resolved against the directory containing the
configuration file.  Unknown members are rejected so that typos are caught
early; every error message names the offending member.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .calendar import DEFAULT_WEEKEND, BusinessCalendar, load_holiday_file, parse_weekend
from .clock import parse_date
from .errors import CalendarError, ConfigError, ValidationError
from .ledger.journal import NonBusinessDayPolicy
from .money import RoundingMode, get_currency

ENV_CONFIG = "SETTLELITE_CONFIG"
ENV_DATABASE = "SETTLELITE_DB"
MAX_EXPIRY_HOURS = 24 * 366


@dataclass(frozen=True)
class CalendarConfig:
    weekend: frozenset[int] = DEFAULT_WEEKEND
    holiday_files: tuple[Path, ...] = ()
    holidays: frozenset[date] = frozenset()


@dataclass(frozen=True)
class PostingConfig:
    non_business_day: NonBusinessDayPolicy = NonBusinessDayPolicy.ALLOW


@dataclass(frozen=True)
class HoldConfig:
    default_expiry_hours: int = 168
    max_expiry_hours: int = 720

    @property
    def default_expiry(self) -> timedelta:
        return timedelta(hours=self.default_expiry_hours)

    @property
    def max_expiry(self) -> timedelta:
        return timedelta(hours=self.max_expiry_hours)


@dataclass(frozen=True)
class Config:
    """Validated runtime configuration."""

    database: Path | None = None
    default_currency: str = "USD"
    rounding: RoundingMode = RoundingMode.HALF_UP
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    posting: PostingConfig = field(default_factory=PostingConfig)
    holds: HoldConfig = field(default_factory=HoldConfig)
    source: Path | None = None

    def build_calendar(self) -> BusinessCalendar:
        """Construct the business calendar, reading holiday files."""
        holidays: dict[date, str] = {}
        for path in self.calendar.holiday_files:
            try:
                holidays.update(load_holiday_file(path))
            except CalendarError as exc:
                raise ConfigError(f"calendar.holiday_files: {exc.message}") from None
        for day in self.calendar.holidays:
            holidays.setdefault(day, "")
        return BusinessCalendar(self.calendar.weekend, holidays)

    def with_database(self, database: str | os.PathLike[str] | None) -> "Config":
        """Return a copy whose database path is overridden."""
        if database is None:
            return self
        return Config(
            database=Path(database),
            default_currency=self.default_currency,
            rounding=self.rounding,
            calendar=self.calendar,
            posting=self.posting,
            holds=self.holds,
            source=self.source,
        )


def default_config() -> Config:
    return Config()


class _Section:
    def __init__(self, data: object, path: str) -> None:
        if not isinstance(data, Mapping):
            raise ConfigError(f"{path} must be an object")
        self.data = data
        self.path = path

    def check_keys(self, allowed: set[str]) -> None:
        unknown = sorted(set(self.data) - allowed)
        if unknown:
            raise ConfigError(f"{self.path}.{unknown[0]}: unknown setting")

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def where(self, key: str) -> str:
        return f"{self.path}.{key}"

    def string(self, key: str) -> str | None:
        value = self.data.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{self.where(key)}: must be a nonempty string")
        return value

    def positive_int(self, key: str, default: int, *, maximum: int) -> int:
        value = self.data.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
            raise ConfigError(f"{self.where(key)}: must be an integer from 1 to {maximum}")
        return value

    def string_list(self, key: str) -> list[str]:
        value = self.data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigError(f"{self.where(key)}: must be a list of strings")
        return value


def _resolve(base_dir: Path | None, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    return path


def parse_config(data: object, *, base_dir: Path | None = None, source: Path | None = None) -> Config:
    """Validate a decoded JSON configuration object."""
    root = _Section(data, "config")
    root.check_keys({"database", "default_currency", "rounding", "calendar", "posting", "holds"})

    database_text = root.string("database")
    database = _resolve(base_dir, database_text) if database_text else None

    currency_text = root.get("default_currency")
    default_currency = "USD"
    if currency_text is not None:
        try:
            default_currency = get_currency(currency_text).code
        except ValidationError as exc:
            raise ConfigError(f"config.default_currency: {exc.message}") from None

    rounding = RoundingMode.HALF_UP
    if root.get("rounding") is not None:
        try:
            rounding = RoundingMode.parse(root.get("rounding"))
        except ValidationError as exc:
            raise ConfigError(f"config.rounding: {exc.message}") from None

    calendar = CalendarConfig()
    if root.get("calendar") is not None:
        section = _Section(root.get("calendar"), "config.calendar")
        section.check_keys({"weekend", "holiday_files", "holidays"})
        weekend = DEFAULT_WEEKEND
        if section.get("weekend") is not None:
            try:
                weekend = parse_weekend(section.string_list("weekend"))
            except CalendarError as exc:
                raise ConfigError(f"config.calendar.weekend: {exc.message}") from None
        files = tuple(_resolve(base_dir, item) for item in section.string_list("holiday_files"))
        holidays: set[date] = set()
        for item in section.string_list("holidays"):
            try:
                holidays.add(parse_date(item))
            except ValidationError as exc:
                raise ConfigError(f"config.calendar.holidays: {exc.message}") from None
        calendar = CalendarConfig(weekend, files, frozenset(holidays))

    posting = PostingConfig()
    if root.get("posting") is not None:
        section = _Section(root.get("posting"), "config.posting")
        section.check_keys({"non_business_day"})
        if section.get("non_business_day") is not None:
            try:
                policy = NonBusinessDayPolicy.parse(section.get("non_business_day"))
            except ValidationError as exc:
                raise ConfigError(f"config.posting.non_business_day: {exc.message}") from None
            posting = PostingConfig(policy)

    holds = HoldConfig()
    if root.get("holds") is not None:
        section = _Section(root.get("holds"), "config.holds")
        section.check_keys({"default_expiry_hours", "max_expiry_hours"})
        default_hours = section.positive_int("default_expiry_hours", 168, maximum=MAX_EXPIRY_HOURS)
        max_hours = section.positive_int("max_expiry_hours", 720, maximum=MAX_EXPIRY_HOURS)
        if default_hours > max_hours:
            raise ConfigError(
                "config.holds.default_expiry_hours: must not exceed max_expiry_hours"
            )
        holds = HoldConfig(default_hours, max_hours)

    return Config(
        database=database,
        default_currency=default_currency,
        rounding=rounding,
        calendar=calendar,
        posting=posting,
        holds=holds,
        source=source,
    )


def load_config(path: str | os.PathLike[str]) -> Config:
    """Read, decode and validate the configuration file at ``path``."""
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"configuration file not found: {file_path}") from None
    except OSError as exc:
        raise ConfigError(f"cannot read configuration file {file_path}: {exc.strerror}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{file_path}: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}"
        ) from None
    return parse_config(data, base_dir=file_path.resolve().parent, source=file_path)


def resolve_config(
    config_path: str | None = None,
    database: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> Config:
    """Combine an optional config file, environment and explicit overrides.

    Precedence for the database path: explicit argument, then
    ``SETTLELITE_DB``, then the config file's ``database`` member.  The
    config file itself comes from the argument or ``SETTLELITE_CONFIG``.
    """
    env = os.environ if environ is None else environ
    path = config_path or env.get(ENV_CONFIG) or None
    config = load_config(path) if path else default_config()
    return config.with_database(database or env.get(ENV_DATABASE) or None)
