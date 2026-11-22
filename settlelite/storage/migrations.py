"""Migration runner with checksum verification."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from ..clock import Clock, SystemClock, format_timestamp
from ..errors import MigrationError
from .schema import MIGRATION_SOURCES

_BOOKKEEPING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


def _normalise(statement: str) -> str:
    return " ".join(statement.split())


@dataclass(frozen=True)
class Migration:
    """One schema change identified by a positive version number."""

    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        """SHA-256 of the whitespace-normalised statements."""
        text = "\n".join(_normalise(statement) for statement in self.statements)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @property
    def label(self) -> str:
        return f"{self.version:04d}_{self.name}"


@dataclass(frozen=True)
class AppliedMigration:
    version: int
    name: str
    checksum: str
    applied_at: str


def validate_sequence(migrations: Sequence[Migration]) -> None:
    """Require versions to be unique and contiguous starting at one."""
    for expected, migration in enumerate(migrations, start=1):
        if migration.version != expected:
            raise MigrationError(
                f"migration versions must be contiguous from 1; found {migration.version} "
                f"at position {expected}"
            )
        if not migration.statements:
            raise MigrationError(f"migration {migration.label} has no statements")


MIGRATIONS: tuple[Migration, ...] = tuple(
    Migration(version, name, statements) for version, name, statements in MIGRATION_SOURCES
)
validate_sequence(MIGRATIONS)


def latest_version(migrations: Sequence[Migration] = MIGRATIONS) -> int:
    return migrations[-1].version if migrations else 0


class MigrationRunner:
    """Applies and verifies migrations against one connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        migrations: Sequence[Migration] = MIGRATIONS,
        *,
        clock: Clock | None = None,
    ) -> None:
        validate_sequence(migrations)
        self.connection = connection
        self.migrations = tuple(migrations)
        self.clock = clock or SystemClock()

    def _has_bookkeeping(self) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        return row is not None

    def applied(self) -> list[AppliedMigration]:
        """Migrations recorded in the database, in version order."""
        if not self._has_bookkeeping():
            return []
        rows = self.connection.execute(
            "SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        return [AppliedMigration(*tuple(row)) for row in rows]

    def current_version(self) -> int:
        applied = self.applied()
        return applied[-1].version if applied else 0

    def latest_version(self) -> int:
        return latest_version(self.migrations)

    def verify(self) -> None:
        """Check that the recorded history matches the known migrations."""
        known = {migration.version: migration for migration in self.migrations}
        for expected, record in enumerate(self.applied(), start=1):
            if record.version != expected:
                raise MigrationError(f"schema history has a gap before version {record.version}")
            migration = known.get(record.version)
            if migration is None:
                raise MigrationError(
                    f"database schema version {record.version} is newer than this release "
                    f"supports (latest {self.latest_version()})"
                )
            if migration.checksum != record.checksum:
                raise MigrationError(
                    f"migration {migration.label} was modified after it was applied"
                )

    def pending(self) -> list[Migration]:
        self.verify()
        current = self.current_version()
        return [migration for migration in self.migrations if migration.version > current]

    def migrate(self, *, target: int | None = None) -> list[Migration]:
        """Apply pending migrations up to ``target`` (default: latest).

        Each migration runs in its own transaction together with its
        bookkeeping row, so a failure leaves the schema at the last fully
        applied version.
        """
        if self.connection.in_transaction:
            raise MigrationError("migrations cannot run inside an open transaction")
        if target is not None and not 0 <= target <= self.latest_version():
            raise MigrationError(f"unknown target schema version: {target}")
        self.connection.execute(_BOOKKEEPING)
        if target is not None and target < self.current_version():
            raise MigrationError("downgrading the schema is not supported")
        applied: list[Migration] = []
        for migration in self.pending():
            if target is not None and migration.version > target:
                break
            self._apply(migration)
            applied.append(migration)
        return applied

    def _apply(self, migration: Migration) -> None:
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    format_timestamp(self.clock.now()),
                ),
            )
        except sqlite3.Error as exc:
            connection.execute("ROLLBACK")
            raise MigrationError(f"migration {migration.label} failed: {exc}") from exc
        connection.execute("COMMIT")
