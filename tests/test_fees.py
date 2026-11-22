import copy
import unittest

from settlelite.errors import (
    CurrencyMismatchError,
    FeeScheduleError,
    IdempotencyError,
    InsufficientFundsError,
    NotFoundError,
    ValidationError,
)
from settlelite.fees import compute_fee, parse_schedule
from settlelite.ledger import EntryKind
from settlelite.money import RoundingMode

from support import balance, fund, make_app, standard_accounts

CARD = {
    "name": "card",
    "currency": "USD",
    "components": [
        {"type": "flat", "amount_minor": 30},
        {"type": "percentage", "bps": 290},
    ],
}

TIERED = {
    "name": "volume",
    "currency": "USD",
    "rounding": "half_even",
    "components": [
        {
            "type": "tiered",
            "tiers": [
                {"up_to_minor": 10000, "bps": 100},
                {"up_to_minor": 50000, "bps": 50},
                {"up_to_minor": None, "bps": 25},
            ],
        }
    ],
}


def schedule(**changes):
    data = copy.deepcopy(CARD)
    data.update(changes)
    return parse_schedule(data)


class ParseScheduleTests(unittest.TestCase):
    def test_defaults(self):
        parsed = parse_schedule(CARD)
        self.assertIs(parsed.rounding, RoundingMode.HALF_UP)
        self.assertEqual(parsed.minimum, 0)
        self.assertIsNone(parsed.maximum)
        self.assertEqual(parsed.splits, ())
        self.assertEqual(parsed.version, 0)

    def test_default_rounding_can_be_supplied(self):
        self.assertIs(parse_schedule(CARD, default_rounding=RoundingMode.DOWN).rounding,
                      RoundingMode.DOWN)

    def test_definition_round_trip(self):
        parsed = parse_schedule(TIERED)
        self.assertEqual(parse_schedule(parsed.definition()), parsed)

    def assertInvalid(self, data, fragment):
        with self.assertRaises(FeeScheduleError) as caught:
            parse_schedule(data)
        self.assertIn(fragment, str(caught.exception))

    def test_invalid_schedules(self):
        cases = [
            (dict(CARD, name="Card!"), "name"),
            (dict(CARD, currency="usd"), "currency"),
            (dict(CARD, rounding="nearest"), "rounding"),
            (dict(CARD, components=[]), "components"),
            (dict(CARD, extra=1), "unknown member 'extra'"),
            (dict(CARD, components=[{"type": "flat"}]), "components[0].amount_minor is required"),
            (dict(CARD, components=[{"type": "flat", "amount_minor": -1}]), "between"),
            (dict(CARD, components=[{"type": "percentage", "bps": 10001}]), "bps"),
            (dict(CARD, components=[{"type": "percentage", "bps": 5, "min_minor": 9,
                                     "max_minor": 1}]), "max_minor"),
            (dict(CARD, components=[{"type": "bogus"}]), "type"),
            (dict(CARD, components=[{"type": "flat", "amount_minor": True}]), "integer"),
            (dict(CARD, minimum_minor=10, maximum_minor=5), "maximum_minor"),
            (dict(CARD, splits=[{"account": "Bad Code", "weight": 1}]), "splits[0].account"),
            (dict(CARD, splits=[{"account": "a", "weight": 0}]), "weight"),
            (dict(CARD, splits=[{"account": "a", "weight": 1}, {"account": "a", "weight": 2}]),
             "repeats"),
            ([], "must be an object"),
        ]
        for data, fragment in cases:
            with self.subTest(fragment=fragment):
                self.assertInvalid(data, fragment)

    def test_invalid_tiers(self):
        def tiers(*items):
            return dict(TIERED, components=[{"type": "tiered", "tiers": list(items)}])

        self.assertInvalid(tiers(), "must not be empty")
        self.assertInvalid(tiers({"up_to_minor": 100, "bps": 1}), "last tier must be unbounded")
        self.assertInvalid(tiers({"up_to_minor": None, "bps": 1}, {"up_to_minor": None, "bps": 1}),
                           "only the last tier")
        self.assertInvalid(tiers({"up_to_minor": 100, "bps": 1}, {"up_to_minor": 50, "bps": 1},
                                 {"up_to_minor": None, "bps": 1}), "ascending")
        self.assertInvalid(tiers({"up_to_minor": None, "bps": 1, "flat": 2}), "unknown member")


