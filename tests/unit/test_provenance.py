"""Tests for the SourceRecord provenance model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from steam_badge_optimizer.models.provenance import SourceKind, SourceRecord


def _rec(**overrides) -> SourceRecord:
    base = {
        "kind": SourceKind.STEAM_MARKET,
        "url": "https://steamcommunity.com/market/priceoverview/",
        "fetched_at": datetime(2026, 7, 4, tzinfo=UTC),
        "parser_version": "1.0",
        "raw_sha256": SourceRecord.sha256_of(b"{}"),
        "cache_ttl_seconds": 3600,
    }
    base.update(overrides)
    return SourceRecord(**base)


def test_sha256_is_deterministic() -> None:
    assert SourceRecord.sha256_of(b"abc") == SourceRecord.sha256_of(b"abc")
    assert SourceRecord.sha256_of(b"abc") != SourceRecord.sha256_of(b"abd")
    assert len(SourceRecord.sha256_of(b"")) == 64


def test_fresh_record_is_not_stale() -> None:
    rec = _rec()
    now = rec.fetched_at + timedelta(seconds=10)
    assert rec.is_stale(now=now) is False


def test_expired_record_is_stale() -> None:
    rec = _rec(cache_ttl_seconds=60)
    now = rec.fetched_at + timedelta(seconds=61)
    assert rec.is_stale(now=now) is True


def test_no_ttl_never_stale() -> None:
    rec = _rec(cache_ttl_seconds=None)
    assert rec.is_stale(now=rec.fetched_at + timedelta(days=3650)) is False


def test_manual_import_has_no_url() -> None:
    rec = _rec(kind=SourceKind.MANUAL_IMPORT, url=None, file_name="inventory.json")
    assert rec.url is None
    assert rec.file_name == "inventory.json"


def test_unattributed_record_is_rejected() -> None:
    # #0.4 negative path: a record with neither a url nor a file_name has no origin,
    # so it must not be constructible at all — the persistence layer never sees it.
    with pytest.raises(ValidationError, match="needs a url or a file_name"):
        _rec(url=None, file_name=None)
