"""SQLite schema and a minimal, atomic, forward-only migration runner.

Deliberately uses the stdlib ``sqlite3`` and hand-written SQL rather than an ORM +
migration framework (SQLAlchemy/Alembic): Badgewright is an offline single-user tool
and a heavy stack would add dependency surface for no gain (per the approving vote's
scope guardrail).

Each migration is an ordered list of individual SQL statements. They are executed
one-by-one inside an explicit ``BEGIN … COMMIT`` (SQLite DDL is transactional), so a
statement that fails mid-migration rolls the whole migration back and leaves
``user_version`` unchanged — the next run retries cleanly. (We must NOT use
``executescript`` here: it issues its own COMMIT before running, which would defeat
the rollback.) ``user_version`` records how many migrations have applied, so
``apply_migrations`` is idempotent. Never edit an already-shipped migration — append.
"""

from __future__ import annotations

import sqlite3

__all__ = ["MIGRATIONS", "apply_migrations", "schema_version"]

# Each entry is one migration: an ordered list of single SQL statements.
# Index 0 => user_version 1.
MIGRATIONS: list[list[str]] = [
    # --- v1: initial schema -------------------------------------------------
    [
        # Provenance for every imported datum. A retrieval is uniquely identified by
        # its bytes *and* the time it was fetched, so two distinct fetches that happen
        # to return identical bytes are two rows (preserving time-series resolution),
        # while re-importing the exact same fetch dedups.
        """
        CREATE TABLE source_record (
            id                INTEGER PRIMARY KEY,
            kind              TEXT NOT NULL,
            url               TEXT,
            file_name         TEXT,
            fetched_at        TEXT NOT NULL,        -- ISO-8601; NOT NULL (provenance required)
            parser_version    TEXT NOT NULL,
            raw_sha256        TEXT NOT NULL,
            cache_ttl_seconds INTEGER,
            http_status       INTEGER,
            UNIQUE (raw_sha256, fetched_at)
        )
        """,
        """
        CREATE TABLE steam_app (
            appid     INTEGER PRIMARY KEY,
            name      TEXT NOT NULL,
            source_id INTEGER REFERENCES source_record(id)
        )
        """,
        """
        CREATE TABLE badge_set (
            appid     INTEGER PRIMARY KEY,
            set_size  INTEGER NOT NULL,
            source_id INTEGER REFERENCES source_record(id)
        )
        """,
        """
        CREATE TABLE card (
            appid            INTEGER NOT NULL,
            market_hash_name TEXT NOT NULL,
            card_name        TEXT,
            is_foil          INTEGER NOT NULL DEFAULT 0,
            marketable       INTEGER NOT NULL DEFAULT 1,
            tradable         INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (appid, market_hash_name)
        )
        """,
        # Single-user local tool: current-state rows keyed by logical identity.
        """
        CREATE TABLE user_card_inventory (
            appid            INTEGER NOT NULL,
            market_hash_name TEXT NOT NULL,
            quantity         INTEGER NOT NULL,
            is_foil          INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (appid, market_hash_name)
        )
        """,
        """
        CREATE TABLE user_badge_progress (
            appid   INTEGER NOT NULL,
            is_foil INTEGER NOT NULL DEFAULT 0,
            level   INTEGER NOT NULL,
            PRIMARY KEY (appid, is_foil)
        )
        """,
        # Append-only price history: one row per (item, fetch time). The UNIQUE key
        # makes re-importing the same observation idempotent while still recording an
        # unchanged price at a later time as a new point.
        """
        CREATE TABLE price_snapshot (
            id               INTEGER PRIMARY KEY,
            appid            INTEGER NOT NULL,
            market_hash_name TEXT NOT NULL,
            lowest_cents     INTEGER,
            median_cents     INTEGER,
            currency         TEXT NOT NULL,
            volume           INTEGER,
            fetched_at       TEXT NOT NULL,
            source_id        INTEGER NOT NULL REFERENCES source_record(id),
            UNIQUE (appid, market_hash_name, fetched_at)
        )
        """,
        "CREATE INDEX idx_price_snapshot_item "
        "ON price_snapshot (appid, market_hash_name, fetched_at)",
    ],
    # --- v2: ask-side depth (number of listings) from the market search endpoint. --
    # priceoverview gives 24h `volume`; search/render gives current `listings` — both are
    # nullable liquidity signals depending on the source. Adding a nullable column needs
    # no table rebuild.
    [
        "ALTER TABLE price_snapshot ADD COLUMN listings INTEGER",
    ],
    # --- v3: non-card community holdings (booster packs, gems, sacks, other). ------
    # Kept in a separate table so the card inventory stays card-shaped; `kind` records
    # the item type. Current-state rows keyed by logical identity (like the card table).
    [
        """
        CREATE TABLE user_item_holding (
            appid            INTEGER NOT NULL,
            market_hash_name TEXT NOT NULL,
            kind             TEXT NOT NULL,
            quantity         INTEGER NOT NULL,
            PRIMARY KEY (appid, market_hash_name)
        )
        """,
    ],
]


def schema_version(conn: sqlite3.Connection) -> int:
    """Return how many migrations have been applied (SQLite ``user_version``)."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Apply any unapplied migrations atomically and in order; return new version.

    Idempotent. Each migration runs in an explicit transaction: on any error the
    whole migration is rolled back and ``user_version`` is left untouched, so a
    partially-applied (bricked) schema is impossible.
    """
    current = schema_version(conn)
    for index in range(current, len(MIGRATIONS)):
        try:
            conn.execute("BEGIN")
            for statement in MIGRATIONS[index]:
                conn.execute(statement)
            # user_version can't be parameterized; index+1 is an int we control.
            conn.execute(f"PRAGMA user_version = {index + 1}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return schema_version(conn)
