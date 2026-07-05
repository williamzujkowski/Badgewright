"""Tests for top-K priceoverview enrichment of cheapest-badges (#74 reshaped)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import orjson
import respx
from typer.testing import CliRunner

from steam_badge_optimizer.cli import app
from steam_badge_optimizer.config import Settings
from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import (
    BadgeSet,
    Card,
    MarketItem,
    Money,
    PriceSnapshot,
    SourceKind,
    SourceRecord,
    SteamApp,
)

runner = CliRunner()
PRICEOVERVIEW = "https://steamcommunity.com/market/priceoverview/"


def _sweep_price(store: Store, appid: int, name: str, cents: int, listings: int) -> None:
    # Simulates sweep-sourced data: has `listings` but NO volume (search/render).
    store.add_price_snapshot(
        PriceSnapshot(
            item=MarketItem(appid=appid, market_hash_name=name),
            lowest=Money(cents, "USD"),
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


def _seed(tmp_path) -> Settings:
    s = Settings.resolve(data_dir=str(tmp_path))
    s.data_dir.mkdir(parents=True, exist_ok=True)
    with Store(s.db_path) as store:
        store.upsert_app(SteamApp(appid=100, name="Looks Cheap"))
        store.upsert_badge_set(BadgeSet(appid=100, set_size=2))
        # Two cheap cards that LOOK liquid via search listings...
        for n in ("100-A", "100-B"):
            store.upsert_card(Card(appid=100, market_hash_name=n))
            _sweep_price(store, 100, n, 3, listings=99)
    return s


def _overview(volume: str) -> httpx.Response:
    return httpx.Response(
        200,
        content=orjson.dumps(
            {"success": True, "lowest_price": "$0.03", "median_price": "$0.03", "volume": volume}
        ),
    )


class TestEnrichmentOptIn:
    def test_enrich_requires_online_and_confirm(self, tmp_path) -> None:
        _seed(tmp_path)
        for args in (
            ["market", "cheapest-badges", "--enrich-top", "1", "--data-dir", str(tmp_path)],
            [
                "market",
                "cheapest-badges",
                "--enrich-top",
                "1",
                "--online",
                "--data-dir",
                str(tmp_path),
            ],
        ):
            result = runner.invoke(app, args)
            assert result.exit_code == 2, args

    def test_negative_enrich_rejected(self, tmp_path) -> None:
        _seed(tmp_path)
        result = runner.invoke(
            app, ["market", "cheapest-badges", "--enrich-top", "-1", "--data-dir", str(tmp_path)]
        )
        assert result.exit_code == 2


class TestEnrichmentDemotesIlliquid:
    @respx.mock
    def test_zero_volume_demotes_a_seemingly_cheap_badge(self, tmp_path) -> None:
        # Cards look liquid via 99 search listings, but real 24h volume is 0 -> after
        # enrichment the badge must be flagged thin (not liquid).
        respx.get(PRICEOVERVIEW).mock(return_value=_overview(volume="0"))
        s = _seed(tmp_path)
        result = runner.invoke(
            app,
            [
                "market",
                "cheapest-badges",
                "--enrich-top",
                "1",
                "--online",
                "--confirm",
                "--data-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        assert "thin" in result.output.lower()  # real volume revealed the illiquidity
        with Store(s.db_path) as store:
            snap = store.latest_price(100, "100-A")
            assert snap.volume == 0  # enriched with real 24h volume
