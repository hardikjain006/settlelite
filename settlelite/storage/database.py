"""Opening SQLite databases and running queries."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..clock import Clock, SystemClock
from ..errors import SchemaError, StorageError
from .migrations import MIGRATIONS, Migration, MigrationRunner
from .unit_of_work import UnitOfWork

MEMORY = ":memory:"

Params = Sequence[Any] | dict[str, Any]


def connect(path: str | os.PathLike[str], *, create: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection configured the way SettleLite expects.

    Foreign keys are enforced, rows are returned as :class:`sqlite3.Row`,
    and the connection runs in autocommit mode so :class:`UnitOfWork` can
    control transactions explicitly.  Unless ``create`` is true the database
    file must already exist.
    """
    target = os.fspath(path)
    try:
        if target == MEMORY:
            connection = sqlite3.connect(MEMORY, isolation_level=None)
        else:
            if not create and not os.path.exists(target):
                raise StorageError(f"database does not exist: {target}; run init first")
            if create:
                parent = os.path.dirname(os.path.abspath(target))
                if not os.path.isdir(parent):
                    raise StorageError(f"directory does not exist: {parent}")
            mode = "rwc" if create else "rw"
            uri = Path(os.path.abspath(target)).as_uri() + f"?mode={mode}"
            connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.Error as exc:
        raise StorageError(f"cannot open database {target}: {exc}") from exc
    return connection


class Database:
    """A connection plus its unit of work and schema helpers."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        path: str = MEMORY,
        migrations: Sequence[Migration] = MIGRATIONS,
    ) -> None:
        self.connection = connection
        self.path = path
        self.migrations = tuple(migrations)
        self.unit_of_work = UnitOfWork(connection)

    @classmethod
    def open(
        cls,
        path: str | os.PathLike[str],
        *,
        create: bool = False,
        migrations: Sequence[Migration] = MIGRATIONS,
    ) -> "Database":
        """Open the database at ``path``; see :func:`connect`."""
        return cls(connect(path, create=create), path=os.fspath(path), migrations=migrations)

    @classmethod
    def memory(cls, *, migrate: bool = True, clock: Clock | None = None) -> "Database":
        """Open a private in-memory database, migrated by default."""
        db = cls(connect(MEMORY))
        if migrate:
            db.migrate(clock=clock)
        return db

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run the block atomically; nests via savepoints."""
        with self.unit_of_work.begin() as connection:
            yield connection

    def execute(self, sql: str, params: Params = ()) -> sqlite3.Cursor:
        try:
            return self.connection.execute(sql, params)
        except sqlite3.Error as exc:
            raise StorageError(f"database error: {exc}") from exc

    def query_one(self, sql: str, params: Params = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def query_all(self, sql: str, params: Params = ()) -> list[sqlite3.Row]:
        return self.execute(sql, params).fetchall()

    def iterate(self, sql: str, params: Params = ()) -> Iterable[sqlite3.Row]:
        return self.execute(sql, params)

    def runner(self, clock: Clock | None = None) -> MigrationRunner:
        return MigrationRunner(self.connection, self.migrations, clock=clock or SystemClock())

    def migrate(self, *, target: int | None = None, clock: Clock | None = None) -> list[Migration]:
        """Apply pending migrations and return the ones applied."""
        return self.runner(clock).migrate(target=target)

    def schema_version(self) -> int:
        return self.runner().current_version()

    def require_current_schema(self) -> None:
        """Raise :class:`SchemaError` unless every known migration is applied."""
        runner = self.runner()
        runner.verify()
        current = runner.current_version()
        latest = runner.latest_version()
        if current == 0:
            raise SchemaError("database is not initialized; run init first")
        if current < latest:
            raise SchemaError(
                f"database schema is at version {current}, expected {latest}; run init to upgrade"
            )
