"""Tests for the gem economy price layer (#95): Sack-of-Gems USD/gem + booster cost."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from steam_badge_optimizer.analytics import (
    booster_crafting_cost_gems,
    gem_value,
    gems_to_money,
    latest_sack_price,
    sack_of_gems_item,
)
from steam_badge_optimizer.analytics.gem_economy import (
    GEMS_PER_SACK,
    SACK_OF_GEMS_APPID,
    SACK_OF_GEMS_HASH,
)
from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import (
    MarketItem,
    Money,
    PriceSnapshot,
    SourceKind,
    SourceRecord,
)


def _seed_sack(store: Store, cents: int, *, currency: str = "USD") -> None:
    store.add_price_snapshot(
        PriceSnapshot(
            item=MarketItem(appid=SACK_OF_GEMS_APPID, market_hash_name=SACK_OF_GEMS_HASH),
            lowest=Money(cents, currency),
            source=SourceRecord(
                kind=SourceKind.STEAM_MARKET,
                url="https://steamcommunity.com/market/priceoverview/",
                fetched_at=datetime.now(UTC),
                parser_version="1",
                raw_sha256=SourceRecord.sha256_of(f"sack{cents}".encode()),
                cache_ttl_seconds=86400,
            ),
        )
    )


class TestSackItem:
    def test_identity(self) -> None:
        item = sack_of_gems_item()
        assert item.appid == SACK_OF_GEMS_APPID == 753
        assert item.market_hash_name == SACK_OF_GEMS_HASH == "753-Sack of Gems"
        assert GEMS_PER_SACK == 1000


class TestGemValue:
    def test_gross_is_price_over_thousand(self) -> None:
        # A 50¢ sack => 1000 gems => 0.05¢ per gem, in the sack's currency.
        v = gem_value(Money(50, "USD"))
        assert v.currency == "USD"
        assert v.cents_per_gem == Decimal(50) / 1000  # 0.05¢

    def test_net_is_sellers_proceeds_after_fee(self) -> None:
        # The 15% fee is on the seller's proceeds, added on top for the buyer price, so a
        # seller nets list / 1.15 (NOT list * 0.85).
        v = gem_value(Money(50, "USD"))
        assert v.net_cents_per_gem == v.cents_per_gem / Decimal("1.15")
        assert v.net_cents_per_gem < v.cents_per_gem

    def test_currency_carried_through(self) -> None:
        assert gem_value(Money(65, "EUR")).currency == "EUR"


class TestGemsToMoney:
    def test_thousand_gems_gross_equals_sack(self) -> None:
        v = gem_value(Money(50, "USD"))
        assert gems_to_money(1000, v) == Money(50, "USD")  # round-trips the sack price

    def test_net_is_after_fee_and_half_up(self) -> None:
        v = gem_value(Money(50, "USD"))
        # 0.0425¢/gem * 1000 = 42.5 -> ROUND_HALF_UP -> 43.
        assert gems_to_money(1000, v, net=True) == Money(43, "USD")

    def test_zero_gems_is_zero(self) -> None:
        assert gems_to_money(0, gem_value(Money(50, "USD"))) == Money(0, "USD")

    def test_negative_gems_rejected(self) -> None:
        with pytest.raises(ValueError):
            gems_to_money(-1, gem_value(Money(50, "USD")))


class TestBoosterCraftingCost:
    @pytest.mark.parametrize(
        ("set_size", "gems"),
        [(5, 1200), (6, 1000), (8, 750), (10, 600), (15, 400), (1, 6000)],
    )
    def test_known_recipe_values(self, set_size: int, gems: int) -> None:
        assert booster_crafting_cost_gems(set_size) == gems

    def test_rounds_to_nearest(self) -> None:
        assert booster_crafting_cost_gems(7) == round(6000 / 7) == 857

    @pytest.mark.parametrize("bad", [0, -1, -10])
    def test_rejects_non_positive(self, bad: int) -> None:
        with pytest.raises(ValueError):
            booster_crafting_cost_gems(bad)

    def test_large_gem_count_is_exact(self) -> None:
        # Decimal keeps this exact where float would drift.
        v = gem_value(Money(50, "USD"))
        assert gems_to_money(10_000_000, v) == Money(500_000, "USD")


class TestLatestSackPrice:
    def test_reads_cached_snapshot(self, tmp_path) -> None:
        db = tmp_path / "t.sqlite3"
        with Store(db) as store:
            assert latest_sack_price(store) is None
            _seed_sack(store, 47)
            snap = latest_sack_price(store)
            assert snap is not None and snap.lowest == Money(47, "USD")

    def test_currency_filter_does_not_mask_matching_older_price(self, tmp_path) -> None:
        # Regression: a newer EUR fetch must not hide an existing USD price (M2).
        with Store(tmp_path / "t.sqlite3") as store:
            _seed_sack(store, 47, currency="USD")
            _seed_sack(store, 60, currency="EUR")  # newer, different currency
            usd = latest_sack_price(store, currency="USD")
            assert usd is not None and usd.lowest == Money(47, "USD")
            assert latest_sack_price(store, currency="EUR").lowest == Money(60, "EUR")
            assert latest_sack_price(store, currency="GBP") is None


class TestRefreshSackPrice:
    def test_uses_guarded_path_with_sack_item(self, tmp_path, monkeypatch) -> None:
        import steam_badge_optimizer.sources.steam_market as sm
        from steam_badge_optimizer.analytics.gem_economy import (
            refresh_sack_price,
            sack_of_gems_item,
        )

        calls: dict[str, object] = {}

        def fake_refresh(store, client, items, currency, *, force=False, **_):
            calls["items"] = items
            calls["currency"] = currency
            _seed_sack(store, 60, currency=currency)

        monkeypatch.setattr(sm, "refresh_prices", fake_refresh)
        with Store(tmp_path / "t.sqlite3") as store:
            snap = refresh_sack_price(store, client=object(), currency="USD")  # type: ignore[arg-type]
        assert calls["items"] == [sack_of_gems_item()]  # exactly the Sack, nothing else
        assert calls["currency"] == "USD"
        assert snap is not None and snap.lowest == Money(60, "USD")


class TestCli:
    def test_offline_reads_cached_and_shows_booster_cost(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app
        from steam_badge_optimizer.config import Settings

        s = Settings.resolve(data_dir=str(tmp_path))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with Store(s.db_path) as store:
            _seed_sack(store, 50)
        res = CliRunner().invoke(
            app, ["market", "gems", "--set-size", "6", "--data-dir", str(tmp_path)]
        )
        assert res.exit_code == 0
        assert "Sack of Gems" in res.stdout
        assert "1000 gems" in res.stdout
        assert "6-card set: 1000 gems" in res.stdout

    def test_no_cached_price_exits_nonzero(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        res = CliRunner().invoke(app, ["market", "gems", "--data-dir", str(tmp_path)])
        assert res.exit_code == 1
        assert "No cached Sack-of-Gems price" in res.stdout

    def test_online_without_confirm_rejected(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        res = CliRunner().invoke(app, ["market", "gems", "--online", "--data-dir", str(tmp_path)])
        assert res.exit_code == 2
        assert "--confirm" in res.stdout

    def test_bad_set_size_rejected(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        res = CliRunner().invoke(
            app, ["market", "gems", "--set-size", "0", "--data-dir", str(tmp_path)]
        )
        assert res.exit_code == 2

    def test_only_other_currency_cached_exits_nonzero(self, tmp_path) -> None:
        # A cached EUR sack must not satisfy a default-USD read (M2 at the CLI level).
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app
        from steam_badge_optimizer.config import Settings

        s = Settings.resolve(data_dir=str(tmp_path))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with Store(s.db_path) as store:
            _seed_sack(store, 60, currency="EUR")
        res = CliRunner().invoke(app, ["market", "gems", "--data-dir", str(tmp_path)])
        assert res.exit_code == 1
        assert "No cached Sack-of-Gems price in USD" in res.stdout

    def test_online_ratelimited_falls_back_to_cache(self, tmp_path, monkeypatch) -> None:
        from typer.testing import CliRunner

        import steam_badge_optimizer.analytics as analytics
        from steam_badge_optimizer.cli import app
        from steam_badge_optimizer.config import Settings
        from steam_badge_optimizer.sources.http_client import RateLimited

        s = Settings.resolve(data_dir=str(tmp_path))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with Store(s.db_path) as store:
            _seed_sack(store, 50)  # a cached price to fall back to

        def boom(*_a, **_k):
            raise RateLimited("https://steamcommunity.com/market/priceoverview/", None)

        monkeypatch.setattr(analytics, "refresh_sack_price", boom)
        res = CliRunner().invoke(
            app, ["market", "gems", "--online", "--confirm", "--data-dir", str(tmp_path)]
        )
        assert res.exit_code == 0
        assert "rate-limited" in res.stdout.lower()
        assert "Sack of Gems" in res.stdout  # still shows the cached value
