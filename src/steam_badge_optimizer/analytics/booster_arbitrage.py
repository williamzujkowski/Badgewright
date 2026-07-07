"""Flag Booster Packs that sell for less than their card contents are worth (#98).

Increment 3 of the arbitrage epic (#94). A Booster Pack unpacks into
:data:`CARDS_PER_BOOSTER` cards drawn from the game's set. If the pack's market price is
below the expected net resale value of those cards, buying the pack and reselling the
cards is (in expectation) an arbitrage. This module computes that comparison from cached
card floors + a freshly fetched booster quote and flags the profitable, liquid ones.

**Research, not advice — Badgewright never trades.** The expectation is exact
(``E[value] = 3 * mean(card asks)`` by linearity, so mean — not median — is the right
estimator), but the money figure is an **optimistic ceiling, not a conservative floor**:
it values each drawn card at the *current lowest ask*, which you can only realize by
undercutting it, and it ignores that dumping three cards depresses that floor. Worse, when
one card dominates a set the EV is positive while the *median* single-pack outcome is a
loss (you most likely draw three cheap cards) — so a positive margin is flagged with a
skew warning and confidence is capped at LOW. A pack is only "actionable"-flagged when it
is buyable AND every card in the set has real resale **demand** (24h volume), never on
ask-depth alone (asks are competition when you're selling, not liquidity). Turning cards
into gems is an alternative exit but needs the card goo value (open spike #100), so only
the resale exit is modeled here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from ..models import Confidence, Money
from .gem_economy import STEAM_MARKET_FEE

if TYPE_CHECKING:
    from ..db import Store
    from ..sources.booster_market import BoosterQuote

__all__ = ["CARDS_PER_BOOSTER", "BoosterArbitrage", "evaluate_booster", "scan_booster_arbitrage"]

#: A Booster Pack unpacks into this many cards.
CARDS_PER_BOOSTER = 3
#: Minimum depth to treat a pack as buyable (its asks) or a card as resellable (its 24h
#: volume / demand). Deliberately above a token 1-2: a "liquid" resale needs real demand,
#: not two stale listings, or dumping three cards just craters the floor we valued against.
#: This bar is STRICTER than cheapest_badges.MIN_LISTINGS (2): there the signal is
#: *buyability* (asks-or-volume), here it is *resale demand* (volume only), which is scarcer.
MIN_LISTINGS = 5
#: When the priciest card is this many times the set mean, value is concentrated in one
#: card: EV is positive but the median single-pack outcome is a loss. Flag it.
SKEW_FLAG = 2.0


@dataclass(frozen=True, slots=True)
class BoosterArbitrage:
    appid: int
    set_size: int
    booster_cost: Money  # what you pay for the pack (its lowest ask)
    contents_ev_net: Money  # expected net resale of CARDS_PER_BOOSTER cards, after fee
    margin_cents: int  # contents_ev_net - booster_cost (SIGNED; positive = arbitrage)
    liquid: bool
    confidence: Confidence
    signals: list[str] = field(default_factory=list)

    @property
    def profitable(self) -> bool:
        return self.margin_cents > 0


def evaluate_booster(
    card_lowest_cents: list[int],
    booster_lowest_cents: int,
    *,
    currency: str,
    fee: Decimal = STEAM_MARKET_FEE,
) -> tuple[Money, Money, int]:
    """Return (contents_ev_net, booster_cost, signed_margin_cents).

    Contents EV = ``CARDS_PER_BOOSTER`` * mean(card lowest asks), netted of the seller fee
    (``/ (1 + fee)``, matching the gem layer). Margin is the net EV minus the pack cost.
    """
    if not card_lowest_cents:
        raise ValueError("need at least one card price to estimate contents value")
    if booster_lowest_cents < 0:
        raise ValueError("booster_lowest_cents must be >= 0")
    mean_card = Decimal(sum(card_lowest_cents)) / len(card_lowest_cents)
    gross_ev = CARDS_PER_BOOSTER * mean_card
    net_ev = gross_ev / (Decimal(1) + fee)
    net_ev_cents = int(net_ev.to_integral_value(rounding=ROUND_HALF_UP))
    return (
        Money(net_ev_cents, currency),
        Money(booster_lowest_cents, currency),
        net_ev_cents - booster_lowest_cents,
    )


def scan_booster_arbitrage(
    store: Store,
    quotes: dict[int, BoosterQuote],
    *,
    currency: str = "USD",
    min_listings: int = MIN_LISTINGS,
    top: int = 50,
) -> list[BoosterArbitrage]:
    """Rank games where the Booster Pack looks cheaper than its card contents (research only).

    ``quotes`` maps appid -> freshly-fetched :class:`BoosterQuote`. Card floors come from the
    Store's cached prices (currency-matched). Games without a full set of priced cards, or
    whose quote is in another currency, are skipped. Liquid+profitable rank first.
    """
    if min_listings < 1:
        raise ValueError("min_listings must be >= 1")
    # The catalog set size is the floor for a full set; a partial discovery would bias the
    # EV low over a cheap subset (like the sibling modules, we require completeness).
    set_sizes = {bs.appid: bs.set_size for bs in store.list_badge_sets()}
    out: list[BoosterArbitrage] = []
    for appid, quote in quotes.items():
        if quote.currency != currency:
            continue
        cards = store.cards_for_app(appid, include_foil=False)
        if not cards:
            continue
        expected = set_sizes.get(appid)
        if expected and len(cards) < expected:
            continue  # incomplete set — EV would be biased low; skip rather than mislead

        card_lowest: list[int] = []
        card_liquidity: list[tuple[int | None, int | None]] = []  # (asks, volume)
        complete = True
        for card in cards:
            latest = store.latest_price(appid, card.market_hash_name, currency=currency)
            if latest is None or latest.lowest is None:
                complete = False
                break
            card_lowest.append(latest.lowest.cents)
            hist = store.price_history(appid, card.market_hash_name)
            asks = next((s.listings for s in reversed(hist) if s.listings is not None), None)
            vol = next((s.volume for s in reversed(hist) if s.volume is not None), None)
            card_liquidity.append((asks, vol))
        if not complete or not card_lowest:
            continue

        ev_net, cost, margin = evaluate_booster(card_lowest, quote.lowest_cents, currency=currency)

        # Buying the pack: its ASK depth is availability. Reselling the cards: only 24h
        # VOLUME (demand) counts — ask depth is competition, not liquidity, when selling.
        def _has_demand(pair: tuple[int | None, int | None]) -> bool:
            _asks, vol = pair
            return vol is not None and vol >= min_listings

        booster_liquid = quote.listings is not None and quote.listings >= min_listings
        cards_liquid = all(_has_demand(p) for p in card_liquidity)
        liquid = booster_liquid and cards_liquid

        signals: list[str] = ["optimistic: 3 random cards resold at lowest ask, net of fee"]
        if not booster_liquid:
            signals.append(f"thin booster: {quote.listings} listing(s) (< {min_listings})")
        if not cards_liquid:
            signals.append(f"resale demand unconfirmed: a card has < {min_listings} 24h sales")
        # Skew: is one card worth >= SKEW_FLAG x the set mean? Compared as integers
        # (max*n >= SKEW_FLAG*total) to avoid a float mean in this Decimal-strict module.
        total_lowest = sum(card_lowest)
        if total_lowest > 0 and max(card_lowest) * len(card_lowest) >= SKEW_FLAG * total_lowest:
            signals.append("value concentrated in one card — the median pack is likely a loss")

        # Never above LOW: even a liquid, profitable-in-expectation flag is an optimistic
        # ceiling on a high-variance 3-card draw.
        confidence = Confidence.LOW
        out.append(
            BoosterArbitrage(
                appid=appid,
                set_size=len(cards),
                booster_cost=cost,
                contents_ev_net=ev_net,
                margin_cents=margin,
                liquid=liquid,
                confidence=confidence,
                signals=signals,
            )
        )
    # Liquid+profitable first, then by margin (highest first), then appid.
    out.sort(key=lambda b: (not (b.liquid and b.profitable), -b.margin_cents, b.appid))
    return out[:top]
