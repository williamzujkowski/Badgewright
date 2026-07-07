"""Fetch a card's "goo" (gem) value — the gems you get for turning it into gems (#101).

Two anonymous, read-only GETs (both pass ``safety.py``; verified by the #100 spike):

1. The card's PUBLIC market-listing *render* JSON
   (``/market/listings/753/<hash>/render/``) carries, in the item's ``owner_actions``, a
   ``GetGooValue('%contextid%','%assetid%', <appid>, <item_type>, <border_color>)`` link.
   ``item_type`` is per-card and cannot be safely defaulted, so we scrape it from here.
2. ``/auction/ajaxgetgoovalueforitemtype/?appid&item_type&border_color`` then returns
   ``{"success":1,"goo_value":"25"}`` (border_color 1 = foil, ~10x a normal card).

Goo values are stable per card, so results are cached (``card_goo_value`` table); a re-run
skips cached cards unless ``force``. Bounded by ``max_cards``, rate-polite via SafeClient,
and 429-hard-stop (``RateLimited`` propagates). All parsing is defensive: any missing/odd
field yields ``None`` (skip that card), never a crash. Read-only — Badgewright never turns
a card into gems; it only reports what a card *would* yield.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import quote

from ..config import CURRENCY_IDS
from ..models import CardGooValue
from ..models.provenance import SourceKind, SourceRecord
from .http_client import FetchError, RateLimited, SafeClient

if TYPE_CHECKING:
    from ..db import Store
    from ..models import Card

__all__ = [
    "GOO_VALUE_URL",
    "LISTING_RENDER_URL",
    "GooRefreshResult",
    "fetch_card_goo",
    "fetch_goo_params",
    "fetch_goo_value",
    "refresh_goo_values",
]

LISTING_RENDER_URL = "https://steamcommunity.com/market/listings/753/{hash}/render/"
GOO_VALUE_URL = "https://steamcommunity.com/auction/ajaxgetgoovalueforitemtype/"
PARSER_VERSION = "1"
GOO_TTL_SECONDS = 90 * 24 * 3600  # goo values change only on a rare Steam re-rate
MAX_RENDER_BYTES = 4 * 1024 * 1024
MAX_GOO_BYTES = 64 * 1024

# GetGooValue( '%contextid%', '%assetid%', <appid>, <item_type>, <border_color> )
_GOO_CALL_RE = re.compile(
    r"GetGooValue\(\s*'[^']*'\s*,\s*'[^']*'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)"
)


@dataclass(frozen=True, slots=True)
class GooRefreshResult:
    fetched: int
    skipped_cached: int
    failed: int


def _parse_goo_params(data: object) -> tuple[int, int] | None:
    """Extract (item_type, border_color) from a listing render JSON, or None if absent."""
    if not isinstance(data, dict):
        return None
    assets = data.get("assets")
    if not isinstance(assets, dict):
        return None
    # assets[appid][contextid][assetid] -> description with owner_actions.
    for by_context in assets.values():
        if not isinstance(by_context, dict):
            continue
        for by_asset in by_context.values():
            if not isinstance(by_asset, dict):
                continue
            for asset in by_asset.values():
                if not isinstance(asset, dict):
                    continue
                for action in asset.get("owner_actions") or []:
                    link = action.get("link") if isinstance(action, dict) else None
                    if isinstance(link, str) and "GetGooValue" in link:
                        m = _GOO_CALL_RE.search(link)
                        if m:
                            return int(m.group(2)), int(m.group(3))  # item_type, border_color
    return None


def fetch_goo_params(
    client: SafeClient, market_hash_name: str, currency: str = "USD"
) -> tuple[int, int] | None:
    """Scrape (item_type, border_color) from a card's public listing render JSON.

    Raises :class:`RateLimited` (429) so callers can hard-stop; other errors -> None.
    """
    url = LISTING_RENDER_URL.format(hash=quote(market_hash_name))
    params = {"count": 1, "currency": CURRENCY_IDS.get(currency.upper(), 1), "format": "json"}
    try:
        resp = client.get(url, params=params, max_bytes=MAX_RENDER_BYTES)
    except RateLimited:
        raise
    except FetchError:
        return None
    try:
        return _parse_goo_params(resp.json())
    except ValueError:
        return None


def fetch_goo_value(
    client: SafeClient, appid: int, item_type: int, border_color: int
) -> int | None:
    """Fetch the gem yield for an (appid, item_type, border_color), or None.

    Raises :class:`RateLimited` (429) so callers can hard-stop; other errors -> None.
    """
    params = {"appid": appid, "item_type": item_type, "border_color": border_color}
    try:
        resp = client.get(GOO_VALUE_URL, params=params, max_bytes=MAX_GOO_BYTES)
    except RateLimited:
        raise
    except FetchError:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("success"):
        return None
    raw = data.get("goo_value")
    if not isinstance(raw, str | int):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def fetch_card_goo(client: SafeClient, card: Card, currency: str = "USD") -> CardGooValue | None:
    """Scrape a card's item_type then fetch its goo value. None if either step fails."""
    params = fetch_goo_params(client, card.market_hash_name, currency)
    if params is None:
        return None
    item_type, border_color = params
    value = fetch_goo_value(client, card.appid, item_type, border_color)
    if value is None:
        return None
    return CardGooValue(
        appid=card.appid,
        market_hash_name=card.market_hash_name,
        item_type=item_type,
        border_color=border_color,
        goo_value=value,
    )


def refresh_goo_values(
    store: Store,
    client: SafeClient,
    cards: list[Card],
    *,
    currency: str = "USD",
    force: bool = False,
    max_cards: int | None = None,
) -> GooRefreshResult:
    """Fetch + cache goo values for ``cards`` (caller filters to foils by default).

    Skips already-cached cards (unless ``force``) for free; ``max_cards`` bounds how many
    are actually FETCHED. Re-raises :class:`RateLimited` so a bulk run stops rather than
    hammering the endpoint.
    """
    fetched = skipped = failed = 0
    for card in cards:
        if not force and store.goo_value_for(card.appid, card.market_hash_name) is not None:
            skipped += 1
            continue
        if max_cards is not None and fetched + failed >= max_cards:
            break
        goo = fetch_card_goo(client, card, currency)
        if goo is None:
            failed += 1
            continue
        source = SourceRecord(
            kind=SourceKind.STEAM_MARKET,
            url=GOO_VALUE_URL,
            fetched_at=datetime.now(UTC),
            parser_version=PARSER_VERSION,
            raw_sha256=SourceRecord.sha256_of(
                f"goo{goo.appid}{goo.market_hash_name}{goo.item_type}"
                f"{goo.border_color}{goo.goo_value}".encode()
            ),
            cache_ttl_seconds=GOO_TTL_SECONDS,
        )
        store.upsert_goo_value(goo, source)
        fetched += 1
    return GooRefreshResult(fetched=fetched, skipped_cached=skipped, failed=failed)
