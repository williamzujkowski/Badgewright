"""The load-bearing tests for Badgewright's read-only boundary.

If any of these regress, the tool can act on a Steam account. Treat a failure
here as a release blocker, not a flaky test.
"""

from __future__ import annotations

import pytest

from steam_badge_optimizer.safety import (
    ALLOWED_METHODS,
    SafetyViolationError,
    assert_safe_request,
    is_host_allowed,
)


class TestMethodAllowlist:
    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get", "Head"])
    def test_read_methods_allowed(self, method: str) -> None:
        # Should not raise.
        assert_safe_request(method, "https://steamcommunity.com/market/priceoverview/")

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post"])
    def test_mutating_methods_refused(self, method: str) -> None:
        with pytest.raises(SafetyViolationError):
            assert_safe_request(method, "https://steamcommunity.com/market/priceoverview/")

    def test_allowlist_contains_only_safe_verbs(self) -> None:
        assert {"GET", "HEAD", "OPTIONS"} >= ALLOWED_METHODS
        assert "POST" not in ALLOWED_METHODS


class TestHostAllowlist:
    @pytest.mark.parametrize(
        "url",
        [
            "https://steamcommunity.com/market/priceoverview/",
            "https://api.steampowered.com/IPlayerService/GetBadges/v1/",
            "https://store.steampowered.com/app/440/",
            "https://raw.githubusercontent.com/foo/steam-badges-db/main/badges.json",
        ],
    )
    def test_allowed_hosts_pass(self, url: str) -> None:
        assert_safe_request("GET", url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example.com/steal",
            "https://steamcommunity.com.evil.com/market/",  # suffix spoof
            "https://not-steamcommunity.com/market/",
        ],
    )
    def test_disallowed_hosts_refused(self, url: str) -> None:
        with pytest.raises(SafetyViolationError):
            assert_safe_request("GET", url)

    def test_subdomain_of_allowed_host_passes(self) -> None:
        assert is_host_allowed("steamcdn-a.akamaihd.net") is False
        assert is_host_allowed("foo.steamcommunity.com") is True

    def test_non_http_scheme_refused(self) -> None:
        with pytest.raises(SafetyViolationError):
            assert_safe_request("GET", "file:///etc/passwd")
        with pytest.raises(SafetyViolationError):
            assert_safe_request("GET", "steam://buy/440")


class TestForbiddenRouteTripwire:
    @pytest.mark.parametrize(
        "url",
        [
            "https://steamcommunity.com/market/sellitem/",
            "https://steamcommunity.com/market/createbuyorder/",
            "https://steamcommunity.com/market/cancelbuyorder/",
            "https://steamcommunity.com/market/removelisting/12345",
            "https://steamcommunity.com/tradeoffer/ajaxcraftbadge/",
            "https://steamcommunity.com/tradeoffer/new/?partner=1",
            "https://steamcommunity.com/trade/1234",
        ],
    )
    def test_action_routes_refused_even_on_get(self, url: str) -> None:
        # Belt-and-suspenders: even a GET to a known action route fails closed.
        with pytest.raises(SafetyViolationError):
            assert_safe_request("GET", url)

    def test_card_name_containing_trade_does_not_false_positive(self) -> None:
        # A legitimate price fetch for a card named "Trade Federation" must not be
        # blocked by the trade tripwire (the fragment is path-anchored).
        assert_safe_request(
            "GET",
            "https://steamcommunity.com/market/priceoverview/"
            "?appid=753&market_hash_name=Trade+Federation",
        )
