import unittest

from settlelite import parse_cents


class MoneyTests(unittest.TestCase):
    def test_positive_cents_are_exact(self):
        self.assertEqual(parse_cents(10000), 10000)

    def test_zero_is_valid(self):
        self.assertEqual(parse_cents(0), 0)

    def test_negative_cents_are_preserved(self):
        self.assertEqual(parse_cents(-81), -81)

    def test_large_cents_are_not_rounded(self):
        self.assertEqual(parse_cents(10**40 + 1), 10**40 + 1)

    def test_booleans_are_not_money(self):
        with self.assertRaises(ValueError):
            parse_cents(True)

    def test_float_is_not_implicitly_rounded(self):
        with self.assertRaises(ValueError):
            parse_cents(80.01)

    def test_string_is_not_implicitly_coerced(self):
        with self.assertRaises(ValueError):
            parse_cents("8000")

    def test_null_is_not_money(self):
        with self.assertRaises(ValueError):
            parse_cents(None)
