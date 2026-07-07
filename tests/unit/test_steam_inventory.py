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
        # Heavy (x3, normal) + Pyro (x1, foil); the marketable background is now retained
        # as an OTHER holding (not dropped); orphan asset skipped.
        assert len(result.cards) == 2
        assert result.skipped == 1
        assert result.total_assets == 5
        assert len(result.holdings) == 1  # the profile background, retained as OTHER
        assert result.holdings[0].market_hash_name == "440-Background"
        assert result.holdings[0].kind.value == "other"
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
    def test_max_pages_bounds_and_flags_truncation(self) -> None:
        url = inv.INVENTORY_URL.format(steamid=STEAMID)
        endless = {"assets": [], "descriptions": [], "more_items": 1, "last_assetid": "1"}
        respx.get(url).mock(return_value=httpx.Response(200, content=orjson.dumps(endless)))
        with SafeClient() as c:
            raw = inv.fetch_inventory(c, STEAMID, max_pages=3)
        assert respx.calls.call_count == 3  # bounded, did not loop forever
        assert inv.parse_inventory_json(raw).truncated is True  # partial result flagged

    @respx.mock
    def test_missing_cursor_stops_without_none_string(self) -> None:
        url = inv.INVENTORY_URL.format(steamid=STEAMID)
        # more_items truthy but no last_assetid: must stop, not send start_assetid=None.
        respx.get(url).mock(
            return_value=httpx.Response(200, content=orjson.dumps({"assets": [], "more_items": 1}))
        )
        with SafeClient() as c:
            inv.fetch_inventory(c, STEAMID, max_pages=5)
        assert respx.calls.call_count == 1
        assert "start_assetid" not in str(respx.calls.last.request.url)

    @respx.mock
    def test_non_403_http_error_propagates(self) -> None:
        from steam_badge_optimizer.sources.http_client import HTTPStatusError

        respx.get(inv.INVENTORY_URL.format(steamid=STEAMID)).mock(return_value=httpx.Response(404))
        with SafeClient() as c, pytest.raises(HTTPStatusError):
            inv.fetch_inventory(c, STEAMID)


class TestCli:
    def test_online_404_is_clean_error_not_traceback(self) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app

        runner = CliRunner()
        with respx.mock:
            respx.get(inv.INVENTORY_URL.format(steamid=STEAMID)).mock(
                return_value=httpx.Response(404)
            )
            result = runner.invoke(
                app, ["inventory", "import", "--steamid", str(STEAMID), "--online"]
            )
        assert result.exit_code == 1
        assert "Import failed" in result.output


def _inv_json(*items: dict) -> bytes:
    """Build an inventory JSON envelope from (asset-fields + description) item dicts."""
    assets, descriptions = [], []
    for i, it in enumerate(items):
        cid, iid = f"c{i}", f"i{i}"
        assets.append({"classid": cid, "instanceid": iid, "amount": str(it.pop("_amount", "1"))})
        descriptions.append({"classid": cid, "instanceid": iid, **it})
    return orjson.dumps({"assets": assets, "descriptions": descriptions})


def _tag(cat: str, internal: str) -> dict:
    return {"category": cat, "internal_name": internal}


class TestHoldings:
    def test_classifies_booster_sack_gems_and_other(self) -> None:
        from steam_badge_optimizer.models import ItemKind

        raw = _inv_json(
            {  # a normal card (stays a card, not a holding)
                "market_hash_name": "440-Heavy",
                "market_fee_app": 440,
                "type": "Trading Card",
                "marketable": 1,
                "tags": [_tag("cardborder", "cardborder_0")],
            },
            {  # booster pack (item_class_5)
                "market_hash_name": "440-Team Fortress 2 Booster Pack",
                "market_fee_app": 440,
                "type": "Booster Pack",
                "marketable": 1,
                "tags": [_tag("item_class", "item_class_5")],
                "_amount": "2",
            },
            {  # Sack of Gems
                "market_hash_name": "753-Sack of Gems",
                "type": "Steam Gems",
                "marketable": 1,
                "tags": [_tag("item_class", "item_class_6")],
            },
            {  # loose gems: REAL shape (#112) — carries mhn "753-Gems", non-marketable,
                # amount = gem count. Must classify despite name present + marketable 0.
                "market_hash_name": "753-Gems",
                "market_name": "Gems",
                "type": "Steam Gems",
                "marketable": 0,
                "tags": [_tag("item_class", "item_class_7")],
                "_amount": "5000",
            },
            {  # a profile background -> OTHER (marketable community good)
                "market_hash_name": "440-A Background",
                "market_fee_app": 440,
                "type": "Profile Background",
                "marketable": 1,
                "tags": [_tag("item_class", "item_class_3")],
            },
        )
        result = inv.parse_inventory_json(raw)
        assert len(result.cards) == 1  # the card is not a holding
        by = {h.market_hash_name: h for h in result.holdings}
        assert by["440-Team Fortress 2 Booster Pack"].kind is ItemKind.BOOSTER_PACK
        assert by["440-Team Fortress 2 Booster Pack"].appid == 440
        assert by["440-Team Fortress 2 Booster Pack"].quantity == 2
        assert by["753-Sack of Gems"].kind is ItemKind.SACK_OF_GEMS
        assert by["753-Sack of Gems"].appid == 753
        assert by["753-Gems"].kind is ItemKind.GEMS
        assert by["753-Gems"].quantity == 5000  # gem count, not copies
        assert by["440-A Background"].kind is ItemKind.OTHER

    def test_unmarketable_unclassifiable_item_skipped_not_held(self) -> None:
        raw = _inv_json(
            {  # a non-marketable emoticon with no special class -> ignored (no holding)
                "market_hash_name": "440-:emote:",
                "market_fee_app": 440,
                "type": "Emoticon",
                "marketable": 0,
                "tags": [_tag("item_class", "item_class_4")],
            },
        )
        result = inv.parse_inventory_json(raw)
        assert result.holdings == [] and result.cards == []

    def test_holdings_persist_and_roundtrip(self, tmp_path) -> None:
        from steam_badge_optimizer.models import ItemKind

        raw = _inv_json(
            {
                "market_hash_name": "753-Sack of Gems",
                "type": "Steam Gems",
                "marketable": 1,
                "tags": [_tag("item_class", "item_class_6")],
                "_amount": "3",
            },
        )
        p = tmp_path / "inv.json"
        p.write_bytes(raw)
        with Store.in_memory() as store:
            result = inv.import_from_file(store, p)  # full parse + persist path
            assert len(result.holdings) == 1
            held = store.list_item_holdings()
            assert len(held) == 1
            assert held[0].kind is ItemKind.SACK_OF_GEMS and held[0].quantity == 3
