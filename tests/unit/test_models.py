"""Tests for the core domain models (validation + derived behavior)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from steam_badge_optimizer.models import (
    BadgeSet,
    Card,
    Confidence,
    MarketItem,
    Money,
    PriceSnapshot,
    PurchaseCandidate,
    SourceKind,
    SourceRecord,
    SteamApp,
    UserBadgeProgress,
    UserCardInventory,
)


def _source() -> SourceRecord:
    return SourceRecord(
        kind=SourceKind.STEAM_MARKET,
        url="https://steamcommunity.com/market/priceoverview/",
        fetched_at=datetime(2026, 7, 5, tzinfo=UTC),
        parser_version="1.0",
        raw_sha256=SourceRecord.sha256_of(b"{}"),
        cache_ttl_seconds=86400,
    )


class TestSteamApp:
    def test_valid(self) -> None:
        app = SteamApp(appid=440, name="Team Fortress 2")
        assert "440" in app.market_url()

    @pytest.mark.parametrize("appid", [0, -1])
    def test_bad_appid(self, appid: int) -> None:
        with pytest.raises(ValueError):
            SteamApp(appid=appid, name="x")

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError):
            SteamApp(appid=1, name="")


class TestBadge:
    def test_set_size_bounds(self) -> None:
        with pytest.raises(ValueError):
            BadgeSet(appid=1, set_size=0)
        assert BadgeSet(appid=1, set_size=8).xp_to_max_normal(100) == 500

    def test_card_defaults(self) -> None:
        c = Card(appid=1, market_hash_name="1-Foo")
        assert c.marketable and c.tradable and not c.is_foil


class TestUserBadgeProgress:
    @pytest.mark.parametrize(
        ("level", "foil", "ok"),
        [(0, False, True), (5, False, True), (6, False, False), (1, True, True), (2, True, False)],
    )
    def test_level_caps(self, level: int, foil: bool, ok: bool) -> None:
        if ok:
            UserBadgeProgress(appid=1, level=level, is_foil=foil)
        else:
            with pytest.raises(ValueError):
                UserBadgeProgress(appid=1, level=level, is_foil=foil)

    def test_remaining_and_maxed(self) -> None:
        p = UserBadgeProgress(appid=1, level=3)
        assert p.remaining_normal_levels() == 2
        assert not p.is_maxed
        assert UserBadgeProgress(appid=1, level=5).is_maxed
        assert UserBadgeProgress(appid=1, level=1, is_foil=True).remaining_normal_levels() == 0


class TestInventory:
    def test_quantity_non_negative(self) -> None:
        assert UserCardInventory(appid=1, market_hash_name="c", quantity=0).quantity == 0
        with pytest.raises(ValueError):
            UserCardInventory(appid=1, market_hash_name="c", quantity=-1)


class TestMarket:
    def test_listings_url_is_encoded(self) -> None:
        item = MarketItem(appid=753, market_hash_name="440-Mann Co. Crate")
        url = item.listings_url()
        assert "753" in url and "%20" in url  # space encoded

    def test_snapshot_price_and_staleness(self) -> None:
        item = MarketItem(appid=1, market_hash_name="c")
        snap = PriceSnapshot(
            item=item, lowest=Money(3, "USD"), median=Money(5, "USD"), volume=120, source=_source()
        )
        assert snap.has_price
        assert snap.is_stale(now=datetime(2026, 7, 7, tzinfo=UTC)) is True

    def test_snapshot_without_price(self) -> None:
        snap = PriceSnapshot(item=MarketItem(appid=1, market_hash_name="c"), source=_source())
        assert snap.has_price is False


class TestPurchaseCandidate:
    def test_estimated_total(self) -> None:
        cand = PurchaseCandidate(
            appid=1,
            market_hash_name="c",
            missing_quantity=5,
            estimated_unit_price=Money(3, "USD"),
            confidence=Confidence.HIGH,
        )
        assert cand.estimated_total == Money(15, "USD")

    def test_unpriced_total_is_none(self) -> None:
        cand = PurchaseCandidate(appid=1, market_hash_name="c", missing_quantity=2)
        assert cand.estimated_total is None

    def test_missing_quantity_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            PurchaseCandidate(appid=1, market_hash_name="c", missing_quantity=0)
