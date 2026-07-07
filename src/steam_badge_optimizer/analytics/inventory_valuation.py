"""Value the user's held card inventory against the live market (research only).

Increment 2a of the arbitrage epic (#94, #97): given the cards a user holds
(``user_card_inventory``) and whatever prices the Store has cached, compute what each
holding is worth at the current market floor and the portfolio total. This is descriptive
valuation only — Badgewright never buys, sells, or advises; you act manually in Steam.

Valuation basis is the current **lowest ask** (what a copy would sell into / cost to
replace), matching the cost basis used elsewhere. A holding whose price isn't cached in
the requested currency is reported as *unpriced* (never valued at zero), so the total is
always a floor over the priced subset and the unpriced count is visible.

Non-card holdings (increment 2b-ii, #97) are valued too: **gems** are marked to the
Sack-of-Gems price via the gem layer (``quantity`` gems x per-gem), and booster packs /
sacks / other marketable items at their own cached lowest ask. Card->gem *yield*
comparison is still separate (open until #101 lands the goo-value reader).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models import ItemKind, Money
from .gem_economy import gem_value, gems_to_money, latest_sack_price

if TYPE_CHECKING:
    from ..db import Store

__all__ = ["HoldingValue", "InventoryValuation", "value_inventory"]


@dataclass(frozen=True, slots=True)
class HoldingValue:
    appid: int
    market_hash_name: str
    quantity: int
    is_foil: bool
    unit_price: Money | None  # latest lowest ask in the requested currency, else None
    line_value: Money | None  # unit_price * quantity, else None (unpriced)
    kind: str = "card"  # "card" or an ItemKind value (booster_pack, gems, sack_of_gems, other)
    signals: list[str] = field(default_factory=list)

    @property
    def priced(self) -> bool:
        return self.line_value is not None


@dataclass(frozen=True, slots=True)
class InventoryValuation:
    currency: str
    total_value: Money  # sum over priced holdings (a floor; unpriced excluded)
    priced_count: int
    unpriced_count: int
    holdings: list[HoldingValue]


def value_inventory(
    store: Store,
    *,
    currency: str = "USD",
    top: int | None = None,
) -> InventoryValuation:
    """Value every held card at its latest cached lowest ask (research only).

    Holdings are returned most-valuable first, with unpriced holdings last. ``top`` caps
    how many holdings are returned (the totals still reflect the whole inventory).
    """
    holdings: list[HoldingValue] = []
    total_cents = 0
    priced_count = 0
    unpriced_count = 0

    for inv in store.list_inventory():
        if inv.quantity <= 0:
            continue  # nothing actually held
        # Currency-aware: the newest snapshot whose lowest ask is in `currency`, so a stray
        # fetch in another currency can't mask a usable price (mirrors the gem layer).
        latest = store.latest_price(inv.appid, inv.market_hash_name, currency=currency)
        unit: Money | None = None
        line: Money | None = None
        signals: list[str] = []
        if latest is not None and latest.lowest is not None and latest.lowest.currency == currency:
            unit = latest.lowest
            line = Money(unit.cents * inv.quantity, currency)
            total_cents += line.cents
            priced_count += 1
        else:
            unpriced_count += 1
            signals.append(f"unpriced (no cached {currency} market price)")
        holdings.append(
            HoldingValue(
                appid=inv.appid,
                market_hash_name=inv.market_hash_name,
                quantity=inv.quantity,
                is_foil=inv.is_foil,
                unit_price=unit,
                line_value=line,
                signals=signals,
            )
        )

    # Non-card holdings: gems are marked to the Sack-of-Gems price; everything else
    # (booster packs, sacks, backgrounds/emoticons) at its own cached lowest ask.
    sack = latest_sack_price(store, currency=currency)
    per_gem = gem_value(sack.lowest) if sack is not None and sack.lowest is not None else None
    for held in store.list_item_holdings():
        if held.quantity <= 0:
            continue
        unit = None
        line = None
        signals = []
        if held.kind is ItemKind.GEMS:
            if per_gem is not None:
                line = gems_to_money(held.quantity, per_gem)  # mark-to-market via the Sack
                total_cents += line.cents
                priced_count += 1
            else:
                unpriced_count += 1
                signals.append("unpriced (no cached Sack-of-Gems price)")
        else:
            latest = store.latest_price(held.appid, held.market_hash_name, currency=currency)
            if latest is not None and latest.lowest is not None:
                unit = latest.lowest
                line = Money(unit.cents * held.quantity, currency)
                total_cents += line.cents
                priced_count += 1
            else:
                unpriced_count += 1
                signals.append(f"unpriced (no cached {currency} market price)")
        holdings.append(
            HoldingValue(
                appid=held.appid,
                market_hash_name=held.market_hash_name,
                quantity=held.quantity,
                is_foil=False,
                unit_price=unit,
                line_value=line,
                kind=str(held.kind),
                signals=signals,
            )
        )

    # Most valuable first; unpriced holdings (line_value None) sort last. Tiebreak on
    # (appid, name) so equal-value holdings are deterministically ordered.
    holdings.sort(
        key=lambda h: (
            h.line_value is None,
            -(h.line_value.cents if h.line_value else 0),
            h.appid,
            h.market_hash_name,
        )
    )
    if top is not None:
        holdings = holdings[:top]
    return InventoryValuation(
        currency=currency,
        total_value=Money(total_cents, currency),
        priced_count=priced_count,
        unpriced_count=unpriced_count,
        holdings=holdings,
    )
