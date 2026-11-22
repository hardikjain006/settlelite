"""Helpers shared by command implementations."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, TextIO

from ..app import SettleLite
from ..clock import Clock, FixedClock, SystemClock, parse_date, parse_timestamp
from ..config import Config, resolve_config
from ..errors import ValidationError
from ..jsonutil import canonical_json
from ..money import parse_amount, require_storable

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_REJECTED = 2

Payload = dict[str, Any]


@dataclass
class CommandContext:
    """Streams and lazily-built runtime objects for one invocation."""

    args: argparse.Namespace
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)
    _config: Config | None = None
    _app: SettleLite | None = None

    def emit(self, payload: Payload) -> None:
        """Write one compact, sorted-key JSON object and a newline."""
        self.stdout.write(canonical_json(payload) + "\n")

    @property
    def config(self) -> Config:
        if self._config is None:
            self._config = resolve_config(
                getattr(self.args, "config", None), getattr(self.args, "db", None)
            )
        return self._config

    @property
    def clock(self) -> Clock:
        now = getattr(self.args, "now", None)
        return FixedClock(parse_timestamp(now)) if now else SystemClock()

    def app(self, *, create: bool = False) -> SettleLite:
        if self._app is None:
            self._app = SettleLite.open(config=self.config, clock=self.clock, create=create)
        return self._app

    def close(self) -> None:
        if self._app is not None:
            self._app.close()
            self._app = None


def add_runtime_options(parser: argparse.ArgumentParser) -> None:
    """Options accepted by every command that touches a database."""
    group = parser.add_argument_group("runtime")
    group.add_argument(
        "--db", metavar="PATH", help="database file (overrides config and SETTLELITE_DB)"
    )
    group.add_argument(
        "--config", metavar="PATH", help="JSON configuration file (or SETTLELITE_CONFIG)"
    )
    group.add_argument(
        "--now",
        metavar="TIMESTAMP",
        help="use this ISO-8601 instant as the current time instead of the system clock",
    )


def date_arg(value: str | None) -> date | None:
    return parse_date(value) if value is not None else None


def timestamp_arg(value: str | None) -> datetime | None:
    return parse_timestamp(value) if value is not None else None


def positive_int_arg(value: str | None, *, name: str) -> int | None:
    if value is None:
        return None
    try:
        number = int(value, 10)
    except ValueError:
        raise ValidationError(f"{name} must be an integer: {value!r}") from None
    if number <= 0:
        raise ValidationError(f"{name} must be positive")
    return number


def amount_arg(text: str, currency: str, *, minor: bool) -> int:
    """Parse a CLI amount as major-unit decimal, or integer minor units."""
    if minor:
        stripped = text.strip()
        if not stripped.lstrip("-").isdigit():
            raise ValidationError(f"minor-unit amount must be an integer: {text!r}")
        return require_storable(int(stripped, 10))
    return parse_amount(text, currency)


def split_assignment(spec: str, *, option: str) -> tuple[str, str]:
    """Split ``ACCOUNT=AMOUNT``."""
    account, sep, amount = spec.partition("=")
    if not sep or not account or not amount:
        raise ValidationError(f"{option} expects ACCOUNT=AMOUNT: {spec!r}")
    return account, amount
