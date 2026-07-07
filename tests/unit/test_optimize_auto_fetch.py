"""Tests for `sbo optimize --auto-fetch` (#69): candidate selection + fetch orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from typer.testing import CliRunner

from steam_badge_optimizer.cli import _auto_fetch_candidates, app
from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import (
    BadgeSet,
    Card,
    Money,
    PriceSnapshot,
    SourceKind,
    SourceRecord,
    SteamApp,
    UserBadgeProgress,
    UserCardInventory,
)

runner = CliRunner()


def _inc(*appids: int):
    return [SimpleNamespace(appid=a) for a in appids]


class TestCandidateSelection:
    def test_filters_to_owned_or_partial_and_sorts(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            # appid 1 owns 2 cards, appid 2 owns 1, appid 3 partial progress, appid 4 nothing.
            for name in ("1-A", "1-B"):
                store.upsert_inventory(
                    UserCardInventory(appid=1, market_hash_name=name, quantity=1)
                )
            store.upsert_inventory(UserCardInventory(appid=2, market_hash_name="2-A", quantity=1))
            store.upsert_badge_progress(UserBadgeProgress(appid=3, level=2))
            got = _auto_fetch_candidates(store, _inc(1, 2, 3, 4), max_games=10)
        # 4 excluded; sorted most-owned-first: 1(2 cards), 2(1), 3(0 owned but partial)
        assert got == [1, 2, 3]

    def test_max_games_caps(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            for a in (1, 2, 3):
                store.upsert_inventory(
                    UserCardInventory(appid=a, market_hash_name=f"{a}-A", quantity=1)
                )
            assert _auto_fetch_candidates(store, _inc(1, 2, 3), max_games=2) == [1, 2]

    def test_maxed_progress_not_partial(self, tmp_path) -> None:
        with Store(tmp_path / "t.sqlite3") as store:
            store.upsert_badge_progress(UserBadgeProgress(appid=5, level=5))  # maxed, not partial
            assert _auto_fetch_candidates(store, _inc(5), max_games=10) == []


def _seed_relevant_incomplete(data_dir) -> None:
    """A game (220) the user owns 1 of 2 cards in, with the set neither fully discovered
    nor priced -> a relevant incomplete badge that --auto-fetch should target."""
    from steam_badge_optimizer.config import Settings

    s = Settings.resolve(data_dir=str(data_dir))
    s.data_dir.mkdir(parents=True, exist_ok=True)
    with Store(s.db_path) as store:
        store.upsert_app(SteamApp(appid=220, name="Half-Life 2"))
        store.upsert_badge_set(BadgeSet(appid=220, set_size=2))
        store.upsert_card(Card(appid=220, market_hash_name="220-A"))  # only 1 of 2 known
        store.upsert_inventory(UserCardInventory(appid=220, market_hash_name="220-A", quantity=1))


class TestCli:
    def test_bad_max_games_rejected(self, tmp_path) -> None:
        res = runner.invoke(
            app, ["optimize", "--auto-fetch", "--max-games", "0", "--data-dir", str(tmp_path)]
        )
        assert res.exit_code == 2

    def test_nothing_relevant_to_fetch(self, tmp_path) -> None:
        # Empty store -> no incomplete badges the user is involved with.
        res = runner.invoke(app, ["optimize", "--auto-fetch", "--data-dir", str(tmp_path)])
        assert res.exit_code == 0
        assert "Nothing to auto-fetch" in res.stdout

    def test_orchestration_fetches_then_replans(self, tmp_path, monkeypatch) -> None:
        import steam_badge_optimizer.sources.card_discovery as cd
        import steam_badge_optimizer.sources.steam_market as sm

        _seed_relevant_incomplete(tmp_path)

        def fake_import_cards(store, client, appid, set_size):
            for name in (f"{appid}-A", f"{appid}-B"):  # "discover" the full set
                store.upsert_card(Card(appid=appid, market_hash_name=name))

        def fake_refresh_prices(store, client, items, currency, **_):
            for it in items:
                store.add_price_snapshot(
                    PriceSnapshot(
                        item=it,
                        lowest=Money(50, currency),
                        volume=100,
                        source=SourceRecord(
                            kind=SourceKind.STEAM_MARKET,
                            url="https://steamcommunity.com/market/priceoverview/",
                            fetched_at=datetime.now(UTC),
                            parser_version="1",
                            raw_sha256=SourceRecord.sha256_of(it.market_hash_name.encode()),
                            cache_ttl_seconds=86400,
                        ),
                    )
                )

        monkeypatch.setattr(cd, "import_cards", fake_import_cards)
        monkeypatch.setattr(sm, "refresh_prices", fake_refresh_prices)
        res = runner.invoke(
            app, ["optimize", "--auto-fetch", "--max-games", "5", "--data-dir", str(tmp_path)]
        )
        assert res.exit_code == 0
        assert "Auto-fetching 1 relevant game" in res.stdout
        assert "fetched Half-Life 2 (appid 220)" in res.stdout
