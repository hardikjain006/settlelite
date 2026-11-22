"""Request log that makes state-changing operations idempotent.

Callers supply a request id with every state-changing operation.  The first
time an id is seen, the operation runs and the log stores a fingerprint of
its payload together with a reference to the result.  Repeating the same
request returns the stored result instead of acting twice; reusing the id
for a different payload is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..clock import Clock, format_timestamp
from ..errors import IdempotencyError, ValidationError
from ..jsonutil import digest
from ..storage.database import Database
from ..storage.repository import Repository

MAX_REQUEST_ID_LENGTH = 128


def validate_request_id(value: object) -> str:
    """Request ids are 1-128 printable characters without surrounding spaces."""
    if not isinstance(value, str) or not value:
        raise ValidationError("request_id must be a nonempty string")
    if len(value) > MAX_REQUEST_ID_LENGTH:
        raise ValidationError(f"request_id must be at most {MAX_REQUEST_ID_LENGTH} characters")
    if value != value.strip() or any(not ch.isprintable() for ch in value):
        raise ValidationError("request_id must be printable without leading or trailing spaces")
    return value


def fingerprint(operation: str, payload: dict[str, Any]) -> str:
    """Stable hash identifying an operation and its normalised payload."""
    return digest({"operation": operation, "payload": payload})


@dataclass(frozen=True)
class RequestRecord:
    request_id: str
    operation: str
    fingerprint: str
    result_type: str
    result_id: int | None
    created_at: str


class RequestLog(Repository):
    """Lookup and storage of processed request ids."""

    def __init__(self, db: Database, clock: Clock) -> None:
        super().__init__(db)
        self.clock = clock

    def find(self, request_id: str) -> RequestRecord | None:
        row = self._one("SELECT * FROM request_log WHERE request_id = ?", (request_id,))
        if row is None:
            return None
        return RequestRecord(
            request_id=row["request_id"],
            operation=row["operation"],
            fingerprint=row["fingerprint"],
            result_type=row["result_type"],
            result_id=row["result_id"],
            created_at=row["created_at"],
        )

    def check(self, request_id: str, operation: str, print_: str) -> RequestRecord | None:
        """Return the prior record for an identical request, if any.

        Raises :class:`IdempotencyError` when the id was used for a
        different operation or payload.
        """
        record = self.find(request_id)
        if record is None:
            return None
        if record.operation != operation:
            raise IdempotencyError(
                f"request_id {request_id!r} was already used for {record.operation}",
                details={"request_id": request_id, "operation": record.operation},
            )
        if record.fingerprint != print_:
            raise IdempotencyError(
                f"request_id {request_id!r} was already used with a different payload",
                details={"request_id": request_id},
            )
        return record

    def remember(
        self,
        request_id: str,
        operation: str,
        print_: str,
        *,
        result_type: str,
        result_id: int | None,
    ) -> RequestRecord:
        created_at = format_timestamp(self.clock.now())
        self.db.execute(
            "INSERT INTO request_log (request_id, operation, fingerprint, result_type, "
            "result_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (request_id, operation, print_, result_type, result_id, created_at),
        )
        return RequestRecord(request_id, operation, print_, result_type, result_id, created_at)
