"""Rank the cheapest badges to make from scratch (Tier 2 of Epic #71).

Source-agnostic: given whatever per-card lowest prices + ask-side depth the Store holds
(from card discovery + pricing today, or a bulk market sweep later), this computes, per
game, the cost to craft one full badge set (buy one of each card) and ranks the cheapest.

Crafting one set = one badge level = a flat ``XP_PER_BADGE_LEVEL`` XP, so ranking by set
cost is the same as ranking by cost-per-XP.

Liquidity gate (the vote's key condition): a "cheap" badge whose cards have almost no
listings is not actually buyable — a single listing vanishes on purchase. So a set is
**liquid** only if every card has at least :data:`MIN_LISTINGS` asks (using ``listings``
from the market search, or 24h ``volume`` as a fallback proxy). Illiquid sets are flagged
and never rank ahead of liquid ones — they can't masquerade as the cheapest badge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..config import XP_PER_BADGE_LEVEL
from ..models import Confidence, Money

if TYPE_CHECKING:
    from ..db import Store

__all__ = ["MIN_LISTINGS", "BadgeSetCost", "rank_cheapest_badges"]

MIN_LISTINGS = 2  # a set needs at least this many asks per card to be reliably buyable
DOMINANCE_FLAG = 0.4  # one card >= 40% of set cost = bottleneck


@dataclass(frozen=True, slots=True)
class BadgeSetCost:
    appid: int
    set_size: int
    total_cost: Money
    cost_per_xp_cents: float  # set cost / XP for one craft
    min_liquidity: int | None  # min asks/volume across the set's cards (None = unknown)
    liquid: bool
    bottleneck_fraction: float | None  # max card cost / total
    confidence: Confidence
    signals: list[str] = field(default_factory=list)


def rank_cheapest_badges(
    store: Store,
    *,
    currency: str = "USD",
    min_listings: int = MIN_LISTINGS,
    top: int = 50,
) -> list[BadgeSetCost]:
    """Rank fully-known, fully-priced badge sets cheapest-first (research only).

    Liquid sets rank ahead of illiquid ones; within each group, cheapest first.
    """
    if top <= 0:
        raise ValueError("top must be positive")
    if min_listings < 1:
        raise ValueError("min_listings must be >= 1 (a threshold of 0 would trust every set)")
    out: list[BadgeSetCost] = []
    for badge_set in store.list_badge_sets():
        appid = badge_set.appid
        cards = store.cards_for_app(appid, include_foil=False)
        # Need at least the catalog's card count, and (below) every discovered card priced.
        # The market is authoritative for the actual card list — some games legitimately have
        # more normal cards than the (sometimes stale) catalog count, so we cost ALL of them
        # (conservative: never under-costs) rather than dropping the badge on an exact mismatch.
        if badge_set.set_size == 0 or len(cards) < badge_set.set_size:
            continue  # not fully known — can't cost the whole set

        unit_cents: list[int] = []
        card_liquidity: list[tuple[int | None, int | None]] = []  # (asks, volume) per card
        complete = True
        for card in cards:
            hist = store.price_history(appid, card.market_hash_name)
            if not hist:
                complete = False
                break
            latest = hist[-1]
            # Cost basis is the CURRENT lowest ask ONLY — never the median. A median is a
            # past sale that can sit below the lowest ask (or exist with no ask at all), so
            # using it would present a price a buyer cannot fill. No current ask => unbuyable.
            if latest.lowest is None or latest.lowest.currency != currency:
                complete = False
                break
            unit_cents.append(latest.lowest.cents)
            # Liquidity is buyability: the most recent ASK count (from any snapshot — search
            # gives listings, priceoverview doesn't, so enrichment must not lose it), with
            # 24h volume as a secondary signal. A card is buyable if EITHER is adequate.
            asks = next((s.listings for s in reversed(hist) if s.listings is not None), None)
            vol = next((s.volume for s in reversed(hist) if s.volume is not None), None)
            card_liquidity.append((asks, vol))
        if not complete or not unit_cents:
            continue

        total = sum(unit_cents)
        signals: list[str] = []
        if len(cards) != badge_set.set_size:
            signals.append(
                f"market has {len(cards)} cards; catalog says {badge_set.set_size} "
                "(costing all market cards)"
            )

        def _known(pair: tuple[int | None, int | None]) -> bool:
            return pair[0] is not None or pair[1] is not None

        def _buyable(pair: tuple[int | None, int | None]) -> bool:
            asks, vol = pair
            return (asks is not None and asks >= min_listings) or (
                vol is not None and vol >= min_listings
            )

        known_signals = [v for pair in card_liquidity for v in pair if v is not None]
        min_liq = min(known_signals) if known_signals else None
        any_unknown = any(not _known(p) for p in card_liquidity)
        # Liquid only if EVERY card is known-and-buyable (a card with no depth data at all is
        # unbuyable-until-proven and must not be silently excluded).
        liquid = not any_unknown and all(_buyable(p) for p in card_liquidity)
        if any_unknown:
            signals.append("liquidity unknown for a card — can't confirm it's buyable")
        elif not liquid:
            signals.append(f"thin: a card has only {min_liq} listing(s)/sale(s) (< {min_listings})")

        bottleneck = max(unit_cents) / total if total > 0 else None
        if bottleneck is not None and bottleneck >= DOMINANCE_FLAG:
            signals.append(f"one card is {bottleneck * 100:.0f}% of set cost (bottleneck)")

        confidence = (
            Confidence.HIGH
            if liquid and not signals
            else (Confidence.MEDIUM if liquid else Confidence.LOW)
        )
        out.append(
            BadgeSetCost(
                appid=appid,
                set_size=len(cards),  # the actual number of cards costed (market truth)
                total_cost=Money(total, currency),
                cost_per_xp_cents=total / XP_PER_BADGE_LEVEL,
                min_liquidity=min_liq,
                liquid=liquid,
                bottleneck_fraction=bottleneck,
                confidence=confidence,
                signals=signals,
            )
        )
    # Liquid sets first (illiquid can never be the "cheapest"), then by set cost, then appid.
    out.sort(key=lambda b: (not b.liquid, b.total_cost.cents, b.appid))
    return out[:top]
