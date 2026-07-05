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
        # No median cached, so extra copies use the +15% inflation (ceil):
        #   A: 10 + 3*ceil(11.5=12) = 46; B: 20 + 4*23 = 112; C: 30 + 4*ceil(34.5=35) = 170.
        assert badge.complete is True
        assert badge.crafts_needed == 5
        assert badge.known_cost == Money(46 + 112 + 170, "USD")  # 328
        assert badge.estimated_cost == Money(328, "USD")
        assert badge.expected_xp == 500
        assert badge.confidence is Confidence.LOW  # level was assumed


def test_multi_unit_estimate_modeled_and_over_floor() -> None:
    # Buying several copies walks the order book: the estimate exceeds missing*lowest,
    # is labeled modeled (not measured), and can't claim HIGH confidence.
    with Store.in_memory() as store:
        _seed_full_badge(store)  # missing 4-5 copies per card
        badge = compute_costs(store, target_level=5).badges[0]
        naive_floor = 4 * 10 + 5 * 20 + 5 * 30  # 290
        assert badge.known_cost.cents > naive_floor  # no longer under-budgets
        assert any("modeled" in n for n in badge.notes)
        assert badge.confidence is not Confidence.HIGH


class TestMultiUnitModel:
    def test_never_undershoots_naive_floor(self) -> None:
        from steam_badge_optimizer.optimize.cost import _multi_unit_line_cents

        for base in (3, 10, 137):
            for median in (None, base, base * 2, base * 10):
                for qty in range(1, 8):
                    cost = _multi_unit_line_cents(base, median, qty)
                    assert cost >= base * qty  # never below k*lowest

    def test_monotonic_non_decreasing_in_quantity(self) -> None:
        from steam_badge_optimizer.optimize.cost import _multi_unit_line_cents

        prev = -1
        for qty in range(0, 10):
            cost = _multi_unit_line_cents(50, 80, qty)
            assert cost >= prev
            prev = cost

    def test_median_is_capped_to_bound_overestimate(self) -> None:
        from steam_badge_optimizer.optimize.cost import _multi_unit_line_cents

        # A spiky median (10x lowest) is capped at 2x lowest for the extra copies.
        # 2 copies: 100 + min(1000, 200) = 300, not 100 + 1000.
        assert _multi_unit_line_cents(100, 1000, 2) == 300

    def test_inflation_used_when_no_median(self) -> None:
        from steam_badge_optimizer.optimize.cost import _multi_unit_line_cents

        # 2 copies, base 100, no median: 100 + ceil(115) = 215.
        assert _multi_unit_line_cents(100, None, 2) == 215


def test_candidate_totals_reconcile_with_badge_cost() -> None:
    # Per-card modeled totals must sum to the badge known_cost (no naive-floor leak into
    # the report line items).
    with Store.in_memory() as store:
        _seed_full_badge(store)
        badge = compute_costs(store, target_level=5).badges[0]
        line_sum = sum(c.estimated_total.cents for c in badge.candidates)
        assert line_sum == badge.known_cost.cents
        # And each line total exceeds the naive unit*qty floor for multi-copy lines.
        for c in badge.candidates:
            if c.missing_quantity > 1:
                assert c.estimated_total.cents > c.estimated_unit_price.cents * c.missing_quantity


def test_median_proxy_used_in_full_path() -> None:
    with Store.in_memory() as store:
        store.upsert_badge_set(BadgeSet(appid=1, set_size=1))
        store.upsert_card(Card(appid=1, market_hash_name="1-A"))
        store.add_price_snapshot(
            PriceSnapshot(
                item=MarketItem(appid=1, market_hash_name="1-A"),
                lowest=Money(100, "USD"),
                median=Money(150, "USD"),
                volume=200,
                source=SourceRecord(
                    kind=SourceKind.STEAM_MARKET,
                    url="https://steamcommunity.com/market/priceoverview/",
                    fetched_at=datetime.now(UTC),
                    parser_version="1",
                    raw_sha256=SourceRecord.sha256_of(b"m"),
                    cache_ttl_seconds=86400,
                ),
            )
        )
        # missing 5: 100 + 4*min(150, 200) = 100 + 600 = 700.
        assert compute_costs(store, target_level=5).badges[0].known_cost == Money(700, "USD")


