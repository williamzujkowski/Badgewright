"""Tests for card-name discovery (parser, mocked search, reconciliation)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
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
    def test_found_more_than_size_is_leakage_not_complete(self) -> None:
        respx.get(cd.SEARCH_URL).mock(
            return_value=httpx.Response(200, content=FIXTURE.read_bytes())
        )
        with Store.in_memory() as store, SafeClient() as c:
            result = cd.import_cards(store, c, 440, set_size=2)  # found 3 normal > 2
            assert result.complete is False
            assert any("leakage" in n for n in result.notes)

    def test_import_from_file(self) -> None:
        with Store.in_memory() as store:
            result = cd.import_from_file(store, FIXTURE, 440, set_size=3)
            assert result.complete is True
            assert len(store.cards_for_app(440)) == 3


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
