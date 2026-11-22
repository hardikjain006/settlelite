import unittest

from settlelite.errors import (
    AccountStateError,
    ConflictError,
    CurrencyError,
    NotFoundError,
    ValidationError,
)
from settlelite.ledger import AccountStatus, AccountType, Direction

from support import credit, debit, fund, make_app, standard_accounts


class AccountTypeTests(unittest.TestCase):
    def test_normal_sides(self):
        self.assertIs(AccountType.ASSET.normal_side, Direction.DEBIT)
        self.assertIs(AccountType.EXPENSE.normal_side, Direction.DEBIT)
        self.assertIs(AccountType.LIABILITY.normal_side, Direction.CREDIT)
        self.assertIs(AccountType.REVENUE.decrease_side, Direction.DEBIT)

    def test_signed_effect(self):
        self.assertEqual(AccountType.LIABILITY.signed(Direction.CREDIT, 5), 5)
        self.assertEqual(AccountType.LIABILITY.signed(Direction.DEBIT, 5), -5)


class AccountServiceTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.addCleanup(self.app.close)

    def test_open_and_get(self):
        account = self.app.accounts.open("wallet:alice", "  Alice  ", "liability", "EUR")
        self.assertEqual(account.name, "Alice")
        self.assertEqual(self.app.accounts.get("wallet:alice"), account)
        self.assertTrue(account.is_open)
        self.assertFalse(account.allow_negative)

    def test_duplicate_code(self):
        self.app.accounts.open("cash", "Cash", "asset", "USD")
        with self.assertRaises(ConflictError):
            self.app.accounts.open("cash", "Cash again", "asset", "USD")

    def test_invalid_inputs(self):
        cases = [
            ("Cash", "Cash", "asset", "USD", ValidationError),
            ("cash", "", "asset", "USD", ValidationError),
            ("cash", "Cash", "income", "USD", ValidationError),
            ("cash", "Cash", "asset", "XYZ", CurrencyError),
        ]
        for code, name, type_, currency, error in cases:
            with self.subTest(code=code, name=name, type=type_), self.assertRaises(error):
                self.app.accounts.open(code, name, type_, currency)

    def test_unknown_account(self):
        with self.assertRaises(NotFoundError):
            self.app.accounts.get("ghost")

    def test_list_filters_and_order(self):
        standard_accounts(self.app)
        self.app.accounts.open("wallet:eur", "Euro", "liability", "EUR")
        codes = [a.code for a in self.app.accounts.list()]
        self.assertEqual(codes, sorted(codes))
        self.assertEqual([a.code for a in self.app.accounts.list(currency="EUR")], ["wallet:eur"])
        self.assertEqual([a.code for a in self.app.accounts.list(type="asset")], ["cash"])

    def test_close_requires_zero_balance(self):
        standard_accounts(self.app)
        fund(self.app, "wallet:alice", 100)
        with self.assertRaises(AccountStateError):
            self.app.accounts.close("wallet:alice")
        self.app.journal.post(
            request_id="out", lines=[debit("wallet:alice", 100), credit("cash", 100)]
        )
        closed = self.app.accounts.close("wallet:alice")
        self.assertIs(closed.status, AccountStatus.CLOSED)
        self.assertEqual(closed.closed_at, "2026-03-02T09:00:00Z")

    def test_close_blocked_by_active_hold(self):
        standard_accounts(self.app)
        self.app.accounts.open("credit:line", "Line", "liability", "USD", allow_negative=True)
        self.app.holds.place(request_id="h", account="credit:line", amount=10)
        with self.assertRaises(AccountStateError):
            self.app.accounts.close("credit:line")

    def test_closed_account_rejects_postings_until_reopened(self):
        standard_accounts(self.app)
        self.app.accounts.close("wallet:bob")
        with self.assertRaises(AccountStateError):
            fund(self.app, "wallet:bob", 1)
        with self.assertRaises(AccountStateError):
            self.app.accounts.close("wallet:bob")
        self.app.accounts.reopen("wallet:bob")
        fund(self.app, "wallet:bob", 1)
        with self.assertRaises(AccountStateError):
            self.app.accounts.reopen("wallet:bob")

    def test_lifecycle_is_audited(self):
        self.app.accounts.open("cash", "Cash", "asset", "USD")
        self.app.accounts.close("cash")
        actions = [e.action for e in self.app.audit.entries(entity_type="account")]
        self.assertEqual(actions, ["account.opened", "account.closed"])