class ComputeFeeTests(unittest.TestCase):
    def test_flat_plus_percentage(self):
        quote = compute_fee(schedule(), 10000)
        self.assertEqual([line.amount for line in quote.lines], [30, 290])
        self.assertEqual(quote.total, 320)

    def test_percentage_rounding_modes(self):
        components = [{"type": "percentage", "bps": 250}]
        self.assertEqual(compute_fee(schedule(components=components), 100).total, 3)
        even = schedule(components=components, rounding="half_even")
        self.assertEqual(compute_fee(even, 100).total, 2)
        down = schedule(components=components, rounding="down")
        self.assertEqual(compute_fee(down, 199).total, 4)

    def test_percentage_bounds(self):
        bounded = schedule(components=[{"type": "percentage", "bps": 100, "min_minor": 25,
                                        "max_minor": 500}])
        self.assertEqual(compute_fee(bounded, 100).total, 25)
        self.assertEqual(compute_fee(bounded, 10000).total, 100)
        self.assertEqual(compute_fee(bounded, 10**8).total, 500)

    def test_graduated_tiers(self):
        tiered = parse_schedule(TIERED)
        self.assertEqual(compute_fee(tiered, 5000).total, 50)
        self.assertEqual(compute_fee(tiered, 10000).total, 100)
        self.assertEqual(compute_fee(tiered, 30000).total, 200)
        self.assertEqual(compute_fee(tiered, 100000).total, 425)

    def test_tiers_round_once(self):
        tiered = parse_schedule(TIERED)
        # 10050 -> 100 + 50 * 0.005 = 100.25, rounded once
        self.assertEqual(compute_fee(tiered, 10050).total, 100)

    def test_schedule_minimum_and_maximum_add_adjustment_lines(self):
        low = compute_fee(schedule(minimum_minor=100), 100)
        self.assertEqual(low.total, 100)
        self.assertEqual(low.lines[-1].label, "minimum")
        high = compute_fee(schedule(maximum_minor=200), 100000)
        self.assertEqual(high.total, 200)
        self.assertEqual(high.lines[-1].label, "maximum")

    def test_zero_amount_still_pays_flat_fee(self):
        self.assertEqual(compute_fee(schedule(), 0).total, 30)

    def test_negative_amount_rejected(self):
        with self.assertRaises(ValidationError):
            compute_fee(schedule(), -1)

    def test_labels(self):
        labelled = schedule(components=[{"type": "flat", "amount_minor": 5, "label": "network"}])
        self.assertEqual(compute_fee(labelled, 1).to_dict()["lines"],
                         [{"label": "network", "amount_minor": 5}])


class FeeServiceTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.addCleanup(self.app.close)
        standard_accounts(self.app)
        fund(self.app, "wallet:alice", 10000)

    def test_save_versions(self):
        first = self.app.fees.save(CARD)
        same = self.app.fees.save(CARD)
        changed = self.app.fees.save(dict(CARD, minimum_minor=50))
        self.assertTrue(first.created)
        self.assertFalse(same.created)
        self.assertEqual(same.schedule.version, 1)
        self.assertEqual(changed.schedule.version, 2)
        self.assertEqual(self.app.fees.get("card").version, 2)
        self.assertEqual(self.app.fees.get("card", 1).minimum, 0)
        self.assertEqual([s.version for s in self.app.fees.history("card")], [1, 2])

    def test_list_returns_latest_versions(self):
        self.app.fees.save(CARD)
        self.app.fees.save(dict(CARD, minimum_minor=1))
        self.app.fees.save(TIERED)
        self.assertEqual([(s.name, s.version) for s in self.app.fees.list()],
                         [("card", 2), ("volume", 1)])

    def test_unknown_schedule(self):
        with self.assertRaises(NotFoundError):
            self.app.fees.get("card")
        with self.assertRaises(NotFoundError):
            self.app.fees.history("card")

    def test_quote(self):
        self.app.fees.save(CARD)
        self.assertEqual(self.app.fees.quote("card", 10000).total, 320)

    def test_charge_posts_fee_entry(self):
        self.app.fees.save(CARD)
        result = self.app.fees.charge(request_id="fee-1", schedule="card", payer="wallet:alice",
                                      amount=10000, revenue="revenue:fees")
        self.assertIs(result.entry.kind, EntryKind.FEE)
        self.assertEqual(result.entry.memo, "Fee card v1")
        self.assertEqual(balance(self.app, "wallet:alice"), 10000 - 320)
        self.assertEqual(balance(self.app, "revenue:fees"), 320)
        self.assertEqual(result.charge.fee_amount, 320)

    def test_charge_with_splits_allocates_remainder(self):
        self.app.accounts.open("revenue:partner", "Partner", "revenue", "USD")
        self.app.fees.save(dict(CARD, components=[{"type": "flat", "amount_minor": 100}],
                                splits=[{"account": "revenue:fees", "weight": 1},
                                        {"account": "revenue:partner", "weight": 2}]))
        result = self.app.fees.charge(request_id="fee-1", schedule="card", payer="wallet:alice",
                                      amount=0)
        self.assertEqual(len(result.entry.postings), 3)
        self.assertEqual(balance(self.app, "revenue:fees"), 33)
        self.assertEqual(balance(self.app, "revenue:partner"), 67)

    def test_revenue_target_rules(self):
        self.app.fees.save(CARD)
        with self.assertRaises(ValidationError):
            self.app.fees.charge(request_id="f", schedule="card", payer="wallet:alice", amount=1)
        self.app.fees.save(dict(CARD, name="split", splits=[{"account": "revenue:fees", "weight": 1}]))
        with self.assertRaises(ValidationError):
            self.app.fees.charge(request_id="g", schedule="split", payer="wallet:alice", amount=1,
                                 revenue="revenue:fees")

    def test_zero_fee_records_charge_without_entry(self):
        self.app.fees.save(dict(CARD, components=[{"type": "percentage", "bps": 1}]))
        result = self.app.fees.charge(request_id="f", schedule="card", payer="wallet:alice",
                                      amount=10, revenue="revenue:fees")
        self.assertIsNone(result.entry)
        self.assertEqual(result.quote.total, 0)
        self.assertEqual(len(self.app.fees.list_charges()), 1)
        again = self.app.fees.charge(request_id="f", schedule="card", payer="wallet:alice",
                                     amount=10, revenue="revenue:fees")
        self.assertTrue(again.replayed)

    def test_charge_replay_uses_original_version(self):
        self.app.fees.save(CARD)
        first = self.app.fees.charge(request_id="f", schedule="card", payer="wallet:alice",
                                     amount=10000, revenue="revenue:fees")
        self.app.fees.save(dict(CARD, components=[{"type": "flat", "amount_minor": 1}]))
        again = self.app.fees.charge(request_id="f", schedule="card", payer="wallet:alice",
                                     amount=10000, revenue="revenue:fees")
        self.assertTrue(again.replayed)
        self.assertEqual(again.quote.total, first.quote.total)
        self.assertEqual(again.quote.version, 1)
        self.assertEqual(balance(self.app, "revenue:fees"), 320)

    def test_charge_conflicts(self):
        self.app.fees.save(CARD)
        self.app.fees.charge(request_id="f", schedule="card", payer="wallet:alice",
                             amount=10000, revenue="revenue:fees")
        with self.assertRaises(IdempotencyError):
            self.app.fees.charge(request_id="f", schedule="card", payer="wallet:alice",
                                 amount=1, revenue="revenue:fees")

    def test_charge_currency_mismatch(self):
        self.app.fees.save(dict(CARD, name="eur", currency="EUR"))
        with self.assertRaises(CurrencyMismatchError):
            self.app.fees.charge(request_id="f", schedule="eur", payer="wallet:alice",
                                 amount=1, revenue="revenue:fees")

    def test_charge_requires_funds(self):
        self.app.fees.save(CARD)
        with self.assertRaises(InsufficientFundsError):
            self.app.fees.charge(request_id="f", schedule="card", payer="wallet:bob",
                                 amount=1, revenue="revenue:fees")
        self.assertEqual(self.app.fees.list_charges(), [])

    def test_list_charges_filters(self):
        self.app.fees.save(CARD)
        fund(self.app, "wallet:bob", 1000)
        for request_id, payer in (("a", "wallet:alice"), ("b", "wallet:bob")):
            self.app.fees.charge(request_id=request_id, schedule="card", payer=payer,
                                 amount=100, revenue="revenue:fees")
        self.assertEqual([c.request_id for c in self.app.fees.list_charges(payer="wallet:bob")],
                         ["b"])
        self.assertEqual(len(self.app.fees.list_charges(schedule="card")), 2)

    def test_fee_charge_is_audited(self):
        self.app.fees.save(CARD)
        self.app.fees.charge(request_id="f", schedule="card", payer="wallet:alice",
                             amount=100, revenue="revenue:fees")
        actions = [e.action for e in self.app.audit.entries()]
        self.assertIn("fee_schedule.saved", actions)
        self.assertEqual(actions[-1], "fee.charged")
