"""``init``, ``validate``, ``audit`` and ``calendar`` commands."""

from __future__ import annotations

import argparse
import json

from ..app import SettleLite
from ..calendar import BusinessCalendar
from ..errors import ConfigError, ValidationError
from ..events import EventError, decode_event, encode_event
from ..jsonutil import canonical_json
from ..storage.database import Database
from .common import (
    EXIT_OK,
    EXIT_REJECTED,
    CommandContext,
    Payload,
    add_runtime_options,
    date_arg,
    positive_int_arg,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    init = subparsers.add_parser("init", help="create or upgrade a database")
    add_runtime_options(init)
    init.set_defaults(handler=run_init)

    validate = subparsers.add_parser(
        "validate", help="validate event envelopes read from stdin, one per line"
    )
    validate.set_defaults(handler=run_validate)

    audit = subparsers.add_parser("audit", help="inspect the audit log")
    audit_sub = audit.add_subparsers(dest="action", required=True, metavar="ACTION")
    audit_list = audit_sub.add_parser("list", help="list audit entries")
    add_runtime_options(audit_list)
    audit_list.add_argument("--entity-type")
    audit_list.add_argument("--entity-id")
    audit_list.add_argument("--action", dest="audit_action")
    audit_list.add_argument("--after", metavar="SEQ", help="only entries after this sequence number")
    audit_list.add_argument("--limit")
    audit_list.set_defaults(handler=run_audit_list)
    audit_verify = audit_sub.add_parser("verify", help="verify the audit hash chain")
    add_runtime_options(audit_verify)
    audit_verify.set_defaults(handler=run_audit_verify)

    calendar = subparsers.add_parser("calendar", help="business-day calculations")
    calendar_sub = calendar.add_subparsers(dest="action", required=True, metavar="ACTION")
    check = calendar_sub.add_parser("check", help="report whether a date is a business day")
    check.add_argument("date")
    next_day = calendar_sub.add_parser("next", help="next business day after a date")
    next_day.add_argument("date")
    add = calendar_sub.add_parser("add", help="add business days to a date")
    add.add_argument("date")
    add.add_argument("days", help="number of business days; may be negative")
    between = calendar_sub.add_parser("between", help="count business days in (START, END]")
    between.add_argument("start")
    between.add_argument("end")
    for parser, handler in ((check, run_calendar_check), (next_day, run_calendar_next),
                            (add, run_calendar_add), (between, run_calendar_between)):
        parser.add_argument("--config", metavar="PATH", help="JSON configuration file")
        parser.set_defaults(handler=handler)


def run_init(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    config = ctx.config
    if config.database is None:
        raise ConfigError("no database configured; pass --db or set SETTLELITE_DB")
    with Database.open(config.database, create=True) as db:
        applied = db.migrate(clock=ctx.clock)
        db.require_current_schema()
        version = db.schema_version()
    return {
        "database": str(config.database),
        "schema_version": version,
        "applied": [migration.label for migration in applied],
    }


def run_validate(args: argparse.Namespace, ctx: CommandContext) -> int:
    """Validate envelopes; never touches a database."""
    status = EXIT_OK
    for line in ctx.stdin:
        try:
            event = decode_event(line)
            result = {"ok": True, "event": json.loads(encode_event(event))}
        except EventError as exc:
            result = {"ok": False, "error": "invalid_event", "detail": str(exc)}
            status = EXIT_REJECTED
        ctx.stdout.write(canonical_json(result) + "\n")
    return status


def run_audit_list(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    app: SettleLite = ctx.app()
    after = positive_int_arg(args.after, name="--after") or 0
    entries = app.audit.entries(
        entity_type=args.entity_type,
        entity_id=args.entity_id,
        action=args.audit_action,
        after_seq=after,
        limit=positive_int_arg(args.limit, name="--limit"),
    )
    return {"entries": [entry.to_dict() for entry in entries]}


def run_audit_verify(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    checked = ctx.app().audit.verify()
    return {"verified": checked}


def _calendar(ctx: CommandContext) -> BusinessCalendar:
    return ctx.config.build_calendar()


def run_calendar_check(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    calendar = _calendar(ctx)
    day = date_arg(args.date)
    assert day is not None
    return {
        "date": day.isoformat(),
        "business_day": calendar.is_business_day(day),
        "weekend": calendar.is_weekend(day),
        "holiday": calendar.holiday_name(day),
    }


def run_calendar_next(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    day = date_arg(args.date)
    assert day is not None
    return {"date": day.isoformat(), "next": _calendar(ctx).next_business_day(day).isoformat()}


def run_calendar_add(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    day = date_arg(args.date)
    assert day is not None
    try:
        count = int(args.days, 10)
    except ValueError:
        raise ValidationError(f"days must be an integer: {args.days!r}") from None
    result = _calendar(ctx).add_business_days(day, count)
    return {"date": day.isoformat(), "days": count, "result": result.isoformat()}


def run_calendar_between(args: argparse.Namespace, ctx: CommandContext) -> Payload:
    start = date_arg(args.start)
    end = date_arg(args.end)
    assert start is not None and end is not None
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "business_days": _calendar(ctx).business_days_between(start, end),
    }
