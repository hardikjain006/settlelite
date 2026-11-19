"""Validate a stream of event envelopes."""

import argparse
import json
import sys

from .events import EventError, decode_event, encode_event


def main() -> int:
    parser = argparse.ArgumentParser(description="SettleLite event validation")
    parser.add_argument("command", choices=["validate"])
    parser.parse_args()
    status = 0
    for line in sys.stdin:
        try:
            event = decode_event(line)
            result = {"ok": True, "event": json.loads(encode_event(event))}
        except EventError as exc:
            result = {"ok": False, "error": "invalid_event", "detail": str(exc)}
            status = 2
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return status


if __name__ == "__main__":
    sys.exit(main())
