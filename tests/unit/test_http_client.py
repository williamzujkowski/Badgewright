"""Tests for the guarded read-only HTTP client (respx-mocked; no live Steam)."""

from __future__ import annotations

import httpx
import pytest
import respx
from tenacity.wait import wait_none

from steam_badge_optimizer.safety import SafetyViolationError
from steam_badge_optimizer.sources.http_client import (
    FetchError,
    RateLimited,
    SafeClient,
    redact_url,
)

ALLOWED = "https://steamcommunity.com/market/priceoverview/"


def _client(**kw) -> SafeClient:
    kw.setdefault("retry_wait", wait_none())
    return SafeClient(**kw)


class TestGuard:
    def test_disallowed_host_blocked_before_network(self) -> None:
        # No respx route registered: if a request were made, respx would raise. The
        # guard must reject first.
        with respx.mock, _client() as c:
            with pytest.raises(SafetyViolationError):
                c.get("https://evil.example.com/steal")
            assert respx.calls.call_count == 0

    def test_action_route_blocked(self) -> None:
        with respx.mock, _client() as c:
            with pytest.raises(SafetyViolationError):
                c.get("https://steamcommunity.com/market/sellitem/")
            assert respx.calls.call_count == 0

    def test_no_post_method_exists(self) -> None:
        assert not hasattr(SafeClient, "post")
        assert not hasattr(SafeClient, "put")


class TestRedaction:
    @pytest.mark.parametrize(
        ("url", "expected_masked"),
        [
            ("https://api.steampowered.com/x?key=SECRET&steamid=1", "key=REDACTED"),
            ("https://h/x?steamid=1&token=abc", "token=REDACTED"),
            ("https://h/x?access_token=zzz", "access_token=REDACTED"),
        ],
    )
    def test_sensitive_params_masked(self, url: str, expected_masked: str) -> None:
        out = redact_url(url)
        assert expected_masked in out
        assert "SECRET" not in out and "abc" not in out and "zzz" not in out

    def test_non_sensitive_untouched(self) -> None:
        url = "https://steamcommunity.com/market/priceoverview/?appid=753&market_hash_name=x"
        assert redact_url(url) == url

    @respx.mock
    def test_error_message_redacts_key(self) -> None:
        url = "https://api.steampowered.com/IPlayerService/GetBadges/v1/"
        respx.get(url).mock(return_value=httpx.Response(500))
        with _client() as c, pytest.raises(FetchError) as exc:
            c.get(url, params={"key": "TOPSECRET", "steamid": "1"})
        assert "TOPSECRET" not in str(exc.value)
        assert "REDACTED" in str(exc.value)

    @respx.mock
    def test_transport_error_redacts_key(self) -> None:
        # Even if httpx's own message echoed the key-bearing URL, it must be masked.
        url = "https://api.steampowered.com/IPlayerService/GetBadges/v1/"
        respx.get(url).mock(
            side_effect=httpx.ConnectError(f"failed connecting to {url}?key=TOPSECRET")
        )
        with _client(max_attempts=1) as c, pytest.raises(FetchError) as exc:
            c.get(url, params={"key": "TOPSECRET", "steamid": "1"})
        assert "TOPSECRET" not in str(exc.value)

    @respx.mock
    def test_response_url_is_redacted(self) -> None:
        url = "https://api.steampowered.com/IPlayerService/GetBadges/v1/"
        respx.get(url).mock(return_value=httpx.Response(200, content=b"{}"))
        with _client() as c:
            resp = c.get(url, params={"key": "TOPSECRET", "steamid": "1"})
        assert "TOPSECRET" not in resp.url  # exposed URL never carries the key


class TestFetch:
    @respx.mock
    def test_success_returns_body_and_json(self) -> None:
        respx.get(ALLOWED).mock(return_value=httpx.Response(200, content=b'{"ok": true}'))
        with _client() as c:
            resp = c.get(ALLOWED)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    @respx.mock
    def test_honest_user_agent_sent(self) -> None:
        route = respx.get(ALLOWED).mock(return_value=httpx.Response(200, content=b"{}"))
        with _client() as c:
            c.get(ALLOWED)
        ua = route.calls.last.request.headers["user-agent"]
        assert ua.startswith("Badgewright/")

    @respx.mock
    def test_429_surfaces_as_rate_limited(self) -> None:
        respx.get(ALLOWED).mock(return_value=httpx.Response(429, headers={"Retry-After": "30"}))
        with _client() as c, pytest.raises(RateLimited) as exc:
            c.get(ALLOWED)
        assert exc.value.retry_after == 30.0
        # Must not have hammered past the limit.
        assert respx.calls.call_count == 1

    @respx.mock
    def test_4xx_raises_fetch_error(self) -> None:
        respx.get(ALLOWED).mock(return_value=httpx.Response(404))
        with _client() as c, pytest.raises(FetchError):
            c.get(ALLOWED)

    @respx.mock
    def test_transient_transport_error_is_retried(self) -> None:
        respx.get(ALLOWED).mock(
            side_effect=[httpx.ConnectError("boom"), httpx.Response(200, content=b"{}")]
        )
        with _client(max_attempts=3) as c:
            resp = c.get(ALLOWED)
        assert resp.status_code == 200
        assert respx.calls.call_count == 2

    @respx.mock
    def test_redirect_is_not_followed(self) -> None:
        # A 3xx would escape the guard, so it must surface as an error, never chased.
        respx.get(ALLOWED).mock(
            return_value=httpx.Response(302, headers={"Location": "https://evil.example.com/"})
        )
        with _client() as c, pytest.raises(FetchError):
            c.get(ALLOWED)
        assert respx.calls.call_count == 1  # not followed

    def test_forbidden_fragment_in_params_blocked(self) -> None:
        # A forbidden action route smuggled via query params is caught before I/O.
        with respx.mock, _client() as c:
            with pytest.raises(SafetyViolationError):
                c.get("https://steamcommunity.com/market/", params={"x": "sellitem"})
            assert respx.calls.call_count == 0

    @respx.mock
    def test_oversized_response_capped_midstream(self) -> None:
        respx.get(ALLOWED).mock(return_value=httpx.Response(200, content=b"x" * 5000))
        with _client() as c, pytest.raises(FetchError):
            c.get(ALLOWED, max_bytes=1000)

    def test_credentialed_host_resolves_to_steam(self) -> None:
        # Documents that userinfo before an allowed host stays on the allowed host
        # (last-@ wins in both the guard and httpx) — not a bypass.
        from steam_badge_optimizer.safety import is_host_allowed

        assert is_host_allowed("steamcommunity.com") is True
        assert is_host_allowed("evil.com") is False

    @respx.mock
    def test_no_cookies_persisted(self) -> None:
        respx.get(ALLOWED).mock(
            return_value=httpx.Response(200, content=b"{}", headers={"Set-Cookie": "sid=secret"})
        )
        with _client() as c:
            c.get(ALLOWED)
            # A session token must never be retained for a possible replay.
            assert len(c._client.cookies) == 0
