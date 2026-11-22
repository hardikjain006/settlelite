"""SettleLite: a small double-entry payments ledger and settlement back office.

The package root re-exports the event-envelope and exact-cent interfaces
described in CONTRACT.md together with the application facade.
"""

from .app import SettleLite
from .events import Event, EventError, decode_event, encode_event
from .money import Money, parse_cents
from .version import __version__

__all__ = [
    "Event",
    "EventError",
    "Money",
    "SettleLite",
    "__version__",
    "decode_event",
    "encode_event",
    "parse_cents",
]
