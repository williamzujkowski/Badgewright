"""Import the steam-badges-db trading-card catalog.

Source: ``nolddor/steam-badges-db`` — a ``badges.json`` mapping appid -> ``{name,
size}`` (cards in the set), refreshed hourly. We import it two ways: from a local
file (offline) or from its raw URL via the guarded :class:`SafeClient`. Both paths
attach provenance and persist normalized ``SteamApp`` + ``BadgeSet`` rows.

Parsing is lenient about individual malformed entries (a huge external file may have
a few) — they are counted and skipped, not fatal — but strict about the envelope
(valid JSON object, within a size cap).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import orjson
from pydantic import ValidationError

from ..db import Store
from ..models import BadgeSet, SteamApp
from ..models.provenance import SourceKind, SourceRecord
from .http_client import SafeClient

__all__ = [
    "DEFAULT_BADGES_URL",
    "CatalogParseError",
    "ImportResult",
    "import_from_file",
    "import_from_url",
    "parse_badges_json",
]

DEFAULT_BADGES_URL = "https://raw.githubusercontent.com/nolddor/steam-badges-db/master/badges.json"
PARSER_VERSION = "1"
CATALOG_TTL_SECONDS = 3600  # steam-badges-db refreshes hourly
MAX_BYTES = 32 * 1024 * 1024  # reject an implausibly large catalog (bomb guard)


class CatalogParseError(ValueError):
    """The catalog envelope could not be parsed (bad JSON, wrong shape, too large)."""


@dataclass(frozen=True, slots=True)
class ImportResult:
    imported: int
    skipped: int


def parse_badges_json(raw: bytes) -> tuple[list[tuple[SteamApp, BadgeSet]], int]:
    """Parse badges.json bytes into (list of (app, badge_set), skipped_count).

    Raises :class:`CatalogParseError` on a bad/oversized envelope; skips (and counts)
    individual entries that fail validation.
    """
    if len(raw) > MAX_BYTES:
        raise CatalogParseError(f"catalog exceeds size cap ({len(raw)} > {MAX_BYTES} bytes)")
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise CatalogParseError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CatalogParseError(f"expected a JSON object, got {type(data).__name__}")

    parsed: list[tuple[SteamApp, BadgeSet]] = []
    skipped = 0
    for appid_key, entry in data.items():
        try:
            appid = int(appid_key)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not isinstance(entry, dict):
            skipped += 1
            continue
        size = entry.get("size")
        if not isinstance(size, int) or isinstance(size, bool):
            skipped += 1
            continue
        name = entry.get("name") or f"App {appid}"
        try:
            app = SteamApp(appid=appid, name=str(name))
            badge_set = BadgeSet(appid=appid, set_size=size)
        except ValidationError:
            skipped += 1
            continue
        parsed.append((app, badge_set))
    return parsed, skipped


def _persist(store: Store, parsed: list[tuple[SteamApp, BadgeSet]], source: SourceRecord) -> int:
    for app, badge_set in parsed:
        store.upsert_app(app, source)
        store.upsert_badge_set(badge_set, source)
    return len(parsed)


def import_from_file(store: Store, path: str | Path) -> ImportResult:
    """Import the catalog from a local badges.json file."""
    file_path = Path(path)
    # Check size BEFORE reading so an oversized file can't OOM the process.
    if file_path.stat().st_size > MAX_BYTES:
        raise CatalogParseError(f"file exceeds size cap ({file_path.stat().st_size} bytes)")
    raw = file_path.read_bytes()
    parsed, skipped = parse_badges_json(raw)
    source = SourceRecord(
        kind=SourceKind.STEAM_BADGES_DB,
        file_name=Path(path).name,
        fetched_at=datetime.now(UTC),
        parser_version=PARSER_VERSION,
        raw_sha256=SourceRecord.sha256_of(raw),
        cache_ttl_seconds=CATALOG_TTL_SECONDS,
    )
    imported = _persist(store, parsed, source)
    return ImportResult(imported=imported, skipped=skipped)


def import_from_url(
    store: Store, client: SafeClient, url: str = DEFAULT_BADGES_URL
) -> ImportResult:
    """Import the catalog from its raw URL via the guarded client (read-only)."""
    # max_bytes makes the cap load-bearing during download (not after a full read).
    resp = client.get(url, max_bytes=MAX_BYTES)
    raw = resp.content
    parsed, skipped = parse_badges_json(raw)
    source = SourceRecord(
        kind=SourceKind.STEAM_BADGES_DB,
        url=url,
        fetched_at=datetime.now(UTC),
        parser_version=PARSER_VERSION,
        raw_sha256=SourceRecord.sha256_of(raw),
        cache_ttl_seconds=CATALOG_TTL_SECONDS,
        http_status=resp.status_code,
    )
    imported = _persist(store, parsed, source)
    return ImportResult(imported=imported, skipped=skipped)
