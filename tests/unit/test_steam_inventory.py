"""Tests for inventory ingestion (pure parser, mocked fetch, file import)."""

from __future__ import annotations

from pathlib import Path

import httpx
import orjson
import pytest
import respx

from steam_badge_optimizer.db import Store
from steam_badge_optimizer.sources import steam_inventory as inv
from steam_badge_optimizer.sources.http_client import SafeClient

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "inventory.json"
STEAMID = 76561197960287930


class TestParse:
    def test_fixture_counts_and_dedup(self) -> None:
        result = inv.parse_inventory_json(FIXTURE.read_bytes())
        # Heavy (x3, normal) + Pyro (x1, foil); background ignored; orphan asset skipped.
        assert len(result.cards) == 2
        assert result.skipped == 1
        assert result.total_assets == 5
        by_name = {c.inventory.market_hash_name: c for c in result.cards}
        assert by_name["440-Heavy"].inventory.quantity == 3  # summed across assets
        assert by_name["440-Heavy"].inventory.is_foil is False
        assert by_name["440-Heavy"].inventory.appid == 440  # game appid, not 753

    def test_foil_detected_by_tag_not_locale(self) -> None:
        # The Pyro description's type is German ("Karte mit Sammelbild") — foil status
        # must come from the cardborder tag, not an English substring.
        result = inv.parse_inventory_json(FIXTURE.read_bytes())
        pyro = next(c for c in result.cards if c.inventory.market_hash_name == "440-Pyro")
        assert pyro.inventory.is_foil is True
        assert pyro.card.tradable is False  # tradable:0 in fixture

    @pytest.mark.parametrize("bad", [b"not json", b"[1,2,3]", b'"str"'])
    def test_bad_envelope_fails_loud(self, bad: bytes) -> None:
        with pytest.raises(inv.InventoryParseError):
            inv.parse_inventory_json(bad)

    def test_empty_inventory_is_ok(self) -> None:
        result = inv.parse_inventory_json(b'{"assets": [], "descriptions": []}')
        assert result.cards == []
        assert result.skipped == 0


class TestFileImport:
    def test_import_persists_cards_and_inventory(self) -> None:
        with Store.in_memory() as store:
            result = inv.import_from_file(store, FIXTURE)
            assert len(result.cards) == 2
            assert store.list_card_items()  # cards discovered for the price fetcher
            q = store.conn.execute(
                "SELECT quantity FROM user_card_inventory WHERE market_hash_name='440-Heavy'"
            ).fetchone()[0]
            assert q == 3
            assert store.source_count() == 1  # provenance recorded

    def test_missing_file_errors(self, tmp_path) -> None:
        with Store.in_memory() as store, pytest.raises(inv.InventoryParseError):
            inv.import_from_file(store, tmp_path / "nope.json")


class TestFetch:
    def test_invalid_steamid_rejected(self) -> None:
        with SafeClient() as c, pytest.raises(ValueError):
            inv.fetch_inventory(c, 123)

    @respx.mock
    def test_private_inventory_raises_typed_error(self) -> None:
        respx.get(inv.INVENTORY_URL.format(steamid=STEAMID)).mock(return_value=httpx.Response(403))
        with SafeClient() as c, pytest.raises(inv.PrivateInventoryError):
            inv.fetch_inventory(c, STEAMID)

    @respx.mock
    def test_pagination_aggregates_pages(self) -> None:
        url = inv.INVENTORY_URL.format(steamid=STEAMID)
        page1 = {
            "assets": [{"classid": "c1", "instanceid": "i1", "amount": "1"}],
            "descriptions": [
                {
                    "classid": "c1",
                    "instanceid": "i1",
                    "market_hash_name": "440-A",
                    "market_fee_app": 440,
                    "type": "Trading Card",
                    "tags": [{"category": "cardborder", "internal_name": "cardborder_0"}],
                }
            ],
            "more_items": 1,
            "last_assetid": "999",
        }
        page2 = {
            "assets": [{"classid": "c2", "instanceid": "i2", "amount": "1"}],
            "descriptions": [
                {
                    "classid": "c2",
                    "instanceid": "i2",
                    "market_hash_name": "440-B",
                    "market_fee_app": 440,
                    "type": "Trading Card",
                    "tags": [{"category": "cardborder", "internal_name": "cardborder_0"}],
                }
            ],
        }
        respx.get(url).mock(
            side_effect=[
                httpx.Response(200, content=orjson.dumps(page1)),
                httpx.Response(200, content=orjson.dumps(page2)),
            ]
        )
        with SafeClient() as c:
            raw = inv.fetch_inventory(c, STEAMID, max_pages=5)
        result = inv.parse_inventory_json(raw)
        assert {c.inventory.market_hash_name for c in result.cards} == {"440-A", "440-B"}
        assert respx.calls.call_count == 2

    @respx.mock
    def test_max_pages_bounds_requests(self) -> None:
        url = inv.INVENTORY_URL.format(steamid=STEAMID)
        endless = {
            "assets": [],
            "descriptions": [],
            "more_items": 1,
            "last_assetid": "1",
        }
        respx.get(url).mock(return_value=httpx.Response(200, content=orjson.dumps(endless)))
        with SafeClient() as c:
            inv.fetch_inventory(c, STEAMID, max_pages=3)
        assert respx.calls.call_count == 3  # bounded, did not loop forever
