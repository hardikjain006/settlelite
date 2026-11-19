"""Exact integer-cent values, without implicit coercion."""


def parse_cents(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("amount_cents must be an integer")
    return value
