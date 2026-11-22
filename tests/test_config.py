import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from settlelite.config import load_config, parse_config, resolve_config
from settlelite.errors import ConfigError
from settlelite.ledger import NonBusinessDayPolicy
from settlelite.money import RoundingMode


class ParseConfigTests(unittest.TestCase):
    def test_empty_config_uses_defaults(self):
        config = parse_config({})
        self.assertIsNone(config.database)
        self.assertEqual(config.default_currency, "USD")
        self.assertIs(config.rounding, RoundingMode.HALF_UP)
        self.assertIs(config.posting.non_business_day, NonBusinessDayPolicy.ALLOW)
        self.assertEqual(config.holds.default_expiry_hours, 168)

    def test_full_config(self):
        config = parse_config(
            {
                "database": "ledger.db",
                "default_currency": "EUR",
                "rounding": "half_even",
                "calendar": {"weekend": ["fri", "sat"], "holidays": ["2026-04-03"]},
                "posting": {"non_business_day": "roll_forward"},
                "holds": {"default_expiry_hours": 24, "max_expiry_hours": 48},
            },
            base_dir=Path("/srv/settle"),
        )
        self.assertEqual(config.database, Path("/srv/settle/ledger.db"))
        self.assertEqual(config.default_currency, "EUR")
        self.assertIs(config.rounding, RoundingMode.HALF_EVEN)
        self.assertEqual(config.calendar.weekend, frozenset({4, 5}))
        self.assertIs(config.posting.non_business_day, NonBusinessDayPolicy.ROLL_FORWARD)
        self.assertFalse(config.build_calendar().is_business_day(date(2026, 4, 3)))

    def assertConfigError(self, data, fragment):
        with self.assertRaises(ConfigError) as caught:
            parse_config(data)
        self.assertIn(fragment, str(caught.exception))

    def test_unknown_top_level_key(self):
        self.assertConfigError({"databse": "x.db"}, "config.databse")

    def test_unknown_nested_key(self):
        self.assertConfigError({"holds": {"expiry": 1}}, "config.holds.expiry")

    def test_bad_currency(self):
        self.assertConfigError({"default_currency": "dollars"}, "config.default_currency")

    def test_bad_rounding(self):
        self.assertConfigError({"rounding": "bankers"}, "config.rounding")

    def test_bad_policy(self):
        self.assertConfigError({"posting": {"non_business_day": "skip"}}, "non_business_day")

    def test_bad_holiday(self):
        self.assertConfigError({"calendar": {"holidays": ["03/04/2026"]}}, "config.calendar.holidays")

    def test_default_expiry_cannot_exceed_maximum(self):
        self.assertConfigError(
            {"holds": {"default_expiry_hours": 100, "max_expiry_hours": 10}}, "default_expiry_hours"
        )

    def test_non_object_root(self):
        self.assertConfigError([], "config must be an object")


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_relative_paths_resolve_against_config_dir(self):
        self.write("holidays.txt", "2026-03-09 Holiday\n")
        path = self.write(
            "settle.json",
            json.dumps({"database": "data.db", "calendar": {"holiday_files": ["holidays.txt"]}}),
        )
        config = load_config(path)
        self.assertEqual(config.database, self.dir.resolve() / "data.db")
        self.assertFalse(config.build_calendar().is_business_day(date(2026, 3, 9)))

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config(self.dir / "absent.json")

    def test_invalid_json_reports_position(self):
        path = self.write("bad.json", '{"database": }')
        with self.assertRaises(ConfigError) as caught:
            load_config(path)
        self.assertIn("line 1", str(caught.exception))

    def test_missing_holiday_file_is_a_config_error(self):
        path = self.write("c.json", json.dumps({"calendar": {"holiday_files": ["nope.txt"]}}))
        with self.assertRaises(ConfigError):
            load_config(path).build_calendar()

    def test_resolve_precedence(self):
        path = self.write("c.json", json.dumps({"database": "from-file.db"}))
        env = {"SETTLELITE_CONFIG": os.fspath(path), "SETTLELITE_DB": "from-env.db"}
        self.assertEqual(resolve_config(environ=env).database, Path("from-env.db"))
        self.assertEqual(resolve_config(database="explicit.db", environ=env).database,
                         Path("explicit.db"))
        self.assertEqual(resolve_config(environ={"SETTLELITE_CONFIG": os.fspath(path)}).database,
                         self.dir.resolve() / "from-file.db")
        self.assertIsNone(resolve_config(environ={}).database)


class ExampleFileTests(unittest.TestCase):
    EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

    def test_example_config_and_holidays_load(self):
        config = load_config(self.EXAMPLES / "settlelite.json")
        calendar = config.build_calendar()
        self.assertEqual(calendar.next_business_day(date(2026, 7, 2)), date(2026, 7, 6))
        self.assertIs(config.posting.non_business_day, NonBusinessDayPolicy.ROLL_FORWARD)

    def test_example_fee_schedules_parse(self):
        from settlelite.fees import parse_schedule

        for path in sorted((self.EXAMPLES / "fees").glob("*.json")):
            with self.subTest(path=path.name):
                schedule = parse_schedule(json.loads(path.read_text(encoding="utf-8")))
                self.assertEqual(schedule.name, path.stem)
