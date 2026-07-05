"""Tests for historical price-anomaly detection (research only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from steam_badge_optimizer.analytics import AnomalyKind, detect_anomalies
from steam_badge_optimizer.analytics.anomalies import MIN_SNAPSHOTS
from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import (
    MarketItem,
    Money,
    PriceSnapshot,
    SourceKind,
    SourceRecord,
)

BASE = datetime(2026, 7, 1, tzinfo=UTC)


def _hist(
    store: Store,
    name: str,
    lows: list[int],
    *,
    median: int | None = None,
    currency: str = "USD",
    volume: int = 100,
) -> None:
    for i, low in enumerate(lows):
        store.add_price_snapshot(
            PriceSnapshot(
                item=MarketItem(appid=1, market_hash_name=name),
                lowest=Money(low, currency),
                median=Money(median, currency) if median is not None else None,
                volume=volume,
                source=SourceRecord(
                    kind=SourceKind.STEAM_MARKET,
                    url="https://steamcommunity.com/market/priceoverview/",
                    fetched_at=BASE + timedelta(days=i),
                    parser_version="1",
                    raw_sha256=SourceRecord.sha256_of(f"{name}{i}{low}".encode()),
                    cache_ttl_seconds=86400,
                ),
            )
        )


class TestFailClosed:
    def test_insufficient_history_no_anomaly(self) -> None:
        with Store.in_memory() as store:
            _hist(store, "1-A", [100, 50])  # only 2 points < MIN
            assert detect_anomalies(store) == []

    def test_stable_prices_no_anomaly(self) -> None:
        with Store.in_memory() as store:
            _hist(store, "1-A", [100] * (MIN_SNAPSHOTS + 2))
            assert detect_anomalies(store) == []

    def test_mild_noise_is_not_an_anomaly(self) -> None:
        # A dead-stable dollar card with a few cents of jitter must NOT flag (the
        # z-score detector that fired ~7% on such series was removed).
        with Store.in_memory() as store:
            _hist(store, "1-A", [100, 101, 99, 100, 98, 102, 99])
            assert detect_anomalies(store) == []

    def test_penny_card_jitter_not_a_drop(self) -> None:
        # 1-cent integer jitter on a ~3c card is a 30%+ move but is pure noise.
        with Store.in_memory() as store:
            _hist(store, "1-A", [4, 3, 4, 3, 2, 3])
            assert detect_anomalies(store) == []  # below MIN_MEANINGFUL_CENTS

    def test_top_must_be_positive(self) -> None:
        with Store.in_memory() as store, pytest.raises(ValueError):
            detect_anomalies(store, top=0)


class TestDetectors:
    def test_sudden_drop(self) -> None:
        with Store.in_memory() as store:
            _hist(store, "1-A", [100, 100, 100, 100, 20])  # latest 20 << mean ~100
            kinds = {a.kind for a in detect_anomalies(store)}
            assert AnomalyKind.SUDDEN_DROP in kinds

    def test_stale_median_vs_live_lowest(self) -> None:
        with Store.in_memory() as store:
            # Stable lowest but a median far above it -> stale/lagging median.
            _hist(store, "1-A", [40] * (MIN_SNAPSHOTS + 1), median=100)
            anoms = detect_anomalies(store)
            assert any(a.kind is AnomalyKind.STALE_MEDIAN for a in anoms)

    def test_anomalies_are_never_high_confidence(self) -> None:
        from steam_badge_optimizer.models import Confidence

        with Store.in_memory() as store:
            _hist(store, "1-A", [100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 20])
            for a in detect_anomalies(store):
                assert a.confidence is not Confidence.HIGH

    def test_currency_consistent_history_only(self) -> None:
        with Store.in_memory() as store:
            # EUR history (would look like a drop) must not be scanned under USD.
            _hist(store, "1-A", [100, 100, 100, 100, 20], currency="EUR")
            assert detect_anomalies(store, currency="USD") == []
