"""Discover the full trading-card list for a game (Epic 3.2).

The optimizer can only mark a badge *complete* when it knows every card's market hash
name. Inventory only reveals the cards a user owns, so most sets stay "incomplete".
This module enumerates a game's full card list from the Steam Community Market search
endpoint (a read-only GET), so the cost calculator can cost those sets.

Fail-closed (per the approving vote):

* Only when the discovered **normal** card count equals the catalog ``set_size`` is the
  set marked fully known. ``found < size`` leaves it incomplete; ``found > size`` is a
  signal-quality problem (foil/filter leakage), not completion — discovery never
  overrides the catalog and never invents a missing card name.
* Foils are excluded from the set count via both the card ``type`` and a ``(Foil)`` name
  check. Discovered foils are still stored (flagged) for later foil support.

The endpoint is unofficial, so a manual-import fallback is provided for blocked users.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson

from ..models import Card
from ..models.provenance import SourceKind, SourceRecord
from .http_client import SafeClient

if TYPE_CHECKING:
    from ..db import Store

__all__ = [
    "SEARCH_URL",
    "CardDiscoveryError",
    "DiscoveredCard",
    "DiscoveryResult",
    "discover_cards",
    "import_cards",
    "import_from_file",
    "parse_search_results",
]

SEARCH_URL = "https://steamcommunity.com/market/search/render/"
PARSER_VERSION = "1"
CATALOG_TTL_SECONDS = 7 * 24 * 3600  # a set's card list rarely changes
PAGE_SIZE = 100
MAX_PAGES = 5
MAX_BYTES = 8 * 1024 * 1024


class CardDiscoveryError(ValueError):
    """The search response could not be parsed."""


@dataclass(frozen=True, slots=True)
class DiscoveredCard:
    market_hash_name: str
    is_foil: bool


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    appid: int
    set_size: int
    normal: list[str]
    foil: list[str]
    complete: bool
    notes: list[str] = field(default_factory=list)

    @property
    def normal_count(self) -> int:
        return len(self.normal)


def _is_foil(hash_name: str, card_type: str) -> bool:
    return "(foil)" in hash_name.lower() or "foil" in card_type.lower()


def parse_search_results(raw: bytes) -> list[DiscoveredCard]:
    """Parse a search/render JSON page into discovered cards (deduped within page)."""
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise CardDiscoveryError(f"invalid search JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CardDiscoveryError(f"expected a JSON object, got {type(data).__name__}")
    results = data.get("results")
    if not isinstance(results, list):
        raise CardDiscoveryError("search response missing a 'results' list")

    seen: dict[str, DiscoveredCard] = {}
    for entry in results:
        if not isinstance(entry, dict):
            continue
        hash_name = entry.get("hash_name")
        if not isinstance(hash_name, str) or not hash_name:
            continue
        desc = entry.get("asset_description")
        card_type = desc.get("type", "") if isinstance(desc, dict) else ""
        seen[hash_name] = DiscoveredCard(
            market_hash_name=hash_name, is_foil=_is_foil(hash_name, str(card_type))
        )
    return list(seen.values())


def _total_count(raw: bytes) -> int:
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return 0
    total = data.get("total_count") if isinstance(data, dict) else None
    return int(total) if isinstance(total, int) else 0


def discover_cards(
    client: SafeClient,
    appid: int,
    *,
    max_pages: int = MAX_PAGES,
    page_size: int = PAGE_SIZE,
) -> list[DiscoveredCard]:
    """Enumerate a game's trading cards via the market search endpoint (paginated)."""
    if not isinstance(appid, int) or appid <= 0:
        raise ValueError(f"appid must be a positive int, got {appid!r}")

    found: dict[str, DiscoveredCard] = {}
    start = 0
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "norender": 1,
            "appid": 753,
            "count": page_size,
            "start": start,
            "category_753_Game[]": f"tag_app_{appid}",
            "category_753_item_class[]": "tag_item_class_2",
        }
        resp = client.get(SEARCH_URL, params=params, max_bytes=MAX_BYTES)
        raw = resp.content
        for card in parse_search_results(raw):
            found.setdefault(card.market_hash_name, card)
        start += page_size
        if start >= _total_count(raw):
            break
    return list(found.values())


def _reconcile(appid: int, set_size: int, cards: list[DiscoveredCard]) -> DiscoveryResult:
    normal = sorted(c.market_hash_name for c in cards if not c.is_foil)
    foil = sorted(c.market_hash_name for c in cards if c.is_foil)
    notes: list[str] = []
    if len(normal) == set_size:
        complete = True
    else:
        complete = False
        if len(normal) < set_size:
            notes.append(f"found {len(normal)} of {set_size} cards (partial discovery)")
        else:
            notes.append(
                f"found {len(normal)} cards but catalog set size is {set_size} "
                "(possible foil/filter leakage); left incomplete"
            )
    return DiscoveryResult(
        appid=appid, set_size=set_size, normal=normal, foil=foil, complete=complete, notes=notes
    )


def _persist(store: Store, result: DiscoveryResult, source: SourceRecord) -> None:
    for name in result.normal:
        store.upsert_card(Card(appid=result.appid, market_hash_name=name, is_foil=False))
    for name in result.foil:
        store.upsert_card(Card(appid=result.appid, market_hash_name=name, is_foil=True))
    store.record_source(source)


def import_cards(
    store: Store, client: SafeClient, appid: int, set_size: int, **kwargs: Any
) -> DiscoveryResult:
    """Discover and persist a game's card names via the market search endpoint."""
    cards = discover_cards(client, appid, **kwargs)
    result = _reconcile(appid, set_size, cards)
    source = SourceRecord(
        kind=SourceKind.STEAM_MARKET_SEARCH,
        url=SEARCH_URL,
        fetched_at=datetime.now(UTC),
        parser_version=PARSER_VERSION,
        raw_sha256=SourceRecord.sha256_of(orjson.dumps(sorted(result.normal + result.foil))),
        cache_ttl_seconds=CATALOG_TTL_SECONDS,
    )
    _persist(store, result, source)
    return result


def import_from_file(store: Store, path: str | Path, appid: int, set_size: int) -> DiscoveryResult:
    """Import a saved search/render JSON page (manual fallback for blocked users)."""
    file_path = Path(path)
    if not file_path.is_file():
        raise CardDiscoveryError(f"not a file: {file_path}")
    if file_path.stat().st_size > MAX_BYTES:
        raise CardDiscoveryError(f"file exceeds size cap ({file_path.stat().st_size} bytes)")
    cards = parse_search_results(file_path.read_bytes())
    result = _reconcile(appid, set_size, cards)
    source = SourceRecord(
        kind=SourceKind.STEAM_MARKET_SEARCH,
        file_name=file_path.name,
        fetched_at=datetime.now(UTC),
        parser_version=PARSER_VERSION,
        raw_sha256=SourceRecord.sha256_of(file_path.read_bytes()),
        cache_ttl_seconds=CATALOG_TTL_SECONDS,
    )
    _persist(store, result, source)
    return result
