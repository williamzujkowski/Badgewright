"""Source-provenance model (Issue 0.3).

Every imported datum — a price, a badge state, an inventory row, a catalog entry
— must be traceable to a :class:`SourceRecord`: where it came from, when, which
parser read it, and a hash of the raw bytes so a cached snapshot can be audited
or invalidated. Provenance is a hard requirement, not metadata: the persistence
layer stores ``source_url`` and ``fetched_at`` as NOT NULL so nothing un-attributed
can be cached (per the security review).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class SourceKind(StrEnum):
    """How a datum entered the system."""

    STEAM_WEBAPI = "steam_webapi"
    STEAM_MARKET = "steam_market"
    STEAM_INVENTORY = "steam_inventory"
    STEAM_BADGE_PAGE = "steam_badge_page"
    STEAM_BADGES_DB = "steam_badges_db"
    MANUAL_IMPORT = "manual_import"


class SourceRecord(BaseModel):
    """Provenance for a single imported/cached datum."""

    kind: SourceKind
    url: str | None = Field(
        default=None,
        description="Origin URL (http/https), or None for a manual/file import.",
    )

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError(f"url must be http(s), got {value!r}")
        return value

    file_name: str | None = Field(
        default=None, description="Basename of the imported file, for manual imports."
    )
    fetched_at: datetime = Field(description="UTC time the datum was retrieved/imported.")
    parser_version: str = Field(description="Version of the parser that produced the datum.")
    raw_sha256: str = Field(description="SHA-256 of the raw source bytes, for audit/dedup.")
    cache_ttl_seconds: int | None = Field(
        default=None, description="How long the snapshot is considered fresh; None = no expiry."
    )
    http_status: int | None = Field(default=None)

    def is_stale(self, *, now: datetime | None = None) -> bool:
        """True if the snapshot has aged past its TTL. No TTL => never stale."""
        if self.cache_ttl_seconds is None:
            return False
        reference = now or datetime.now(UTC)
        return reference - self.fetched_at > timedelta(seconds=self.cache_ttl_seconds)

    @staticmethod
    def sha256_of(raw: bytes) -> str:
        """Hex SHA-256 of raw bytes; the canonical way to fill ``raw_sha256``."""
        return hashlib.sha256(raw).hexdigest()
