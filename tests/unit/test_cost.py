"""Tests for the cost-to-complete calculator."""

from __future__ import annotations

from datetime import UTC, datetime

from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import (
    BadgeSet,
    Card,
    Confidence,
    MarketItem,
    Money,
    PriceSnapshot,
    SourceKind,
    SourceRecord,
    UserBadgeProgress,
    UserCardInventory,
)
from steam_badge_optimizer.optimize import compute_costs


def _price(store: Store, appid: int, name: str, cents: int, *, volume: int = 100) -> None:
    store.add_price_snapshot(
        PriceSnapshot(
            item=MarketItem(appid=appid, market_hash_name=name),
            lowest=Money(cents, "USD"),
            volume=volume,
            source=SourceRecord(
                kind=SourceKind.STEAM_MARKET,
                url="https://steamcommunity.com/market/priceoverview/",
                fetched_at=datetime.now(UTC),
                parser_version="1",
                raw_sha256=SourceRecord.sha256_of(name.encode()),
                cache_ttl_seconds=86400,
            ),
        )
    )


def _seed_full_badge(store: Store, appid: int = 440) -> None:
    """A 3-card badge (A/B/C) all known and priced 10/20/30c; owns 1x A."""
    store.upsert_badge_set(BadgeSet(appid=appid, set_size=3))
    for name in ("440-A", "440-B", "440-C"):
        store.upsert_card(Card(appid=appid, market_hash_name=name))
    _price(store, appid, "440-A", 10)
    _price(store, appid, "440-B", 20)
    _price(store, appid, "440-C", 30)
    store.upsert_inventory(UserCardInventory(appid=appid, market_hash_name="440-A", quantity=1))


def test_complete_badge_cost_and_math() -> None:
    with Store.in_memory() as store:
        _seed_full_badge(store)
        report = compute_costs(store, target_level=5)
        badge = report.badges[0]
        # crafts_needed = 5 (no progress => level 0). missing A=4,B=5,C=5.
        # cost = 4*10 + 5*20 + 5*30 = 290c.
        assert badge.complete is True
        assert badge.crafts_needed == 5
        assert badge.known_cost == Money(290, "USD")
        assert badge.estimated_cost == Money(290, "USD")
        assert badge.expected_xp == 500
        assert badge.confidence is Confidence.LOW  # level was assumed


def test_crafts_needed_uses_current_level() -> None:
    with Store.in_memory() as store:
        _seed_full_badge(store)
        store.upsert_badge_progress(UserBadgeProgress(appid=440, level=3))
        badge = compute_costs(store, target_level=5).badges[0]
        # crafts_needed = 2. missing A=max(0,2-1)=1, B=2, C=2 => 10 + 40 + 60 = 110.
        assert badge.crafts_needed == 2
        assert badge.known_cost == Money(110, "USD")
        assert badge.confidence is not Confidence.LOW  # level known, prices fresh+volume


def test_incomplete_when_card_names_unknown() -> None:
    with Store.in_memory() as store:
        store.upsert_badge_set(BadgeSet(appid=440, set_size=5))  # 5 cards in set...
        store.upsert_card(Card(appid=440, market_hash_name="440-A"))  # ...but only 1 known
        _price(store, 440, "440-A", 10)
        badge = compute_costs(store).badges[0]
        assert badge.complete is False
        assert badge.estimated_cost is None  # never fabricate a cost
        assert badge.known_cards == 1
        assert any("unknown" in n for n in badge.notes)


def test_unpriced_card_makes_badge_incomplete_not_free() -> None:
    with Store.in_memory() as store:
        store.upsert_badge_set(BadgeSet(appid=440, set_size=2))
        store.upsert_card(Card(appid=440, market_hash_name="440-A"))
        store.upsert_card(Card(appid=440, market_hash_name="440-B"))
        _price(store, 440, "440-A", 10)  # B has no price
        badge = compute_costs(store).badges[0]
        assert badge.complete is False
        assert badge.estimated_cost is None
        assert any("no cached price" in n for n in badge.notes)


def test_ready_to_craft_when_owns_full_set() -> None:
    with Store.in_memory() as store:
        store.upsert_badge_set(BadgeSet(appid=440, set_size=2))
        for name in ("440-A", "440-B"):
            store.upsert_card(Card(appid=440, market_hash_name=name))
            _price(store, 440, name, 10)
            store.upsert_inventory(UserCardInventory(appid=440, market_hash_name=name, quantity=1))
        report = compute_costs(store)
        assert report.ready_to_craft()
        assert report.badges[0].ready_to_craft is True


def test_maxed_badge_excluded_from_complete() -> None:
    with Store.in_memory() as store:
        _seed_full_badge(store)
        store.upsert_badge_progress(UserBadgeProgress(appid=440, level=5))
        report = compute_costs(store, target_level=5)
        assert report.badges[0].crafts_needed == 0
        assert report.complete_badges() == []  # nothing to do


def test_foil_cards_excluded_by_default() -> None:
    with Store.in_memory() as store:
        store.upsert_badge_set(BadgeSet(appid=440, set_size=1))
        store.upsert_card(Card(appid=440, market_hash_name="440-A"))
        store.upsert_card(Card(appid=440, market_hash_name="440-A-foil", is_foil=True))
        _price(store, 440, "440-A", 10)
        badge = compute_costs(store).badges[0]
        # Only the 1 non-foil card counts toward the set.
        assert badge.known_cards == 1
        assert badge.complete is True
