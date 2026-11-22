import csv
import io
import json
import unittest
from datetime import date

from settlelite.errors import ValidationError
from settlelite.reports import (
    EXPORT_COLUMNS,
    ExportFilter,
    build_balance_report,
    build_statement,
    export_journal,
)

from support import credit, debit, fund, make_app, standard_accounts


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.addCleanup(self.app.close)
        standard_accounts(self.app)
        self.app.journal.post(request_id="feb", lines=[debit("cash", 300), credit("wallet:alice", 300)],
                              memo="february", effective_date=date(2026, 2, 20))
        fund(self.app, "wallet:alice", 1000, request_id="mar")
        self.app.journal.post(request_id="pay", lines=[debit("wallet:alice", 250),
                                                       credit("wallet:bob", 250)], memo="p2p, \"quoted\"")


class BalanceReportTests(ReportTestCase):
    def test_rows_sorted_and_totals_balanced(self):
        report = build_balance_report(self.app.balances, self.app.accounts.list())
        codes = [row.balance.account.code for row in report.rows]
        self.assertEqual(codes, sorted(codes))
        self.assertTrue(report.balanced)
        totals = report.totals[0]
        self.assertEqual(totals.currency, "USD")
        self.assertEqual(totals.debits, 1550)
        self.assertEqual(totals.by_type["liability"], 1300)
        self.assertEqual(totals.by_type["asset"], 1300)

    def test_as_of_excludes_later_entries(self):
        report = build_balance_report(self.app.balances, self.app.accounts.list(),
                                      as_of=date(2026, 2, 28))
        balances = {row.balance.account.code: row.balance.balance for row in report.rows}
        self.assertEqual(balances["wallet:alice"], 300)
        self.assertEqual(balances["wallet:bob"], 0)

    def test_nonzero_filter_and_closed_accounts(self):
        self.app.accounts.open("old", "Old", "asset", "USD")
        self.app.accounts.close("old")
        report = build_balance_report(self.app.balances, self.app.accounts.list(), include_zero=False)
        self.assertNotIn("merchant:shop", [row.balance.account.code for row in report.rows])
        self.assertNotIn("old", [row.balance.account.code for row in report.rows])
        with_closed = build_balance_report(self.app.balances, self.app.accounts.list(),
                                           include_closed=True)
        self.assertIn("old", [row.balance.account.code for row in with_closed.rows])

    def test_reserved_amounts_are_reported(self):
        self.app.holds.place(request_id="h", account="wallet:alice", amount=50)
        report = build_balance_report(self.app.balances, self.app.accounts.list())
        row = next(r for r in report.rows if r.balance.account.code == "wallet:alice")
        self.assertEqual(row.to_dict()["reserved_minor"], 50)
        self.assertEqual(row.to_dict()["available_minor"], 1000)

    def test_currencies_are_totalled_separately(self):
        self.app.accounts.open("cash:eur", "Euro cash", "asset", "EUR", allow_negative=True)
        self.app.accounts.open("wallet:eur", "Euro wallet", "liability", "EUR")
        self.app.journal.post(request_id="eur", lines=[debit("cash:eur", 9), credit("wallet:eur", 9)])
        report = build_balance_report(self.app.balances, self.app.accounts.list())
        self.assertEqual([t.currency for t in report.totals], ["EUR", "USD"])
        trial = self.app.balances.trial_balance()
        self.assertEqual([(t.currency, t.debits, t.balanced) for t in trial],
                         [("EUR", 9, True), ("USD", 1550, True)])


class StatementTests(ReportTestCase):
    def test_running_balance(self):
        alice = self.app.accounts.get("wallet:alice")
        statement = build_statement(self.app.journal, self.app.balances, alice)
        self.assertEqual([line.amount for line in statement.lines], [300, 1000, -250])
        self.assertEqual([line.balance for line in statement.lines], [300, 1300, 1050])
        self.assertEqual(statement.closing_balance, 1050)

    def test_period_uses_opening_balance(self):
        alice = self.app.accounts.get("wallet:alice")
        statement = build_statement(self.app.journal, self.app.balances, alice,
                                    start=date(2026, 3, 1))
        self.assertEqual(statement.opening_balance, 300)
        self.assertEqual(len(statement.lines), 2)

    def test_empty_statement(self):
        shop = self.app.accounts.get("merchant:shop")
        statement = build_statement(self.app.journal, self.app.balances, shop)
        self.assertEqual(statement.closing_balance, 0)
        self.assertEqual(statement.to_dict()["lines"], [])

    def test_bad_period(self):
        alice = self.app.accounts.get("wallet:alice")
        with self.assertRaises(ValidationError):
            build_statement(self.app.journal, self.app.balances, alice,
                            start=date(2026, 3, 2), end=date(2026, 3, 1))


class ExportTests(ReportTestCase):
    def export(self, fmt="csv", **filters):
        out = io.StringIO()
        count = export_journal(self.app.db, out, fmt=fmt, selection=ExportFilter(**filters))
        return count, out.getvalue()

    def test_csv_has_header_and_one_row_per_posting(self):
        count, text = self.export()
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(tuple(rows[0]), EXPORT_COLUMNS)
        self.assertEqual(count, 6)
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[1][1], "feb")

    def test_csv_quotes_memo(self):
        _, text = self.export()
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(rows[-1]["memo"], 'p2p, "quoted"')
        self.assertEqual(rows[-1]["reverses_entry_id"], "")

    def test_jsonl_rows(self):
        count, text = self.export("jsonl")
        records = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(count, len(records))
        self.assertEqual(records[0]["amount"], "3.00")
        self.assertEqual(list(records[0]), sorted(records[0]))

    def test_order_is_by_effective_date_then_entry(self):
        _, text = self.export("jsonl")
        keys = [(r["effective_date"], r["entry_id"], r["line"])
                for r in map(json.loads, text.splitlines())]
        self.assertEqual(keys, sorted(keys))

    def test_filters(self):
        count, _ = self.export(account="wallet:bob")
        self.assertEqual(count, 1)
        count, _ = self.export(start=date(2026, 3, 1))
        self.assertEqual(count, 4)
        count, _ = self.export(end=date(2026, 2, 28), currency="USD")
        self.assertEqual(count, 2)
        with self.assertRaises(ValidationError):
            ExportFilter(start=date(2026, 3, 2), end=date(2026, 3, 1))

    def test_export_is_deterministic(self):
        self.assertEqual(self.export()[1], self.export()[1])

    def test_unknown_format(self):
        with self.assertRaises(ValidationError):
            self.export("xml")
