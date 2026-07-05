"""Ingest the user's per-game badge levels (Epic 2.4).

Without this, the cost calculator assumes every badge is level 0, overstating cost and
XP. This reads the user's real badge levels so plans are accurate.

Primary source: the official Steam Web API ``IPlayerService/GetBadges`` (stable JSON).
It needs the user's own **Web API key** — a read-only public-data key, **not** an
account password or Steam Guard secret. The key is supplied via the ``SBO_STEAM_API_KEY``
environment variable and is **never persisted** (not to the DB, provenance, logs, or
error messages — the guarded client redacts ``key=`` from any URL it reports). A saved
GetBadges JSON can also be imported offline (manual fallback). HTML scraping of the
public badges page is intentionally deferred (fragile) until proven necessary.

Foil badges are out of scope (as elsewhere); unknown levels stay unknown (the cost
calculator keeps its assume-0-with-a-note fallback for badges with no progress row).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import orjson

from ..config import MAX_NORMAL_BADGE_LEVEL
from ..models import UserBadgeProgress
from ..models.provenance import SourceKind, SourceRecord
from .http_client import SafeClient

if TYPE_CHECKING:
    from ..db import Store

__all__ = [
    "GETBADGES_URL",
    "BadgeProgressError",
    "BadgeProgressResult",
    "MissingApiKeyError",
    "api_key_from_env",
    "import_from_api",
    "import_from_file",
    "parse_badges_response",
]

GETBADGES_URL = "https://api.steampowered.com/IPlayerService/GetBadges/v1/"
PARSER_VERSION = "1"
TTL_SECONDS = 24 * 3600
MAX_BYTES = 8 * 1024 * 1024


class BadgeProgressError(ValueError):
    """The GetBadges response could not be parsed."""


class MissingApiKeyError(RuntimeError):
    """No Steam Web API key was provided for the online badge-progress fetch."""

    def __init__(self) -> None:
        super().__init__(
            "Set SBO_STEAM_API_KEY to your Steam Web API key (read-only; get one at "
            "https://steamcommunity.com/dev/apikey), or use --file with a saved GetBadges "
            "JSON. The key is used only for this request and is never stored."
        )


@dataclass(frozen=True, slots=True)
class BadgeProgressResult:
    imported: int
    skipped: int


def parse_badges_response(raw: bytes) -> list[UserBadgeProgress]:
    """Parse a GetBadges JSON payload into normal game-badge progress rows.

    Skips non-game badges (no appid), foil badges (border_color == 1), and malformed
    entries. Level is clamped to the normal-badge cap.
    """
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise BadgeProgressError(f"invalid GetBadges JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BadgeProgressError(f"expected a JSON object, got {type(data).__name__}")
    response = data.get("response")
    if not isinstance(response, dict):
        raise BadgeProgressError("GetBadges response missing a 'response' object")
    badges = response.get("badges")
    if badges is None:
        return []  # a valid response for a user with no badges
    if not isinstance(badges, list):
        raise BadgeProgressError("'badges' must be a list")

    out: dict[int, UserBadgeProgress] = {}
    for badge in badges:
        if not isinstance(badge, dict):
            continue
        appid = badge.get("appid")
        level = badge.get("level")
        if not isinstance(appid, int) or appid <= 0 or not isinstance(level, int):
            continue  # community badge (no appid) or malformed
        if badge.get("border_color") == 1:
            continue  # foil badge — out of scope
        clamped = max(0, min(level, MAX_NORMAL_BADGE_LEVEL))
        # Keep the highest level seen per app (defensive against duplicates).
        existing = out.get(appid)
        if existing is None or clamped > existing.level:
            out[appid] = UserBadgeProgress(appid=appid, level=clamped, is_foil=False)
    return list(out.values())


def _persist(store: Store, rows: list[UserBadgeProgress], source: SourceRecord) -> int:
    for row in rows:
        store.upsert_badge_progress(row)
    store.record_source(source)
    return len(rows)


def import_from_api(
    store: Store, client: SafeClient, steamid64: int, api_key: str
) -> BadgeProgressResult:
    """Fetch badge levels from the Steam Web API and persist them.

    The API key is used only for this request; the stored provenance URL never contains
    it, and the guarded client redacts it from any error.
    """
    if not api_key:
        raise MissingApiKeyError()
    resp = client.get(
        GETBADGES_URL,
        params={"key": api_key, "steamid": steamid64},
        max_bytes=MAX_BYTES,
    )
    rows = parse_badges_response(resp.content)
    source = SourceRecord(
        kind=SourceKind.STEAM_WEBAPI,
        url=GETBADGES_URL,  # base endpoint only — never the key-bearing URL
        fetched_at=datetime.now(UTC),
        parser_version=PARSER_VERSION,
        raw_sha256=SourceRecord.sha256_of(resp.content),
        cache_ttl_seconds=TTL_SECONDS,
        http_status=resp.status_code,
    )
    imported = _persist(store, rows, source)
    return BadgeProgressResult(imported=imported, skipped=0)


def import_from_file(store: Store, path: str | Path) -> BadgeProgressResult:
    """Import badge levels from a saved GetBadges JSON file (offline fallback)."""
    file_path = Path(path)
    if not file_path.is_file():
        raise BadgeProgressError(f"not a file: {file_path}")
    if file_path.stat().st_size > MAX_BYTES:
        raise BadgeProgressError(f"file exceeds size cap ({file_path.stat().st_size} bytes)")
    raw = file_path.read_bytes()
    rows = parse_badges_response(raw)
    source = SourceRecord(
        kind=SourceKind.STEAM_WEBAPI,
        file_name=file_path.name,
        fetched_at=datetime.now(UTC),
        parser_version=PARSER_VERSION,
        raw_sha256=SourceRecord.sha256_of(raw),
        cache_ttl_seconds=TTL_SECONDS,
    )
    imported = _persist(store, rows, source)
    return BadgeProgressResult(imported=imported, skipped=0)


def api_key_from_env() -> str:
    """Read the Steam Web API key from SBO_STEAM_API_KEY (empty string if unset)."""
    import os

    return os.environ.get("SBO_STEAM_API_KEY", "")
