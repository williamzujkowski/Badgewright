"""Tests for badge-progress ingestion (parser, API, file, key-redaction)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import BadgeSet
from steam_badge_optimizer.optimize import compute_costs
from steam_badge_optimizer.sources import badge_progress as bp
from steam_badge_optimizer.sources.http_client import SafeClient

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "getbadges.json"
STEAMID = 76561197960287930
API_KEY = "SECRETKEY123"


class TestParse:
    def test_parses_normal_game_badges_only(self) -> None:
        rows = bp.parse_badges_response(FIXTURE.read_bytes())
        levels = {r.appid: r.level for r in rows}
        # community badge (no appid) and foil (border_color 1) skipped; 99 clamped to 5.
        assert levels == {220: 3, 440: 5, 730: 5}
        assert all(r.is_foil is False for r in rows)

    def test_no_badges_is_empty(self) -> None:
        assert bp.parse_badges_response(b'{"response": {}}') == []

    @pytest.mark.parametrize("bad", [b"not json", b"[1]", b'{"response": []}'])
    def test_bad_envelope_raises(self, bad: bytes) -> None:
        with pytest.raises(bp.BadgeProgressError):
            bp.parse_badges_response(bad)


class TestFileImport:
    def test_persists_levels(self) -> None:
        with Store.in_memory() as store:
            result = bp.import_from_file(store, FIXTURE)
            assert result.imported == 3
            assert store.get_badge_progress(220).level == 3
            assert store.get_badge_progress(440).level == 5

    def test_progress_makes_plan_accurate(self) -> None:
        # The whole point: real level 3 -> crafts_needed 2, not the assumed 5.
        with Store.in_memory() as store:
            store.upsert_badge_set(BadgeSet(appid=220, set_size=1))
            assert compute_costs(store, target_level=5).badges[0].crafts_needed == 5  # assumed 0
            bp.import_from_file(store, FIXTURE)
            badge = compute_costs(store, target_level=5).badges[0]
            assert badge.crafts_needed == 2  # 5 - level 3
            assert badge.current_level == 3


class TestApiImport:
    @respx.mock
    def test_api_persists_and_hides_key_in_provenance(self) -> None:
        route = respx.get(bp.GETBADGES_URL).mock(
            return_value=httpx.Response(200, content=FIXTURE.read_bytes())
        )
        with Store.in_memory() as store, SafeClient() as client:
            result = bp.import_from_api(store, client, STEAMID, API_KEY)
            assert result.imported == 3
            # The key was sent as a query param...
            assert f"key={API_KEY}" in str(route.calls.last.request.url)
            # ...but is NEVER stored in provenance.
            row = store.conn.execute("SELECT url FROM source_record").fetchone()
            assert API_KEY not in (row["url"] or "")

    def test_missing_key_raises_typed_error(self) -> None:
        with (
            Store.in_memory() as store,
            SafeClient() as client,
            pytest.raises(bp.MissingApiKeyError),
        ):
            bp.import_from_api(store, client, STEAMID, "")

    @respx.mock
    def test_api_error_does_not_leak_key(self) -> None:
        respx.get(bp.GETBADGES_URL).mock(return_value=httpx.Response(403))
        with Store.in_memory() as store, SafeClient() as client:
            try:
                bp.import_from_api(store, client, STEAMID, API_KEY)
            except Exception as exc:
                assert API_KEY not in str(exc)
                assert "REDACTED" in str(exc)
            else:
                raise AssertionError("expected an error")
