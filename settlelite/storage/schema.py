"""Numbered schema migrations.

Each migration is a tuple of single SQL statements.  Migrations are applied
in version order and are never edited once released: the runner stores a
checksum of every applied migration and refuses to operate on a database
whose history does not match.
"""

from __future__ import annotations

ACCOUNTS = (
    """
    CREATE TABLE accounts (
        id INTEGER PRIMARY KEY,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        type TEXT NOT NULL
            CHECK (type IN ('asset', 'liability', 'equity', 'revenue', 'expense')),
        currency TEXT NOT NULL CHECK (length(currency) = 3),
        allow_negative INTEGER NOT NULL DEFAULT 0 CHECK (allow_negative IN (0, 1)),
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
        created_at TEXT NOT NULL,
        closed_at TEXT
    )
    """,
)

JOURNAL = (
    """
    CREATE TABLE journal_entries (
        id INTEGER PRIMARY KEY,
        request_id TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL
            CHECK (kind IN ('standard', 'reversal', 'fee', 'capture')),
        memo TEXT NOT NULL DEFAULT '',
        effective_date TEXT NOT NULL,
        currency TEXT NOT NULL,
        created_at TEXT NOT NULL,
        reverses_entry_id INTEGER UNIQUE REFERENCES journal_entries (id)
    )
    """,
    """
    CREATE TABLE postings (
        id INTEGER PRIMARY KEY,
        entry_id INTEGER NOT NULL REFERENCES journal_entries (id),
        line_no INTEGER NOT NULL CHECK (line_no >= 1),
        account_id INTEGER NOT NULL REFERENCES accounts (id),
        direction TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
        amount INTEGER NOT NULL CHECK (amount > 0),
        UNIQUE (entry_id, line_no)
    )
    """,
    "CREATE INDEX postings_by_account ON postings (account_id, entry_id)",
)

REQUEST_LOG = (
    """
    CREATE TABLE request_log (
        request_id TEXT PRIMARY KEY,
        operation TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        result_type TEXT NOT NULL,
        result_id INTEGER,
        created_at TEXT NOT NULL
    )
    """,
)

HOLDS = (
    """
    CREATE TABLE holds (
        id INTEGER PRIMARY KEY,
        request_id TEXT NOT NULL UNIQUE,
        account_id INTEGER NOT NULL REFERENCES accounts (id),
        amount INTEGER NOT NULL CHECK (amount > 0),
        currency TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'captured', 'released', 'expired')),
        reference TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        resolved_at TEXT,
        captured_amount INTEGER,
        capture_entry_id INTEGER REFERENCES journal_entries (id)
    )
    """,
    "CREATE INDEX holds_by_account_status ON holds (account_id, status)",
)

FEE_SCHEDULES = (
    """
    CREATE TABLE fee_schedules (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version >= 1),
        currency TEXT NOT NULL,
        definition TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (name, version)
    )
    """,
    """
    CREATE TABLE fee_charges (
        id INTEGER PRIMARY KEY,
        request_id TEXT NOT NULL UNIQUE,
        schedule_name TEXT NOT NULL,
        schedule_version INTEGER NOT NULL,
        payer_account_id INTEGER NOT NULL REFERENCES accounts (id),
        base_amount INTEGER NOT NULL CHECK (base_amount >= 0),
        fee_amount INTEGER NOT NULL CHECK (fee_amount >= 0),
        entry_id INTEGER REFERENCES journal_entries (id),
        created_at TEXT NOT NULL,
        FOREIGN KEY (schedule_name, schedule_version)
            REFERENCES fee_schedules (name, version)
    )
    """,
)

AUDIT_LOG = (
    """
    CREATE TABLE audit_log (
        seq INTEGER PRIMARY KEY,
        recorded_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        action TEXT NOT NULL,
        details TEXT NOT NULL,
        prev_hash TEXT NOT NULL,
        hash TEXT NOT NULL UNIQUE
    )
    """,
    "CREATE INDEX audit_by_entity ON audit_log (entity_type, entity_id)",
    """
    CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit log is append-only');
    END
    """,
    """
    CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
    BEGIN
        SELECT RAISE(ABORT, 'audit log is append-only');
    END
    """,
)

QUERY_INDEXES = (
    "CREATE INDEX journal_by_effective_date ON journal_entries (effective_date, id)",
    "CREATE INDEX holds_by_expiry ON holds (status, expires_at)",
)

MIGRATION_SOURCES: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (1, "accounts", ACCOUNTS),
    (2, "journal", JOURNAL),
    (3, "request_log", REQUEST_LOG),
    (4, "holds", HOLDS),
    (5, "fee_schedules", FEE_SCHEDULES),
    (6, "audit_log", AUDIT_LOG),
    (7, "query_indexes", QUERY_INDEXES),
)
