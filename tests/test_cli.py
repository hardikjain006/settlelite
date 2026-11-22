import json
import os
import subprocess
import sys
import tempfile
import unittest

from support import run_cli, run_json

NOW = "2026-03-02T09:00:00Z"


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "ledger.db")
        status, out = run_json("init", "--db", self.db, "--now", NOW)
        self.assertEqual(status, 0, out)

    def cli(self, *argv, stdin=""):
        return run_json(*argv, "--db", self.db, "--now", NOW, stdin=stdin)

    def ok(self, *argv, stdin=""):
        status, out = self.cli(*argv, stdin=stdin)
        self.assertEqual(status, 0, out)
        self.assertTrue(out["ok"])
        return out

    def rejected(self, *argv, error=None):
        status, out = self.cli(*argv)
        self.assertEqual(status, 2, out)
        self.assertFalse(out["ok"])
        if error is not None:
            self.assertEqual(out["error"], error)
        return out

    def setup_accounts(self):
        self.ok("account", "open", "cash", "--name", "Cash", "--type", "asset", "--allow-negative")
        self.ok("account", "open", "wallet:alice", "--name", "Alice", "--type", "liability")
        self.ok("account", "open", "merchant:shop", "--name", "Shop", "--type", "liability")
        self.ok("account", "open", "revenue:fees", "--name", "Fees", "--type", "revenue")
        self.ok("post", "--request-id", "dep", "--debit", "cash=100.00",
                "--credit", "wallet:alice=100.00", "--memo", "deposit")

    def write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(data if isinstance(data, str) else json.dumps(data))
        return path


class InitTests(CliTestCase):
    def test_init_reports_applied_migrations(self):
        other = os.path.join(self.tmp.name, "other.db")
        status, out = run_json("init", "--db", other)
        self.assertEqual(status, 0)
        self.assertEqual(out["applied"][0], "0001_accounts")
        self.assertEqual(out["schema_version"], len(out["applied"]))

    def test_init_is_idempotent(self):
        out = self.ok("init")
        self.assertEqual(out["applied"], [])

    def test_commands_require_existing_database(self):
        missing = os.path.join(self.tmp.name, "missing.db")
        status, out = run_json("account", "list", "--db", missing)
        self.assertEqual(status, 1)
        self.assertEqual(out["error"], "storage_error")
        self.assertFalse(os.path.exists(missing))

    def test_no_database_configured(self):
        env_db = os.environ.pop("SETTLELITE_DB", None)
        env_config = os.environ.pop("SETTLELITE_CONFIG", None)
        try:
            status, out = run_json("account", "list")
        finally:
            if env_db is not None:
                os.environ["SETTLELITE_DB"] = env_db
            if env_config is not None:
                os.environ["SETTLELITE_CONFIG"] = env_config
        self.assertEqual(status, 2)
        self.assertEqual(out["error"], "invalid_config")

    def test_config_file_supplies_database(self):
        config = self.write("settle.json", {"database": "ledger.db", "default_currency": "EUR"})
        status, out = run_json("account", "open", "wallet:x", "--name", "X", "--type",
                               "liability", "--config", config)
        self.assertEqual(status, 0, out)
        self.assertEqual(out["account"]["currency"], "EUR")

    def test_invalid_config(self):
        config = self.write("bad.json", {"databse": "x"})
        status, out = run_json("account", "list", "--config", config)
        self.assertEqual(status, 2)
        self.assertEqual(out["error"], "invalid_config")


