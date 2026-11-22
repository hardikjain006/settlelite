import unittest
from datetime import date, datetime, timedelta, timezone

from settlelite.clock import FixedClock, format_timestamp, parse_date, parse_timestamp
from settlelite.errors import ValidationError


class TimestampTests(unittest.TestCase):
    def test_round_trip(self):
        instant = parse_timestamp("2026-03-02T09:00:00Z")
        self.assertEqual(format_timestamp(instant), "2026-03-02T09:00:00Z")

    def test_offset_is_normalised_to_utc(self):
        instant = parse_timestamp("2026-03-02T10:30:00+01:30")
        self.assertEqual(format_timestamp(instant), "2026-03-02T09:00:00Z")

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValidationError):
            parse_timestamp("2026-03-02T09:00:00")

    def test_garbage_rejected(self):
        with self.assertRaises(ValidationError):
            parse_timestamp("yesterday")

    def test_format_rejects_naive(self):
        with self.assertRaises(ValueError):
            format_timestamp(datetime(2026, 1, 1))


class DateTests(unittest.TestCase):
    def test_strict_dates(self):
        self.assertEqual(parse_date("2026-02-28"), date(2026, 2, 28))
        for text in ("2026-2-28", "2026-02-30", "20260228", None):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_date(text)


class FixedClockTests(unittest.TestCase):
    def test_advance_and_set(self):
        clock = FixedClock("2026-03-02T09:00:00Z")
        clock.advance(hours=2)
        self.assertEqual(clock.now(), datetime(2026, 3, 2, 11, tzinfo=timezone.utc))
        clock.set("2026-04-01T00:00:00Z")
        self.assertEqual(clock.now().month, 4)

    def test_cannot_move_backwards(self):
        clock = FixedClock("2026-03-02T09:00:00Z")
        with self.assertRaises(ValueError):
            clock.advance(seconds=-1)

    def test_microseconds_are_dropped(self):
        clock = FixedClock(datetime(2026, 1, 1, 0, 0, 0, 999, tzinfo=timezone.utc))
        self.assertEqual(clock.now().microsecond, 0)
        self.assertEqual(clock.now() + timedelta(0), clock.now())
