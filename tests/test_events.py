import json
import subprocess
import sys
import unittest

from settlelite import EventError, decode_event, encode_event


def post(**changes):
    data = {"version": "v1", "request_id": "post-B", "item_id": "B",
            "kind": "post", "amount_cents": 10000}
    data.update(changes)
    return data


class EventTests(unittest.TestCase):
    def test_post_defaults_revision_to_zero(self):
        self.assertEqual(decode_event(post()).revision, 0)

    def test_post_round_trip_preserves_all_values(self):
        event = decode_event(post(item_id="賬 B", amount_cents=10**40 + 1, revision=7))
        self.assertEqual(decode_event(encode_event(event)), event)

    def test_member_order_is_not_event_identity(self):
        data = post(revision=1)
        self.assertEqual(decode_event(json.dumps(data)),
                         decode_event(json.dumps(dict(reversed(list(data.items()))))))

    def test_void_round_trip_has_no_amount(self):
        event = decode_event({"version": "v2", "request_id": "void-B",
                              "item_id": "B", "kind": "void"})
        self.assertIsNone(event.amount_cents)
        self.assertNotIn("amount_cents", json.loads(encode_event(event)))
        self.assertEqual(decode_event(encode_event(event)), event)

    def test_syntactic_validation_accepts_other_versions(self):
        self.assertEqual(decode_event(post(version="future-17")).version, "future-17")

    def test_missing_identity_is_invalid(self):
        data = post()
        del data["request_id"]
        with self.assertRaises(EventError):
            decode_event(data)

    def test_unknown_member_is_invalid(self):
        with self.assertRaises(EventError):
            decode_event(post(extra="unexpected"))

    def test_negative_post_is_invalid(self):
        with self.assertRaises(EventError):
            decode_event(post(amount_cents=-1))

    def test_boolean_revision_is_invalid(self):
        with self.assertRaises(EventError):
            decode_event(post(revision=True))

    def test_void_amount_is_invalid(self):
        with self.assertRaises(EventError):
            decode_event({"version": "v1", "request_id": "v", "item_id": "B",
                          "kind": "void", "amount_cents": 0})

    def test_invalid_json_is_invalid(self):
        with self.assertRaises(EventError):
            decode_event("{")

    def test_non_object_is_invalid(self):
        with self.assertRaises(EventError):
            decode_event("[]")

    def test_cli_emits_normalized_post(self):
        result = subprocess.run([sys.executable, "-m", "settlelite", "validate"],
                                input=json.dumps(post()) + "\n", text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         {"ok": True, "event": json.loads(encode_event(decode_event(post())))})
        self.assertTrue(result.stdout.endswith("\n"))

    def test_cli_continues_after_an_invalid_line(self):
        result = subprocess.run([sys.executable, "-m", "settlelite", "validate"],
                                input="[]\n" + json.dumps(post()) + "\n",
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["error"], "invalid_event")
        self.assertTrue(records[1]["ok"])
