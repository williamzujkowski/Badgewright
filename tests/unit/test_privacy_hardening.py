"""Privacy & credential-safety invariants (Epics 8.4, 1.3, 0.4, 9.5)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import get_args

import httpx
import orjson
import pytest
import respx
from pydantic import BaseModel
from typer.testing import CliRunner

from steam_badge_optimizer import models as models_pkg
from steam_badge_optimizer.cli import app
from steam_badge_optimizer.db import Store
from steam_badge_optimizer.db.schema import MIGRATIONS
from steam_badge_optimizer.models import MarketItem, SteamApp
from steam_badge_optimizer.models.provenance import SourceRecord
from steam_badge_optimizer.sources import steam_market as sm
from steam_badge_optimizer.sources.http_client import SafeClient

runner = CliRunner()

# Substrings that make a field/column name credential-shaped whatever it is called. An
# exact denylist only blocks the names someone already thought of.
SECRET_NAME_FRAGMENTS = (
    "secret",
    "password",
    "passwd",
    "credential",
    "steamguard",
    "session",
    "token",
)

# Anything that would smell like a stored Steam credential/session secret.
FORBIDDEN_SECRET_NAMES = {
    "steamloginsecure",
    "sessionid",
    "session_id",
    "shared_secret",
    "identity_secret",
    "revocation_code",
    "password",
    "steam_guard",
    "steamguard",
    "access_token",
    "refresh_token",
}


class TestDeleteAll:
    def _seed(self, data_dir) -> None:
        from datetime import UTC, datetime

        from steam_badge_optimizer.config import Settings
        from steam_badge_optimizer.models import (
            MarketItem,
            Money,
            PriceSnapshot,
            SourceKind,
            SourceRecord,
            UserCardInventory,
        )

        s = Settings.resolve(data_dir=str(data_dir))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with Store(s.db_path) as store:
            store.upsert_app(SteamApp(appid=440, name="Team Fortress 2"))
            # Seed multiple data types so the completeness grep catches any of them.
            store.upsert_inventory(
                UserCardInventory(appid=440, market_hash_name="440-SecretCard", quantity=3)
            )
            store.add_price_snapshot(
                PriceSnapshot(
                    item=MarketItem(appid=440, market_hash_name="440-SecretCard"),
                    lowest=Money(1234, "USD"),
                    source=SourceRecord(
                        kind=SourceKind.STEAM_MARKET,
                        url="https://steamcommunity.com/market/priceoverview/",
                        fetched_at=datetime.now(UTC),
                        parser_version="1",
                        raw_sha256=SourceRecord.sha256_of(b"x"),
                        cache_ttl_seconds=86400,
                    ),
                )
            )

    def test_deletes_db_and_leaves_no_data(self, tmp_path) -> None:
        from steam_badge_optimizer.config import Settings

        self._seed(tmp_path)
        db = Settings.resolve(data_dir=str(tmp_path)).db_path
        assert db.is_file()
        result = runner.invoke(app, ["delete-all", "--yes", "--data-dir", str(tmp_path)])
        assert result.exit_code == 0
        # DB and every journal/WAL sidecar are gone.
        for suffix in ("", "-wal", "-shm", "-journal"):
            assert not (db.parent / f"{db.name}{suffix}").exists()
        # No trace of ANY imported data type (app name, card, price) survives.
        for f in tmp_path.rglob("*"):
            if f.is_file():
                blob = f.read_bytes()
                assert b"Team Fortress 2" not in blob
                assert b"440-SecretCard" not in blob

    def test_nothing_to_delete_is_graceful(self, tmp_path) -> None:
        result = runner.invoke(app, ["delete-all", "--yes", "--data-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "no local data" in result.output.lower()

    def test_prompt_abort_keeps_data(self, tmp_path) -> None:
        from steam_badge_optimizer.config import Settings

        self._seed(tmp_path)
        result = runner.invoke(app, ["delete-all", "--data-dir", str(tmp_path)], input="n\n")
        assert result.exit_code == 1
        assert Settings.resolve(data_dir=str(tmp_path)).db_path.is_file()  # untouched


class TestNoStoredSecrets:
    def test_no_model_field_is_a_credential(self) -> None:
        # The domain models must not be able to hold a Steam credential/secret. Matched on
        # SUBSTRINGS, not an exact denylist: an exact list only blocks names someone already
        # thought of, so a field like `steam_session_token` would sail through it.
        checked = 0
        for name in dir(models_pkg):
            obj = getattr(models_pkg, name)
            if isinstance(obj, type) and issubclass(obj, BaseModel):
                checked += 1
                for field in obj.model_fields:
                    lowered = field.lower()
                    assert lowered not in FORBIDDEN_SECRET_NAMES, f"{name}.{field}"
                    for fragment in SECRET_NAME_FRAGMENTS:
                        assert fragment not in lowered, (
                            f"{name}.{field} looks like a credential ({fragment!r})"
                        )
        assert checked >= 8  # sanity: we actually inspected the models

    def test_no_schema_column_is_a_credential(self) -> None:
        ddl = " ".join(stmt for migration in MIGRATIONS for stmt in migration).lower()
        for secret in FORBIDDEN_SECRET_NAMES:
            assert secret not in ddl, f"schema references {secret!r}"


class TestEgressAudit:
    @pytest.mark.parametrize("module", ["requests", "aiohttp", "pycurl", "websockets", "tornado"])
    def test_no_extra_network_library_installed(self, module: str) -> None:
        # Egress audit: the only sanctioned network client is httpx (via SafeClient).
        # A dependency pulling in another network library would widen the egress surface.
        assert importlib.util.find_spec(module) is None, (
            f"{module} is installed — review the dependency that pulled it in"
        )


class TestNoRawBodyPersistence:
    """No Steam response body ever reaches disk (#12).

    The cached-HTML-sanitization item was closed as obsolete on the grounds that only a
    ``raw_sha256`` digest is persisted, never the fetched bytes. That is a claim about
    behaviour, so the primary test below exercises it end to end: fetch a mocked
    response carrying a sentinel the parser ignores, persist the result, then scan the
    database file itself. The structural checks that follow are defence in depth.
    """

    # A string present in the response body but in no parsed field, so it can only
    # reach the database if something persisted the raw body.
    SENTINEL = "SBO-RAW-BODY-SENTINEL-9d41c0"

    @respx.mock
    def test_fetched_body_does_not_reach_the_database_file(self, tmp_path: Path) -> None:
        respx.get(sm.PRICEOVERVIEW_URL).mock(
            return_value=httpx.Response(
                200,
                content=orjson.dumps(
                    {
                        "success": True,
                        "lowest_price": "$0.03",
                        "median_price": "$0.05",
                        "volume": "1,234",
                        # Ignored by the parser; present only in the raw bytes.
                        "unparsed_extra": self.SENTINEL,
                    }
                ),
            )
        )
        with SafeClient() as client:
            item = MarketItem(appid=440, market_hash_name="440-Heavy")
            snap = sm.fetch_price(client, item, "USD")
        assert snap is not None
        assert self.SENTINEL.encode() not in snap.source.raw_sha256.encode()  # digest, not bytes

        db_path = tmp_path / "sbo.sqlite3"
        with Store(db_path) as store:
            store.add_price_snapshot(snap)

        # Scan every file the store may have written, not just the main DB.
        scanned = 0
        for path in tmp_path.rglob("*"):
            if path.is_file():
                scanned += 1
                assert self.SENTINEL.encode() not in path.read_bytes(), (
                    f"raw response bytes reached {path.name} — response bodies must not persist"
                )
        assert scanned  # sanity: we actually looked at something

    # Name fragments implying stored content rather than a digest of it. Matched as
    # substrings so `cached_html`, `page_body` and `raw_body` are caught too.
    FORBIDDEN_BODY_FRAGMENTS = ("html", "body", "content", "payload", "response", "markup")
    # Columns that legitimately contain one of the fragments above.
    BODY_FRAGMENT_ALLOWLIST = ("http_status",)

    def test_no_schema_column_holds_a_response_body(self) -> None:
        ddl = " ".join(stmt for migration in MIGRATIONS for stmt in migration).lower()
        assert "blob" not in ddl, "schema declares a BLOB column — could hold a response body"
        # Every declared column is "<name> <TYPE>"; check the names, not the whole DDL.
        for name in re.findall(r"(\w+)\s+(?:text|blob|varchar)\b", ddl):
            if name in self.BODY_FRAGMENT_ALLOWLIST:
                continue
            for fragment in self.FORBIDDEN_BODY_FRAGMENTS:
                assert fragment not in name, (
                    f"column {name!r} looks like it stores a response body, not a digest"
                )

    def test_provenance_keeps_only_a_digest(self) -> None:
        assert "raw_sha256" in SourceRecord.model_fields
        for name, field in SourceRecord.model_fields.items():
            # Walk the annotation: `bytes | None` must fail this too, not just `bytes`.
            assert bytes not in _annotation_types(field.annotation), (
                f"SourceRecord.{name} can hold raw bytes"
            )


def _annotation_types(annotation: object) -> set[object]:
    """Flatten a type annotation into the set of concrete types it can hold."""
    args = get_args(annotation)
    if not args:
        return {annotation}
    found: set[object] = set()
    for arg in args:
        found |= _annotation_types(arg)
    return found
