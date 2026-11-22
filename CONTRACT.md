# SettleLite public contract

## Event validation

`settlelite.parse_cents(value)` accepts a Python integer, including negative and
arbitrarily large values, without rounding or conversion. It rejects booleans,
floats, strings, and null with ValueError. Individual event rules constrain signs.

`settlelite.decode_event(payload)` accepts a JSON object string or a mapping and
returns a frozen `settlelite.Event`. Invalid input raises `settlelite.EventError`,
a ValueError. Each envelope has nonempty string `version`, `request_id`, and
`item_id`, and `kind` equal to `post` or `void`. Whitespace is significant inside
identifiers; object member order and JSON whitespace are not identity.

A post requires nonnegative integer `amount_cents`. Its optional nonnegative
integer `revision` defaults to zero. A void has neither amount_cents nor revision.
Unknown members are invalid. Event validation accepts every syntactically valid
nonempty version string; admission to a ledger is a separate feature.

`settlelite.encode_event(event)` returns compact, sorted JSON for the normalized
envelope. Posts include the defaulted revision; voids omit amount and revision.
Decoding this representation preserves the Event exactly.

`python -m settlelite validate` reads one JSON envelope per stdin line. A valid
line returns `{"ok":true,"event":<normalized object>}`; an invalid line returns
`{"ok":false,"error":"invalid_event","detail":<diagnostic string>}`. Every
result is a compact JSON object followed by one newline. Processing continues
after invalid lines. Exit is zero if every line is valid, two otherwise; empty
input is a successful stream with no results. The diagnostic wording is not
contractual. The CLI never mutates a ledger or creates a database.

## Ledger command line

All other commands operate on a SQLite database chosen by `--db PATH`, then
the `SETTLELITE_DB` environment variable, then the `database` member of the
configuration file given by `--config PATH` or `SETTLELITE_CONFIG`. Only
`init` creates a database file; every other command requires an existing
database whose schema is at the latest migration.

Each command writes exactly one compact JSON object with sorted keys followed
by one newline, except `export` without `--output`, which writes the export
itself. Success objects contain `"ok":true`. Failures are
`{"ok":false,"error":<code>,"detail":<diagnostic>}`, optionally with a
`context` object. Error codes are contractual; diagnostic wording is not.

Exit status is 0 on success, 2 when the request is rejected (invalid input,
unknown records, rule violations, invalid configuration, idempotency
conflicts) and 1 when the database cannot be opened or used. Command-line
usage errors also exit 2 and print usage to stderr.

Error codes: `invalid_input`, `invalid_amount`, `invalid_currency`,
`currency_mismatch`, `unbalanced_entry`, `invalid_calendar`,
`invalid_fee_schedule`, `not_found`, `conflict`, `idempotency_conflict`,
`insufficient_funds`, `account_state`, `hold_state`, `invalid_config`,
`storage_error`, `schema_out_of_date`, `migration_failed`, `audit_integrity`.

Amount arguments are decimal strings in major units with at most as many
fractional digits as the account's currency allows (`12.50` USD, `1500`
JPY). With `--minor` they are integer minor units. JSON output always reports
integer minor units in `*_minor` members, alongside formatted strings.

`--request-id` makes `post`, `reverse`, `hold place`, `hold capture` and
`fees charge` idempotent. Repeating a request with the same id and the same
arguments returns the original result with `"replayed":true` and changes
nothing. Reusing an id with different arguments, or for a different
operation, fails with `idempotency_conflict`. A rejected request records
nothing, so its id may be retried.

`--now TIMESTAMP` replaces the system clock for one invocation. Timestamps in
output are UTC, formatted `YYYY-MM-DDTHH:MM:SSZ`; dates are `YYYY-MM-DD`.
