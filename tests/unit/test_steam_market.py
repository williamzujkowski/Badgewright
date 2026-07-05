"""Tests for the priceoverview price fetcher (respx-mocked; no live Steam)."""

from __future__ import annotations

import httpx
import orjson
import pytest
import respx

from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import MarketItem
from steam_badge_optimizer.sources import steam_market as sm
from steam_badge_optimizer.sources.http_client import RateLimited, SafeClient

ITEM = MarketItem(appid=440, market_hash_name="440-Heavy")


def _overview(**fields) -> httpx.Response:
    return httpx.Response(200, content=orjson.dumps({"success": True, **fields}))


def _route():
    return respx.get(sm.PRICEOVERVIEW_URL)


class TestFetchPrice:
    @respx.mock
    def test_parses_price_and_volume(self) -> None:
        _route().mock(
            return_value=_overview(lowest_price="$0.03", median_price="$0.05", volume="1,234")
        )
        with SafeClient() as c:
            snap = sm.fetch_price(c, ITEM, "USD")
        assert snap is not None
        assert snap.lowest.cents == 3
        assert snap.median.cents == 5
        assert snap.volume == 1234
        assert snap.source.raw_sha256  # provenance attached

    @respx.mock
    def test_params_are_encoded_not_concatenated(self) -> None:
        route = _route().mock(return_value=_overview(lowest_price="$1.00"))
        item = MarketItem(appid=570, market_hash_name="Inscribed Blade & Bow")
        with SafeClient() as c:
            sm.fetch_price(c, item, "EUR")
        req = route.calls.last.request
        # market_hash_name is URL-encoded in the query (& -> %26), never raw.
        url = str(req.url)
        assert "%26" in url and " & " not in url
        assert "currency=3" in url  # EUR id
        # Cards are priced under the community appid 753, NOT the game appid (570).
        assert "appid=753" in url and "appid=570" not in url

    @respx.mock
    def test_success_false_returns_none(self) -> None:
        _route().mock(return_value=httpx.Response(200, content=orjson.dumps({"success": False})))
        with SafeClient() as c:
            assert sm.fetch_price(c, ITEM) is None

    @respx.mock
    def test_no_price_fields_returns_none_not_zero(self) -> None:
        # success but no lowest/median -> None, never a poisoned zero snapshot.
        _route().mock(return_value=_overview(volume="5"))
        with SafeClient() as c:
            assert sm.fetch_price(c, ITEM) is None

    @respx.mock
    def test_http_error_degrades_to_none(self) -> None:
        _route().mock(return_value=httpx.Response(404))
        with SafeClient() as c:
            assert sm.fetch_price(c, ITEM) is None

    @respx.mock
    def test_malformed_json_returns_none(self) -> None:
        _route().mock(return_value=httpx.Response(200, content=b"not json at all"))
        with SafeClient() as c:
            assert sm.fetch_price(c, ITEM) is None

    @respx.mock
    def test_garbage_price_string_returns_none(self) -> None:
        _route().mock(return_value=_overview(lowest_price="Free", median_price="N/A"))
        with SafeClient() as c:
            assert sm.fetch_price(c, ITEM) is None

    @respx.mock
    def test_median_only_persists(self) -> None:
        _route().mock(return_value=_overview(median_price="$0.05"))
        with SafeClient() as c:
            snap = sm.fetch_price(c, ITEM)
        assert snap is not None
        assert snap.lowest is None
        assert snap.median.cents == 5

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1,234", 1234),
            ("1.234", 1234),
            ("", None),
            (None, None),
            ("abc", None),
            (1234, 1234),
            (12.0, 12),
            (True, None),
            (-5, None),
        ],
    )
    def test_parse_volume_edges(self, value: object, expected: int | None) -> None:
        assert sm._parse_volume(value) == expected

    @respx.mock
    def test_429_propagates(self) -> None:
        _route().mock(return_value=httpx.Response(429))
        with SafeClient() as c, pytest.raises(RateLimited):
            sm.fetch_price(c, ITEM)

    def test_unknown_currency_raises(self) -> None:
        with SafeClient() as c, pytest.raises(ValueError):
            sm.fetch_price(c, ITEM, "XYZ")


class TestRefreshPrices:
    @respx.mock
    def test_persists_and_skips_fresh_cache(self) -> None:
        _route().mock(return_value=_overview(lowest_price="$0.03", volume="10"))
        with Store.in_memory() as store, SafeClient() as c:
            r1 = sm.refresh_prices(store, c, [ITEM], "USD")
            assert (r1.fetched, r1.skipped_cached, r1.failed) == (1, 0, 0)
            # Second run: the snapshot is fresh (within TTL) so it is reused, not refetched.
            r2 = sm.refresh_prices(store, c, [ITEM], "USD")
            assert (r2.fetched, r2.skipped_cached) == (0, 1)
            assert respx.calls.call_count == 1

    @respx.mock
    def test_force_refetches(self) -> None:
        _route().mock(return_value=_overview(lowest_price="$0.03", volume="10"))
        with Store.in_memory() as store, SafeClient() as c:
            sm.refresh_prices(store, c, [ITEM], "USD")
            r = sm.refresh_prices(store, c, [ITEM], "USD", force=True)
            assert r.fetched == 1

    @respx.mock
    def test_failed_lookup_counted_not_fatal(self) -> None:
        _route().mock(return_value=httpx.Response(404))
        with Store.in_memory() as store, SafeClient() as c:
            r = sm.refresh_prices(store, c, [ITEM], "USD")
            assert (r.fetched, r.failed) == (0, 1)
            assert store.latest_price(440, "440-Heavy") is None

    @respx.mock
    def test_429_midloop_stops_after_partial_persistence(self) -> None:
        # First item OK, second returns 429: the first is persisted, the loop stops.
        _route().mock(
            side_effect=[_overview(lowest_price="$0.03", volume="10"), httpx.Response(429)]
        )
        item_a = MarketItem(appid=440, market_hash_name="440-A")
        item_b = MarketItem(appid=440, market_hash_name="440-B")
        item_c = MarketItem(appid=440, market_hash_name="440-C")
        with Store.in_memory() as store, SafeClient() as c:
            with pytest.raises(RateLimited):
                sm.refresh_prices(store, c, [item_a, item_b, item_c], "USD")
            assert store.latest_price(440, "440-A") is not None  # A persisted
            assert store.latest_price(440, "440-B") is None  # B/C never stored
            assert store.latest_price(440, "440-C") is None
            assert respx.calls.call_count == 2  # C never attempted
