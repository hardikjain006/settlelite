"""Append-only, hash-chained audit log of state changes.

Every state-changing service operation records one or more audit entries
inside the same transaction as the change itself.  Each entry stores the
SHA-256 hash of its predecessor, so any edit to a stored entry is detected
by :meth:`AuditLog.verify`.  Database triggers additionally reject
``UPDATE`` and ``DELETE`` on the table.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .clock import Clock, SystemClock, format_timestamp
from .errors import AuditIntegrityError, ValidationError
from .jsonutil import canonical_json
from .storage.database import Database

GENESIS_HASH = "0" * 64
_ACTION_RE = re.compile(r"^[a-z_]+\.[a-z_]+$")


@dataclass(frozen=True)
class AuditEntry:
    """One immutable audit record."""

    seq: int
    recorded_at: str
    actor: str
    entity_type: str
    entity_id: str
    action: str
    details: dict[str, Any]
    prev_hash: str
    hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "recorded_at": self.recorded_at,
            "actor": self.actor,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "action": self.action,
            "details": self.details,
            "hash": self.hash,
        }


def entry_hash(
    prev_hash: str,
    *,
    seq: int,
    recorded_at: str,
    actor: str,
    entity_type: str,
    entity_id: str,
    action: str,
    details: Mapping[str, Any],
) -> str:
    """Compute the chained hash for an audit entry."""
    body = canonical_json(
        {
            "seq": seq,
            "recorded_at": recorded_at,
            "actor": actor,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "details": dict(details),
        }
    )
    return hashlib.sha256((prev_hash + "\n" + body).encode("utf-8")).hexdigest()


def _row_to_entry(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        seq=row["seq"],
        recorded_at=row["recorded_at"],
        actor=row["actor"],
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        action=row["action"],
        details=json.loads(row["details"]),
        prev_hash=row["prev_hash"],
        hash=row["hash"],
    )


class AuditLog:
    """Records and reads audit entries for one database."""

    def __init__(self, db: Database, clock: Clock | None = None, *, actor: str = "system") -> None:
        if not actor or not isinstance(actor, str):
            raise ValidationError("audit actor must be a nonempty string")
        self.db = db
        self.clock = clock or SystemClock()
        self.actor = actor

    def record(
        self,
        entity_type: str,
        entity_id: object,
        action: str,
        details: Mapping[str, Any] | None = None,
    ) -> AuditEntry:
        """Append an entry describing ``action`` on an entity.

        ``action`` is a dotted ``entity.verb`` name such as
        ``"account.opened"``.  ``details`` must be JSON-serialisable.
        """
        if not _ACTION_RE.match(action):
            raise ValidationError(f"audit action must look like 'entity.verb': {action!r}")
        payload = dict(details or {})
        with self.db.transaction():
            last = self.db.query_one("SELECT seq, hash FROM audit_log ORDER BY seq DESC LIMIT 1")
            seq = (last["seq"] + 1) if last else 1
            prev_hash = last["hash"] if last else GENESIS_HASH
            recorded_at = format_timestamp(self.clock.now())
            digest = entry_hash(
                prev_hash,
                seq=seq,
                recorded_at=recorded_at,
                actor=self.actor,
                entity_type=entity_type,
                entity_id=str(entity_id),
                action=action,
                details=payload,
            )
            self.db.execute(
                "INSERT INTO audit_log (seq, recorded_at, actor, entity_type, entity_id, action, "
                "details, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    seq,
                    recorded_at,
                    self.actor,
                    entity_type,
                    str(entity_id),
                    action,
                    canonical_json(payload),
                    prev_hash,
                    digest,
                ),
            )
        return AuditEntry(
            seq, recorded_at, self.actor, entity_type, str(entity_id), action, payload,
            prev_hash, digest,
        )

    def entries(
        self,
        *,
        entity_type: str | None = None,
        entity_id: object | None = None,
        action: str | None = None,
        after_seq: int = 0,
        limit: int | None = None,
    ) -> list[AuditEntry]:
        """Return entries in sequence order, optionally filtered."""
        clauses = ["seq > ?"]
        params: list[Any] = [after_seq]
        if entity_type is not None:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if entity_id is not None:
            clauses.append("entity_id = ?")
            params.append(str(entity_id))
        if action is not None:
            clauses.append("action = ?")
            params.append(action)
        sql = f"SELECT * FROM audit_log WHERE {' AND '.join(clauses)} ORDER BY seq"
        if limit is not None:
            if limit <= 0:
                raise ValidationError("limit must be positive")
            sql += " LIMIT ?"
            params.append(limit)
        return [_row_to_entry(row) for row in self.db.query_all(sql, params)]

    def count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) FROM audit_log")
        return int(row[0]) if row else 0

    def verify(self) -> int:
        """Recompute the hash chain and return the number of entries checked.

        Raises :class:`AuditIntegrityError` naming the first bad sequence
        number if an entry was altered, removed, or reordered.
        """
        prev_hash = GENESIS_HASH
        checked = 0
        for row in self.db.iterate("SELECT * FROM audit_log ORDER BY seq"):
            entry = _row_to_entry(row)
            checked += 1
            if entry.seq != checked:
                raise AuditIntegrityError(
                    f"audit sequence gap at {checked}", details={"seq": entry.seq}
                )
            if entry.prev_hash != prev_hash:
                raise AuditIntegrityError(
                    f"audit entry {entry.seq} does not follow its predecessor",
                    details={"seq": entry.seq},
                )
            expected = entry_hash(
                prev_hash,
                seq=entry.seq,
                recorded_at=entry.recorded_at,
                actor=entry.actor,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                action=entry.action,
                details=entry.details,
            )
            if expected != entry.hash:
                raise AuditIntegrityError(
                    f"audit entry {entry.seq} hash mismatch", details={"seq": entry.seq}
                )
            prev_hash = entry.hash
        return checked
