"""Tests for the SQLite persistence layer (against :memory: and a temp file)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from steam_badge_optimizer.db import Store, apply_migrations, schema_version
from steam_badge_optimizer.db.schema import MIGRATIONS
from steam_badge_optimizer.models import (
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


def _source(payload: bytes, *, when: datetime | None = None) -> SourceRecord:
    return SourceRecord(
        kind=SourceKind.STEAM_MARKET,
        url="https://steamcommunity.com/market/priceoverview/",
        fetched_at=when or datetime(2026, 7, 5, tzinfo=UTC),
        parser_version="1.0",
        raw_sha256=SourceRecord.sha256_of(payload),
        cache_ttl_seconds=86400,
    )


@pytest.fixture
def store() -> Store:
    s = Store.in_memory()
    yield s
    s.close()


class TestMigrations:
    def test_fresh_db_is_fully_migrated(self, store: Store) -> None:
        assert schema_version(store.conn) == len(MIGRATIONS)

    def test_apply_is_idempotent(self, store: Store) -> None:
        before = schema_version(store.conn)
        assert apply_migrations(store.conn) == before  # no-op second run
        assert apply_migrations(store.conn) == before

    def test_failed_migration_rolls_back_atomically(self, store: Store, monkeypatch) -> None:
        # A migration whose last statement is invalid must leave NO partial state and
        # NOT bump user_version, so the runner can retry cleanly (the HIGH finding).
        good_then_bad = [
            "CREATE TABLE t_probe (x INTEGER)",
            "INSERT INTO t_probe (x) VALUES (1)",
            "THIS IS NOT SQL",
        ]
        monkeypatch.setattr(
            "steam_badge_optimizer.db.schema.MIGRATIONS",
            [*MIGRATIONS, good_then_bad],
        )
        before = schema_version(store.conn)
        with pytest.raises(sqlite3.OperationalError):
            apply_migrations(store.conn)
        assert schema_version(store.conn) == before  # not bumped
        # The partial table must have been rolled back.
        exists = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='t_probe'"
        ).fetchone()
        assert exists is None

    def test_persists_to_file(self, tmp_path) -> None:
        db = tmp_path / "sbo.sqlite3"
        with Store(db) as s:
            s.upsert_app(SteamApp(appid=440, name="TF2"))
        assert db.exists()
        with Store(db) as s2:  # reopen: data survived, migrations not re-run destructively
            assert s2.get_app(440) is not None


class TestCatalogUpsert:
    def test_upsert_app_is_current_state(self, store: Store) -> None:
        store.upsert_app(SteamApp(appid=440, name="Team Fortress 2"))
        store.upsert_app(SteamApp(appid=440, name="TF2 (renamed)"))
        assert store.get_app(440).name == "TF2 (renamed)"
        assert len(store.list_apps()) == 1  # no duplicate logical row

    def test_upsert_badge_set_and_card(self, store: Store) -> None:
        store.upsert_badge_set(BadgeSet(appid=440, set_size=8))
        store.upsert_card(Card(appid=440, market_hash_name="440-Heavy", card_name="Heavy"))
        store.upsert_card(Card(appid=440, market_hash_name="440-Heavy", card_name="Heavy v2"))
        row = store.conn.execute("SELECT COUNT(*) FROM card").fetchone()[0]
        assert row == 1


class TestUserState:
    def test_inventory_and_badge_progress_upsert(self, store: Store) -> None:
        store.upsert_inventory(UserCardInventory(appid=1, market_hash_name="c", quantity=2))
        store.upsert_inventory(UserCardInventory(appid=1, market_hash_name="c", quantity=5))
        q = store.conn.execute("SELECT quantity FROM user_card_inventory").fetchone()[0]
        assert q == 5
        store.upsert_badge_progress(UserBadgeProgress(appid=1, level=2))
        store.upsert_badge_progress(UserBadgeProgress(appid=1, level=4))
        lvl = store.conn.execute("SELECT level FROM user_badge_progress").fetchone()[0]
        assert lvl == 4


class TestPriceHistoryAndProvenance:
    def _snap(self, low: int, payload: bytes, when: datetime) -> PriceSnapshot:
        return PriceSnapshot(
            item=MarketItem(appid=440, market_hash_name="440-Heavy"),
            lowest=Money(low, "USD"),
            median=Money(low + 2, "USD"),
            volume=100,
            source=_source(payload, when=when),
        )

    def test_history_is_append_only_and_ordered(self, store: Store) -> None:
        t0 = datetime(2026, 7, 1, tzinfo=UTC)
        assert store.add_price_snapshot(self._snap(3, b"a", t0)) is True
        assert store.add_price_snapshot(self._snap(4, b"b", t0 + timedelta(days=1))) is True
        hist = store.price_history(440, "440-Heavy")
        assert [h.lowest.cents for h in hist] == [3, 4]
        assert store.latest_price(440, "440-Heavy").lowest.cents == 4

    def test_same_fetch_dedups(self, store: Store) -> None:
        t0 = datetime(2026, 7, 1, tzinfo=UTC)
        assert store.add_price_snapshot(self._snap(3, b"same", t0)) is True
        # Re-importing the identical fetch (same item, same fetch time) is a no-op.
        assert store.add_price_snapshot(self._snap(3, b"same", t0)) is False
        assert len(store.price_history(440, "440-Heavy")) == 1
        assert store.source_count() == 1

    def test_unchanged_price_at_later_time_is_new_point(self, store: Store) -> None:
        # Byte-identical payloads at DIFFERENT fetch times are distinct observations
        # (the MEDIUM finding): price history must not collapse them.
        t0 = datetime(2026, 7, 1, tzinfo=UTC)
        assert store.add_price_snapshot(self._snap(3, b"same", t0)) is True
        assert store.add_price_snapshot(self._snap(3, b"same", t0 + timedelta(days=1))) is True
        assert len(store.price_history(440, "440-Heavy")) == 2

    def test_currency_mismatch_rejected(self) -> None:
        # Enforced at the model layer, so a lossy single-currency row can't be built.
        with pytest.raises(ValueError):
            PriceSnapshot(
                item=MarketItem(appid=1, market_hash_name="c"),
                lowest=Money(3, "USD"),
                median=Money(5, "EUR"),
                source=_source(b"x"),
            )

    def test_foreign_key_enforced(self, store: Store) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            store.conn.execute(
                "INSERT INTO price_snapshot "
                "(appid, market_hash_name, currency, fetched_at, source_id) "
                "VALUES (1, 'c', 'USD', '2026-07-01T00:00:00+00:00', 99999)"
            )

    def test_provenance_round_trips(self, store: Store) -> None:
        store.add_price_snapshot(self._snap(3, b"a", datetime(2026, 7, 1, tzinfo=UTC)))
        snap = store.latest_price(440, "440-Heavy")
        assert snap.source.raw_sha256 == SourceRecord.sha256_of(b"a")
        assert snap.source.kind == SourceKind.STEAM_MARKET

    def test_snapshot_without_price_stored(self, store: Store) -> None:
        snap = PriceSnapshot(item=MarketItem(appid=1, market_hash_name="x"), source=_source(b"np"))
        assert store.add_price_snapshot(snap) is True
        assert store.latest_price(1, "x").has_price is False
