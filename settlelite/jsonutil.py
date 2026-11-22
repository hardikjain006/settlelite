"""Canonical JSON helpers.

SettleLite uses one JSON dialect everywhere a byte-stable representation
matters: CLI output, audit hashes, and idempotency fingerprints.  The
dialect is compact, has sorted object keys, and keeps non-ASCII text as-is.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialise ``value`` as compact JSON with sorted keys."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    """Return the SHA-256 hex digest of the canonical JSON form of ``value``."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def loads_object(text: str, *, what: str = "document") -> dict[str, Any]:
    """Parse ``text`` as JSON and require the top-level value to be an object.

    Raises :class:`ValueError` with a short description on failure; callers
    wrap it into the domain-specific error type they report.
    """
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{what} is not valid JSON: {exc.msg} at line {exc.lineno}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be a JSON object")
    return value
