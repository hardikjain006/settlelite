# SettleLite

SettleLite is a small payments ledger and settlement back office written in
standard-library Python. It keeps a double-entry journal in SQLite, reserves
funds with authorization holds, computes and posts fees, understands business
days, and produces balance reports and journal exports. Every state change is
idempotent by request id and recorded in a hash-chained audit log.

Run from this repository with Python 3.12:

```sh
python -m settlelite validate
python -m unittest discover -s tests -v
```

`validate` reads one JSON object per input line and emits one JSON result per
line. See CONTRACT.md for its public interface and for the contract of the
ledger commands. The product has no external runtime or test dependencies.
Its repository-owned test entrypoint above is the complete suite.

Build the native environment with `docker build --target goal-run .`. The
Dockerfile also declares a `task-base` stage for the same product runtime.

## Quick start

```sh
export SETTLELITE_DB=ledger.db
python -m settlelite init
python -m settlelite account open cash --name "Operating cash" --type asset --allow-negative
python -m settlelite account open wallet:alice --name Alice --type liability
python -m settlelite account open merchant:shop --name Shop --type liability
python -m settlelite account open revenue:fees --name "Fee revenue" --type revenue

python -m settlelite post --request-id dep-1 \
    --debit cash=100.00 --credit wallet:alice=100.00 --memo "card top-up"
python -m settlelite hold place --request-id auth-1 --account wallet:alice \
    --amount 25.00 --expires-in 72
python -m settlelite hold capture 1 --request-id cap-1 --to merchant:shop --amount 20.00
python -m settlelite balance wallet:alice
python -m settlelite export --format jsonl
```

Every command prints one compact JSON object with sorted keys. Exit status is
0 on success, 2 when the request is rejected and 1 when storage fails.

## Commands

| Command | Purpose |
| --- | --- |
| `init` | Create the database or apply pending schema migrations |
| `account open/show/list/close/reopen` | Manage accounts |
| `post` | Post a balanced entry from repeated `--debit` / `--credit` lines |
| `reverse ENTRY_ID` | Post the exact opposite of an entry (once per entry) |
| `entry show/list` | Inspect journal entries |
| `balance [ACCOUNT]` | One account, all accounts, or `--trial` per-currency totals |
| `statement ACCOUNT` | Lines with running balance for a period |
| `hold place/capture/release/show/list/expire` | Authorization holds |
| `fees set/show/list/history/quote/charge/charges` | Fee schedules and charges |
| `export` | Journal postings as CSV or JSON lines |
| `audit list/verify` | Read and verify the audit log |
| `calendar check/next/add/between` | Business-day arithmetic |
| `validate` | Validate event envelopes from stdin |

Database-backed commands accept `--db`, `--config` and `--now`. Run any
command with `--help` for its options.

## Configuration

An optional JSON file (`--config` or `SETTLELITE_CONFIG`) sets the database
path, default currency, fee rounding mode, the business calendar (weekend days
and holiday files), the policy for entries dated on non-business days
(`allow`, `reject`, `roll_forward`) and hold expiry limits. Unknown settings
are rejected. See `examples/settlelite.json`; holiday files contain one
`YYYY-MM-DD name` per line.

## Value dates, reversals and retries

Every journal entry carries a value date (`effective_date`) and a posting
timestamp (`created_at`). The value date is the `--date` given to the command
that creates the entry (`post`, `reverse`, `fees charge` or `hold capture`)
or, when it is omitted, the current UTC date. The non-business-day policy
and the configured calendar (weekend days, holiday files and inline
holidays) apply to every entry in the same way, whichever of these commands
creates it: under `roll_forward` an entry whose value date is a weekend day
or a holiday is booked on the next business day, and under `reject` the
request fails with `invalid_input` and records nothing.

Balances `--as-of`, trial balances, statements, `entry list` and exports
select and order entries by value date. The posting timestamp never moves
an entry from one period to another.

A reversal is booked on its own value date: the `--date` given to
`reverse`, or the current date, subject to the same policy. It never takes
the value date of the entry it reverses. A reversal therefore leaves every
figure for earlier dates unchanged: `balance --as-of`, `balance --trial
--as-of`, a statement for an earlier period and an export ending before the
reversal report exactly what they reported before it was made, and the
reversal appears in every report on its own value date. To correct an
earlier period, reverse with an explicit `--date` in that period.

Retrying a request with the same `--request-id` is always safe. See
CONTRACT.md for how a retry is matched to the original request.

## Architecture

```
settlelite/
  money.py        integer minor units, currency table, rounding, allocation
  calendar.py     business-day calendar and holiday files
  clock.py        injectable clocks, timestamp and date parsing
  config.py       JSON configuration loading and validation
  audit.py        append-only, hash-chained audit log
  errors.py       exception hierarchy with stable error codes
  events.py       event-envelope validation (see CONTRACT.md)
  app.py          SettleLite facade wiring the services together
  storage/        SQLite connections, numbered migrations, unit of work
  ledger/         accounts, journal entries, balances, request log
  holds/          authorization holds and the reservation source
  fees/           fee schedules, fee computation, fee charges
  reports/        balance report, statements, journal export
  cli/            argparse commands
```

Amounts are Python integers in the currency's minor unit; no floating point
is used anywhere. Each journal entry is in a single currency and must
balance. Accounts have a type (asset, liability, equity, revenue, expense)
that determines their normal side; balances are reported on that side.
Accounts that do not allow negative balances can never be taken below their
*available* balance: the ledger balance minus active holds.

Services receive a `Database`, a `Clock` and the `AuditLog`. Writes run
inside `Database.transaction()`, which nests through savepoints, so composite
operations such as capturing a hold (journal entry, hold update, request log,
audit records) commit or roll back as a unit. The ledger learns about holds
only through the `ReservationSource` protocol, which the holds package
implements.

Fee schedules are versioned: saving a changed schedule creates a new
version, and each charge records the version it used. Schedules combine flat,
percentage (basis points, with optional bounds) and graduated tiered
components, apply schedule-wide minimum and maximum fees, and may split the
collected fee across several revenue accounts by weight.

The library can also be used directly:

```python
from settlelite import SettleLite
from settlelite.ledger import PostingInput

with SettleLite.open("ledger.db", create=True) as app:
    app.accounts.open("cash", "Cash", "asset", "USD", allow_negative=True)
    app.accounts.open("wallet:bob", "Bob", "liability", "USD")
    app.journal.post(
        request_id="dep-2",
        lines=[PostingInput("cash", "debit", 5000), PostingInput("wallet:bob", "credit", 5000)],
    )
```
