"""Tests for the bounded, opt-in market sweep (#73) — every fence is an invariant here."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import orjson
import pytest
import respx

from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import BadgeSet
from steam_badge_optimizer.sources.http_client import SafeClient
from steam_badge_optimizer.sources.market_sweep import (
    StopReason,
    _game_appid,
    sweep_cheapest,
)

SEARCH = "https://steamcommunity.com/market/search/render/"


def _card(hash_name: str, price: int, listings: int = 250) -> dict:
    return {
        "hash_name": hash_name,
        "sell_price": price,
        "sell_listings": listings,
        "asset_description": {"type": "Trading Card"},
    }


def _page(start: int, total: int, *, cards: list[dict]) -> httpx.Response:
    """A search/render page with an explicit list of card entries."""
    return httpx.Response(200, content=orjson.dumps({"total_count": total, "results": cards}))


def _full_page(start: int, total: int, *, appid_base: int, n: int = 100) -> httpx.Response:
    """A realistic full page of `n` distinct-appid cards (the endpoint's real page size)."""
    cards = [_card(f"{appid_base + i}-Card", 1 + i) for i in range(n)]
    return _page(start, total, cards=cards)


def _by_start(pages: dict[int, httpx.Response], *, default_total: int = 10_000):
    """respx side effect: return the page whose `start` matches, else an empty page."""

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start", 0))
        if start in pages:
            return pages[start]
        body = orjson.dumps({"total_count": default_total, "results": []})
        return httpx.Response(200, content=body)

    return handler


def _fixed_clock():
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    return lambda: ts


class TestGameAppid:
    @pytest.mark.parametrize(
        ("hash_name", "expected"),
        [("440-Heavy", 440), ("753-Foo", 753), ("NoPrefix", None), ("-x", None)],
    )
    def test_parses_prefix(self, hash_name: str, expected: int | None) -> None:
        assert _game_appid(hash_name) == expected


class TestSweep:
    @respx.mock
    def test_persists_prices_across_pages(self, tmp_path) -> None:
        respx.get(SEARCH).mock(
            side_effect=_by_start(
                {
                    0: _full_page(0, 400, appid_base=1000),
                    100: _full_page(100, 400, appid_base=2000),
                }
            )
        )
        with Store.in_memory() as store, SafeClient(min_interval_s=0) as client:
            result = sweep_cheapest(
                store, client, tmp_path, max_pages=2, page_size=100, clock=_fixed_clock()
            )
            assert result.pages_fetched == 2
            assert result.cards_priced == 200  # two real pages of 100
            assert store.latest_price(1000, "1000-Card") is not None
            assert store.latest_price(2001, "2001-Card").lowest.cents == 2

    @respx.mock
    def test_max_pages_hard_caps_requests(self, tmp_path) -> None:
        # A huge market, but max_pages=2 must issue EXACTLY 2 requests and stop.
        route = respx.get(SEARCH).mock(
            side_effect=_by_start(
                {
                    i * 100: _full_page(i * 100, 1_000_000, appid_base=10_000 + i * 100)
                    for i in range(50)
                }
            )
        )
        with Store.in_memory() as store, SafeClient(min_interval_s=0) as client:
            result = sweep_cheapest(store, client, tmp_path, max_pages=2, page_size=100)
        assert route.call_count == 2
        assert result.pages_fetched == 2
        assert result.stop_reason is StopReason.MAX_PAGES
        assert result.next_cursor == 200  # advanced by the real page size (100), twice

    @respx.mock
    def test_resumes_from_persisted_cursor(self, tmp_path) -> None:
        pages = {
            i * 100: _full_page(i * 100, 1_000_000, appid_base=10_000 + i * 100) for i in range(5)
        }
        route = respx.get(SEARCH).mock(side_effect=_by_start(pages))
        with Store.in_memory() as store, SafeClient(min_interval_s=0) as client:
            first = sweep_cheapest(store, client, tmp_path, max_pages=1, page_size=100)
            assert first.next_cursor == 100
            # Second run must RESUME from start=100, not restart at 0.
            second = sweep_cheapest(store, client, tmp_path, max_pages=1, page_size=100)
            assert second.resumed_from == 100
        starts = [int(c.request.url.params.get("start", 0)) for c in route.calls]
        assert starts == [0, 100]  # never re-fetched page 0

    @respx.mock
    def test_hard_stops_on_429(self, tmp_path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            start = int(request.url.params.get("start", 0))
            if start == 0:
                return _full_page(0, 1_000_000, appid_base=1000)
            return httpx.Response(429)  # rate limited on the 2nd page

        route = respx.get(SEARCH).mock(side_effect=handler)
        with Store.in_memory() as store, SafeClient(min_interval_s=0) as client:
            result = sweep_cheapest(store, client, tmp_path, max_pages=10, page_size=100)
            assert store.latest_price(1000, "1000-Card") is not None  # page-1 data kept
        assert result.stop_reason is StopReason.RATE_LIMITED
        assert result.pages_fetched == 1  # only the successful page counted
        assert result.next_cursor == 100  # progress persisted; resumes here
        assert route.call_count == 2  # tried page 2 once, then STOPPED (no retry storm)

    @respx.mock
    def test_stops_at_end_of_market_and_clears_cursor(self, tmp_path) -> None:
        # One full page of 100 (total 150); the next page comes back empty -> end.
        respx.get(SEARCH).mock(side_effect=_by_start({0: _full_page(0, 150, appid_base=1000)}))
        with Store.in_memory() as store, SafeClient(min_interval_s=0) as client:
            result = sweep_cheapest(store, client, tmp_path, max_pages=10, page_size=100)
        assert result.stop_reason is StopReason.END_OF_MARKET
        assert result.next_cursor is None
        assert not (tmp_path / "sweep_cursor.json").exists()  # cleared on completion

    @respx.mock
    def test_early_exit_after_k_complete_sets(self, tmp_path) -> None:
        # Catalog says appid 100 needs 2 cards; a page prices both -> 1 complete set.
        respx.get(SEARCH).mock(
            side_effect=_by_start(
                {0: _page(0, 1_000_000, cards=[_card("100-A", 1, 9), _card("100-B", 2, 9)])}
            )
        )
        with Store.in_memory() as store, SafeClient(min_interval_s=0) as client:
            store.upsert_badge_set(BadgeSet(appid=100, set_size=2))
            result = sweep_cheapest(
                store, client, tmp_path, max_pages=10, page_size=100, stop_after_complete_sets=1
            )
        assert result.stop_reason is StopReason.EARLY_EXIT
        assert result.complete_sets >= 1
        assert result.pages_fetched == 1  # stopped right after the completing page

    @respx.mock
    def test_cursor_file_is_under_data_dir(self, tmp_path) -> None:
        respx.get(SEARCH).mock(
            side_effect=_by_start({0: _full_page(0, 1_000_000, appid_base=1000)})
        )
        with Store.in_memory() as store, SafeClient(min_interval_s=0) as client:
            sweep_cheapest(store, client, tmp_path, max_pages=1, page_size=100)
        assert (tmp_path / "sweep_cursor.json").is_file()  # fixed name, no traversal

    def test_max_pages_must_be_positive(self, tmp_path) -> None:
        with (
            Store.in_memory() as store,
            SafeClient(min_interval_s=0) as client,
            pytest.raises(ValueError),
        ):
            sweep_cheapest(store, client, tmp_path, max_pages=0)


class TestSweepCliIsOptIn:
    """The bulk sweep must be impossible to trigger by accident (no live network in CI)."""

    @respx.mock
    def test_refuses_without_both_flags(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        # If any of these DID hit the network, respx (no routes registered) would raise.
        runner = CliRunner()
        for args in (
            ["market", "sweep", "--data-dir", str(tmp_path)],  # neither flag
            ["market", "sweep", "--online", "--data-dir", str(tmp_path)],  # only --online
            ["market", "sweep", "--confirm", "--data-dir", str(tmp_path)],  # only --confirm
        ):
            result = runner.invoke(app, args)
            assert result.exit_code == 2, args
            assert "off by default" in result.output.lower() or "both" in result.output.lower()
        assert respx.calls.call_count == 0  # never touched the network
