import unittest
from datetime import date

from settlelite.config import parse_config
from settlelite.errors import (
    AccountStateError,
    ConflictError,
    CurrencyMismatchError,
    IdempotencyError,
    InsufficientFundsError,
    NotFoundError,
    UnbalancedEntryError,
    ValidationError,
)
from settlelite.ledger import EntryKind, PostingInput, validate_request_id

from support import available, balance, credit, debit, fund, make_app, standard_accounts


class PostingInputTests(unittest.TestCase):
    def test_direction_is_parsed(self):
        self.assertEqual(PostingInput("cash", "debit", 1).direction.value, "debit")

    def test_rejects_bad_values(self):
        for args in (("cash", "left", 1), ("cash", "debit", 0), ("cash", "debit", 1.5),
                     ("Cash", "debit", 1), ("cash", "credit", True)):
            with self.subTest(args=args), self.assertRaises(ValidationError):
                PostingInput(*args)

    def test_request_id_validation(self):
        self.assertEqual(validate_request_id("req-1"), "req-1")
        for bad in ("", " padded", "x" * 129, "tab\there", None):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_request_id(bad)


class PostTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.addCleanup(self.app.close)
        standard_accounts(self.app)

    def test_balanced_entry_updates_balances(self):
        entry = fund(self.app, "wallet:alice", 2500)
        self.assertEqual(entry.kind, EntryKind.STANDARD)
        self.assertEqual(entry.total, 2500)
        self.assertEqual(entry.effective_date, date(2026, 3, 2))
        self.assertEqual(balance(self.app, "wallet:alice"), 2500)
        self.assertEqual(balance(self.app, "cash"), 2500)

    def test_multi_line_entry(self):
        fund(self.app, "wallet:alice", 1000)
        result = self.app.journal.post(
            request_id="split",
            lines=[debit("wallet:alice", 1000), credit("wallet:bob", 600),
                   credit("merchant:shop", 400)],
        )
        self.assertEqual([p.line_no for p in result.entry.postings], [1, 2, 3])
        self.assertEqual(balance(self.app, "wallet:bob"), 600)
        self.assertEqual(balance(self.app, "merchant:shop"), 400)

    def test_unbalanced_entry(self):
        with self.assertRaises(UnbalancedEntryError):
            self.app.journal.post(request_id="u", lines=[debit("cash", 10), credit("wallet:bob", 9)])

    def test_single_line_entry(self):
        with self.assertRaises(UnbalancedEntryError):
            self.app.journal.post(request_id="u", lines=[debit("cash", 10)])

    def test_entry_needs_both_sides(self):
        with self.assertRaises(UnbalancedEntryError):
            self.app.journal.post(request_id="u", lines=[debit("cash", 10), debit("wallet:bob", 10)])

    def test_unknown_account(self):
        with self.assertRaises(NotFoundError):
            self.app.journal.post(request_id="u", lines=[debit("cash", 1), credit("ghost", 1)])

    def test_mixed_currency(self):
        self.app.accounts.open("wallet:eur", "Euro", "liability", "EUR")
        with self.assertRaises(CurrencyMismatchError):
            self.app.journal.post(request_id="u", lines=[debit("cash", 1), credit("wallet:eur", 1)])

    def test_closed_account(self):
        self.app.accounts.close("wallet:bob")
        with self.assertRaises(AccountStateError):
            fund(self.app, "wallet:bob", 1)

    def test_insufficient_funds(self):
        fund(self.app, "wallet:alice", 100)
        with self.assertRaises(InsufficientFundsError) as caught:
            self.app.journal.post(
                request_id="over", lines=[debit("wallet:alice", 101), credit("wallet:bob", 101)]
            )
        self.assertEqual(caught.exception.details["available_minor"], 100)
        self.assertEqual(balance(self.app, "wallet:alice"), 100)

    def test_allow_negative_accounts_may_overdraw(self):
        self.app.accounts.open("credit:line", "Line", "liability", "USD", allow_negative=True)
        self.app.journal.post(request_id="o", lines=[debit("credit:line", 50), credit("wallet:bob", 50)])
        self.assertEqual(balance(self.app, "credit:line"), -50)

    def test_spending_exactly_the_balance(self):
        fund(self.app, "wallet:alice", 100)
        self.app.journal.post(
            request_id="all", lines=[debit("wallet:alice", 100), credit("wallet:bob", 100)]
        )
        self.assertEqual(balance(self.app, "wallet:alice"), 0)

    def test_same_account_on_both_sides_nets_out(self):
        fund(self.app, "wallet:alice", 10)
        self.app.journal.post(
            request_id="net",
            lines=[debit("wallet:alice", 30), credit("wallet:alice", 25), credit("wallet:bob", 5)],
        )
        self.assertEqual(balance(self.app, "wallet:alice"), 5)

    def test_explicit_effective_date_and_memo(self):
        result = self.app.journal.post(
            request_id="dated", lines=[debit("cash", 5), credit("wallet:bob", 5)],
            memo="back-dated", effective_date=date(2026, 2, 27),
        )
        self.assertEqual(result.entry.effective_date, date(2026, 2, 27))
        self.assertEqual(result.entry.memo, "back-dated")

    def test_memo_validation(self):
        with self.assertRaises(ValidationError):
            self.app.journal.post(request_id="m", lines=[debit("cash", 5), credit("wallet:bob", 5)],
                                  memo="line\nbreak")

    def test_posting_is_audited(self):
        entry = fund(self.app, "wallet:alice", 10)
        audit = self.app.audit.entries(entity_type="entry", entity_id=entry.id)
        self.assertEqual([e.action for e in audit], ["entry.posted"])
        self.assertEqual(audit[0].details["total_minor"], 10)

    def test_list_filters(self):
        fund(self.app, "wallet:alice", 10)
        self.app.journal.post(request_id="b", lines=[debit("cash", 5), credit("wallet:bob", 5)],
                              effective_date=date(2026, 2, 1))
        bob = self.app.accounts.get("wallet:bob")
        self.assertEqual([e.request_id for e in self.app.journal.list()], ["b", "fund-wallet:alice-10"])
        self.assertEqual([e.request_id for e in self.app.journal.list(account=bob)], ["b"])
        self.assertEqual(len(self.app.journal.list(start=date(2026, 3, 1))), 1)
        self.assertEqual(len(self.app.journal.list(limit=1)), 1)
        with self.assertRaises(ValidationError):
            self.app.journal.list(start=date(2026, 3, 2), end=date(2026, 3, 1))


class IdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.addCleanup(self.app.close)
        standard_accounts(self.app)
        self.lines = [debit("cash", 700), credit("wallet:alice", 700)]

    def test_replay_returns_original_entry(self):
        first = self.app.journal.post(request_id="dep-1", lines=self.lines)
        second = self.app.journal.post(request_id="dep-1", lines=list(self.lines))
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(first.entry, second.entry)
        self.assertEqual(balance(self.app, "wallet:alice"), 700)

    def test_replay_on_a_later_day_still_matches(self):
        self.app.journal.post(request_id="dep-1", lines=self.lines)
        self.app.clock.advance(days=3)
        self.assertTrue(self.app.journal.post(request_id="dep-1", lines=self.lines).replayed)

    def test_different_payload_conflicts(self):
        self.app.journal.post(request_id="dep-1", lines=self.lines)
        with self.assertRaises(IdempotencyError):
            self.app.journal.post(request_id="dep-1",
                                  lines=[debit("cash", 701), credit("wallet:alice", 701)])
        with self.assertRaises(IdempotencyError):
            self.app.journal.post(request_id="dep-1", lines=self.lines, memo="changed")

    def test_request_id_shared_across_operations_conflicts(self):
        entry = self.app.journal.post(request_id="dep-1", lines=self.lines).entry
        with self.assertRaises(IdempotencyError):
            self.app.journal.reverse(request_id="dep-1", entry_id=entry.id)

    def test_rejected_request_can_be_retried(self):
        with self.assertRaises(InsufficientFundsError):
            self.app.journal.post(request_id="pay", lines=[debit("wallet:alice", 5), credit("wallet:bob", 5)])
        fund(self.app, "wallet:alice", 5)
        result = self.app.journal.post(request_id="pay", lines=[debit("wallet:alice", 5), credit("wallet:bob", 5)])
        self.assertFalse(result.replayed)

    def test_record_rejects_duplicate_request_id(self):
        self.app.journal.post(request_id="dep-1", lines=self.lines)
        with self.assertRaises(ConflictError):
            self.app.journal.record(request_id="dep-1", lines=self.lines)


class ReversalTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.addCleanup(self.app.close)
        standard_accounts(self.app)
        self.entry = fund(self.app, "wallet:alice", 900)

    def test_reversal_offsets_entry(self):
        result = self.app.journal.reverse(request_id="rev", entry_id=self.entry.id)
        reversal = result.entry
        self.assertIs(reversal.kind, EntryKind.REVERSAL)
        self.assertEqual(reversal.reverses_entry_id, self.entry.id)
        self.assertEqual(reversal.memo, f"Reversal of entry {self.entry.id}")
        self.assertEqual(balance(self.app, "wallet:alice"), 0)
        self.assertEqual(balance(self.app, "cash"), 0)
        directions = [(p.account_code, p.direction.value) for p in reversal.postings]
        self.assertEqual(directions, [("cash", "credit"), ("wallet:alice", "debit")])

    def test_entry_reversed_only_once(self):
        self.app.journal.reverse(request_id="rev", entry_id=self.entry.id)
        with self.assertRaises(ConflictError):
            self.app.journal.reverse(request_id="rev-2", entry_id=self.entry.id)

    def test_reversal_replay(self):
        first = self.app.journal.reverse(request_id="rev", entry_id=self.entry.id)
        again = self.app.journal.reverse(request_id="rev", entry_id=self.entry.id)
        self.assertTrue(again.replayed)
        self.assertEqual(first.entry.id, again.entry.id)

    def test_reversal_cannot_be_reversed(self):
        reversal = self.app.journal.reverse(request_id="rev", entry_id=self.entry.id).entry
        with self.assertRaises(ConflictError):
            self.app.journal.reverse(request_id="rev-rev", entry_id=reversal.id)

    def test_reversal_respects_available_funds(self):
        self.app.journal.post(request_id="spend",
                              lines=[debit("wallet:alice", 500), credit("wallet:bob", 500)])
        with self.assertRaises(InsufficientFundsError):
            self.app.journal.reverse(request_id="rev", entry_id=self.entry.id)
        self.assertIsNone(self.app.journal.reversal_of(self.entry.id))

    def test_unknown_entry(self):
        with self.assertRaises(NotFoundError):
            self.app.journal.reverse(request_id="rev", entry_id=999)
        with self.assertRaises(ValidationError):
            self.app.journal.reverse(request_id="rev", entry_id=0)

    def test_reversal_is_audited_on_original(self):
        self.app.journal.reverse(request_id="rev", entry_id=self.entry.id)
        actions = [e.action for e in self.app.audit.entries(entity_type="entry",
                                                            entity_id=self.entry.id)]
        self.assertEqual(actions, ["entry.posted", "entry.reversed"])


class BusinessDayPolicyTests(unittest.TestCase):
    def app_with(self, policy):
        config = parse_config({"posting": {"non_business_day": policy},
                               "calendar": {"holidays": ["2026-03-09"]}})
        app = make_app(config, now="2026-03-07T12:00:00Z")
        self.addCleanup(app.close)
        standard_accounts(app)
        return app

    def test_allow_keeps_weekend_date(self):
        app = self.app_with("allow")
        self.assertEqual(fund(app, "wallet:bob", 1).effective_date, date(2026, 3, 7))

    def test_roll_forward_skips_weekend_and_holiday(self):
        app = self.app_with("roll_forward")
        self.assertEqual(fund(app, "wallet:bob", 1).effective_date, date(2026, 3, 10))

    def test_reject_refuses_non_business_day(self):
        app = self.app_with("reject")
        with self.assertRaises(ValidationError):
            fund(app, "wallet:bob", 1)
        entry = app.journal.post(request_id="ok", lines=[debit("cash", 1), credit("wallet:bob", 1)],
                                 effective_date=date(2026, 3, 6)).entry
        self.assertEqual(entry.effective_date, date(2026, 3, 6))

    def test_available_reflects_postings(self):
        app = self.app_with("allow")
        fund(app, "wallet:bob", 3)
        self.assertEqual(available(app, "wallet:bob"), 3)
