"""Tests for card-name discovery (parser, mocked search, reconciliation)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import orjson
import pytest
import respx

from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import (
    BadgeSet,
    MarketItem,
    Money,
    PriceSnapshot,
    SourceKind,
    SourceRecord,
)
from steam_badge_optimizer.optimize import compute_costs
from steam_badge_optimizer.sources import card_discovery as cd
from steam_badge_optimizer.sources.http_client import SafeClient

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "card_search_440.json"


class TestParse:
    def test_parses_and_flags_foil(self) -> None:
        cards = cd.parse_search_results(FIXTURE.read_bytes())
        names = {c.market_hash_name: c.is_foil for c in cards}
        assert names == {
            "440-Heavy": False,
            "440-Pyro": False,
            "440-Scout": False,
            "440-Heavy (Foil)": True,
        }

    @pytest.mark.parametrize("bad", [b"not json", b"[1,2]", b'{"success":true}'])
    def test_bad_envelope_raises(self, bad: bytes) -> None:
        with pytest.raises(cd.CardDiscoveryError):
            cd.parse_search_results(bad)


class TestDiscover:
    def test_invalid_appid_rejected(self) -> None:
        with SafeClient() as c, pytest.raises(ValueError):
            cd.discover_cards(c, 0)

    @respx.mock
    def test_discovers_via_search(self) -> None:
        respx.get(cd.SEARCH_URL).mock(
            return_value=httpx.Response(200, content=FIXTURE.read_bytes())
        )
        with SafeClient() as c:
            cards = cd.discover_cards(c, 440)
        assert {c.market_hash_name for c in cards} == {
            "440-Heavy",
            "440-Pyro",
            "440-Scout",
            "440-Heavy (Foil)",
        }
        assert respx.calls.call_count == 1  # total_count 4 < page size -> one page

    @respx.mock
    def test_multi_page_pagination_aggregates_and_stops(self) -> None:
        def page(names: list[str]) -> httpx.Response:
            return httpx.Response(
                200,
                content=orjson.dumps(
                    {
                        "success": True,
                        "total_count": 4,
                        "results": [
                            {"hash_name": n, "asset_description": {"type": "Trading Card"}}
                            for n in names
                        ],
                    }
                ),
            )

        respx.get(cd.SEARCH_URL).mock(
            side_effect=[page(["440-A", "440-B"]), page(["440-C", "440-D"])]
        )
        with SafeClient() as c:
            cards = cd.discover_cards(c, 440, page_size=2)
        assert {c.market_hash_name for c in cards} == {"440-A", "440-B", "440-C", "440-D"}
        assert respx.calls.call_count == 2  # start 0 -> 2 -> 4 >= total_count 4, stop

    @respx.mock
    def test_appid_is_in_category_param(self) -> None:
        route = respx.get(cd.SEARCH_URL).mock(
            return_value=httpx.Response(200, content=FIXTURE.read_bytes())
        )
        with SafeClient() as c:
            cd.discover_cards(c, 440)
        url = str(route.calls.last.request.url)
        assert "tag_app_440" in url and "tag_item_class_2" in url


class TestReconcileAndPersist:
    @respx.mock
    def test_complete_when_count_matches_set_size(self) -> None:
        respx.get(cd.SEARCH_URL).mock(
            return_value=httpx.Response(200, content=FIXTURE.read_bytes())
        )
        with Store.in_memory() as store, SafeClient() as c:
            result = cd.import_cards(store, c, 440, set_size=3)
            assert result.complete is True
            assert result.normal_count == 3
            # Persisted: 3 normal cards discovered (foil stored separately, is_foil=1).
            assert len(store.cards_for_app(440, include_foil=False)) == 3
            assert store.source_count() == 1

    @respx.mock
    def test_incomplete_when_fewer_than_set_size(self) -> None:
        respx.get(cd.SEARCH_URL).mock(
            return_value=httpx.Response(200, content=FIXTURE.read_bytes())
        )
        with Store.in_memory() as store, SafeClient() as c:
            result = cd.import_cards(store, c, 440, set_size=5)  # catalog says 5, found 3
            assert result.complete is False
            assert any("3 of 5" in n for n in result.notes)

    @respx.mock
    def test_found_more_than_catalog_ratchets_set_size_up(self) -> None:
        # #79: a COMPLETE market enumeration finding more normal cards than the (stale)
        # catalog corrects the stored set size UPWARD to market truth.
        respx.get(cd.SEARCH_URL).mock(
            return_value=httpx.Response(200, content=FIXTURE.read_bytes())
        )
        with Store.in_memory() as store, SafeClient() as c:
            result = cd.import_cards(store, c, 440, set_size=2)  # catalog 2, market 3
            assert result.complete is True
            assert result.normal_count == 3
            stored = {b.appid: b.set_size for b in store.list_badge_sets()}
            assert stored[440] == 3  # corrected upward and persisted


class TestPagination:
    @respx.mock
    def test_enumerates_across_pages_by_actual_count(self) -> None:
        # The endpoint caps a page below `count`; enumeration must advance by the ACTUAL
        # returned count (else a >1-page set is under-discovered) and reach total_count.
        def _entry(name: str) -> dict:
            return {
                "hash_name": name,
                "sell_price": 5,
                "sell_listings": 10,
                "asset_description": {"type": "Trading Card"},
            }

        def handler(request: httpx.Request) -> httpx.Response:
            start = int(request.url.params.get("start", 0))
            if start == 0:
                cards = [_entry(f"440-C{i}") for i in range(4)]
            elif start == 4:
                cards = [_entry(f"440-C{i}") for i in range(4, 6)]
            else:
                cards = []
            return httpx.Response(200, content=orjson.dumps({"total_count": 6, "results": cards}))

        route = respx.get(cd.SEARCH_URL).mock(side_effect=handler)
        with SafeClient(min_interval_s=0) as c:
            cards, complete = cd._enumerate_cards(c, 440)
        assert len(cards) == 6  # all cards across both pages
        assert complete is True
        starts = [int(call.request.url.params.get("start", 0)) for call in route.calls]
        assert starts == [0, 4]  # advanced by ACTUAL count (4), not page_size

    @respx.mock
    def test_max_pages_truncation_is_not_complete(self) -> None:
        # A set bigger than max_pages can fetch must report complete=False (never ratchet).
        def _entry(name: str) -> dict:
            return {"hash_name": name, "asset_description": {"type": "Trading Card"}}

        def handler(request: httpx.Request) -> httpx.Response:
            start = int(request.url.params.get("start", 0))
            cards = [_entry(f"440-C{start}")]  # 1 card/page, total_count 999 (never reached)
            return httpx.Response(200, content=orjson.dumps({"total_count": 999, "results": cards}))

        respx.get(cd.SEARCH_URL).mock(side_effect=handler)
        with SafeClient(min_interval_s=0) as c:
            cards, complete = cd._enumerate_cards(c, 440, max_pages=3)
        assert complete is False  # exhausted the page budget, did NOT reach total_count
        assert len(cards) == 3  # only what the budget allowed


class TestReconcileSetSize:
    def test_ratchets_up_on_complete_market_undercount(self) -> None:
        assert cd.reconcile_set_size(6, 7, True) == (7, True)  # Amber Throne 6 -> 7

    def test_never_lowers_below_catalog_floor(self) -> None:
        # Market undercount (a card with no listings) on a complete page must NOT lower it.
        assert cd.reconcile_set_size(6, 5, True) == (6, False)

    def test_no_change_when_incomplete_even_if_more_found(self) -> None:
        # A truncated / page-capped enumeration is not authoritative -> never overwrite.
        assert cd.reconcile_set_size(6, 7, False) == (6, False)

    def test_no_change_when_equal(self) -> None:
        assert cd.reconcile_set_size(6, 6, True) == (6, False)

    @respx.mock
    def test_foils_do_not_inflate_the_ratchet(self) -> None:
        # A page with 2 normal + several foils must ratchet on the NORMAL count only, not
        # let foils fake a larger set.
        def _entry(name: str, foil: bool) -> dict:
            t = "Foil Trading Card" if foil else "Trading Card"
            return {"hash_name": name, "sell_price": 5, "asset_description": {"type": t}}

        body = orjson.dumps(
            {
                "total_count": 5,
                "results": [
                    _entry("440-A", False),
                    _entry("440-B", False),
                    _entry("440-A (Foil)", True),
                    _entry("440-B (Foil)", True),
                    _entry("440-C (Foil)", True),
                ],
            }
        )
        respx.get(cd.SEARCH_URL).mock(return_value=httpx.Response(200, content=body))
        with Store.in_memory() as store, SafeClient(min_interval_s=0) as c:
            store.upsert_badge_set(BadgeSet(appid=440, set_size=2))  # catalog
            cd.import_cards(store, c, 440, set_size=2)  # 2 normal + 3 foil discovered
            stored = {b.appid: b.set_size for b in store.list_badge_sets()}
            assert stored[440] == 2  # foils excluded -> normal count == catalog -> no ratchet

    def test_import_from_file(self) -> None:
        with Store.in_memory() as store:
            result = cd.import_from_file(store, FIXTURE, 440, set_size=3)
            assert result.complete is True
            assert len(store.cards_for_app(440)) == 3

    def test_rerun_replaces_not_accumulates(self, tmp_path) -> None:
        # HIGH regression: two runs with different cards must NOT accumulate to a
        # false complete. The second (authoritative) run replaces the first.
        run2 = tmp_path / "run2.json"
        run2.write_bytes(
            orjson.dumps(
                {
                    "success": True,
                    "total_count": 1,
                    "results": [
                        {"hash_name": "440-Xyz", "asset_description": {"type": "Trading Card"}}
                    ],
                }
            )
        )
        with Store.in_memory() as store:
            cd.import_from_file(store, FIXTURE, 440, set_size=3)  # finds 3
            cd.import_from_file(store, run2, 440, set_size=3)  # now only 1
            names = {c.market_hash_name for c in store.cards_for_app(440)}
            assert names == {"440-Xyz"}  # replaced, not {A,B,C,Xyz}

    def test_foreign_app_card_dropped_no_false_complete(self, tmp_path) -> None:
        # HIGH regression: a leaked wrong-app card must not count toward the set.
        contaminated = tmp_path / "c.json"
        contaminated.write_bytes(
            orjson.dumps(
                {
                    "success": True,
                    "total_count": 3,
                    "results": [
                        {"hash_name": "440-Heavy", "asset_description": {"type": "Trading Card"}},
                        {"hash_name": "440-Pyro", "asset_description": {"type": "Trading Card"}},
                        {"hash_name": "570-Axe", "asset_description": {"type": "Trading Card"}},
                    ],
                }
            )
        )
        with Store.in_memory() as store:
            result = cd.import_from_file(store, contaminated, 440, set_size=3)
            assert result.complete is False  # only 2 belong to 440, not 3
            assert any("not belonging to appid 440" in n for n in result.notes)
            assert "570-Axe" not in {c.market_hash_name for c in store.cards_for_app(440)}

    def test_game_title_containing_foil_not_misclassified(self, tmp_path) -> None:
        f = tmp_path / "foilball.json"
        f.write_bytes(
            orjson.dumps(
                {
                    "success": True,
                    "total_count": 1,
                    "results": [
                        {
                            "hash_name": "99-Card",
                            "asset_description": {"type": "Foilball Trading Card"},
                        }
                    ],
                }
            )
        )
        cards = cd.parse_search_results(f.read_bytes())
        assert cards[0].is_foil is False  # "Foilball" is not a foil card


class TestDiscoveryUnblocksOptimizer:
    def test_discovered_and_priced_badge_becomes_complete(self) -> None:
        # The whole point: discovery + pricing turns an "incomplete" badge complete.
        with Store.in_memory() as store:
            store.upsert_badge_set(BadgeSet(appid=440, set_size=3))
            # Before discovery: no cards known -> incomplete.
            assert compute_costs(store).badges[0].complete is False
            cd.import_from_file(store, FIXTURE, 440, set_size=3)
            for name in ("440-Heavy", "440-Pyro", "440-Scout"):
                store.add_price_snapshot(
                    PriceSnapshot(
                        item=MarketItem(appid=440, market_hash_name=name),
                        lowest=Money(10, "USD"),
                        volume=100,
                        source=SourceRecord(
                            kind=SourceKind.STEAM_MARKET,
                            url="https://steamcommunity.com/market/priceoverview/",
                            fetched_at=datetime.now(UTC),
                            parser_version="1",
                            raw_sha256=SourceRecord.sha256_of(name.encode()),
                            cache_ttl_seconds=86400,
                        ),
                    )
                )
            badge = compute_costs(store).badges[0]
            assert badge.complete is True
            assert badge.known_cards == 3
