import unittest
from datetime import datetime, timedelta, timezone

from settlelite.config import parse_config
from settlelite.errors import (
    CurrencyMismatchError,
    HoldStateError,
    IdempotencyError,
    InsufficientFundsError,
    NotFoundError,
    ValidationError,
)
from settlelite.holds import HoldStatus
from settlelite.ledger import EntryKind

from support import available, balance, credit, debit, fund, make_app, standard_accounts


class HoldTestCase(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.addCleanup(self.app.close)
        standard_accounts(self.app)
        fund(self.app, "wallet:alice", 1000)

    def place(self, amount=400, request_id="h1", **kwargs):
        return self.app.holds.place(request_id=request_id, account="wallet:alice",
                                    amount=amount, **kwargs).hold


class PlaceTests(HoldTestCase):
    def test_hold_reduces_available_not_balance(self):
        hold = self.place()
        self.assertIs(hold.status, HoldStatus.ACTIVE)
        self.assertEqual(balance(self.app, "wallet:alice"), 1000)
        self.assertEqual(available(self.app, "wallet:alice"), 600)

    def test_default_expiry(self):
        hold = self.place()
        self.assertEqual(hold.expires_at, "2026-03-09T09:00:00Z")

    def test_explicit_expiry(self):
        hold = self.place(expires_in=timedelta(hours=2))
        self.assertEqual(hold.expires_at, "2026-03-02T11:00:00Z")
        other = self.place(request_id="h2", amount=1,
                           expires_at=datetime(2026, 3, 3, tzinfo=timezone.utc))
        self.assertEqual(other.expires_at, "2026-03-03T00:00:00Z")

    def test_expiry_validation(self):
        with self.assertRaises(ValidationError):
            self.place(expires_in=timedelta(hours=1), expires_at=datetime(2026, 3, 3, tzinfo=timezone.utc))
        with self.assertRaises(ValidationError):
            self.place(expires_at=datetime(2026, 3, 1, tzinfo=timezone.utc))
        with self.assertRaises(ValidationError):
            self.place(expires_in=timedelta(days=31))

    def test_hold_cannot_exceed_available(self):
        self.place(amount=700)
        with self.assertRaises(InsufficientFundsError):
            self.place(request_id="h2", amount=301)
        self.place(request_id="h3", amount=300)
        self.assertEqual(available(self.app, "wallet:alice"), 0)

    def test_hold_blocks_postings_that_spend_reserved_funds(self):
        self.place(amount=800)
        with self.assertRaises(InsufficientFundsError):
            self.app.journal.post(request_id="spend",
                                  lines=[debit("wallet:alice", 201), credit("wallet:bob", 201)])
        self.app.journal.post(request_id="spend",
                              lines=[debit("wallet:alice", 200), credit("wallet:bob", 200)])

    def test_place_replay_and_conflict(self):
        first = self.app.holds.place(request_id="h1", account="wallet:alice", amount=10)
        again = self.app.holds.place(request_id="h1", account="wallet:alice", amount=10)
        self.assertTrue(again.replayed)
        self.assertEqual(first.hold.id, again.hold.id)
        with self.assertRaises(IdempotencyError):
            self.app.holds.place(request_id="h1", account="wallet:alice", amount=11)

    def test_reference_validation(self):
        with self.assertRaises(ValidationError):
            self.place(reference="x" * 121)
        self.assertEqual(self.place(reference="order-77").reference, "order-77")

    def test_invalid_amount(self):
        with self.assertRaises(ValidationError):
            self.place(amount=0)

    def test_allow_negative_account_can_hold_beyond_balance(self):
        self.app.accounts.open("credit:line", "Line", "liability", "USD", allow_negative=True)
        hold = self.app.holds.place(request_id="cl", account="credit:line", amount=5000).hold
        self.assertEqual(hold.amount, 5000)


class CaptureTests(HoldTestCase):
    def test_full_capture(self):
        hold = self.place()
        result = self.app.holds.capture(request_id="c1", hold_id=hold.id, destination="merchant:shop")
        self.assertIs(result.hold.status, HoldStatus.CAPTURED)
        self.assertEqual(result.hold.captured_amount, 400)
        self.assertIs(result.entry.kind, EntryKind.CAPTURE)
        self.assertEqual(result.entry.memo, f"Capture of hold {hold.id}")
        self.assertEqual(balance(self.app, "wallet:alice"), 600)
        self.assertEqual(available(self.app, "wallet:alice"), 600)
        self.assertEqual(balance(self.app, "merchant:shop"), 400)

    def test_partial_capture_releases_remainder(self):
        hold = self.place()
        result = self.app.holds.capture(request_id="c1", hold_id=hold.id,
                                        destination="merchant:shop", amount=150)
        self.assertEqual(result.hold.captured_amount, 150)
        self.assertEqual(available(self.app, "wallet:alice"), 850)

    def test_capture_can_use_entire_balance_when_fully_held(self):
        hold = self.place(amount=1000)
        self.app.holds.capture(request_id="c1", hold_id=hold.id, destination="merchant:shop")
        self.assertEqual(balance(self.app, "wallet:alice"), 0)

    def test_capture_more_than_held(self):
        hold = self.place()
        with self.assertRaises(ValidationError):
            self.app.holds.capture(request_id="c1", hold_id=hold.id,
                                   destination="merchant:shop", amount=401)

    def test_capture_only_once(self):
        hold = self.place()
        self.app.holds.capture(request_id="c1", hold_id=hold.id, destination="merchant:shop")
        with self.assertRaises(HoldStateError):
            self.app.holds.capture(request_id="c2", hold_id=hold.id, destination="merchant:shop")

    def test_capture_replay(self):
        hold = self.place()
        first = self.app.holds.capture(request_id="c1", hold_id=hold.id, destination="merchant:shop")
        again = self.app.holds.capture(request_id="c1", hold_id=hold.id, destination="merchant:shop")
        self.assertTrue(again.replayed)
        self.assertEqual(first.entry.id, again.entry.id)
        self.assertEqual(balance(self.app, "merchant:shop"), 400)

    def test_capture_to_other_currency(self):
        self.app.accounts.open("merchant:eu", "EU shop", "liability", "EUR")
        hold = self.place()
        with self.assertRaises(CurrencyMismatchError):
            self.app.holds.capture(request_id="c1", hold_id=hold.id, destination="merchant:eu")

    def test_capture_to_same_account(self):
        hold = self.place()
        with self.assertRaises(ValidationError):
            self.app.holds.capture(request_id="c1", hold_id=hold.id, destination="wallet:alice")

    def test_capture_expired_hold(self):
        hold = self.place(expires_in=timedelta(hours=1))
        self.app.clock.advance(hours=1)
        with self.assertRaises(HoldStateError) as caught:
            self.app.holds.capture(request_id="c1", hold_id=hold.id, destination="merchant:shop")
        self.assertIn("expired", str(caught.exception))

    def test_capture_unknown_hold(self):
        with self.assertRaises(NotFoundError):
            self.app.holds.capture(request_id="c1", hold_id=42, destination="merchant:shop")


class ReleaseAndExpiryTests(HoldTestCase):
    def test_release_frees_funds(self):
        hold = self.place()
        released = self.app.holds.release(hold.id, reason="customer cancelled")
        self.assertIs(released.status, HoldStatus.RELEASED)
        self.assertEqual(available(self.app, "wallet:alice"), 1000)
        with self.assertRaises(HoldStateError):
            self.app.holds.release(hold.id)

    def test_expired_hold_stops_reserving_before_sweep(self):
        hold = self.place(expires_in=timedelta(hours=1))
        self.app.clock.advance(minutes=59)
        self.assertEqual(available(self.app, "wallet:alice"), 600)
        self.app.clock.advance(minutes=1)
        self.assertEqual(available(self.app, "wallet:alice"), 1000)
        stored = self.app.holds.get(hold.id)
        self.assertIs(stored.status, HoldStatus.ACTIVE)
        self.assertIs(stored.effective_status(self.app.clock.now()), HoldStatus.EXPIRED)

    def test_sweep_marks_overdue_holds(self):
        short = self.place(expires_in=timedelta(hours=1))
        long = self.place(request_id="h2", amount=10, expires_in=timedelta(hours=5))
        self.app.clock.advance(hours=2)
        expired = self.app.holds.expire_due()
        self.assertEqual([h.id for h in expired], [short.id])
        self.assertIs(self.app.holds.get(long.id).status, HoldStatus.ACTIVE)
        self.assertEqual(self.app.holds.expire_due(), [])

    def test_release_of_expired_hold_rejected(self):
        hold = self.place(expires_in=timedelta(hours=1))
        self.app.clock.advance(hours=3)
        with self.assertRaises(HoldStateError):
            self.app.holds.release(hold.id)

    def test_list_filters(self):
        a = self.place(amount=1)
        self.place(request_id="h2", amount=2)
        self.app.holds.release(a.id)
        self.assertEqual(len(self.app.holds.list(account="wallet:alice")), 2)
        self.assertEqual([h.id for h in self.app.holds.list(status="released")], [a.id])
        with self.assertRaises(ValidationError):
            self.app.holds.list(status="pending")

    def test_hold_lifecycle_is_audited(self):
        hold = self.place()
        self.app.holds.release(hold.id)
        actions = [e.action for e in self.app.audit.entries(entity_type="hold", entity_id=hold.id)]
        self.assertEqual(actions, ["hold.placed", "hold.released"])

    def test_configured_expiry_defaults(self):
        config = parse_config({"holds": {"default_expiry_hours": 3, "max_expiry_hours": 6}})
        app = make_app(config)
        self.addCleanup(app.close)
        standard_accounts(app)
        fund(app, "wallet:alice", 10)
        hold = app.holds.place(request_id="h", account="wallet:alice", amount=1).hold
        self.assertEqual(hold.expires_at, "2026-03-02T12:00:00Z")
        with self.assertRaises(ValidationError):
            app.holds.place(request_id="h2", account="wallet:alice", amount=1,
                            expires_in=timedelta(hours=7))

    def test_to_dict_reports_effective_status(self):
        hold = self.place(expires_in=timedelta(hours=1))
        later = self.app.clock.now() + timedelta(hours=2)
        self.assertEqual(hold.to_dict(later)["status"], "expired")
        self.assertEqual(hold.to_dict()["status"], "active")