class AccountCommandTests(CliTestCase):
    def test_open_show_list_close(self):
        out = self.ok("account", "open", "wallet:bob", "--name", "Bob", "--type", "liability",
                      "--currency", "GBP")
        self.assertEqual(out["account"]["currency"], "GBP")
        shown = self.ok("account", "show", "wallet:bob")
        self.assertEqual(shown["balance"]["balance_minor"], 0)
        listed = self.ok("account", "list", "--currency", "GBP")
        self.assertEqual([a["code"] for a in listed["accounts"]], ["wallet:bob"])
        self.assertEqual(self.ok("account", "close", "wallet:bob")["account"]["status"], "closed")
        self.assertEqual(self.ok("account", "reopen", "wallet:bob")["account"]["status"], "open")

    def test_duplicate_account(self):
        self.ok("account", "open", "cash", "--name", "Cash", "--type", "asset")
        self.rejected("account", "open", "cash", "--name", "Cash", "--type", "asset",
                      error="conflict")

    def test_unknown_account(self):
        self.rejected("account", "show", "ghost", error="not_found")

    def test_output_is_compact_sorted_json(self):
        status, text = run_cli("account", "list", "--db", self.db)
        self.assertEqual(status, 0)
        self.assertEqual(text, '{"accounts":[],"ok":true}\n')


class PostCommandTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.setup_accounts()

    def test_post_parses_major_units(self):
        out = self.ok("post", "--request-id", "p1", "--debit", "wallet:alice=12.34",
                      "--credit", "merchant:shop=12.34")
        self.assertEqual(out["entry"]["total_minor"], 1234)
        self.assertFalse(out["replayed"])

    def test_post_minor_units(self):
        out = self.ok("post", "--request-id", "p1", "--minor", "--debit", "wallet:alice=5",
                      "--credit", "merchant:shop=5")
        self.assertEqual(out["entry"]["total_minor"], 5)

    def test_replay_flag(self):
        args = ("post", "--request-id", "p1", "--debit", "wallet:alice=1", "--credit",
                "merchant:shop=1")
        self.ok(*args)
        self.assertTrue(self.ok(*args)["replayed"])

    def test_rejections(self):
        self.rejected("post", "--request-id", "p", "--debit", "wallet:alice=1.001",
                      "--credit", "merchant:shop=1.001", error="invalid_amount")
        self.rejected("post", "--request-id", "p", "--debit", "wallet:alice=2",
                      "--credit", "merchant:shop=1", error="unbalanced_entry")
        self.rejected("post", "--request-id", "p", "--debit", "wallet:alice=500",
                      "--credit", "merchant:shop=500", error="insufficient_funds")
        self.rejected("post", "--request-id", "p", "--debit", "wallet:alice",
                      "--credit", "merchant:shop=1", error="invalid_input")
        self.rejected("post", "--request-id", "p", "--debit", "wallet:alice=1",
                      error="invalid_input")
        self.rejected("post", "--request-id", "dep", "--debit", "cash=1",
                      "--credit", "wallet:alice=1", error="idempotency_conflict")
        self.rejected("post", "--request-id", "p", "--debit", "wallet:alice=1",
                      "--credit", "merchant:shop=1", "--date", "2026-02-30", error="invalid_input")

    def test_reverse_and_entry_show(self):
        out = self.ok("reverse", "1", "--request-id", "rev")
        self.assertEqual(out["entry"]["reverses_entry_id"], 1)
        shown = self.ok("entry", "show", "1")
        self.assertEqual(shown["reversed_by"], out["entry"]["id"])
        self.rejected("reverse", "1", "--request-id", "rev2", error="conflict")
        self.rejected("reverse", "abc", "--request-id", "rev3", error="invalid_input")

    def test_entry_list(self):
        out = self.ok("entry", "list", "--account", "wallet:alice", "--kind", "standard")
        self.assertEqual([e["request_id"] for e in out["entries"]], ["dep"])

    def test_balance_views(self):
        single = self.ok("balance", "wallet:alice")
        self.assertEqual(single["balance"]["balance"], "100.00")
        report = self.ok("balance", "--nonzero")
        self.assertEqual([a["account"] for a in report["accounts"]], ["cash", "wallet:alice"])
        self.assertTrue(report["balanced"])
        trial = self.ok("balance", "--trial")
        self.assertEqual(trial["trial_balance"][0]["debits_minor"], 10000)
        self.rejected("balance", "cash", "--trial", error="invalid_input")
        early = self.ok("balance", "wallet:alice", "--as-of", "2026-03-01")
        self.assertEqual(early["balance"]["balance_minor"], 0)

    def test_statement(self):
        out = self.ok("statement", "wallet:alice", "--from", "2026-03-01", "--to", "2026-03-31")
        self.assertEqual(out["statement"]["closing_balance_minor"], 10000)


class HoldCommandTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.setup_accounts()

    def test_place_capture(self):
        placed = self.ok("hold", "place", "--request-id", "h1", "--account", "wallet:alice",
                         "--amount", "40", "--expires-in", "24", "--reference", "order-1")
        hold_id = str(placed["hold"]["id"])
        self.assertEqual(placed["hold"]["expires_at"], "2026-03-03T09:00:00Z")
        self.assertEqual(self.ok("balance", "wallet:alice")["balance"]["available_minor"], 6000)
        captured = self.ok("hold", "capture", hold_id, "--request-id", "c1", "--to",
                           "merchant:shop", "--amount", "25")
        self.assertEqual(captured["hold"]["status"], "captured")
        self.assertEqual(captured["entry"]["kind"], "capture")
        self.assertEqual(self.ok("balance", "wallet:alice")["balance"]["available_minor"], 7500)

    def test_release_list_show(self):
        placed = self.ok("hold", "place", "--request-id", "h1", "--account", "wallet:alice",
                         "--amount", "10")
        hold_id = str(placed["hold"]["id"])
        self.assertEqual(self.ok("hold", "release", hold_id)["hold"]["status"], "released")
        self.assertEqual(len(self.ok("hold", "list", "--status", "released")["holds"]), 1)
        self.assertEqual(self.ok("hold", "show", hold_id)["hold"]["id"], int(hold_id))
        self.rejected("hold", "release", hold_id, error="hold_state")
        self.rejected("hold", "show", "99", error="not_found")

    def test_expire_with_clock_override(self):
        self.ok("hold", "place", "--request-id", "h1", "--account", "wallet:alice",
                "--amount", "10", "--expires-at", "2026-03-02T10:00:00Z")
        status, out = run_json("hold", "expire", "--db", self.db, "--now", "2026-03-02T11:00:00Z")
        self.assertEqual(status, 0)
        self.assertEqual(len(out["expired"]), 1)

    def test_rejections(self):
        self.rejected("hold", "place", "--request-id", "h", "--account", "wallet:alice",
                      "--amount", "1000", error="insufficient_funds")
        self.rejected("hold", "place", "--request-id", "h", "--account", "wallet:alice",
                      "--amount", "1", "--expires-in", "0", error="invalid_input")
        self.rejected("hold", "place", "--request-id", "h", "--account", "wallet:alice",
                      "--amount", "1", "--expires-at", "tomorrow", error="invalid_input")


class FeeCommandTests(CliTestCase):
    SCHEDULE = {"name": "card", "currency": "USD",
                "components": [{"type": "flat", "amount_minor": 30},
                               {"type": "percentage", "bps": 290}]}

    def setUp(self):
        super().setUp()
        self.setup_accounts()

    def test_set_quote_charge(self):
        path = self.write("card.json", self.SCHEDULE)
        saved = self.ok("fees", "set", "--file", path)
        self.assertEqual(saved["schedule"]["version"], 1)
        quote = self.ok("fees", "quote", "card", "10.00")
        self.assertEqual(quote["quote"]["fee_minor"], 59)
        charged = self.ok("fees", "charge", "card", "--request-id", "f1", "--payer",
                          "wallet:alice", "--amount", "10.00", "--revenue", "revenue:fees")
        self.assertEqual(charged["entry"]["kind"], "fee")
        self.assertEqual(self.ok("balance", "revenue:fees")["balance"]["balance_minor"], 59)
        self.assertEqual(len(self.ok("fees", "charges", "--payer", "wallet:alice")["charges"]), 1)

    def test_set_from_stdin_and_listing(self):
        self.ok("fees", "set", "--file", "-", stdin=json.dumps(self.SCHEDULE))
        self.ok("fees", "set", "--file", "-", stdin=json.dumps(dict(self.SCHEDULE, minimum_minor=5)))
        self.assertEqual([s["version"] for s in self.ok("fees", "history", "card")["versions"]], [1, 2])
        self.assertEqual(self.ok("fees", "list")["schedules"][0]["version"], 2)
        self.assertEqual(self.ok("fees", "show", "card", "--version", "1")["schedule"]["minimum_minor"], 0)

    def test_invalid_schedule_files(self):
        self.rejected("fees", "set", "--file", self.write("bad.json", "{"),
                      error="invalid_fee_schedule")
        self.rejected("fees", "set", "--file", self.write("list.json", "[]"),
                      error="invalid_fee_schedule")
        self.rejected("fees", "set", "--file", os.path.join(self.tmp.name, "absent.json"),
                      error="invalid_fee_schedule")
        self.rejected("fees", "show", "card", error="not_found")


class ExportCommandTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.setup_accounts()

    def test_csv_to_stdout(self):
        status, text = run_cli("export", "--db", self.db)
        self.assertEqual(status, 0)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("entry_id,request_id"))
        self.assertEqual(len(lines), 3)

    def test_jsonl_to_file(self):
        target = os.path.join(self.tmp.name, "journal.jsonl")
        out = self.ok("export", "--format", "jsonl", "--output", target, "--account", "cash")
        self.assertEqual(out["rows"], 1)
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(json.loads(handle.readline())["account"], "cash")

    def test_export_rejections(self):
        self.rejected("export", "--account", "ghost", error="not_found")
        self.rejected("export", "--currency", "usd", error="invalid_currency")
        self.rejected("export", "--from", "2026-03-05", "--to", "2026-03-01", error="invalid_input")


class AuditAndCalendarCommandTests(CliTestCase):
    def test_audit_list_and_verify(self):
        self.setup_accounts()
        listed = self.ok("audit", "list", "--entity-type", "account")
        self.assertEqual(len(listed["entries"]), 4)
        self.assertEqual(self.ok("audit", "list", "--after", "4", "--limit", "1")["entries"][0]["seq"], 5)
        verified = self.ok("audit", "verify")
        self.assertEqual(verified["verified"], 5)

    def test_calendar_commands(self):
        holidays = self.write("h.txt", "2026-03-09 Holiday\n")
        config = self.write("c.json", {"calendar": {"holiday_files": [holidays]}})
        status, out = run_json("calendar", "next", "2026-03-06", "--config", config)
        self.assertEqual((status, out["next"]), (0, "2026-03-10"))
        status, out = run_json("calendar", "check", "2026-03-09", "--config", config)
        self.assertEqual(out["holiday"], "Holiday")
        self.assertFalse(out["business_day"])
        status, out = run_json("calendar", "add", "2026-03-10", "-2", "--config", config)
        self.assertEqual(out["result"], "2026-03-05")
        status, out = run_json("calendar", "between", "2026-03-06", "2026-03-13")
        self.assertEqual(out["business_days"], 5)
        status, out = run_json("calendar", "add", "2026-03-10", "two")
        self.assertEqual(status, 2)


class ValidateCommandTests(unittest.TestCase):
    def test_validate_never_creates_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "ledger.db")
            env = dict(os.environ, SETTLELITE_DB=db)
            result = subprocess.run(
                [sys.executable, "-m", "settlelite", "validate"],
                input='{"version":"v1","request_id":"r","item_id":"i","kind":"void"}\n',
                text=True, capture_output=True, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(os.path.exists(db))

    def test_validate_empty_input(self):
        status, text = run_cli("validate", stdin="")
        self.assertEqual((status, text), (0, ""))

    def test_validate_in_process_matches_contract(self):
        status, text = run_cli("validate", stdin='{"version":"v1","request_id":"r","item_id":"i",'
                                                 '"kind":"post","amount_cents":5}\nnot json\n')
        self.assertEqual(status, 2)
        first, second = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(first["event"]["revision"], 0)
        self.assertEqual(second["error"], "invalid_event")

    def test_unknown_command_is_a_usage_error(self):
        result = subprocess.run([sys.executable, "-m", "settlelite", "bogus"],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stderr)
