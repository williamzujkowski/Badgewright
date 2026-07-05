"""Tests for steam-badges-db catalog import (file and URL modes)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from steam_badge_optimizer.db import Store
from steam_badge_optimizer.safety import SafetyViolationError
from steam_badge_optimizer.sources import steam_badges_db as sbd
from steam_badge_optimizer.sources.http_client import SafeClient

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "badges.json"


def test_parse_fixture_counts() -> None:
    parsed, skipped = sbd.parse_badges_json(FIXTURE.read_bytes())
    # 220/440/570 valid, 99999901 valid (name defaulted); bad-key + size-0 skipped.
    assert len(parsed) == 4
    assert skipped == 2
    names = {app.appid: app.name for app, _ in parsed}
    assert names[220] == "Half-Life 2"
    assert names[99999901] == "App 99999901"  # missing name defaulted


def test_import_from_file_persists() -> None:
    with Store.in_memory() as store:
        result = sbd.import_from_file(store, FIXTURE)
        assert result.imported == 4
        assert result.skipped == 2
        assert store.get_app(440).name == "Team Fortress 2"
        assert len(store.list_apps()) == 4


@pytest.mark.parametrize("bad", [b"not json", b"[1,2,3]", b'"a string"', b"123"])
def test_bad_envelope_raises(bad: bytes) -> None:
    with pytest.raises(sbd.CatalogParseError):
        sbd.parse_badges_json(bad)


def test_size_cap_enforced(monkeypatch) -> None:
    monkeypatch.setattr(sbd, "MAX_BYTES", 4)
    with pytest.raises(sbd.CatalogParseError):
        sbd.parse_badges_json(b'{"220": {"name": "x", "size": 8}}')


@respx.mock
def test_import_from_url_persists() -> None:
    respx.get(sbd.DEFAULT_BADGES_URL).mock(
        return_value=httpx.Response(200, content=FIXTURE.read_bytes())
    )
    with Store.in_memory() as store, SafeClient() as client:
        result = sbd.import_from_url(store, client, sbd.DEFAULT_BADGES_URL)
        assert result.imported == 4
        assert store.get_app(220).name == "Half-Life 2"
        # Provenance recorded (source rows written for the import).
        assert store.source_count() >= 1


def test_import_from_url_blocked_host() -> None:
    with (
        Store.in_memory() as store,
        SafeClient() as client,
        pytest.raises(SafetyViolationError),
    ):
        sbd.import_from_url(store, client, "https://evil.example.com/badges.json")
