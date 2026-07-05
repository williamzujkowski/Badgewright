"""Tests for market-intelligence scoring (research only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from steam_badge_optimizer.analytics import scan_sets, scan_weakness
from steam_badge_optimizer.analytics.market_scan import (
    MIN_SNAPSHOTS_FOR_VOLATILITY,
    _gap,
    _volatility,
)
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
)


def _snap(
    store: Store,
    appid: int,
    name: str,
    *,
    low: int,
    median: int | None = None,
    volume: int = 100,
    when: datetime | None = None,
    currency: str = "USD",
    ttl: int = 86400,
) -> None:
    store.add_price_snapshot(
        PriceSnapshot(
            item=MarketItem(appid=appid, market_hash_name=name),
            lowest=Money(low, currency),
            median=Money(median, currency) if median is not None else None,
            volume=volume,
            source=SourceRecord(
                kind=SourceKind.STEAM_MARKET,
                url="https://steamcommunity.com/market/priceoverview/",
                fetched_at=when or datetime.now(UTC),
                parser_version="1",
                raw_sha256=SourceRecord.sha256_of(f"{name}{when}{low}".encode()),
                cache_ttl_seconds=ttl,
            ),
        )
    )


class TestPureMetrics:
    def test_gap(self) -> None:
        assert _gap(Money(3, "USD"), Money(5, "USD")) == (5 - 3) / 5
        assert _gap(None, Money(5, "USD")) is None
        assert _gap(Money(3, "USD"), Money(0, "USD")) is None  # no div-by-zero
        assert _gap(Money(6, "USD"), Money(5, "USD")) == 0.0  # clamped, not negative

    def test_volatility_needs_min_history(self) -> None:
        assert _volatility([100, 110, 90]) is None  # < MIN
        cv = _volatility([100] * MIN_SNAPSHOTS_FOR_VOLATILITY)
        assert cv == 0.0  # flat prices => zero volatility
        assert _volatility([100, 200, 100, 200, 100]) is not None


class TestScanWeakness:
    def test_low_volume_never_tops_ranking(self) -> None:
        with Store.in_memory() as store:
            # A juicy gap but near-zero volume must NOT outrank a modest, liquid gap.
            _snap(store, 1, "1-Thin", low=10, median=100, volume=1)  # 90% gap, vol 1
            _snap(store, 2, "2-Liquid", low=80, median=100, volume=500)  # 20% gap, liquid
            rows = scan_weakness(store, min_volume=5, top=10)
            assert rows[0].market_hash_name == "2-Liquid"  # liquidity wins
            thin = next(r for r in rows if r.market_hash_name == "1-Thin")
            assert thin.confidence is Confidence.LOW
            assert any("low volume" in s for s in thin.signals)

    def test_currency_mismatch_skipped(self) -> None:
        with Store.in_memory() as store:
            _snap(store, 1, "1-Eur", low=10, median=100, volume=500, currency="EUR")
            rows = scan_weakness(store, currency="USD", top=10)
            assert rows == []

    def test_insufficient_history_reported(self) -> None:
        with Store.in_memory() as store:
            _snap(store, 1, "1-A", low=50, median=60, volume=100)
            row = scan_weakness(store, top=10)[0]
            assert row.volatility is None
            assert any("insufficient history" in s for s in row.signals)

    def test_volatility_computed_with_enough_history(self) -> None:
        with Store.in_memory() as store:
            base = datetime(2026, 7, 1, tzinfo=UTC)
            for i in range(MIN_SNAPSHOTS_FOR_VOLATILITY):
                _snap(
                    store,
                    1,
                    "1-A",
                    low=50 + i * 5,
                    median=60,
                    volume=100,
                    when=base + timedelta(days=i),
                )
            row = scan_weakness(store, top=10)[0]
            assert row.volatility is not None

    def test_top_must_be_positive(self) -> None:
        with Store.in_memory() as store:
            try:
                scan_weakness(store, top=0)
            except ValueError:
                return
            raise AssertionError("expected ValueError")


class TestScanSets:
    def test_complete_set_cost_and_dominance(self) -> None:
        with Store.in_memory() as store:
            store.upsert_badge_set(BadgeSet(appid=1, set_size=2))
            for name, cost in [("1-A", 10), ("1-B", 90)]:  # B dominates (90% of 100)
                store.upsert_card(Card(appid=1, market_hash_name=name))
                _snap(store, 1, name, low=cost, volume=100)
            sig = next(s for s in scan_sets(store) if s.appid == 1)
            assert sig.complete is True
            assert sig.total_cost == Money(100, "USD")
            assert abs((sig.card_dominance or 0) - 0.9) < 1e-9
            assert any("bottleneck" in s for s in sig.signals)

    def test_incomplete_set_flagged(self) -> None:
        with Store.in_memory() as store:
            store.upsert_badge_set(BadgeSet(appid=1, set_size=3))
            store.upsert_card(Card(appid=1, market_hash_name="1-A"))
            _snap(store, 1, "1-A", low=10, volume=100)
            sig = next(s for s in scan_sets(store) if s.appid == 1)
            assert sig.complete is False
            assert sig.total_cost is None
