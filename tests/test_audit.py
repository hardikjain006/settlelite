import unittest

from settlelite.audit import GENESIS_HASH, AuditLog
from settlelite.clock import FixedClock
from settlelite.errors import AuditIntegrityError, StorageError, ValidationError
from settlelite.storage import Database


class AuditLogTests(unittest.TestCase):
    def setUp(self):
        self.db = Database.memory()
        self.clock = FixedClock("2026-03-02T09:00:00Z")
        self.log = AuditLog(self.db, self.clock, actor="ops")

    def test_entries_are_chained(self):
        first = self.log.record("account", "cash", "account.opened", {"currency": "USD"})
        second = self.log.record("account", "cash", "account.closed")
        self.assertEqual(first.seq, 1)
        self.assertEqual(first.prev_hash, GENESIS_HASH)
        self.assertEqual(second.prev_hash, first.hash)
        self.assertEqual(self.log.verify(), 2)

    def test_filters(self):
        self.log.record("account", "a", "account.opened")
        self.log.record("hold", 1, "hold.placed")
        self.log.record("account", "b", "account.opened")
        self.assertEqual([e.entity_id for e in self.log.entries(entity_type="account")], ["a", "b"])
        self.assertEqual(len(self.log.entries(entity_type="hold", entity_id=1)), 1)
        self.assertEqual([e.seq for e in self.log.entries(after_seq=1, limit=1)], [2])
        self.assertEqual(len(self.log.entries(action="account.opened")), 2)

    def test_updates_and_deletes_are_blocked(self):
        self.log.record("account", "a", "account.opened")
        with self.assertRaises(StorageError):
            self.db.execute("UPDATE audit_log SET actor = 'mallory'")
        with self.assertRaises(StorageError):
            self.db.execute("DELETE FROM audit_log")

    def test_tampering_is_detected(self):
        self.log.record("account", "a", "account.opened", {"name": "A"})
        self.log.record("account", "b", "account.opened", {"name": "B"})
        self.db.execute("DROP TRIGGER audit_log_no_update")
        self.db.execute("UPDATE audit_log SET details = '{\"name\":\"Z\"}' WHERE seq = 1")
        with self.assertRaises(AuditIntegrityError) as caught:
            self.log.verify()
        self.assertEqual(caught.exception.details["seq"], 1)

    def test_rolled_back_change_leaves_no_entry(self):
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                self.log.record("account", "a", "account.opened")
                raise RuntimeError
        self.assertEqual(self.log.count(), 0)

    def test_action_format_is_validated(self):
        with self.assertRaises(ValidationError):
            self.log.record("account", "a", "Opened")

    def test_records_use_the_clock(self):
        self.clock.advance(minutes=5)
        entry = self.log.record("account", "a", "account.opened")
        self.assertEqual(entry.recorded_at, "2026-03-02T09:05:00Z")
        self.assertEqual(entry.actor, "ops")
