"""Tests for booster-pack-vs-contents arbitrage (#98): source fetch + EV scan."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import orjson
import pytest
import respx

from steam_badge_optimizer.analytics import evaluate_booster, scan_booster_arbitrage
from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import (
    Card,
    MarketItem,
    Money,
    PriceSnapshot,
    SourceKind,
    SourceRecord,
)
from steam_badge_optimizer.sources.booster_market import BoosterQuote, fetch_booster_price
from steam_badge_optimizer.sources.http_client import SafeClient

SEARCH = "https://steamcommunity.com/market/search/render/"


def _entry(hash_name: str, price: int | None, listings: int, type_: str) -> dict:
    e: dict = {
        "hash_name": hash_name,
        "sell_listings": listings,
        "asset_description": {"type": type_},
    }
    if price is not None:
        e["sell_price"] = price
    return e


def _results(entries: list[dict]) -> httpx.Response:
    return httpx.Response(
        200, content=orjson.dumps({"total_count": len(entries), "results": entries})
    )


PRICEOVERVIEW = "https://steamcommunity.com/market/priceoverview/"


def _price(
    store: Store,
    appid: int,
    name: str,
    cents: int,
    *,
    volume: int = 50,  # 24h sales = resale demand; a card needs this to count as liquid
    listings: int | None = None,
    currency: str = "USD",
) -> None:
    # priceoverview-SHAPED snapshot: carries 24h `volume`. This is what production uses to
    # establish resale demand — a sweep/search snapshot has `listings` (asks) but NO volume.
    store.upsert_card(Card(appid=appid, market_hash_name=name))
    store.add_price_snapshot(
        PriceSnapshot(
            item=MarketItem(appid=appid, market_hash_name=name),
            lowest=Money(cents, currency),
            listings=listings,
            volume=volume,
            source=SourceRecord(
                kind=SourceKind.STEAM_MARKET,
                url=PRICEOVERVIEW,
                fetched_at=datetime.now(UTC),
                parser_version="1",
                raw_sha256=SourceRecord.sha256_of(f"po{name}{cents}{currency}".encode()),
                cache_ttl_seconds=86400,
            ),
        )
    )


def _price_search_only(
    store: Store, appid: int, name: str, cents: int, *, listings: int = 100, currency: str = "USD"
) -> None:
    # sweep/search-SHAPED snapshot: `listings` (asks) but NO volume — the realistic state
    # before any priceoverview enrichment.
    store.upsert_card(Card(appid=appid, market_hash_name=name))
    store.add_price_snapshot(
        PriceSnapshot(
            item=MarketItem(appid=appid, market_hash_name=name),
            lowest=Money(cents, currency),
            listings=listings,
            volume=None,
            source=SourceRecord(
                kind=SourceKind.STEAM_MARKET_SEARCH,
                url=SEARCH,
                fetched_at=datetime.now(UTC),
                parser_version="1",
                raw_sha256=SourceRecord.sha256_of(f"se{name}{cents}{currency}".encode()),
                cache_ttl_seconds=86400,
            ),
        )
    )


class TestFetchBoosterPrice:
    @respx.mock
    def test_returns_quote_for_the_games_booster(
        self,
    ) -> None:
        respx.get(SEARCH).mock(
            return_value=_results(
                [
                    _entry("220-Half-Life 2", 5, 300, "Trading Card"),  # a card, ignored
                    _entry("220-Half-Life 2 Booster Pack", 45, 12, "Booster Pack"),
                ]
            )
        )
        with SafeClient(min_interval_s=0) as client:
            q = fetch_booster_price(client, 220, "USD")
        assert q == BoosterQuote(
            appid=220,
            market_hash_name="220-Half-Life 2 Booster Pack",
            lowest_cents=45,
            listings=12,
            currency="USD",
        )

    @respx.mock
    def test_ignores_foreign_prefix_and_unpriced(self) -> None:
        respx.get(SEARCH).mock(
            return_value=_results(
                [
                    _entry("999-Other Booster Pack", 30, 5, "Booster Pack"),  # wrong game
                    _entry("220-Half-Life 2 Booster Pack", None, 5, "Booster Pack"),  # unpriced
                ]
            )
        )
        with SafeClient(min_interval_s=0) as client:
            assert fetch_booster_price(client, 220, "USD") is None

    @respx.mock
    def test_no_booster_returns_none(self) -> None:
        respx.get(SEARCH).mock(return_value=_results([]))
        with SafeClient(min_interval_s=0) as client:
            assert fetch_booster_price(client, 220, "USD") is None

    def test_unknown_currency_rejected(self) -> None:
        with SafeClient(min_interval_s=0) as client, pytest.raises(ValueError):
            fetch_booster_price(client, 220, "XYZ")


class TestEvaluateBooster:
    def test_ev_is_three_cards_mean_net_of_fee(self) -> None:
        # mean 40 -> gross 120 -> net 120/1.15 = 104.3 -> 104; pack 90 -> margin 14.
        ev, cost, margin = evaluate_booster([40, 40, 40], 90, currency="USD")
        assert ev == Money(104, "USD")
        assert cost == Money(90, "USD")
        assert margin == 14

    def test_negative_margin_when_pack_dear(self) -> None:
        _, _, margin = evaluate_booster([10, 10, 10], 100, currency="USD")
        assert margin < 0

    def test_empty_cards_rejected(self) -> None:
        with pytest.raises(ValueError):
            evaluate_booster([], 10, currency="USD")

    def test_negative_booster_rejected(self) -> None:
        with pytest.raises(ValueError):
            evaluate_booster([10], -1, currency="USD")


class TestScanBoosterArbitrage:
    def _quote(self, appid: int, cents: int, listings: int | None = 20) -> BoosterQuote:
        return BoosterQuote(
            appid=appid,
            market_hash_name=f"{appid}-Game Booster Pack",
            lowest_cents=cents,
            listings=listings,
            currency="USD",
        )

    def test_flags_profitable_liquid_pack(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            for i in range(3):
                _price(store, 220, f"220-C{i}", 100)  # mean 100 -> net EV 3*100/1.15=261
            results = scan_booster_arbitrage(store, {220: self._quote(220, 200)}, currency="USD")
        assert len(results) == 1
        r = results[0]
        assert r.profitable and r.liquid
        assert r.margin_cents == 261 - 200
        assert r.confidence.value == "low"  # never above LOW: optimistic, high-variance

    def test_currency_mismatch_quote_skipped(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _price(store, 1, "1-A", 100)
            eur_quote = BoosterQuote(1, "1-Game Booster Pack", 50, 20, "EUR")
            assert scan_booster_arbitrage(store, {1: eur_quote}, currency="USD") == []

    def test_card_priced_only_in_other_currency_skips_game(self, tmp_path) -> None:
        # A card priced only in EUR must not be costed for a USD scan (currency-aware).
        with Store(tmp_path / "t.sqlite3") as store:
            _price(store, 1, "1-A", 100, currency="EUR")
            assert scan_booster_arbitrage(store, {1: self._quote(1, 50)}, currency="USD") == []

    def test_resale_demand_uses_volume_not_asks(self, tmp_path) -> None:
        # Cards with deep asks but NO 24h volume are not resale-liquid (asks are
        # competition when selling, not demand).
        with Store(tmp_path / "t.sqlite3") as store:
            for i in range(3):
                _price(store, 1, f"1-C{i}", 100, listings=500, volume=0)
            results = scan_booster_arbitrage(store, {1: self._quote(1, 100)}, currency="USD")
        assert len(results) == 1 and not results[0].liquid
        assert any("resale demand unconfirmed" in s for s in results[0].signals)

    def test_skew_flagged_when_one_card_dominates(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _price(store, 1, "1-A", 10)
            _price(store, 1, "1-B", 10)
            _price(store, 1, "1-C", 300)  # max/mean = 300/106.7 = 2.8 >= SKEW_FLAG
            results = scan_booster_arbitrage(store, {1: self._quote(1, 50)}, currency="USD")
        assert any("concentrated in one card" in s for s in results[0].signals)

    def test_incompletely_priced_game_skipped(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _price(store, 1, "1-A", 100)
            store.upsert_card(Card(appid=1, market_hash_name="1-B"))  # unpriced card in set
            assert scan_booster_arbitrage(store, {1: self._quote(1, 50)}, currency="USD") == []

    def test_partial_set_vs_catalog_size_skipped(self, tmp_path) -> None:
        # #109: all discovered cards priced, but fewer than the catalog set_size -> skip
        # (EV over a cheap subset would be biased low).
        from steam_badge_optimizer.models import BadgeSet

        with Store(tmp_path / "t.sqlite3") as store:
            store.upsert_badge_set(BadgeSet(appid=1, set_size=3))
            _price(store, 1, "1-A", 100)
            _price(store, 1, "1-B", 100)  # only 2 of 3 known
            assert scan_booster_arbitrage(store, {1: self._quote(1, 50)}, currency="USD") == []

    def test_thin_pack_not_liquid_but_still_listed(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            for i in range(3):
                _price(store, 1, f"1-C{i}", 100)
            thin = self._quote(1, 50, listings=1)  # below min_listings
            results = scan_booster_arbitrage(store, {1: thin}, currency="USD", min_listings=2)
        assert len(results) == 1
        assert results[0].profitable and not results[0].liquid

    def test_ranks_liquid_profitable_first(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            for i in range(3):
                _price(store, 1, f"1-C{i}", 100)  # net EV ~261
                _price(store, 2, f"2-C{i}", 100)
            quotes = {
                1: self._quote(1, 260, listings=1),  # profitable but thin
                2: self._quote(2, 200, listings=20),  # profitable + liquid
            }
            results = scan_booster_arbitrage(store, quotes, currency="USD", min_listings=2)
        assert results[0].appid == 2 and results[0].liquid  # liquid+profitable ranks first


class TestCli:
    def test_online_without_confirm_rejected(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        res = CliRunner().invoke(
            app, ["market", "booster-arbitrage", "--online", "--data-dir", str(tmp_path)]
        )
        assert res.exit_code == 2

    def test_bad_max_games_rejected(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        res = CliRunner().invoke(
            app,
            [
                "market",
                "booster-arbitrage",
                "--online",
                "--confirm",
                "--max-games",
                "0",
                "--data-dir",
                str(tmp_path),
            ],
        )
        assert res.exit_code == 2

    @respx.mock
    def test_end_to_end_enriches_volume_then_flags_arb(self, tmp_path, monkeypatch) -> None:
        # #108 regression: cards start SEARCH-only (no volume) — the realistic post-sweep
        # state. The command must enrich them via priceoverview (which carries volume) for
        # the resale-demand gate — and thus the "ARB" flag — to be reachable at all.
        import time

        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app
        from steam_badge_optimizer.config import Settings
        from steam_badge_optimizer.models import BadgeSet, SteamApp

        monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)  # skip rate-limit sleeps
        respx.get(PRICEOVERVIEW).mock(
            return_value=httpx.Response(
                200,
                content=orjson.dumps({"success": True, "lowest_price": "$1.00", "volume": "50"}),
            )
        )
        respx.get(SEARCH).mock(
            return_value=_results([_entry("220-HL2 Booster Pack", 200, 20, "Booster Pack")])
        )
        s = Settings.resolve(data_dir=str(tmp_path))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with Store(s.db_path) as store:
            store.upsert_app(SteamApp(appid=220, name="Half-Life 2"))
            store.upsert_badge_set(BadgeSet(appid=220, set_size=3))
            for i in range(3):
                _price_search_only(store, 220, f"220-C{i}", 100)  # no volume yet
        res = CliRunner().invoke(
            app,
            ["market", "booster-arbitrage", "--online", "--confirm", "--data-dir", str(tmp_path)],
        )
        assert res.exit_code == 0
        assert "Half-Life 2" in res.stdout
        assert "ARB" in res.stdout  # reachable ONLY because enrichment added volume
        assert respx.calls.call_count >= 4  # 3 card priceoverviews + 1 booster search

    def _seed_candidate(self, data_dir) -> None:
        from steam_badge_optimizer.config import Settings
        from steam_badge_optimizer.models import BadgeSet, SteamApp

        s = Settings.resolve(data_dir=str(data_dir))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with Store(s.db_path) as store:
            store.upsert_app(SteamApp(appid=220, name="Half-Life 2"))
            store.upsert_badge_set(BadgeSet(appid=220, set_size=3))
            for i in range(3):
                _price(store, 220, f"220-C{i}", 100)

    @respx.mock
    def test_rate_limit_hard_stops_the_fetch_loop(self, tmp_path) -> None:
        # #102 merge condition: a 429 must stop the loop, not retry past it.
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        self._seed_candidate(tmp_path)
        respx.get(SEARCH).mock(return_value=httpx.Response(429))
        res = CliRunner().invoke(
            app,
            ["market", "booster-arbitrage", "--online", "--confirm", "--data-dir", str(tmp_path)],
        )
        assert res.exit_code == 0
        assert "rate-limited" in res.stdout.lower()

    @respx.mock
    def test_fetch_error_skips_and_continues(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        self._seed_candidate(tmp_path)
        respx.get(SEARCH).mock(return_value=httpx.Response(500))
        res = CliRunner().invoke(
            app,
            ["market", "booster-arbitrage", "--online", "--confirm", "--data-dir", str(tmp_path)],
        )
        assert res.exit_code == 0
        assert "skipped appid 220" in res.stdout
