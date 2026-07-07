"""Tests for the card goo/gem-value source + cache (#101-i)."""

from __future__ import annotations

import httpx
import orjson
import pytest
import respx

from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import Card, CardGooValue
from steam_badge_optimizer.sources.goo_value import (
    GOO_VALUE_URL,
    _parse_goo_params,
    fetch_card_goo,
    fetch_goo_value,
    refresh_goo_values,
)
from steam_badge_optimizer.sources.http_client import RateLimited, SafeClient

RENDER_RE = r"https://steamcommunity\.com/market/listings/753/.*/render/"


def _render(appid: int, item_type: int, border_color: int) -> httpx.Response:
    body = {
        "success": True,
        "assets": {
            "753": {
                "6": {
                    "12345": {
                        "appid": 753,
                        "owner_actions": [
                            {"name": "View badge progress", "link": "javascript:ViewBadge()"},
                            {
                                "name": "Turn into Gems...",
                                "link": (
                                    "javascript:GetGooValue( '%contextid%', '%assetid%', "
                                    f"{appid}, {item_type}, {border_color} )"
                                ),
                            },
                        ],
                    }
                }
            }
        },
    }
    return httpx.Response(200, content=orjson.dumps(body))


def _goo(value: str | int, *, success: object = 1) -> httpx.Response:
    return httpx.Response(200, content=orjson.dumps({"success": success, "goo_value": value}))


class TestParseGooParams:
    def test_extracts_item_type_and_border(self) -> None:
        data = orjson.loads(_render(570, 4, 1).content)
        assert _parse_goo_params(data) == (4, 1)

    def test_missing_owner_actions_returns_none(self) -> None:
        data = {"assets": {"753": {"6": {"1": {"appid": 753}}}}}
        assert _parse_goo_params(data) is None

    @pytest.mark.parametrize("bad", [None, {}, {"assets": []}, {"assets": {"753": "x"}}])
    def test_malformed_returns_none(self, bad: object) -> None:
        assert _parse_goo_params(bad) is None


class TestFetchGooValue:
    @respx.mock
    def test_parses_goo_value(self) -> None:
        respx.get(GOO_VALUE_URL).mock(return_value=_goo("25"))
        with SafeClient(min_interval_s=0) as c:
            assert fetch_goo_value(c, 570, 4, 0) == 25

    @respx.mock
    def test_success_false_returns_none(self) -> None:
        respx.get(GOO_VALUE_URL).mock(return_value=_goo("25", success=0))
        with SafeClient(min_interval_s=0) as c:
            assert fetch_goo_value(c, 570, 4, 0) is None

    @respx.mock
    def test_non_numeric_goo_returns_none(self) -> None:
        respx.get(GOO_VALUE_URL).mock(return_value=_goo("n/a"))
        with SafeClient(min_interval_s=0) as c:
            assert fetch_goo_value(c, 570, 4, 0) is None


class TestFetchCardGoo:
    @respx.mock
    def test_scrapes_then_fetches(self) -> None:
        respx.get(url__regex=RENDER_RE).mock(return_value=_render(570, 4, 1))
        respx.get(GOO_VALUE_URL).mock(return_value=_goo("250"))
        card = Card(appid=570, market_hash_name="570-Razor (Foil)", is_foil=True)
        with SafeClient(min_interval_s=0) as c:
            goo = fetch_card_goo(c, card)
        assert goo == CardGooValue(
            appid=570,
            market_hash_name="570-Razor (Foil)",
            item_type=4,
            border_color=1,
            goo_value=250,
        )

    @respx.mock
    def test_slash_in_name_is_encoded_into_path(self) -> None:
        # A "/" in the card name must be percent-encoded so it can't alter the URL path.
        route = respx.get(url__regex=RENDER_RE).mock(return_value=_render(570, 4, 0))
        respx.get(GOO_VALUE_URL).mock(return_value=_goo("25"))
        with SafeClient(min_interval_s=0) as c:
            fetch_card_goo(c, Card(appid=570, market_hash_name="570/evil", is_foil=False))
        # raw_path is the wire form; the "/" must be percent-encoded (no path breakout).
        raw_path = route.calls.last.request.url.raw_path
        assert b"570%2Fevil" in raw_path and b"/render/" in raw_path

    def test_forbidden_fragment_in_name_skips_not_crashes(self, tmp_path) -> None:
        # A name tripping the safety boundary must skip the card (failed), never crash.
        cards = [Card(appid=570, market_hash_name="570-consumeitem card", is_foil=True)]
        with Store(tmp_path / "t.sqlite3") as store, SafeClient(min_interval_s=0) as c:
            res = refresh_goo_values(store, c, cards)
        assert res.failed == 1 and res.fetched == 0

    @respx.mock
    def test_none_when_item_type_unscrapeable(self) -> None:
        respx.get(url__regex=RENDER_RE).mock(
            return_value=httpx.Response(200, content=orjson.dumps({"assets": {}}))
        )
        card = Card(appid=570, market_hash_name="570-Razor", is_foil=False)
        with SafeClient(min_interval_s=0) as c:
            assert fetch_card_goo(c, card) is None


