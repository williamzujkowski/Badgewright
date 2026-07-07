"""Tests for card-inventory market valuation (#97 / increment 2a)."""

from __future__ import annotations

from datetime import UTC, datetime

from steam_badge_optimizer.analytics import value_inventory
from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import (
    MarketItem,
    Money,
    PriceSnapshot,
    SourceKind,
    SourceRecord,
    UserCardInventory,
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


def _hold(store: Store, appid: int, name: str, qty: int, *, is_foil: bool = False) -> None:
    store.upsert_inventory(
        UserCardInventory(appid=appid, market_hash_name=name, quantity=qty, is_foil=is_foil)
    )


class TestValueInventory:
    def test_totals_and_line_values(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _hold(store, 220, "220-A", 2)
            _price(store, 220, "220-A", 50)  # 2 @ 0.50 = 1.00
            _hold(store, 440, "440-B", 1)
            _price(store, 440, "440-B", 30)  # 1 @ 0.30
            v = value_inventory(store, currency="USD")
        assert v.total_value == Money(130, "USD")
        assert v.priced_count == 2 and v.unpriced_count == 0
        # Most valuable first.
        assert v.holdings[0].market_hash_name == "220-A"
        assert v.holdings[0].line_value == Money(100, "USD")
        assert v.holdings[0].unit_price == Money(50, "USD")

    def test_unpriced_holding_flagged_not_zeroed(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _hold(store, 1, "1-A", 3)
            _price(store, 1, "1-A", 40)
            _hold(store, 2, "2-B", 5)  # no price cached
            v = value_inventory(store, currency="USD")
        assert v.total_value == Money(120, "USD")  # only the priced holding
        assert v.priced_count == 1 and v.unpriced_count == 1
        unpriced = [h for h in v.holdings if not h.priced]
        assert len(unpriced) == 1
        assert unpriced[0].market_hash_name == "2-B"
        assert unpriced[0].line_value is None
        assert "unpriced" in unpriced[0].signals[0]
        # Unpriced sorts last.
        assert v.holdings[-1].market_hash_name == "2-B"

    def test_wrong_currency_price_is_unpriced(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _hold(store, 1, "1-A", 1)
            _price(store, 1, "1-A", 40, currency="EUR")  # only EUR cached
            v = value_inventory(store, currency="USD")
        assert v.priced_count == 0 and v.unpriced_count == 1
        assert v.total_value == Money(0, "USD")

    def test_newer_other_currency_does_not_mask_usable_price(self, tmp_path) -> None:
        # Regression (H1): an older USD price must not be masked by a newer EUR fetch.
        with Store(tmp_path / "t.sqlite3") as store:
            _hold(store, 1, "1-A", 2)
            _price(store, 1, "1-A", 50, currency="USD")  # older
            _price(store, 1, "1-A", 99, currency="EUR")  # newer, different currency
            v = value_inventory(store, currency="USD")
        assert v.priced_count == 1 and v.unpriced_count == 0
        assert v.total_value == Money(100, "USD")  # 2 @ 0.50 USD, not masked

    def test_foil_flag_propagates(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _hold(store, 1, "1-A", 1, is_foil=True)
            _price(store, 1, "1-A", 250)
            v = value_inventory(store, currency="USD")
        assert v.holdings[0].is_foil is True

    def test_multiple_unpriced_counted(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _hold(store, 1, "1-A", 1)
            _hold(store, 2, "2-B", 1)
            _hold(store, 3, "3-C", 1)
            _price(store, 3, "3-C", 10)  # only one priced
            v = value_inventory(store, currency="USD")
        assert v.priced_count == 1 and v.unpriced_count == 2
        assert sum(1 for h in v.holdings if not h.priced) == 2

    def test_large_quantity_line_value(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _hold(store, 1, "1-A", 100_000)
            _price(store, 1, "1-A", 999)
            v = value_inventory(store, currency="USD")
        assert v.total_value == Money(999 * 100_000, "USD")

    def test_equal_value_sort_is_deterministic(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _hold(store, 1, "1-B", 1)
            _hold(store, 1, "1-A", 1)
            _price(store, 1, "1-A", 40)
            _price(store, 1, "1-B", 40)  # equal line value
            names = [h.market_hash_name for h in value_inventory(store, currency="USD").holdings]
        assert names == ["1-A", "1-B"]  # tiebreak by (appid, name)

    def test_zero_quantity_holdings_ignored(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            _hold(store, 1, "1-A", 0)
            _price(store, 1, "1-A", 40)
            v = value_inventory(store, currency="USD")
        assert v.holdings == []
        assert v.priced_count == 0 and v.unpriced_count == 0

    def test_top_caps_listing_but_not_totals(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            for i in range(5):
                _hold(store, 1, f"1-C{i}", 1)
                _price(store, 1, f"1-C{i}", 10 * (i + 1))
            v = value_inventory(store, currency="USD", top=2)
        assert len(v.holdings) == 2  # capped
        assert v.priced_count == 5  # totals cover everything
        assert v.total_value == Money(10 + 20 + 30 + 40 + 50, "USD")
        # The two most valuable are shown.
        assert v.holdings[0].line_value == Money(50, "USD")
        assert v.holdings[1].line_value == Money(40, "USD")

    def test_empty_inventory(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            v = value_inventory(store, currency="USD")
        assert v.holdings == [] and v.total_value == Money(0, "USD")


class TestCli:
    def test_values_and_flags_unpriced(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app
        from steam_badge_optimizer.config import Settings
        from steam_badge_optimizer.models import SteamApp

        s = Settings.resolve(data_dir=str(tmp_path))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with Store(s.db_path) as store:
            store.upsert_app(SteamApp(appid=220, name="Half-Life 2"))
            _hold(store, 220, "220-A", 2)
            _price(store, 220, "220-A", 50)
        res = CliRunner().invoke(app, ["inventory", "value", "--data-dir", str(tmp_path)])
        assert res.exit_code == 0
        assert "Inventory value" in res.stdout
        assert "Half-Life 2" in res.stdout
        assert "1.00" in res.stdout  # 2 @ 0.50

    def test_foil_holding_labeled(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app
        from steam_badge_optimizer.config import Settings
        from steam_badge_optimizer.models import SteamApp

        s = Settings.resolve(data_dir=str(tmp_path))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with Store(s.db_path) as store:
            store.upsert_app(SteamApp(appid=220, name="Half-Life 2"))
            _hold(store, 220, "220-Foil", 1, is_foil=True)
            _price(store, 220, "220-Foil", 250)
        res = CliRunner().invoke(app, ["inventory", "value", "--data-dir", str(tmp_path)])
        assert res.exit_code == 0
        assert "(foil)" in res.stdout

    def test_empty_inventory_exits_nonzero(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        res = CliRunner().invoke(app, ["inventory", "value", "--data-dir", str(tmp_path)])
        assert res.exit_code == 1
        assert "No inventory" in res.stdout

    def test_bad_top_rejected(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        res = CliRunner().invoke(
            app, ["inventory", "value", "--top", "0", "--data-dir", str(tmp_path)]
        )
        assert res.exit_code == 2


def _hold_item(store: Store, appid: int, name: str, kind, qty: int) -> None:
    from steam_badge_optimizer.models import UserItemHolding

    store.upsert_item_holding(
        UserItemHolding(appid=appid, market_hash_name=name, kind=kind, quantity=qty)
    )


def _sack(store: Store, cents: int) -> None:
    _price(store, 753, "753-Sack of Gems", cents)  # 1000 gems


class TestNonCardHoldings:
    def test_values_gems_via_sack_price(self, tmp_path) -> None:
        from steam_badge_optimizer.models import ItemKind

        with Store(tmp_path / "t.sqlite3") as store:
            _sack(store, 50)  # 1000 gems = $0.50 -> $0.00005/gem
            _hold_item(store, 753, "753-Gems", ItemKind.GEMS, 2000)
            v = value_inventory(store, currency="USD")
        gems = next(h for h in v.holdings if h.kind == "gems")
        assert gems.line_value == Money(100, "USD")  # 2000 * 0.05c
        assert gems.unit_price is None  # per-gem is sub-cent
        assert v.total_value == Money(100, "USD") and v.priced_count == 1

    def test_gems_unpriced_without_sack(self, tmp_path) -> None:
        from steam_badge_optimizer.models import ItemKind

        with Store(tmp_path / "t.sqlite3") as store:
            _hold_item(store, 753, "753-Gems", ItemKind.GEMS, 1000)
            v = value_inventory(store, currency="USD")
        assert v.unpriced_count == 1 and v.total_value == Money(0, "USD")
        assert any("Sack-of-Gems" in s for h in v.holdings for s in h.signals)

    def test_values_booster_at_market(self, tmp_path) -> None:
        from steam_badge_optimizer.models import ItemKind

        with Store(tmp_path / "t.sqlite3") as store:
            _hold_item(store, 440, "440-TF2 Booster Pack", ItemKind.BOOSTER_PACK, 2)
            _price(store, 440, "440-TF2 Booster Pack", 30)
            v = value_inventory(store, currency="USD")
        b = next(h for h in v.holdings if h.kind == "booster_pack")
        assert b.line_value == Money(60, "USD") and b.unit_price == Money(30, "USD")

    def test_booster_unpriced_when_no_price(self, tmp_path) -> None:
        from steam_badge_optimizer.models import ItemKind

        with Store(tmp_path / "t.sqlite3") as store:
            _hold_item(store, 440, "440-X Booster Pack", ItemKind.BOOSTER_PACK, 1)
            v = value_inventory(store, currency="USD")
        assert v.unpriced_count == 1 and v.total_value == Money(0, "USD")

    def test_cards_and_holdings_combined_total(self, tmp_path) -> None:
        from steam_badge_optimizer.models import ItemKind

        with Store(tmp_path / "t.sqlite3") as store:
            _hold(store, 220, "220-A", 1)
            _price(store, 220, "220-A", 40)
            _sack(store, 50)
            _hold_item(store, 753, "753-Gems", ItemKind.GEMS, 1000)
            v = value_inventory(store, currency="USD")
        assert v.total_value == Money(40 + 50, "USD")  # card + gems
        assert {h.kind for h in v.holdings} == {"card", "gems"}

    def test_held_sack_valued_at_its_market_price(self, tmp_path) -> None:
        from steam_badge_optimizer.models import ItemKind

        with Store(tmp_path / "t.sqlite3") as store:
            _sack(store, 50)  # Sack of Gems market price = 50c
            _hold_item(store, 753, "753-Sack of Gems", ItemKind.SACK_OF_GEMS, 2)
            v = value_inventory(store, currency="USD")
        sack = next(h for h in v.holdings if h.kind == "sack_of_gems")
        assert sack.line_value == Money(100, "USD") and sack.unit_price == Money(50, "USD")

    def test_booster_priced_in_other_currency_is_unpriced(self, tmp_path) -> None:
        from steam_badge_optimizer.models import ItemKind

        with Store(tmp_path / "t.sqlite3") as store:
            _hold_item(store, 440, "440-B Booster Pack", ItemKind.BOOSTER_PACK, 1)
            _price(store, 440, "440-B Booster Pack", 30, currency="EUR")  # only EUR cached
            v = value_inventory(store, currency="USD")
        assert v.unpriced_count == 1 and v.total_value == Money(0, "USD")

    def test_cli_renders_priced_gems_without_unit(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app
        from steam_badge_optimizer.config import Settings
        from steam_badge_optimizer.models import ItemKind

        s = Settings.resolve(data_dir=str(tmp_path))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with Store(s.db_path) as store:
            _sack(store, 50)
            _hold_item(store, 753, "753-Gems", ItemKind.GEMS, 2000)
        res = CliRunner().invoke(app, ["inventory", "value", "--data-dir", str(tmp_path)])
        assert res.exit_code == 0
        assert "[gems]" in res.stdout
        # gems line shows a value (1.00) but no per-unit "@ " price
        gem_line = next(ln for ln in res.stdout.splitlines() if "[gems]" in ln)
        assert "1.00" in gem_line and " @ " not in gem_line
