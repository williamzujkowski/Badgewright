"""Local SQLite persistence for Badgewright.

A thin, dependency-free store over stdlib ``sqlite3``. All SQL is parameterized.
Imported data carries provenance: :meth:`Store.record_source` dedups by the source's
SHA-256 so re-importing identical bytes reuses one row, and price observations are
append-only history keyed to their source (the same fetch is never double-stored).

Current-state tables (catalog, inventory, badge progress) are upserted by logical
key; price snapshots accumulate.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import TracebackType

from ..models import (
    BadgeSet,
    Card,
    MarketItem,
    Money,
    PriceSnapshot,
    SourceKind,
    SourceRecord,
    SteamApp,
    UserBadgeProgress,
    UserCardInventory,
)
from .schema import apply_migrations

__all__ = ["Store"]


class Store:
    """A local SQLite database of Badgewright's domain data."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        # isolation_level=None => autocommit: statements commit immediately and the
        # migration runner can manage its own explicit BEGIN/COMMIT transactions.
        self.conn = sqlite3.connect(self._path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        apply_migrations(self.conn)

    @classmethod
    def in_memory(cls) -> Store:
        return cls(":memory:")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- provenance ---------------------------------------------------------

    def record_source(self, source: SourceRecord) -> int:
        """Insert a source record; return its row id.

        A retrieval is identified by ``(raw_sha256, fetched_at)`` — re-importing the
        exact same fetch dedups, but a later fetch returning identical bytes is a new
        row so time-series resolution is preserved.
        """
        fetched_at = source.fetched_at.isoformat()
        cur = self.conn.execute(
            """
            INSERT INTO source_record
                (kind, url, file_name, fetched_at, parser_version, raw_sha256,
                 cache_ttl_seconds, http_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(raw_sha256, fetched_at) DO NOTHING
            """,
            (
                str(source.kind),
                str(source.url) if source.url is not None else None,
                source.file_name,
                fetched_at,
                source.parser_version,
                source.raw_sha256,
                source.cache_ttl_seconds,
                source.http_status,
            ),
        )
        if cur.lastrowid and cur.rowcount:
            return int(cur.lastrowid)
        row = self.conn.execute(
            "SELECT id FROM source_record WHERE raw_sha256 = ? AND fetched_at = ?",
            (source.raw_sha256, fetched_at),
        ).fetchone()
        return int(row["id"])

    # --- catalog (current-state upserts) -----------------------------------

    def upsert_app(self, app: SteamApp, source: SourceRecord | None = None) -> None:
        source_id = self.record_source(source) if source else None
        self.conn.execute(
            """
            INSERT INTO steam_app (appid, name, source_id) VALUES (?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET name = excluded.name, source_id = excluded.source_id
            """,
            (app.appid, app.name, source_id),
        )
        self.conn.commit()

    def upsert_badge_set(self, badge_set: BadgeSet, source: SourceRecord | None = None) -> None:
        source_id = self.record_source(source) if source else None
        self.conn.execute(
            """
            INSERT INTO badge_set (appid, set_size, source_id) VALUES (?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET
                set_size = excluded.set_size, source_id = excluded.source_id
            """,
            (badge_set.appid, badge_set.set_size, source_id),
        )
        self.conn.commit()

    def upsert_card(self, card: Card) -> None:
        self.conn.execute(
            """
            INSERT INTO card (appid, market_hash_name, card_name, is_foil, marketable, tradable)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(appid, market_hash_name) DO UPDATE SET
                card_name = excluded.card_name, is_foil = excluded.is_foil,
                marketable = excluded.marketable, tradable = excluded.tradable
            """,
            (
                card.appid,
                card.market_hash_name,
                card.card_name,
                int(card.is_foil),
                int(card.marketable),
                int(card.tradable),
            ),
        )
        self.conn.commit()

    # --- user state (current-state upserts) --------------------------------

    def upsert_inventory(self, inv: UserCardInventory) -> None:
        self.conn.execute(
            """
            INSERT INTO user_card_inventory (appid, market_hash_name, quantity, is_foil)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(appid, market_hash_name) DO UPDATE SET
                quantity = excluded.quantity, is_foil = excluded.is_foil
            """,
            (inv.appid, inv.market_hash_name, inv.quantity, int(inv.is_foil)),
        )
        self.conn.commit()

    def upsert_badge_progress(self, progress: UserBadgeProgress) -> None:
        self.conn.execute(
            """
            INSERT INTO user_badge_progress (appid, is_foil, level) VALUES (?, ?, ?)
            ON CONFLICT(appid, is_foil) DO UPDATE SET level = excluded.level
            """,
            (progress.appid, int(progress.is_foil), progress.level),
        )
        self.conn.commit()

    # --- price history (append-only, source-hash dedup) --------------------

    def add_price_snapshot(self, snap: PriceSnapshot) -> bool:
        """Append a price observation, keyed by (item, fetch time).

        Returns False if an observation for this item at this exact fetch time was
        already stored (idempotent re-import), True if a new row was inserted. A later
        fetch with an unchanged price is a new observation and is recorded.
        """
        source_id = self.record_source(snap.source)
        fetched_at = snap.source.fetched_at.isoformat()
        existing = self.conn.execute(
            """
            SELECT 1 FROM price_snapshot
            WHERE appid = ? AND market_hash_name = ? AND fetched_at = ?
            """,
            (snap.item.appid, snap.item.market_hash_name, fetched_at),
        ).fetchone()
        if existing:
            return False
        priced = snap.lowest or snap.median
        currency = priced.currency if priced is not None else "USD"
        self.conn.execute(
            """
            INSERT INTO price_snapshot
                (appid, market_hash_name, lowest_cents, median_cents, currency, volume,
                 fetched_at, source_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap.item.appid,
                snap.item.market_hash_name,
                snap.lowest.cents if snap.lowest else None,
                snap.median.cents if snap.median else None,
                currency,
                snap.volume,
                snap.source.fetched_at.isoformat(),
                source_id,
            ),
        )
        self.conn.commit()
        return True

    # --- queries ------------------------------------------------------------

    def get_app(self, appid: int) -> SteamApp | None:
        row = self.conn.execute("SELECT * FROM steam_app WHERE appid = ?", (appid,)).fetchone()
        return SteamApp(appid=row["appid"], name=row["name"]) if row else None

    def list_apps(self) -> list[SteamApp]:
        rows = self.conn.execute("SELECT * FROM steam_app ORDER BY appid").fetchall()
        return [SteamApp(appid=r["appid"], name=r["name"]) for r in rows]

    def list_card_items(self) -> list[MarketItem]:
        """All known cards as MarketItems (the set the price fetcher can refresh)."""
        rows = self.conn.execute(
            "SELECT appid, market_hash_name FROM card ORDER BY appid, market_hash_name"
        ).fetchall()
        return [MarketItem(appid=r["appid"], market_hash_name=r["market_hash_name"]) for r in rows]

    def price_history(self, appid: int, market_hash_name: str) -> list[PriceSnapshot]:
        """All price observations for an item, oldest first."""
        rows = self.conn.execute(
            """
            SELECT p.*, s.kind AS s_kind, s.url AS s_url, s.file_name AS s_file_name,
                   s.fetched_at AS s_fetched_at, s.parser_version AS s_parser_version,
                   s.raw_sha256 AS s_raw_sha256, s.cache_ttl_seconds AS s_ttl,
                   s.http_status AS s_http_status
            FROM price_snapshot p JOIN source_record s ON p.source_id = s.id
            WHERE p.appid = ? AND p.market_hash_name = ?
            ORDER BY p.fetched_at, p.id
            """,
            (appid, market_hash_name),
        ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def latest_price(self, appid: int, market_hash_name: str) -> PriceSnapshot | None:
        history = self.price_history(appid, market_hash_name)
        return history[-1] if history else None

    def _row_to_snapshot(self, r: sqlite3.Row) -> PriceSnapshot:
        item = MarketItem(appid=r["appid"], market_hash_name=r["market_hash_name"])
        currency = r["currency"]
        lowest = Money(r["lowest_cents"], currency) if r["lowest_cents"] is not None else None
        median = Money(r["median_cents"], currency) if r["median_cents"] is not None else None
        source = SourceRecord(
            kind=SourceKind(r["s_kind"]),
            url=r["s_url"],
            file_name=r["s_file_name"],
            fetched_at=datetime.fromisoformat(r["s_fetched_at"]),
            parser_version=r["s_parser_version"],
            raw_sha256=r["s_raw_sha256"],
            cache_ttl_seconds=r["s_ttl"],
            http_status=r["s_http_status"],
        )
        return PriceSnapshot(
            item=item, lowest=lowest, median=median, volume=r["volume"], source=source
        )

    def source_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM source_record").fetchone()[0])

    def iter_price_items(self) -> Iterator[tuple[int, str]]:
        for r in self.conn.execute(
            "SELECT DISTINCT appid, market_hash_name FROM price_snapshot"
        ).fetchall():
            yield int(r["appid"]), str(r["market_hash_name"])
