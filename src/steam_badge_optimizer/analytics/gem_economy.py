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
a purchase uses gross; the net figure estimates resale proceeds after fee. Inventory
valuation marks a held gem stash to market at **gross** (the card path likewise values at
the lowest ask, not net), so the two bases stay consistent — use net only when the question
is explicitly "what would I clear by selling?".
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
    "MIN_LISTING_CENTS",
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
    "seller_net_cents",
]

#: A "Sack of Gems" bundles exactly this many gems into one marketable item.
GEMS_PER_SACK = 1000

#: The Sack of Gems is a community item under appid 753 (like cards), not the game appid.
SACK_OF_GEMS_APPID = 753
SACK_OF_GEMS_HASH = "753-Sack of Gems"

#: Steam's headline market fee (5% Steam + 10% game), kept for documentation and for the
#: human-readable "~15%" phrasing in reports. Do NOT divide by it to get seller proceeds:
#: each component carries a 1-cent minimum, so the real fee on a cheap item is far above
#: 15%. Use :func:`seller_net_cents` instead.
STEAM_MARKET_FEE = Decimal("0.15")

#: Steam refuses listings below this price, so there is no seller-proceeds figure under it.
MIN_LISTING_CENTS = 3


def _fee_cents(net_cents: int) -> int:
    """Steam's fee on a sale netting ``net_cents``: 5% + 10%, each floored at 1 cent."""
    return max(1, net_cents * 5 // 100) + max(1, net_cents * 10 // 100)


def seller_net_cents(buyer_cents: int) -> int:
    """What a seller actually receives when a buyer pays ``buyer_cents``.

    Steam computes its 5% cut and the game's 10% cut from the seller's proceeds, and each
    is floored at 1 cent — so the minimum total fee on any sale is 2 cents regardless of
    price. A flat ``price / 1.15`` therefore overstates proceeds badly at the low end:
    at a 3-cent ask it claims 3 cents when the seller nets 1.

    This inverts the fee exactly in integer cents — the largest ``net`` whose fees still
    fit inside ``buyer_cents`` — rather than approximating. Returns 0 below Steam's
    3-cent minimum listing price, where no sale is possible.
    """
    if buyer_cents < MIN_LISTING_CENTS:
        return 0
    # Seed from the flat approximation, then correct in both directions so the result is
    # exact regardless of which side the seed lands on.
    net = max(1, buyer_cents * 100 // 115)
    while net > 1 and net + _fee_cents(net) > buyer_cents:
        net -= 1
    while net + _fee_cents(net + 1) + 1 <= buyer_cents:
        net += 1
    return net if net + _fee_cents(net) <= buyer_cents else 0


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

    Uses the Sack's price as-is for the gross figure. The resale figure nets Steam's fee
    from the SACK price and only then divides: the fee's per-component 1-cent minimums
    apply to the actual transaction (one sack sale), so netting a fractional per-gem
    amount would silently drop them. The currency is carried through unchanged.
    """
    gross = Decimal(sack_price.cents) / GEMS_PER_SACK
    net = Decimal(seller_net_cents(sack_price.cents)) / GEMS_PER_SACK
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

    Delegates to the currency-aware :meth:`Store.latest_price`, which skips stray fetches in
    other currencies so a later one can't mask a usable price.
    """
    return store.latest_price(SACK_OF_GEMS_APPID, SACK_OF_GEMS_HASH, currency=currency)


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
