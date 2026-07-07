"""Flag cards cheaper to buy than the gems they yield (card->gem arbitrage, #101-ii).

For a card with a cached goo value (#101-i), its market lowest ask, and a cached
Sack-of-Gems price (for the per-gem rate), compare the card's cost to the market value of
the gems it would yield: ``goo_value x per_gem``. When the card is cheaper, buying it and
turning it into gems is (at current gem prices) an arbitrage.

**Research, not advice — Badgewright never turns a card into gems.** This is almost
entirely a **foil** phenomenon: a normal card yields only a handful of gems (worth a
sub-cent), so it can essentially never beat its market ask; foils yield ~10x. The gem
value is marked to market (gross); *realizing* it means selling the gems on as a Sack, which
nets the ~15% fee — so the margin is an optimistic ceiling and confidence is capped at LOW.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP
from typing import TYPE_CHECKING

from ..models import Confidence, Money
from .gem_economy import gem_value, latest_sack_price

if TYPE_CHECKING:
    from ..db import Store

__all__ = ["CardGemArbitrage", "scan_card_gem_arbitrage"]


@dataclass(frozen=True, slots=True)
class CardGemArbitrage:
    appid: int
    market_hash_name: str
    is_foil: bool
    card_cost: Money  # market lowest ask
    goo_value: int  # gems the card yields
    gem_value: Money  # goo_value x per-gem, marked to market (gross)
    margin_cents: int  # gem_value - card_cost (SIGNED; positive = arbitrage)
    confidence: Confidence
    signals: list[str] = field(default_factory=list)

    @property
    def profitable(self) -> bool:
        return self.margin_cents > 0


def scan_card_gem_arbitrage(
    store: Store,
    *,
    currency: str = "USD",
    foil_only: bool = True,
    top: int = 50,
) -> list[CardGemArbitrage]:
    """Rank cards whose gem yield is worth more than their market ask (research only).

    Needs a cached Sack-of-Gems price (for the per-gem rate); returns [] without one. Only
    cards with BOTH a cached goo value and a current lowest ask in ``currency`` are scored.
    Profitable cards rank first, by margin.
    """
    sack = latest_sack_price(store, currency=currency)
    if sack is None or sack.lowest is None:
        return []  # can't value gems without a Sack price
    per_gem = gem_value(sack.lowest).cents_per_gem  # Decimal cents/gem (gross)

    out: list[CardGemArbitrage] = []
    for card in store.list_cards(foil_only=foil_only):
        goo = store.goo_value_for(card.appid, card.market_hash_name)
        if goo is None:
            continue
        latest = store.latest_price(card.appid, card.market_hash_name, currency=currency)
        if latest is None or latest.lowest is None or latest.lowest.currency != currency:
            continue
        gem_value_cents = int((per_gem * goo.goo_value).to_integral_value(rounding=ROUND_HALF_UP))
        margin = gem_value_cents - latest.lowest.cents
        signals = ["gem value is mark-to-market; realizing it means selling gems (net ~15% less)"]
        if not card.is_foil:
            signals.append("normal card: gem yield is tiny — arbitrage here is unlikely")
        out.append(
            CardGemArbitrage(
                appid=card.appid,
                market_hash_name=card.market_hash_name,
                is_foil=card.is_foil,
                card_cost=latest.lowest,
                goo_value=goo.goo_value,
                gem_value=Money(max(gem_value_cents, 0), currency),
                margin_cents=margin,
                confidence=Confidence.LOW,  # gem prices move; speculative, never above LOW
                signals=signals,
            )
        )
    out.sort(key=lambda a: (-a.margin_cents, a.appid, a.market_hash_name))
    return out[:top]
