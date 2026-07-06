"""Fetch a game's Booster Pack market price (increment 3 of the arbitrage epic, #98).

A game's Booster Pack is an ordinary marketable community item under appid 753, in item
class 5 (``tag_item_class_5``). One guarded ``search/render`` request filtered to a game +
that class returns the pack's ``hash_name``, lowest ask (``sell_price``), and ask depth
(``sell_listings``) — currency-controlled by the ``currency`` param — so we never touch the
login-gated booster-creator page. Read-only: this only ever GETs public listings.

Per the epic's egress conditions (#102): callers drive this one game at a time behind a
bounded ``--max-games`` cap, rate-polite spacing, and a 429-hard-stop (``RateLimited``
propagates). Nothing here loops the catalog.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import CURRENCY_IDS
from .card_discovery import SEARCH_URL, parse_search_results
from .http_client import SafeClient

__all__ = ["BoosterQuote", "fetch_booster_price"]

MAX_BYTES = 1 * 1024 * 1024  # a single-game class-5 search is tiny
_BOOSTER_SUFFIX = "booster pack"


@dataclass(frozen=True, slots=True)
class BoosterQuote:
    """A game's Booster Pack lowest ask, in one currency."""

    appid: int
    market_hash_name: str
    lowest_cents: int
    listings: int | None
    currency: str


def fetch_booster_price(
    client: SafeClient,
    appid: int,
    currency: str = "USD",
) -> BoosterQuote | None:
    """Fetch a game's Booster Pack lowest ask, or ``None`` if none is listed/priced.

    Raises :class:`RateLimited` (429) so callers can hard-stop; other fetch/parse failures
    degrade to ``None``. The pack is identified by the ``"<appid>-"`` prefix and a
    "Booster Pack" name, so a foreign item leaking into the results is ignored.
    """
    currency = currency.upper()
    if currency not in CURRENCY_IDS:
        raise ValueError(f"unknown currency {currency!r}; known: {sorted(CURRENCY_IDS)}")

    params = {
        "norender": 1,
        "l": "english",
        "appid": 753,
        "currency": CURRENCY_IDS[currency],
        "count": 10,
        "category_753_Game[]": f"tag_app_{appid}",
        "category_753_item_class[]": "tag_item_class_5",
    }
    resp = client.get(SEARCH_URL, params=params, max_bytes=MAX_BYTES)
    # parse_search_results reads hash_name/sell_price/sell_listings generically (its foil
    # check is harmless for packs). Take this game's Booster Pack with a usable price.
    prefix = f"{appid}-"
    for entry in parse_search_results(resp.content):
        name = entry.market_hash_name
        if (
            name.startswith(prefix)
            and name.lower().endswith(_BOOSTER_SUFFIX)
            and entry.sell_price_cents is not None
        ):
            return BoosterQuote(
                appid=appid,
                market_hash_name=name,
                lowest_cents=entry.sell_price_cents,
                listings=entry.listings,
                currency=currency,
            )
    return None
