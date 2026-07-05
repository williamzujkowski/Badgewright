"""Tests for SteamID64 resolution (offline forms + mocked vanity lookup)."""

from __future__ import annotations

import httpx
import pytest
import respx

from steam_badge_optimizer.sources.http_client import SafeClient
from steam_badge_optimizer.sources.steamid import (
    SteamIdError,
    parse_offline,
    resolve_steamid,
)

VALID_ID64 = 76561197960287930  # a plausible individual SteamID64


class TestOffline:
    def test_raw_steamid64(self) -> None:
        assert parse_offline(str(VALID_ID64)) == VALID_ID64

    def test_profiles_url(self) -> None:
        assert parse_offline(f"https://steamcommunity.com/profiles/{VALID_ID64}") == VALID_ID64
        assert parse_offline(f"steamcommunity.com/profiles/{VALID_ID64}/") == VALID_ID64

    def test_vanity_returns_none_offline(self) -> None:
        assert parse_offline("https://steamcommunity.com/id/gabelogannewell") is None
        assert parse_offline("gabelogannewell") is None

    @pytest.mark.parametrize("bad", ["123", "76561197960265727", "99999999999999999", "notanid"])
    def test_out_of_range_or_bad_not_parsed_as_id64(self, bad: str) -> None:
        # Either None (looks like a vanity) or a range error — never a wrong int.
        try:
            result = parse_offline(bad)
        except SteamIdError:
            return
        assert result is None or result >= 76561197960265728


class TestResolve:
    def test_offline_forms_need_no_client(self) -> None:
        assert resolve_steamid(str(VALID_ID64)) == VALID_ID64

    def test_vanity_without_client_errors(self) -> None:
        with pytest.raises(SteamIdError):
            resolve_steamid("gabelogannewell")

    @respx.mock
    def test_vanity_resolved_via_xml(self) -> None:
        route = respx.get("https://steamcommunity.com/id/gabelogannewell").mock(
            return_value=httpx.Response(
                200, content=f"<profile><steamID64>{VALID_ID64}</steamID64></profile>".encode()
            )
        )
        with SafeClient() as c:
            assert resolve_steamid("gabelogannewell", c) == VALID_ID64
        assert "xml=1" in str(route.calls.last.request.url)

    @respx.mock
    def test_vanity_not_found(self) -> None:
        respx.get("https://steamcommunity.com/id/nope").mock(
            return_value=httpx.Response(
                200, content=b"<response><error>not found</error></response>"
            )
        )
        with SafeClient() as c, pytest.raises(SteamIdError):
            resolve_steamid("nope", c)

    def test_hostile_vanity_rejected_before_network(self) -> None:
        # Path-traversal / injection attempts never reach the client.
        with SafeClient() as c:
            for bad in ["../../etc/passwd", "a/b", "sell item", "x" * 100]:
                with pytest.raises(SteamIdError):
                    resolve_steamid(bad, c)
