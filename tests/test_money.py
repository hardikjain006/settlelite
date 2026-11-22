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


from fractions import Fraction

from settlelite.errors import AmountError, CurrencyError, CurrencyMismatchError, ValidationError
from settlelite.money import (
    MAX_AMOUNT,
    Money,
    RoundingMode,
    allocate,
    apply_basis_points,
    divide,
    format_amount,
    get_currency,
    parse_amount,
    require_positive,
    round_fraction,
    split_evenly,
    sum_money,
)


class CurrencyTableTests(unittest.TestCase):
    def test_known_currency_exponents(self):
        self.assertEqual(get_currency("USD").exponent, 2)
        self.assertEqual(get_currency("JPY").exponent, 0)
        self.assertEqual(get_currency("KWD").exponent, 3)

    def test_lower_case_code_is_rejected(self):
        with self.assertRaises(CurrencyError):
            get_currency("usd")

    def test_unknown_code_is_rejected(self):
        with self.assertRaises(CurrencyError):
            get_currency("XXX")

    def test_non_string_code_is_rejected(self):
        with self.assertRaises(CurrencyError):
            get_currency(840)


class RoundingTests(unittest.TestCase):
    def check(self, mode, cases):
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(round_fraction(Fraction(value), mode), expected)

    def test_half_up_rounds_ties_away_from_zero(self):
        self.check(RoundingMode.HALF_UP, [("2.5", 3), ("3.5", 4), ("-2.5", -3), ("2.49", 2)])

    def test_half_even_rounds_ties_to_even(self):
        self.check(RoundingMode.HALF_EVEN, [("2.5", 2), ("3.5", 4), ("-2.5", -2), ("2.51", 3)])

    def test_down_truncates_toward_zero(self):
        self.check(RoundingMode.DOWN, [("2.9", 2), ("-2.9", -2)])

    def test_up_rounds_away_from_zero(self):
        self.check(RoundingMode.UP, [("2.1", 3), ("-2.1", -3), ("2", 2)])

    def test_parse_mode_by_name(self):
        self.assertIs(RoundingMode.parse("half_even"), RoundingMode.HALF_EVEN)

    def test_parse_unknown_mode(self):
        with self.assertRaises(ValidationError):
            RoundingMode.parse("ceiling")

    def test_divide_rounds_exact_quotient(self):
        self.assertEqual(divide(10, 4, RoundingMode.HALF_EVEN), 2)
        self.assertEqual(divide(10, 4, RoundingMode.HALF_UP), 3)

    def test_divide_by_zero(self):
        with self.assertRaises(ZeroDivisionError):
            divide(1, 0, RoundingMode.HALF_UP)

    def test_basis_points(self):
        self.assertEqual(apply_basis_points(12345, 250, RoundingMode.HALF_UP), 309)
        self.assertEqual(apply_basis_points(200, 25, RoundingMode.HALF_EVEN), 0)
        self.assertEqual(apply_basis_points(200, 25, RoundingMode.HALF_UP), 1)


class AllocationTests(unittest.TestCase):
    def test_remainder_goes_to_largest_fractions(self):
        self.assertEqual(allocate(100, [1, 1, 1]), [34, 33, 33])

    def test_allocation_preserves_total(self):
        for amount in (0, 1, 7, 999, 10**12 + 3):
            with self.subTest(amount=amount):
                self.assertEqual(sum(allocate(amount, [3, 5, 11, 2])), amount)

    def test_weights_are_respected(self):
        self.assertEqual(allocate(1000, [70, 30]), [700, 300])

    def test_ties_break_by_position(self):
        self.assertEqual(allocate(5, [1, 1, 1, 1]), [2, 1, 1, 1])

    def test_negative_amount(self):
        self.assertEqual(allocate(-100, [1, 1, 1]), [-34, -33, -33])

    def test_zero_weight_gets_nothing(self):
        self.assertEqual(allocate(10, [0, 1]), [0, 10])

    def test_all_zero_weights_rejected(self):
        with self.assertRaises(ValidationError):
            allocate(10, [0, 0])

    def test_empty_weights_rejected(self):
        with self.assertRaises(ValidationError):
            allocate(10, [])

    def test_negative_weight_rejected(self):
        with self.assertRaises(ValidationError):
            allocate(10, [2, -1])

    def test_split_evenly(self):
        self.assertEqual(split_evenly(10, 3), [4, 3, 3])
        with self.assertRaises(ValidationError):
            split_evenly(10, 0)


class AmountTextTests(unittest.TestCase):
    def test_parse_major_units(self):
        self.assertEqual(parse_amount("12.5", "USD"), 1250)
        self.assertEqual(parse_amount("12", "USD"), 1200)
        self.assertEqual(parse_amount("0.01", "USD"), 1)
        self.assertEqual(parse_amount("-3.20", "USD"), -320)

    def test_parse_respects_exponent(self):
        self.assertEqual(parse_amount("1500", "JPY"), 1500)
        self.assertEqual(parse_amount("1.234", "KWD"), 1234)

    def test_too_many_decimals_rejected(self):
        with self.assertRaises(AmountError):
            parse_amount("1.001", "USD")
        with self.assertRaises(AmountError):
            parse_amount("1.0", "JPY")

    def test_malformed_amounts_rejected(self):
        for text in ("", "1e3", "+1", "1,000", "abc", ".5", "1."):
            with self.subTest(text=text), self.assertRaises(AmountError):
                parse_amount(text, "USD")

    def test_out_of_range_rejected(self):
        with self.assertRaises(AmountError):
            parse_amount("10000000000000.00", "USD")

    def test_format(self):
        self.assertEqual(format_amount(1250, "USD"), "12.50")
        self.assertEqual(format_amount(-5, "USD"), "-0.05")
        self.assertEqual(format_amount(1500, "JPY"), "1500")
        self.assertEqual(format_amount(1, "BHD"), "0.001")

    def test_require_positive(self):
        self.assertEqual(require_positive(5), 5)
        for bad in (0, -1, MAX_AMOUNT + 1, 1.5):
            with self.subTest(bad=bad), self.assertRaises(AmountError):
                require_positive(bad)


class MoneyValueTests(unittest.TestCase):
    def test_same_currency_arithmetic(self):
        self.assertEqual(Money(150, "USD") + Money(50, "USD"), Money(200, "USD"))
        self.assertEqual(Money(150, "USD") - Money(200, "USD"), Money(-50, "USD"))
        self.assertEqual(-Money(1, "EUR"), Money(-1, "EUR"))

    def test_mixed_currency_is_rejected(self):
        with self.assertRaises(CurrencyMismatchError):
            Money(1, "USD") + Money(1, "EUR")
        with self.assertRaises(CurrencyMismatchError):
            Money(1, "USD") < Money(1, "EUR")

    def test_parse_and_format(self):
        value = Money.parse("9.99", "USD")
        self.assertEqual(value.amount, 999)
        self.assertEqual(str(value), "9.99 USD")

    def test_allocate_money(self):
        self.assertEqual(Money(10, "USD").allocate([1, 2]), [Money(3, "USD"), Money(7, "USD")])

    def test_sum_money(self):
        self.assertEqual(sum_money([Money(1, "GBP"), Money(2, "GBP")], "GBP"), Money(3, "GBP"))

    def test_invalid_construction(self):
        with self.assertRaises(AmountError):
            Money(1.5, "USD")
        with self.assertRaises(CurrencyError):
            Money(1, "usd")
