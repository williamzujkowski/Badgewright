"""Tests for Tier-2 cheapest-badge aggregation + the supporting data plumbing."""

from __future__ import annotations

from datetime import UTC, datetime

import orjson
import pytest

from steam_badge_optimizer.analytics import rank_cheapest_badges
from steam_badge_optimizer.db import Store
from steam_badge_optimizer.db.schema import MIGRATIONS, schema_version
from steam_badge_optimizer.models import (
    BadgeSet,
    Card,
    Confidence,
    MarketItem,
    Money,
    PriceSnapshot,
    SourceKind,
    SourceRecord,
)
from steam_badge_optimizer.sources.card_discovery import parse_search_results


def _price(
    store: Store,
    appid: int,
    name: str,
    cents: int,
    *,
    listings: int | None = 100,
    currency: str = "USD",
) -> None:
    store.add_price_snapshot(
        PriceSnapshot(
            item=MarketItem(appid=appid, market_hash_name=name),
            lowest=Money(cents, currency),
            listings=listings,
            source=SourceRecord(
                kind=SourceKind.STEAM_MARKET_SEARCH,
                url="https://steamcommunity.com/market/search/render/",
                fetched_at=datetime.now(UTC),
                parser_version="1",
                raw_sha256=SourceRecord.sha256_of(f"{name}{cents}".encode()),
                cache_ttl_seconds=86400,
            ),
        )
    )


def _seed_set(store: Store, appid: int, prices: list[int], *, listings: int = 100) -> None:
    store.upsert_badge_set(BadgeSet(appid=appid, set_size=len(prices)))
    for i, cents in enumerate(prices):
        name = f"{appid}-C{i}"
        store.upsert_card(Card(appid=appid, market_hash_name=name))
        _price(store, appid, name, cents, listings=listings)


class TestPlumbing:
    def test_migration_v2_present(self) -> None:
        with Store.in_memory() as store:
            assert schema_version(store.conn) == len(MIGRATIONS)  # includes the listings migration

    def test_listings_round_trips(self) -> None:
        with Store.in_memory() as store:
            _price(store, 1, "1-A", 50, listings=7)
            snap = store.latest_price(1, "1-A")
            assert snap.listings == 7

    def test_parser_captures_price_and_listings(self) -> None:
        raw = orjson.dumps(
            {
                "results": [
                    {
                        "hash_name": "440-Heavy",
                        "sell_price": 5,
                        "sell_listings": 688,
                        "asset_description": {"type": "Trading Card"},
                    }
                ]
            }
        )
        card = parse_search_results(raw)[0]
        assert card.sell_price_cents == 5
        assert card.listings == 688


class TestRankCheapestBadges:
    def test_ranks_cheapest_first(self) -> None:
        with Store.in_memory() as store:
            _seed_set(store, 100, [10, 10])  # set cost 20
            _seed_set(store, 200, [50, 5])  # set cost 55
            _seed_set(store, 300, [2, 3])  # set cost 5 (cheapest)
            ranked = rank_cheapest_badges(store)
            assert [b.appid for b in ranked] == [300, 100, 200]
            assert ranked[0].total_cost == Money(5, "USD")
            assert ranked[0].cost_per_xp_cents == 5 / 100

    def test_thin_liquidity_never_ranks_top(self) -> None:
        with Store.in_memory() as store:
            # Cheapest by cost, but one card has a single listing -> not buyable.
            _seed_set(store, 100, [1, 1], listings=1)  # cost 2 but thin
            _seed_set(store, 200, [9, 9], listings=500)  # cost 18 but liquid
            ranked = rank_cheapest_badges(store, min_listings=2)
            assert ranked[0].appid == 200  # liquid wins despite higher cost
            thin = next(b for b in ranked if b.appid == 100)
            assert thin.liquid is False
            assert thin.confidence is Confidence.LOW
            assert any("thin" in s for s in thin.signals)

    def test_incomplete_set_excluded(self) -> None:
        with Store.in_memory() as store:
            store.upsert_badge_set(BadgeSet(appid=1, set_size=3))  # says 3 cards...
            store.upsert_card(Card(appid=1, market_hash_name="1-A"))  # ...only 1 known
            _price(store, 1, "1-A", 10)
            assert rank_cheapest_badges(store) == []

    def test_unpriced_card_excludes_set(self) -> None:
        with Store.in_memory() as store:
            store.upsert_badge_set(BadgeSet(appid=1, set_size=2))
            for n in ("1-A", "1-B"):
                store.upsert_card(Card(appid=1, market_hash_name=n))
            _price(store, 1, "1-A", 10)  # 1-B has no price
            assert rank_cheapest_badges(store) == []

    def test_bottleneck_flagged(self) -> None:
        with Store.in_memory() as store:
            _seed_set(store, 1, [5, 95])  # one card is 95% of cost
            badge = rank_cheapest_badges(store)[0]
            assert badge.bottleneck_fraction is not None
            assert any("bottleneck" in s for s in badge.signals)

    def test_top_must_be_positive(self) -> None:
        with Store.in_memory() as store, pytest.raises(ValueError):
            rank_cheapest_badges(store, top=0)
