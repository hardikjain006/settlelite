"""Syntactic event validation, independent of ledger admission."""

import json
from collections.abc import Mapping
from dataclasses import dataclass

from .money import parse_cents


class EventError(ValueError):
    """An envelope does not satisfy the public event schema."""


@dataclass(frozen=True)
class Event:
    version: str
    request_id: str
    item_id: str
    kind: str
    amount_cents: int | None = None
    revision: int = 0


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise EventError(f"{name} must be a nonempty string")
    return value


def decode_event(payload: str | Mapping[str, object]) -> Event:
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
    except (ValueError, TypeError) as exc:
        raise EventError("invalid JSON object") from exc
    if not isinstance(data, Mapping):
        raise EventError("an event must be an object")
    required = {"version", "request_id", "item_id", "kind"}
    if not required <= data.keys():
        raise EventError("missing required event member")
    version = _text(data["version"], "version")
    request_id = _text(data["request_id"], "request_id")
    item_id = _text(data["item_id"], "item_id")
    kind = data["kind"]
    if kind == "post":
        if set(data) - (required | {"amount_cents", "revision"}):
            raise EventError("unknown post member")
        try:
            amount = parse_cents(data.get("amount_cents"))
            revision = parse_cents(data.get("revision", 0))
        except ValueError as exc:
            raise EventError(str(exc)) from exc
        if amount < 0 or revision < 0:
            raise EventError("post amount and revision must be nonnegative")
        return Event(version, request_id, item_id, kind, amount, revision)
    if kind == "void":
        if set(data) != required:
            raise EventError("void has only the four envelope members")
        return Event(version, request_id, item_id, kind)
    raise EventError("kind must be post or void")


def encode_event(event: Event) -> str:
    data = {
        "version": event.version,
        "request_id": event.request_id,
        "item_id": event.item_id,
        "kind": event.kind,
    }
    if event.kind == "post":
        data.update(amount_cents=event.amount_cents, revision=event.revision)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
