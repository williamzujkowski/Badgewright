"""SQLite schema and a minimal forward-only migration runner.

Deliberately uses the stdlib ``sqlite3`` and hand-written SQL rather than an ORM +
migration framework (SQLAlchemy/Alembic): Badgewright is an offline single-user tool
and a heavy stack would add dependency surface for no gain (per the approving vote's
scope guardrail).

Migrations are an ordered list of SQL scripts. ``user_version`` (a SQLite PRAGMA)
records how many have been applied, so ``apply_migrations`` is idempotent and only
runs the unapplied tail. Never edit an already-shipped migration — append a new one.
"""

from __future__ import annotations

import sqlite3

__all__ = ["MIGRATIONS", "apply_migrations", "schema_version"]

# Each entry is one migration, applied in order. Index 0 => user_version 1.
MIGRATIONS: list[str] = [
    # --- v1: initial schema -------------------------------------------------
    """
    -- Provenance for every imported datum. raw_sha256 is UNIQUE so re-importing
    -- identical source bytes dedups to the same row (source-hash dedup).
    CREATE TABLE source_record (
        id               INTEGER PRIMARY KEY,
        kind             TEXT NOT NULL,
        url              TEXT,
        file_name        TEXT,
        fetched_at       TEXT NOT NULL,          -- ISO-8601 UTC; NOT NULL (provenance required)
        parser_version   TEXT NOT NULL,
        raw_sha256       TEXT NOT NULL UNIQUE,
        cache_ttl_seconds INTEGER,
        http_status      INTEGER
    );

    CREATE TABLE steam_app (
        appid      INTEGER PRIMARY KEY,
        name       TEXT NOT NULL,
        source_id  INTEGER REFERENCES source_record(id)
    );

    CREATE TABLE badge_set (
        appid      INTEGER PRIMARY KEY,
        set_size   INTEGER NOT NULL,
        source_id  INTEGER REFERENCES source_record(id)
    );

    CREATE TABLE card (
        appid            INTEGER NOT NULL,
        market_hash_name TEXT NOT NULL,
        card_name        TEXT,
        is_foil          INTEGER NOT NULL DEFAULT 0,
        marketable       INTEGER NOT NULL DEFAULT 1,
        tradable         INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (appid, market_hash_name)
    );

    -- Single-user local tool: current-state rows keyed by logical identity.
    CREATE TABLE user_card_inventory (
        appid            INTEGER NOT NULL,
        market_hash_name TEXT NOT NULL,
        quantity         INTEGER NOT NULL,
        is_foil          INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (appid, market_hash_name)
    );

    CREATE TABLE user_badge_progress (
        appid    INTEGER NOT NULL,
        is_foil  INTEGER NOT NULL DEFAULT 0,
        level    INTEGER NOT NULL,
        PRIMARY KEY (appid, is_foil)
    );

    -- Append-only price history. One row per observation; dedup is by source hash
    -- (the UNIQUE raw_sha256 on source_record) so the same fetch is not double-stored.
    CREATE TABLE price_snapshot (
        id               INTEGER PRIMARY KEY,
        appid            INTEGER NOT NULL,
        market_hash_name TEXT NOT NULL,
        lowest_cents     INTEGER,
        median_cents     INTEGER,
        currency         TEXT NOT NULL,
        volume           INTEGER,
        fetched_at       TEXT NOT NULL,
        source_id        INTEGER NOT NULL REFERENCES source_record(id)
    );
    CREATE INDEX idx_price_snapshot_item ON price_snapshot (appid, market_hash_name, fetched_at);
    """,
]


def schema_version(conn: sqlite3.Connection) -> int:
    """Return how many migrations have been applied (SQLite ``user_version``)."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Apply any unapplied migrations in order. Returns the resulting version.

    Idempotent: applying twice is a no-op. Each migration runs in a transaction and
    bumps ``user_version`` only on success.
    """
    current = schema_version(conn)
    for index in range(current, len(MIGRATIONS)):
        with conn:  # transaction: commit on success, rollback on error
            conn.executescript(MIGRATIONS[index])
            # user_version can't be parameterized; index+1 is an int we control.
            conn.execute(f"PRAGMA user_version = {index + 1}")
    return schema_version(conn)
