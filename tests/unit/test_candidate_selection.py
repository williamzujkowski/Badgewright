"""Tests for candidate-game selection (targeted completion, #77/#69)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import respx

from steam_badge_optimizer.analytics import select_candidate_games
from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import (
    BadgeSet,
    Card,
    MarketItem,
    Money,
    PriceSnapshot,
    SourceKind,
    SourceRecord,
)

SEARCH = "https://steamcommunity.com/market/search/render/"


def _price(store: Store, appid: int, name: str, cents: int, currency: str = "USD") -> None:
    store.add_price_snapshot(
        PriceSnapshot(
            item=MarketItem(appid=appid, market_hash_name=name),
            lowest=Money(cents, currency),
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


def _partial(store: Store, appid: int, set_size: int, priced: list[int]) -> None:
    """A game whose set has `set_size` cards but only `priced` cards are known+priced."""
    store.upsert_badge_set(BadgeSet(appid=appid, set_size=set_size))
    for i, cents in enumerate(priced):
        name = f"{appid}-C{i}"
        store.upsert_card(Card(appid=appid, market_hash_name=name))
        _price(store, appid, name, cents)


class TestSelectCandidates:
    def test_empty_store(self) -> None:
        with Store.in_memory() as store:
            assert select_candidate_games(store) == []

    def test_max_games_must_be_positive(self) -> None:
        with Store.in_memory() as store, pytest.raises(ValueError):
            select_candidate_games(store, max_games=0)

    def test_uses_median_proxy_not_single_cheap_card(self) -> None:
        # Anti-bias: game B has ONE 1-cent card of a 5-card set. Its completion estimate must
        # use the median proxy for the 4 unpriced slots, NOT assume they cost 1 cent too.
        with Store.in_memory() as store:
            _partial(store, 100, 5, [2, 2, 2, 2])  # A: 4/5 priced at 2c
            _partial(store, 200, 5, [1])  # B: 1/5 priced at 1c
            cands = {c.appid: c for c in select_candidate_games(store)}
            # all known prices = [2,2,2,2,1] -> median 2
            assert cands[200].est_completion_cents == 1 + 4 * 2  # == 9, NOT 5 (1c * 5)
            assert cands[100].est_completion_cents == 8 + 1 * 2  # == 10

    def test_rewards_evidence_over_single_cheap_card(self) -> None:
        # A and B both have only cheap known cards (1c), but A has verified MORE of its set.
        # With a conservative p75 proxy for unpriced slots, A (more evidence) must rank first —
        # a lone cheap card shouldn't tie a well-evidenced cheap set.
        with Store.in_memory() as store:
            _partial(store, 1, 5, [1, 1, 1])  # A: 3/5 cheap (strong evidence)
            _partial(store, 2, 5, [1])  # B: 1/5 cheap (a gamble)
            _partial(store, 3, 5, [50, 60, 70])  # C: expensive -> raises the p75 proxy
            ranked = select_candidate_games(store)
            order = [c.appid for c in ranked]
            assert order.index(1) < order.index(2)  # A before B (evidence wins)

    def test_ranks_by_estimated_completion_cost(self) -> None:
        with Store.in_memory() as store:
            _partial(store, 100, 2, [50])  # est 50 + 1*median
            _partial(store, 200, 2, [1])  # est 1 + 1*median (cheapest)
            _partial(store, 300, 2, [10])
            ranked = select_candidate_games(store)
            assert [c.appid for c in ranked] == [200, 300, 100]

    def test_fully_priced_set_is_not_a_candidate(self) -> None:
        # A set with every card priced is already rankable; don't spend budget re-fetching it.
        with Store.in_memory() as store:
            _partial(store, 100, 2, [5, 6])  # 2/2 priced -> complete, excluded
            _partial(store, 200, 3, [5])  # 1/3 -> candidate
            ranked = select_candidate_games(store)
            assert [c.appid for c in ranked] == [200]

    def test_max_games_caps_and_ties_break_by_appid(self) -> None:
        with Store.in_memory() as store:
            for appid in (300, 100, 200):
                _partial(store, appid, 2, [5])  # identical est -> tie
            ranked = select_candidate_games(store, max_games=2)
            assert [c.appid for c in ranked] == [100, 200]  # cap + deterministic appid order


class _DummyClient:
    def __init__(self, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a) -> bool:
        return False


def _seed_candidate(tmp_path) -> None:
    from steam_badge_optimizer.config import Settings

    s = Settings.resolve(data_dir=str(tmp_path))
    s.data_dir.mkdir(parents=True, exist_ok=True)
    with Store(s.db_path) as store:
        store.upsert_badge_set(BadgeSet(appid=100, set_size=3))
        store.upsert_card(Card(appid=100, market_hash_name="100-A"))
        _price(store, 100, "100-A", 5)


class TestPlanCheapestCliIsOptIn:
    @respx.mock
    def test_refuses_without_both_flags(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        runner = CliRunner()
        for args in (
            ["market", "plan-cheapest", "--data-dir", str(tmp_path)],
            ["market", "plan-cheapest", "--online", "--data-dir", str(tmp_path)],
            ["market", "plan-cheapest", "--confirm", "--data-dir", str(tmp_path)],
        ):
            result = runner.invoke(app, args)
            assert result.exit_code == 2, args
        assert respx.calls.call_count == 0  # never touched the network

    def test_skips_game_on_non_429_error_and_continues(self, tmp_path, monkeypatch) -> None:
        from typer.testing import CliRunner

        import steam_badge_optimizer.sources.card_discovery as cd
        import steam_badge_optimizer.sources.http_client as hc
        from steam_badge_optimizer.cli import app
        from steam_badge_optimizer.sources.http_client import FetchError

        _seed_candidate(tmp_path)
        monkeypatch.setattr(hc, "SafeClient", _DummyClient)
        monkeypatch.setattr(
            cd, "import_cards", lambda *a, **k: (_ for _ in ()).throw(FetchError("blip"))
        )
        result = CliRunner().invoke(
            app, ["market", "plan-cheapest", "--online", "--confirm", "--data-dir", str(tmp_path)]
        )
        assert result.exit_code == 0  # graceful, not a traceback
        assert "skipped appid 100" in result.output

    def test_hard_stops_on_rate_limit(self, tmp_path, monkeypatch) -> None:
        from typer.testing import CliRunner

        import steam_badge_optimizer.sources.card_discovery as cd
        import steam_badge_optimizer.sources.http_client as hc
        from steam_badge_optimizer.cli import app
        from steam_badge_optimizer.sources.http_client import RateLimited

        _seed_candidate(tmp_path)
        monkeypatch.setattr(hc, "SafeClient", _DummyClient)
        monkeypatch.setattr(
            cd, "import_cards", lambda *a, **k: (_ for _ in ()).throw(RateLimited(SEARCH, None))
        )
        result = CliRunner().invoke(
            app, ["market", "plan-cheapest", "--online", "--confirm", "--data-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "rate-limited" in result.output.lower()
