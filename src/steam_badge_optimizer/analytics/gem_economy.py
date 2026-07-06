"""Gem economy: value Steam gems in real money and the gem cost to craft a booster.

Foundation of the arbitrage epic (#94, increment #95). Everything here is *read +
compute* — Badgewright never crafts, buys, or sells; you act manually in Steam.

Two facts make this cheap and safe:

* A **Sack of Gems** is an ordinary marketable item on the Community Market under appid
  753 (``market_hash_name = "753-Sack of Gems"``), bundling exactly
  :data:`GEMS_PER_SACK` gems. So its price flows through the very same guarded price
  layer (``MarketItem`` + ``fetch_price``) as any trading card — no new egress path and
  no schema change — and dividing by 1000 gives a per-gem value.
* Steam's booster-pack recipe costs gems *inversely* proportional to a set's card count,
  well approximated by ``round(6000 / set_size)`` — pure arithmetic, no network.

The per-gem value comes in two flavors: a **gross** figure (what a gem costs to acquire,
i.e. the Sack's lowest ask / 1000) and a **net-of-fee** figure (what a gem you hold is
worth if you sell it back). Steam's ~15% fee is levied on the *seller's* proceeds and
added on top to make the buyer's list price, so a seller nets ``list / (1 + fee)``. Costing
a purchase always uses gross; the net figure is only for valuing gems you already own.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from ..models import MarketItem, Money

if TYPE_CHECKING:
    from ..db import Store
    from ..models import PriceSnapshot
    from ..sources.http_client import SafeClient

__all__ = [
    "GEMS_PER_SACK",
    "SACK_OF_GEMS_APPID",
    "SACK_OF_GEMS_HASH",
    "STEAM_MARKET_FEE",
    "GemValue",
    "booster_crafting_cost_gems",
    "gem_value",
    "gems_to_money",
    "latest_sack_price",
    "refresh_sack_price",
    "sack_of_gems_item",
]

#: A "Sack of Gems" bundles exactly this many gems into one marketable item.
GEMS_PER_SACK = 1000

#: The Sack of Gems is a community item under appid 753 (like cards), not the game appid.
SACK_OF_GEMS_APPID = 753
SACK_OF_GEMS_HASH = "753-Sack of Gems"

#: Steam's market transaction fee (Steam + game), taken from the SELLER's proceeds and
#: added on top to form the buyer's list price. So a seller of a listed item nets
#: ``list / (1 + fee)``. Approximate and region-dependent — used only for the net-of-fee
#: "what your gems are worth to sell" figure, never for costing a purchase.
STEAM_MARKET_FEE = Decimal("0.15")

#: Steam's booster-pack recipe numerator: gems-per-pack ≈ 6000 / (# distinct cards in set).
#: A well-known community approximation (the exact figure is only on the login-gated
#: booster-creator page, which we never read).
_BOOSTER_GEM_NUMERATOR = 6000


@dataclass(frozen=True, slots=True)
class GemValue:
    """Per-gem value in one currency, derived from a Sack-of-Gems price.

    ``cents_per_gem`` is fractional (a gem is worth a tiny fraction of a cent) and is the
    GROSS cost to acquire a gem. ``net_cents_per_gem`` is what a held gem nets if sold,
    after :data:`STEAM_MARKET_FEE` (approximate).
    """

    currency: str
    cents_per_gem: Decimal
    net_cents_per_gem: Decimal


def sack_of_gems_item() -> MarketItem:
    """The :class:`MarketItem` for the Sack of Gems (prices via the normal price layer)."""
    return MarketItem(appid=SACK_OF_GEMS_APPID, market_hash_name=SACK_OF_GEMS_HASH)


def gem_value(sack_price: Money) -> GemValue:
    """Per-gem value from a Sack-of-Gems price (its currency; 1000 gems per sack).

    Uses the Sack's price as-is for the gross figure and nets Steam's ~15% fee for the
    resale figure. The currency is carried through unchanged.
    """
    gross = Decimal(sack_price.cents) / GEMS_PER_SACK
    net = gross / (Decimal(1) + STEAM_MARKET_FEE)
    return GemValue(currency=sack_price.currency, cents_per_gem=gross, net_cents_per_gem=net)


def gems_to_money(gems: int, value: GemValue, *, net: bool = False) -> Money:
    """Convert a gem quantity to :class:`Money` at ``value`` (gross by default).

    ``net=True`` uses the after-fee per-gem rate (what those gems are worth if sold).
    Rounds to the nearest cent (half-up); never returns a negative amount.
    """
    if gems < 0:
        raise ValueError("gems must be >= 0")
    per = value.net_cents_per_gem if net else value.cents_per_gem
    cents = int((per * gems).to_integral_value(rounding=ROUND_HALF_UP))
    return Money(cents=max(cents, 0), currency=value.currency)


def booster_crafting_cost_gems(set_size: int) -> int:
    """Gems needed to craft one booster pack for a set of ``set_size`` distinct cards.

    Steam's recipe is inversely proportional to the set's card count, approximated by
    ``round(6000 / set_size)`` (e.g. 5 cards → 1200, 6 → 1000, 15 → 400).
    """
    if set_size < 1:
        raise ValueError("set_size must be >= 1")
    # Decimal + half-up to match the money path (round() is banker's rounding on a float).
    cost = Decimal(_BOOSTER_GEM_NUMERATOR) / set_size
    return int(cost.to_integral_value(rounding=ROUND_HALF_UP))


def latest_sack_price(store: Store, *, currency: str | None = None) -> PriceSnapshot | None:
    """The most recent cached Sack-of-Gems price, optionally constrained to ``currency``.

    ``store.latest_price`` returns the newest snapshot regardless of currency, so a stray
    fetch in another currency could mask a usable one. When ``currency`` is given, walk the
    history and return the newest snapshot whose lowest ask is in that currency (or ``None``).
    """
    if currency is None:
        return store.latest_price(SACK_OF_GEMS_APPID, SACK_OF_GEMS_HASH)
    for snap in reversed(store.price_history(SACK_OF_GEMS_APPID, SACK_OF_GEMS_HASH)):
        if snap.lowest is not None and snap.lowest.currency == currency:
            return snap
    return None


def refresh_sack_price(
    store: Store,
    client: SafeClient,
    *,
    currency: str = "USD",
    force: bool = False,
) -> PriceSnapshot | None:
    """Fetch (opt-in, guarded) and return the current Sack-of-Gems price in ``currency``.

    Reuses the guarded :func:`refresh_prices` egress path (single item, TTL-cached) so it
    inherits the same rate-politeness and 429-hard-stop as every other price fetch.
    """
    from ..sources.steam_market import refresh_prices

    refresh_prices(store, client, [sack_of_gems_item()], currency, force=force)
    return latest_sack_price(store, currency=currency)
