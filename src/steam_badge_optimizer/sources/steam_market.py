"""Steam Community Market price fetching (the unofficial ``priceoverview`` endpoint).

Fetches lowest/median price + 24h volume for a market item via the guarded
:class:`SafeClient`, parses the localized price strings into :class:`Money`, and
records a provenance-carrying :class:`PriceSnapshot`.

Posture (per the approving vote's security conditions):

* Query params (``appid``, ``currency``, ``market_hash_name``) are passed through
  httpx's param encoder — never string-concatenated — so nothing is smuggled.
* ``currency`` is validated against the known Steam currency set.
* Failures degrade gracefully: a missing/failed/priceless lookup returns ``None``
  (never a poisoned zero snapshot). HTTP 429 is *not* swallowed — it propagates so a
  bulk refresh stops rather than hammering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..config import CURRENCY_IDS
from ..models import MarketItem, Money, PriceSnapshot
from ..models.money import PriceParseError, parse_steam_price
from ..models.provenance import SourceKind, SourceRecord
from .http_client import FetchError, RateLimited, SafeClient

if TYPE_CHECKING:
    from ..db import Store

__all__ = ["DEFAULT_TTL_SECONDS", "RefreshResult", "fetch_price", "refresh_prices"]

PRICEOVERVIEW_URL = "https://steamcommunity.com/market/priceoverview/"
PARSER_VERSION = "1"
DEFAULT_TTL_SECONDS = 24 * 3600
MAX_RESPONSE_BYTES = 64 * 1024  # priceoverview responses are tiny


@dataclass(frozen=True, slots=True)
class RefreshResult:
    fetched: int
    skipped_cached: int
    failed: int


def _parse_money_or_none(text: object, currency: str) -> Money | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return parse_steam_price(text, currency)
    except PriceParseError:
        return None


def _parse_volume(text: object) -> int | None:
    if not isinstance(text, str):
        return None
    digits = re.sub(r"[^0-9]", "", text)
    return int(digits) if digits else None


def fetch_price(
    client: SafeClient,
    item: MarketItem,
    currency: str = "USD",
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> PriceSnapshot | None:
    """Fetch a single price observation, or ``None`` if unavailable/unpriced.

    Raises :class:`RateLimited` (429) so callers can stop; other fetch/parse errors
    and ``success: false`` responses yield ``None`` (graceful degradation).
    """
    currency = currency.upper()
    if currency not in CURRENCY_IDS:
        raise ValueError(f"unknown currency {currency!r}; known: {sorted(CURRENCY_IDS)}")

    params = {
        "appid": item.appid,
        "currency": CURRENCY_IDS[currency],
        "market_hash_name": item.market_hash_name,
    }
    try:
        resp = client.get(PRICEOVERVIEW_URL, params=params, max_bytes=MAX_RESPONSE_BYTES)
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

    lowest = _parse_money_or_none(data.get("lowest_price"), currency)
    median = _parse_money_or_none(data.get("median_price"), currency)
    if lowest is None and median is None:
        # No usable price — do not persist a poisoned/zero snapshot.
        return None

    source = SourceRecord(
        kind=SourceKind.STEAM_MARKET,
        url=PRICEOVERVIEW_URL,
        fetched_at=datetime.now(UTC),
        parser_version=PARSER_VERSION,
        raw_sha256=SourceRecord.sha256_of(resp.content),
        cache_ttl_seconds=ttl_seconds,
        http_status=resp.status_code,
    )
    return PriceSnapshot(
        item=item,
        lowest=lowest,
        median=median,
        volume=_parse_volume(data.get("volume")),
        source=source,
    )


def refresh_prices(
    store: Store,
    client: SafeClient,
    items: list[MarketItem],
    currency: str = "USD",
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    force: bool = False,
) -> RefreshResult:
    """Refresh prices for ``items``, skipping any with a fresh cached snapshot.

    A cached snapshot within its TTL is reused (unless ``force``). Stops early and
    re-raises :class:`RateLimited` rather than hammering a rate-limited endpoint.
    """
    fetched = skipped = failed = 0
    for item in items:
        if not force:
            latest = store.latest_price(item.appid, item.market_hash_name)
            if latest is not None and not latest.is_stale():
                skipped += 1
                continue
        snap = fetch_price(client, item, currency, ttl_seconds=ttl_seconds)
        if snap is None:
            failed += 1
            continue
        store.add_price_snapshot(snap)
        fetched += 1
    return RefreshResult(fetched=fetched, skipped_cached=skipped, failed=failed)
