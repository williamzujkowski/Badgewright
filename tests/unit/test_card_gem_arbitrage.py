"""Tests for card->gem arbitrage analytics (#101-ii)."""

from __future__ import annotations

from datetime import UTC, datetime

from steam_badge_optimizer.analytics import scan_card_gem_arbitrage
from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import (
    Card,
    CardGooValue,
    MarketItem,
    Money,
    PriceSnapshot,
    SourceKind,
    SourceRecord,
)


def _price(store: Store, appid: int, name: str, cents: int, *, currency: str = "USD") -> None:
    store.add_price_snapshot(
        PriceSnapshot(
            item=MarketItem(appid=appid, market_hash_name=name),
            lowest=Money(cents, currency),
            source=SourceRecord(
                kind=SourceKind.STEAM_MARKET,
                url="https://steamcommunity.com/market/priceoverview/",
                fetched_at=datetime.now(UTC),
                parser_version="1",
                raw_sha256=SourceRecord.sha256_of(f"{name}{cents}{currency}".encode()),
                cache_ttl_seconds=86400,
            ),
        )
    )


def _sack(store: Store, cents: int) -> None:
    _price(store, 753, "753-Sack of Gems", cents)  # 1000 gems


def _card(store: Store, appid: int, name: str, *, is_foil: bool) -> None:
    store.upsert_card(Card(appid=appid, market_hash_name=name, is_foil=is_foil))


def _goo(store: Store, appid: int, name: str, value: int, *, border: int = 1) -> None:
    store.upsert_goo_value(
        CardGooValue(
            appid=appid, market_hash_name=name, item_type=4, border_color=border, goo_value=value
        ),
        SourceRecord(
            kind=SourceKind.STEAM_MARKET,
            url="https://steamcommunity.com/auction/ajaxgetgoovalueforitemtype/",
            fetched_at=datetime.now(UTC),
            parser_version="1",
            raw_sha256=SourceRecord.sha256_of(f"goo{name}{value}".encode()),
            cache_ttl_seconds=86400,
        ),
    )


class TestScan:
    def test_flags_foil_cheaper_than_its_gems(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _sack(store, 50)  # $0.50 / 1000 gems => 0.05c/gem
            _card(store, 570, "570-Razor (Foil)", is_foil=True)
            _goo(store, 570, "570-Razor (Foil)", 800)  # 800 gems => 40c
            _price(store, 570, "570-Razor (Foil)", 25)  # card costs 25c
            res = scan_card_gem_arbitrage(store, currency="USD")
        assert len(res) == 1
        r = res[0]
        assert r.gem_value == Money(40, "USD")  # 800 * 0.05c
        assert r.margin_cents == 40 - 25 and r.profitable
        assert r.confidence.value == "low"  # never above LOW

    def test_no_sack_price_returns_empty(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _card(store, 570, "570-Razor (Foil)", is_foil=True)
            _goo(store, 570, "570-Razor (Foil)", 800)
            _price(store, 570, "570-Razor (Foil)", 25)
            assert scan_card_gem_arbitrage(store, currency="USD") == []

    def test_card_without_goo_skipped(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _sack(store, 50)
            _card(store, 570, "570-Razor (Foil)", is_foil=True)
            _price(store, 570, "570-Razor (Foil)", 25)  # no goo cached
            assert scan_card_gem_arbitrage(store, currency="USD") == []

    def test_foil_only_default_skips_normals(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _sack(store, 50)
            _card(store, 1, "1-Normal", is_foil=False)
            _goo(store, 1, "1-Normal", 8, border=0)
            _price(store, 1, "1-Normal", 3)
            assert scan_card_gem_arbitrage(store, currency="USD", foil_only=True) == []
            incl = scan_card_gem_arbitrage(store, currency="USD", foil_only=False)
            assert len(incl) == 1 and not incl[0].is_foil
            assert any("normal card" in s for s in incl[0].signals)

    def test_negative_margin_when_card_dear(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _sack(store, 50)
            _card(store, 570, "570-X (Foil)", is_foil=True)
            _goo(store, 570, "570-X (Foil)", 100)  # 100 gems => 5c
            _price(store, 570, "570-X (Foil)", 200)  # card 200c
            res = scan_card_gem_arbitrage(store, currency="USD")
        assert res[0].margin_cents == 5 - 200 and not res[0].profitable

    def test_wrong_currency_price_skipped(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _sack(store, 50)
            _card(store, 570, "570-Razor (Foil)", is_foil=True)
            _goo(store, 570, "570-Razor (Foil)", 800)
            _price(store, 570, "570-Razor (Foil)", 25, currency="EUR")  # only EUR
            assert scan_card_gem_arbitrage(store, currency="USD") == []

    def test_ranks_by_margin(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _sack(store, 50)
            for i, (goo, cost) in enumerate([(800, 25), (2000, 30)]):
                name = f"570-C{i} (Foil)"
                _card(store, 570, name, is_foil=True)
                _goo(store, 570, name, goo)
                _price(store, 570, name, cost)
            res = scan_card_gem_arbitrage(store, currency="USD")
        # C1: 2000*0.05=100 -30 = 70; C0: 40-25=15 -> C1 first
        assert res[0].market_hash_name == "570-C1 (Foil)" and res[0].margin_cents == 70


class TestCli:
    def test_offline_empty_exits_nonzero(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        res = CliRunner().invoke(app, ["market", "card-gem-arbitrage", "--data-dir", str(tmp_path)])
        assert res.exit_code == 1

    def test_offline_scan_flags(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app
        from steam_badge_optimizer.config import Settings
        from steam_badge_optimizer.models import SteamApp

        s = Settings.resolve(data_dir=str(tmp_path))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with Store(s.db_path) as store:
            store.upsert_app(SteamApp(appid=570, name="Dota 2"))
            _sack(store, 50)
            _card(store, 570, "570-Razor (Foil)", is_foil=True)
            _goo(store, 570, "570-Razor (Foil)", 800)
            _price(store, 570, "570-Razor (Foil)", 25)
        res = CliRunner().invoke(app, ["market", "card-gem-arbitrage", "--data-dir", str(tmp_path)])
        assert res.exit_code == 0
        assert "Dota 2" in res.stdout and "ARB" in res.stdout

    def test_online_without_confirm_rejected(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        res = CliRunner().invoke(
            app, ["market", "card-gem-arbitrage", "--online", "--data-dir", str(tmp_path)]
        )
        assert res.exit_code == 2
