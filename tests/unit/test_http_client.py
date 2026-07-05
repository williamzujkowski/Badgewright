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
    def test_no_cookies_persisted(self) -> None:
        respx.get(ALLOWED).mock(
            return_value=httpx.Response(200, content=b"{}", headers={"Set-Cookie": "sid=secret"})
        )
        with _client() as c:
            c.get(ALLOWED)
            # A session token must never be retained for a possible replay.
            assert len(c._client.cookies) == 0