def test_crafts_needed_uses_current_level() -> None:
    with Store.in_memory() as store:
        _seed_full_badge(store)
        store.upsert_badge_progress(UserBadgeProgress(appid=440, level=3))
        badge = compute_costs(store, target_level=5).badges[0]
        # crafts_needed = 2. missing A=1 (10), B=2 (20 + 23 = 43), C=2 (30 + 35 = 65).
        assert badge.crafts_needed == 2
        assert badge.known_cost == Money(10 + 43 + 65, "USD")  # 118
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


def test_foreign_currency_price_forces_incomplete() -> None:
    # A EUR-priced card must NOT be summed into a USD total (fail closed).
    with Store.in_memory() as store:
        store.upsert_badge_set(BadgeSet(appid=440, set_size=1))
        store.upsert_card(Card(appid=440, market_hash_name="440-A"))
        store.add_price_snapshot(
            PriceSnapshot(
                item=MarketItem(appid=440, market_hash_name="440-A"),
                lowest=Money(500, "EUR"),
                volume=100,
                source=SourceRecord(
                    kind=SourceKind.STEAM_MARKET,
                    url="https://steamcommunity.com/market/priceoverview/",
                    fetched_at=datetime.now(UTC),
                    parser_version="1",
                    raw_sha256=SourceRecord.sha256_of(b"eur"),
                    cache_ttl_seconds=86400,
                ),
            )
        )
        badge = compute_costs(store, currency="USD").badges[0]
        assert badge.complete is False
        assert badge.estimated_cost is None
        assert any("EUR" in n for n in badge.notes)


def test_unmarketable_needed_card_incomplete() -> None:
    with Store.in_memory() as store:
        store.upsert_badge_set(BadgeSet(appid=440, set_size=1))
        store.upsert_card(Card(appid=440, market_hash_name="440-A", marketable=False))
        _price(store, 440, "440-A", 10)
        badge = compute_costs(store).badges[0]
        assert badge.complete is False
        assert any("unmarketable" in n for n in badge.notes)


def test_low_volume_caps_confidence_to_medium() -> None:
    with Store.in_memory() as store:
        _seed_full_badge(store)
        store.upsert_badge_progress(UserBadgeProgress(appid=440, level=4))
        # Re-price one needed card with volume 0 -> low volume.
        _price(store, 440, "440-B", 20, volume=0)
        badge = compute_costs(store, target_level=5, min_volume=5).badges[0]
        assert badge.complete is True
        assert badge.confidence is Confidence.MEDIUM


def test_owns_enough_is_complete_with_zero_cost() -> None:
    with Store.in_memory() as store:
        store.upsert_badge_set(BadgeSet(appid=440, set_size=1))
        store.upsert_card(Card(appid=440, market_hash_name="440-A"))
        _price(store, 440, "440-A", 10)
        store.upsert_badge_progress(UserBadgeProgress(appid=440, level=4))  # 1 craft left
        store.upsert_inventory(UserCardInventory(appid=440, market_hash_name="440-A", quantity=3))
        report = compute_costs(store, target_level=5)
        badge = report.badges[0]
        assert badge.complete is True
        assert badge.known_cost == Money(0, "USD")
        assert badge in report.complete_badges()


def test_known_cards_exceeding_set_size_incomplete() -> None:
    with Store.in_memory() as store:
        store.upsert_badge_set(BadgeSet(appid=440, set_size=1))
        store.upsert_card(Card(appid=440, market_hash_name="440-A"))
        store.upsert_card(Card(appid=440, market_hash_name="440-B"))  # 2 known > set_size 1
        _price(store, 440, "440-A", 10)
        _price(store, 440, "440-B", 20)
        badge = compute_costs(store).badges[0]
        assert badge.complete is False
        assert any("data mismatch" in n for n in badge.notes)


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