class TestRefreshGooValues:
    def _cards(self, n: int) -> list[Card]:
        return [
            Card(appid=570, market_hash_name=f"570-C{i} (Foil)", is_foil=True) for i in range(n)
        ]

    @respx.mock
    def test_fetches_caches_and_roundtrips(self, tmp_path) -> None:
        respx.get(url__regex=RENDER_RE).mock(return_value=_render(570, 4, 1))
        respx.get(GOO_VALUE_URL).mock(return_value=_goo("250"))
        with Store(tmp_path / "t.sqlite3") as store, SafeClient(min_interval_s=0) as c:
            res = refresh_goo_values(store, c, self._cards(2))
            assert (res.fetched, res.skipped_cached, res.failed) == (2, 0, 0)
            cached = store.goo_value_for(570, "570-C0 (Foil)")
            assert cached is not None and cached.goo_value == 250 and cached.border_color == 1

    @respx.mock
    def test_skips_cached_unless_forced(self, tmp_path) -> None:
        respx.get(url__regex=RENDER_RE).mock(return_value=_render(570, 4, 1))
        respx.get(GOO_VALUE_URL).mock(return_value=_goo("250"))
        with Store(tmp_path / "t.sqlite3") as store, SafeClient(min_interval_s=0) as c:
            refresh_goo_values(store, c, self._cards(1))
            again = refresh_goo_values(store, c, self._cards(1))
            assert again.skipped_cached == 1 and again.fetched == 0
            forced = refresh_goo_values(store, c, self._cards(1), force=True)
            assert forced.fetched == 1

    @respx.mock
    def test_max_cards_caps_fetches(self, tmp_path) -> None:
        respx.get(url__regex=RENDER_RE).mock(return_value=_render(570, 4, 1))
        respx.get(GOO_VALUE_URL).mock(return_value=_goo("250"))
        with Store(tmp_path / "t.sqlite3") as store, SafeClient(min_interval_s=0) as c:
            res = refresh_goo_values(store, c, self._cards(5), max_cards=2)
        assert res.fetched == 2

    @respx.mock
    def test_rate_limit_propagates(self, tmp_path) -> None:
        respx.get(url__regex=RENDER_RE).mock(return_value=httpx.Response(429))
        with (
            Store(tmp_path / "t.sqlite3") as store,
            SafeClient(min_interval_s=0) as c,
            pytest.raises(RateLimited),
        ):
            refresh_goo_values(store, c, self._cards(3))

    @respx.mock
    def test_failed_counted_when_unscrapeable(self, tmp_path) -> None:
        respx.get(url__regex=RENDER_RE).mock(
            return_value=httpx.Response(200, content=orjson.dumps({"assets": {}}))
        )
        with Store(tmp_path / "t.sqlite3") as store, SafeClient(min_interval_s=0) as c:
            res = refresh_goo_values(store, c, self._cards(2))
        assert res.failed == 2 and res.fetched == 0
