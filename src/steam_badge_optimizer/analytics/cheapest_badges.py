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
        if len(cards) != badge_set.set_size or badge_set.set_size == 0:
            continue  # not fully known — can't cost the whole set

        unit_cents: list[int] = []
        liquidity: list[int | None] = []
        complete = True
        for card in cards:
            snap = store.latest_price(appid, card.market_hash_name)
            if snap is None:
                complete = False
                break
            unit = snap.lowest or snap.median
            if unit is None or unit.currency != currency:
                complete = False
                break
            unit_cents.append(unit.cents)
            # Prefer ask-side depth (listings); fall back to 24h volume as a proxy.
            liquidity.append(snap.listings if snap.listings is not None else snap.volume)
        if not complete or not unit_cents:
            continue

        total = sum(unit_cents)
        signals: list[str] = []
        known_liq = [x for x in liquidity if x is not None]
        min_liq = min(known_liq) if known_liq else None
        any_unknown = any(x is None for x in liquidity)
        # A set is liquid only if EVERY card is known-and-liquid — a card with no depth
        # data is unbuyable-until-proven, so it must NOT be silently excluded from the gate.
        liquid = not any_unknown and all(x >= min_listings for x in known_liq)
        if any_unknown:
            signals.append("liquidity unknown for a card — can't confirm it's buyable")
        elif not liquid:
            signals.append(f"thin: a card has only {min_liq} listing(s) (< {min_listings}) — risky")

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
                set_size=badge_set.set_size,
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
