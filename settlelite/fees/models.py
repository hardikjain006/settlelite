"""Fee schedule definitions and their JSON representation.

A schedule is a list of components whose fees are added together, followed
by optional schedule-wide minimum and maximum bounds::

    {
      "name": "card-standard",
      "currency": "USD",
      "rounding": "half_up",
      "components": [
        {"type": "flat", "amount_minor": 30},
        {"type": "percentage", "bps": 290, "max_minor": 2500},
        {"type": "tiered", "tiers": [
          {"up_to_minor": 100000, "bps": 50},
          {"up_to_minor": null, "bps": 25}
        ]}
      ],
      "minimum_minor": 50,
      "splits": [{"account": "revenue:fees", "weight": 1}]
    }

Tiered components are graduated: each tier's rate applies only to the
portion of the amount that falls inside that tier.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..errors import FeeScheduleError, ValidationError
from ..ledger.models import validate_account_code
from ..money import MAX_AMOUNT, RoundingMode, get_currency

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
MAX_BPS = 10_000
MAX_COMPONENTS = 20
MAX_TIERS = 20
MAX_SPLITS = 20
MAX_LABEL_LENGTH = 60


def validate_schedule_name(name: object) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise FeeScheduleError(
            "fee schedule name must be 1-64 characters of a-z, 0-9, '_', '.' or '-': "
            f"{name!r}"
        )
    return name


@dataclass(frozen=True)
class FlatComponent:
    """A fixed fee independent of the amount."""

    amount: int
    label: str = "flat"

    def to_dict(self) -> dict[str, Any]:
        return {"type": "flat", "amount_minor": self.amount, "label": self.label}


@dataclass(frozen=True)
class PercentageComponent:
    """A rate in basis points with optional per-component bounds."""

    bps: int
    minimum: int = 0
    maximum: int | None = None
    label: str = "percentage"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "percentage",
            "bps": self.bps,
            "min_minor": self.minimum,
            "max_minor": self.maximum,
            "label": self.label,
        }


@dataclass(frozen=True)
class Tier:
    """Rate for the portion of an amount up to ``up_to`` (``None``: unbounded)."""

    up_to: int | None
    bps: int

    def to_dict(self) -> dict[str, Any]:
        return {"up_to_minor": self.up_to, "bps": self.bps}


@dataclass(frozen=True)
class TieredComponent:
    """Graduated rates over ascending amount bands."""

    tiers: tuple[Tier, ...]
    label: str = "tiered"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "tiered",
            "tiers": [tier.to_dict() for tier in self.tiers],
            "label": self.label,
        }


Component = FlatComponent | PercentageComponent | TieredComponent


@dataclass(frozen=True)
class FeeSplit:
    """Share of collected fees credited to a revenue account."""

    account: str
    weight: int

    def to_dict(self) -> dict[str, Any]:
        return {"account": self.account, "weight": self.weight}


@dataclass(frozen=True)
class FeeSchedule:
    """A named, versioned fee schedule in one currency."""

    name: str
    currency: str
    rounding: RoundingMode
    components: tuple[Component, ...]
    minimum: int = 0
    maximum: int | None = None
    splits: tuple[FeeSplit, ...] = ()
    version: int = 0

    def definition(self) -> dict[str, Any]:
        """Canonical definition, excluding the storage-assigned version."""
        return {
            "name": self.name,
            "currency": self.currency,
            "rounding": self.rounding.value,
            "components": [component.to_dict() for component in self.components],
            "minimum_minor": self.minimum,
            "maximum_minor": self.maximum,
            "splits": [split.to_dict() for split in self.splits],
        }

    def to_dict(self) -> dict[str, Any]:
        data = self.definition()
        data["version"] = self.version
        return data


class _Reader:
    """Strict accessor for one JSON object that reports errors with a path."""

    def __init__(self, data: object, path: str) -> None:
        if not isinstance(data, Mapping):
            raise FeeScheduleError(f"{path} must be an object")
        self.data = data
        self.path = path
        self.seen: set[str] = set()

    def _fail(self, key: str, message: str) -> FeeScheduleError:
        return FeeScheduleError(f"{self.path}.{key} {message}")

    def has(self, key: str) -> bool:
        return key in self.data and self.data[key] is not None

    def integer(
        self,
        key: str,
        *,
        default: int | None = None,
        required: bool = False,
        minimum: int = 0,
        maximum: int = MAX_AMOUNT,
        nullable: bool = False,
    ) -> int | None:
        self.seen.add(key)
        if key not in self.data or (self.data[key] is None and nullable):
            if required:
                raise self._fail(key, "is required")
            return default
        value = self.data[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise self._fail(key, "must be an integer")
        if not minimum <= value <= maximum:
            raise self._fail(key, f"must be between {minimum} and {maximum}")
        return value

    def text(self, key: str, *, default: str | None = None, required: bool = False) -> str | None:
        self.seen.add(key)
        if key not in self.data:
            if required:
                raise self._fail(key, "is required")
            return default
        value = self.data[key]
        if not isinstance(value, str):
            raise self._fail(key, "must be a string")
        return value

    def items(self, key: str, *, required: bool = False, limit: int) -> Sequence[object]:
        self.seen.add(key)
        if key not in self.data:
            if required:
                raise self._fail(key, "is required")
            return []
        value = self.data[key]
        if not isinstance(value, list):
            raise self._fail(key, "must be a list")
        if len(value) > limit:
            raise self._fail(key, f"may have at most {limit} items")
        return value

    def finish(self) -> None:
        unknown = sorted(set(self.data) - self.seen)
        if unknown:
            raise FeeScheduleError(f"{self.path} has unknown member {unknown[0]!r}")


def _label(reader: _Reader, default: str) -> str:
    label = reader.text("label", default=default) or default
    if not label.strip() or len(label) > MAX_LABEL_LENGTH:
        raise FeeScheduleError(f"{reader.path}.label must be 1-{MAX_LABEL_LENGTH} characters")
    return label


def _parse_component(data: object, path: str) -> Component:
    reader = _Reader(data, path)
    kind = reader.text("type", required=True)
    component: Component
    if kind == "flat":
        amount = reader.integer("amount_minor", required=True)
        component = FlatComponent(amount=int(amount or 0), label=_label(reader, "flat"))
    elif kind == "percentage":
        bps = reader.integer("bps", required=True, maximum=MAX_BPS)
        minimum = reader.integer("min_minor", default=0, nullable=True)
        maximum = reader.integer("max_minor", default=None, nullable=True)
        if maximum is not None and maximum < (minimum or 0):
            raise FeeScheduleError(f"{path}.max_minor must not be below min_minor")
        component = PercentageComponent(
            bps=int(bps or 0),
            minimum=int(minimum or 0),
            maximum=maximum,
            label=_label(reader, "percentage"),
        )
    elif kind == "tiered":
        raw_tiers = reader.items("tiers", required=True, limit=MAX_TIERS)
        if not raw_tiers:
            raise FeeScheduleError(f"{path}.tiers must not be empty")
        tiers: list[Tier] = []
        previous = 0
        for index, raw in enumerate(raw_tiers):
            tier_reader = _Reader(raw, f"{path}.tiers[{index}]")
            up_to = tier_reader.integer("up_to_minor", default=None, nullable=True, minimum=1)
            bps = tier_reader.integer("bps", required=True, maximum=MAX_BPS)
            tier_reader.finish()
            last = index == len(raw_tiers) - 1
            if up_to is None and not last:
                raise FeeScheduleError(f"{path}.tiers[{index}] only the last tier may be unbounded")
            if up_to is not None and last:
                raise FeeScheduleError(f"{path}.tiers[{index}] the last tier must be unbounded")
            if up_to is not None and up_to <= previous:
                raise FeeScheduleError(f"{path}.tiers[{index}].up_to_minor must be ascending")
            previous = up_to or previous
            tiers.append(Tier(up_to=up_to, bps=int(bps or 0)))
        component = TieredComponent(tiers=tuple(tiers), label=_label(reader, "tiered"))
    else:
        raise FeeScheduleError(f"{path}.type must be flat, percentage or tiered: {kind!r}")
    reader.finish()
    return component


def parse_schedule(
    data: object, *, default_rounding: RoundingMode = RoundingMode.HALF_UP
) -> FeeSchedule:
    """Validate a JSON-like mapping and build a :class:`FeeSchedule`."""
    reader = _Reader(data, "schedule")
    name = validate_schedule_name(reader.text("name", required=True))
    try:
        currency = get_currency(reader.text("currency", required=True)).code
        rounding_text = reader.text("rounding", default=default_rounding.value)
        rounding = RoundingMode.parse(rounding_text)
    except FeeScheduleError:
        raise
    except ValidationError as exc:
        raise FeeScheduleError(f"schedule: {exc.message}") from None
    raw_components = reader.items("components", required=True, limit=MAX_COMPONENTS)
    if not raw_components:
        raise FeeScheduleError("schedule.components must not be empty")
    components = tuple(
        _parse_component(raw, f"schedule.components[{index}]")
        for index, raw in enumerate(raw_components)
    )
    minimum = int(reader.integer("minimum_minor", default=0, nullable=True) or 0)
    maximum = reader.integer("maximum_minor", default=None, nullable=True)
    if maximum is not None and maximum < minimum:
        raise FeeScheduleError("schedule.maximum_minor must not be below minimum_minor")
    splits: list[FeeSplit] = []
    seen_accounts: set[str] = set()
    for index, raw in enumerate(reader.items("splits", limit=MAX_SPLITS)):
        split_reader = _Reader(raw, f"schedule.splits[{index}]")
        account = split_reader.text("account", required=True)
        try:
            validate_account_code(account)
        except ValidationError as exc:
            raise FeeScheduleError(f"schedule.splits[{index}].account: {exc.message}") from None
        weight = split_reader.integer("weight", required=True, minimum=1, maximum=1_000_000)
        split_reader.finish()
        assert account is not None
        if account in seen_accounts:
            raise FeeScheduleError(f"schedule.splits[{index}] repeats account {account!r}")
        seen_accounts.add(account)
        splits.append(FeeSplit(account=account, weight=int(weight or 0)))
    reader.finish()
    return FeeSchedule(
        name=name,
        currency=currency,
        rounding=rounding,
        components=components,
        minimum=minimum,
        maximum=maximum,
        splits=tuple(splits),
    )
