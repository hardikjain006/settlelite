"""Explicit transaction management on top of an autocommit connection.

Connections are opened with ``isolation_level=None`` so that the sqlite3
module never begins transactions implicitly.  :class:`UnitOfWork` issues
``BEGIN IMMEDIATE`` for the outermost scope and uses savepoints for nested
scopes, which lets services compose: a service method that opens its own
transaction can be called from inside another service's transaction and
the whole operation still commits or rolls back atomically.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


class UnitOfWork:
    """Tracks transaction nesting for one connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._depth = 0
        self._savepoints = 0

    @property
    def depth(self) -> int:
        """Current nesting depth; zero when no transaction is open."""
        return self._depth

    @property
    def active(self) -> bool:
        return self._depth > 0

    @contextmanager
    def begin(self) -> Iterator[sqlite3.Connection]:
        """Open a transaction (or savepoint) for the duration of the block.

        The block commits when it exits normally and rolls back when it
        raises; the exception is always re-raised.
        """
        if self._depth == 0:
            self._connection.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self._connection
            except BaseException:
                self._depth = 0
                self._connection.execute("ROLLBACK")
                raise
            self._depth = 0
            self._connection.execute("COMMIT")
            return
        self._savepoints += 1
        name = f"sl_sp_{self._savepoints}"
        self._connection.execute(f"SAVEPOINT {name}")
        self._depth += 1
        try:
            yield self._connection
        except BaseException:
            self._depth -= 1
            self._connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self._connection.execute(f"RELEASE SAVEPOINT {name}")
            raise
        self._depth -= 1
        self._connection.execute(f"RELEASE SAVEPOINT {name}")
