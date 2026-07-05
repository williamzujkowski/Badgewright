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
