import os
import tempfile
import unittest
from datetime import date

from settlelite.calendar import (
    BusinessCalendar,
    load_holiday_file,
    parse_holiday_lines,
    parse_weekend,
)
from settlelite.errors import CalendarError, ValidationError

FRI = date(2026, 3, 6)
SAT = date(2026, 3, 7)
SUN = date(2026, 3, 8)
MON = date(2026, 3, 9)


class WeekendTests(unittest.TestCase):
    def test_default_weekend_is_saturday_and_sunday(self):
        calendar = BusinessCalendar()
        self.assertTrue(calendar.is_business_day(FRI))
        self.assertFalse(calendar.is_business_day(SAT))
        self.assertFalse(calendar.is_business_day(SUN))

    def test_custom_weekend(self):
        calendar = BusinessCalendar(parse_weekend(["fri", "sat"]))
        self.assertFalse(calendar.is_business_day(FRI))
        self.assertTrue(calendar.is_business_day(SUN))

    def test_unknown_weekday_name(self):
        with self.assertRaises(CalendarError):
            parse_weekend(["funday"])

    def test_duplicate_weekday_name(self):
        with self.assertRaises(CalendarError):
            parse_weekend(["sat", "SAT"])

    def test_all_days_weekend_rejected(self):
        with self.assertRaises(CalendarError):
            BusinessCalendar(range(7))


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.calendar = BusinessCalendar(holidays={MON: "Spring holiday"})

    def test_next_business_day_skips_weekend_and_holiday(self):
        self.assertEqual(self.calendar.next_business_day(FRI), date(2026, 3, 10))

    def test_next_business_day_is_strictly_after(self):
        self.assertEqual(self.calendar.next_business_day(date(2026, 3, 10)), date(2026, 3, 11))

    def test_previous_business_day(self):
        self.assertEqual(self.calendar.previous_business_day(date(2026, 3, 10)), FRI)

    def test_roll_forward_and_backward(self):
        self.assertEqual(self.calendar.roll_forward(SAT), date(2026, 3, 10))
        self.assertEqual(self.calendar.roll_backward(SAT), FRI)
        self.assertEqual(self.calendar.roll_forward(FRI), FRI)

    def test_add_business_days_forward(self):
        self.assertEqual(self.calendar.add_business_days(FRI, 1), date(2026, 3, 10))
        self.assertEqual(self.calendar.add_business_days(FRI, 3), date(2026, 3, 12))

    def test_add_business_days_backward(self):
        self.assertEqual(self.calendar.add_business_days(date(2026, 3, 10), -1), FRI)

    def test_add_zero_rolls_forward(self):
        self.assertEqual(self.calendar.add_business_days(SUN, 0), date(2026, 3, 10))

    def test_add_requires_integer(self):
        with self.assertRaises(ValidationError):
            self.calendar.add_business_days(FRI, 1.0)

    def test_business_days_between(self):
        self.assertEqual(self.calendar.business_days_between(FRI, date(2026, 3, 13)), 4)
        self.assertEqual(self.calendar.business_days_between(date(2026, 3, 13), FRI), -4)
        self.assertEqual(self.calendar.business_days_between(FRI, FRI), 0)

    def test_holiday_lookup(self):
        self.assertEqual(self.calendar.holiday_name(MON), "Spring holiday")
        self.assertIsNone(self.calendar.holiday_name(FRI))
        self.assertEqual(self.calendar.holidays_between(FRI, MON), [(MON, "Spring holiday")])

    def test_with_holidays_returns_new_calendar(self):
        extended = self.calendar.with_holidays([date(2026, 3, 10)])
        self.assertTrue(self.calendar.is_business_day(date(2026, 3, 10)))
        self.assertFalse(extended.is_business_day(date(2026, 3, 10)))


class HolidayFileTests(unittest.TestCase):
    def test_parse_lines_with_comments_and_names(self):
        holidays = parse_holiday_lines(
            ["# national holidays\n", "\n", "2026-12-25 Christmas Day\n", "2026-01-01\n"]
        )
        self.assertEqual(holidays, {date(2026, 12, 25): "Christmas Day", date(2026, 1, 1): ""})

    def test_tab_separated_name(self):
        self.assertEqual(parse_holiday_lines(["2026-07-04\tIndependence Day"]),
                         {date(2026, 7, 4): "Independence Day"})

    def test_bad_date_reports_line_number(self):
        with self.assertRaises(CalendarError) as caught:
            parse_holiday_lines(["2026-01-01", "2026-13-01"], source="h.txt")
        self.assertIn("h.txt:2", str(caught.exception))

    def test_duplicate_date_rejected(self):
        with self.assertRaises(CalendarError):
            parse_holiday_lines(["2026-01-01", "2026-01-01 again"])

    def test_load_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "holidays.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("2026-05-25 Memorial Day\n")
            self.assertEqual(load_holiday_file(path), {date(2026, 5, 25): "Memorial Day"})

    def test_missing_file(self):
        with self.assertRaises(CalendarError):
            load_holiday_file("/nonexistent/holidays.txt")
