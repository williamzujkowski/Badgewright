"""Flag cards cheaper to buy than the gems they yield (card->gem arbitrage, #101-ii).

For a card with a cached goo value (#101-i), its market lowest ask, and a cached
Sack-of-Gems price (for the per-gem rate), compare the card's cost to the market value of
the gems it would yield: ``goo_value x per_gem``. When the card is cheaper, buying it and
turning it into gems is (at current gem prices) an arbitrage.

**Research, not advice — Badgewright never turns a card into gems.** This is almost
entirely a **foil** phenomenon: a normal card yields only a handful of gems (worth a
sub-cent), so it can essentially never beat its market ask; foils yield ~10x. Realizing the
gems as cash means selling them on as a Sack, which nets Steam's ~15% fee — so the ``ARB``
flag is computed on the **net** per-gem value (like the booster-arbitrage sale), not the
gross mark-to-market ceiling, to avoid flagging cards that lose money after the fee. Gem
prices move, so confidence is capped at LOW regardless.
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
    gem_value: Money  # goo_value x per-gem NET of the ~15% sale fee (realizable cash)
    margin_cents: int  # gem_value(net) - card_cost (SIGNED; positive = arbitrage)
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
    gv = gem_value(sack.lowest)
    per_gem_net = gv.net_cents_per_gem  # what you'd clear selling the gems on (after fee)
    per_gem_gross = gv.cents_per_gem  # mark-to-market ceiling (before fee)

    out: list[CardGemArbitrage] = []
    for card in store.list_cards(foil_only=foil_only):
        goo = store.goo_value_for(card.appid, card.market_hash_name)
        if goo is None:
            continue
        latest = store.latest_price(card.appid, card.market_hash_name, currency=currency)
        if latest is None or latest.lowest is None or latest.lowest.currency != currency:
            continue
        net_cents = int((per_gem_net * goo.goo_value).to_integral_value(rounding=ROUND_HALF_UP))
        gross_cents = int((per_gem_gross * goo.goo_value).to_integral_value(rounding=ROUND_HALF_UP))
        # Flag on the NET realizable value (selling the gems costs the ~15% fee), never gross,
        # so a card in the gross-but-not-net band isn't mislabeled a profit.
        margin = net_cents - latest.lowest.cents
        signals = [f"net of the ~15% gem-sale fee (gross ceiling ≈ {gross_cents / 100:.2f})"]
        if not card.is_foil:
            signals.append("normal card: gem yield is tiny — arbitrage here is unlikely")
        out.append(
            CardGemArbitrage(
                appid=card.appid,
                market_hash_name=card.market_hash_name,
                is_foil=card.is_foil,
                card_cost=latest.lowest,
                goo_value=goo.goo_value,
                gem_value=Money(max(net_cents, 0), currency),  # net realizable value
                margin_cents=margin,
                confidence=Confidence.LOW,  # gem prices move; speculative, never above LOW
                signals=signals,
            )
        )
    out.sort(key=lambda a: (-a.margin_cents, a.appid, a.market_hash_name))
    return out[:top]
