import os
import sqlite3
import tempfile
import unittest

from settlelite.errors import MigrationError, SchemaError, StorageError
from settlelite.storage import MIGRATIONS, Database, Migration, MigrationRunner, latest_version
from settlelite.storage.migrations import validate_sequence


class ConnectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "ledger.db")

    def test_missing_database_is_not_created_without_create(self):
        with self.assertRaises(StorageError):
            Database.open(self.path)
        self.assertFalse(os.path.exists(self.path))

    def test_create_and_reopen(self):
        with Database.open(self.path, create=True) as db:
            db.migrate()
        with Database.open(self.path) as db:
            self.assertEqual(db.schema_version(), latest_version())
            db.require_current_schema()

    def test_missing_directory(self):
        with self.assertRaises(StorageError):
            Database.open(os.path.join(self.tmp.name, "nope", "x.db"), create=True)

    def test_foreign_keys_are_enforced(self):
        db = Database.memory()
        with self.assertRaises(StorageError):
            db.execute(
                "INSERT INTO postings (entry_id, line_no, account_id, direction, amount) "
                "VALUES (99, 1, 99, 'debit', 1)"
            )

    def test_uninitialised_database_is_rejected(self):
        db = Database.memory(migrate=False)
        with self.assertRaises(SchemaError):
            db.require_current_schema()

    def test_partially_migrated_database_is_rejected(self):
        db = Database.memory(migrate=False)
        db.migrate(target=3)
        with self.assertRaises(SchemaError):
            db.require_current_schema()


class MigrationTests(unittest.TestCase):
    def test_versions_are_contiguous(self):
        self.assertEqual([m.version for m in MIGRATIONS], list(range(1, len(MIGRATIONS) + 1)))

    def test_migrate_is_idempotent(self):
        db = Database.memory(migrate=False)
        first = db.migrate()
        self.assertEqual(len(first), len(MIGRATIONS))
        self.assertEqual(db.migrate(), [])

    def test_incremental_migration(self):
        db = Database.memory(migrate=False)
        self.assertEqual([m.version for m in db.migrate(target=2)], [1, 2])
        self.assertEqual(db.schema_version(), 2)
        self.assertEqual(db.migrate()[0].version, 3)

    def test_unknown_target(self):
        db = Database.memory(migrate=False)
        with self.assertRaises(MigrationError):
            db.migrate(target=999)

    def test_downgrade_rejected(self):
        db = Database.memory()
        with self.assertRaises(MigrationError):
            db.migrate(target=1)

    def test_modified_migration_detected(self):
        db = Database.memory()
        db.execute("UPDATE schema_migrations SET checksum = 'x' WHERE version = 2")
        with self.assertRaises(MigrationError):
            db.runner().verify()

    def test_newer_database_detected(self):
        db = Database.memory()
        db.execute(
            "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
            "VALUES (?, 'future', 'x', '2030-01-01T00:00:00Z')",
            (latest_version() + 1,),
        )
        with self.assertRaises(MigrationError) as caught:
            db.require_current_schema()
        self.assertIn("newer", str(caught.exception))

    def test_failed_migration_rolls_back(self):
        connection = sqlite3.connect(":memory:", isolation_level=None)
        broken = list(MIGRATIONS[:1]) + [Migration(2, "broken", ("CREATE TABLE t (a)", "NOT SQL"))]
        runner = MigrationRunner(connection, broken)
        with self.assertRaises(MigrationError):
            runner.migrate()
        self.assertEqual(runner.current_version(), 1)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        self.assertNotIn("t", tables)

    def test_sequence_validation(self):
        with self.assertRaises(MigrationError):
            validate_sequence([Migration(2, "x", ("SELECT 1",))])
        with self.assertRaises(MigrationError):
            validate_sequence([Migration(1, "x", ())])

    def test_checksum_ignores_whitespace(self):
        a = Migration(1, "a", ("CREATE TABLE t (a INTEGER)",))
        b = Migration(1, "a", ("CREATE   TABLE t\n  (a INTEGER)",))
        self.assertEqual(a.checksum, b.checksum)


class UnitOfWorkTests(unittest.TestCase):
    def setUp(self):
        self.db = Database.memory(migrate=False)
        self.db.execute("CREATE TABLE items (name TEXT)")

    def names(self):
        return [row[0] for row in self.db.query_all("SELECT name FROM items ORDER BY name")]

    def test_commit(self):
        with self.db.transaction():
            self.db.execute("INSERT INTO items VALUES ('a')")
        self.assertEqual(self.names(), ["a"])

    def test_rollback_on_error(self):
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.db.execute("INSERT INTO items VALUES ('a')")
                raise RuntimeError("boom")
        self.assertEqual(self.names(), [])

    def test_nested_failure_rolls_back_only_inner_scope(self):
        with self.db.transaction():
            self.db.execute("INSERT INTO items VALUES ('outer')")
            with self.assertRaises(ValueError):
                with self.db.transaction():
                    self.db.execute("INSERT INTO items VALUES ('inner')")
                    raise ValueError
            self.assertEqual(self.db.unit_of_work.depth, 1)
        self.assertEqual(self.names(), ["outer"])

    def test_outer_failure_rolls_back_inner_work(self):
        with self.assertRaises(KeyError):
            with self.db.transaction():
                with self.db.transaction():
                    self.db.execute("INSERT INTO items VALUES ('inner')")
                raise KeyError
        self.assertEqual(self.names(), [])
        self.assertFalse(self.db.unit_of_work.active)
