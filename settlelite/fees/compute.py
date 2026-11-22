"""Pure fee computation."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from ..errors import AmountError
from ..money import (
    BASIS_POINTS,
    RoundingMode,
    format_amount,
    parse_cents,
    require_storable,
    round_fraction,
)
from .models import Component, FeeSchedule, FlatComponent, PercentageComponent, TieredComponent


@dataclass(frozen=True)
class FeeLine:
    """Contribution of one component (or a bound adjustment) to a fee."""

    label: str
    amount: int

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "amount_minor": self.amount}


@dataclass(frozen=True)
class FeeQuote:
    """The fee for one base amount under one schedule version."""

    schedule: str
    version: int
    currency: str
    base_amount: int
    lines: tuple[FeeLine, ...]

    @property
    def total(self) -> int:
        return sum(line.amount for line in self.lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule": self.schedule,
            "version": self.version,
            "currency": self.currency,
            "base_amount_minor": self.base_amount,
            "fee_minor": self.total,
            "fee": format_amount(self.total, self.currency),
            "lines": [line.to_dict() for line in self.lines],
        }


def graduated_fraction(component: TieredComponent, amount: int) -> Fraction:
    """Exact (unrounded) fee for a graduated tier table."""
    total = Fraction(0)
    lower = 0
    for tier in component.tiers:
        upper = amount if tier.up_to is None else min(amount, tier.up_to)
        if upper > lower:
            total += Fraction((upper - lower) * tier.bps, BASIS_POINTS)
        if tier.up_to is None or amount <= tier.up_to:
            break
        lower = tier.up_to
    return total


def component_fee(component: Component, amount: int, schedule: FeeSchedule) -> int:
    """Rounded fee contributed by a single component."""
    if isinstance(component, FlatComponent):
        return component.amount
    if isinstance(component, PercentageComponent):
        fee = round_fraction(Fraction(amount * component.bps, BASIS_POINTS), schedule.rounding)
        fee = max(fee, component.minimum)
        if component.maximum is not None:
            fee = min(fee, component.maximum)
        return fee
    if isinstance(component, TieredComponent):
        return round_fraction(graduated_fraction(component, amount), RoundingMode.HALF_EVEN)
    raise TypeError(f"unknown fee component: {component!r}")


def compute_fee(schedule: FeeSchedule, amount: int) -> FeeQuote:
    """Compute the fee charged on a nonnegative base ``amount``.

    Each component is rounded independently with the schedule's rounding
    mode, then the schedule-wide bounds are applied as an explicit
    adjustment line so that the lines always add up to the total.
    """
    parse_cents(amount)
    require_storable(amount)
    if amount < 0:
        raise AmountError("fee base amount must not be negative")
    lines = [
        FeeLine(component.label, component_fee(component, amount, schedule))
        for component in schedule.components
    ]
    subtotal = sum(line.amount for line in lines)
    if subtotal < schedule.minimum:
        lines.append(FeeLine("minimum", schedule.minimum - subtotal))
    elif schedule.maximum is not None and subtotal > schedule.maximum:
        lines.append(FeeLine("maximum", schedule.maximum - subtotal))
    return FeeQuote(
        schedule=schedule.name,
        version=schedule.version,
        currency=schedule.currency,
        base_amount=amount,
        lines=tuple(lines),
    )
