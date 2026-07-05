"""Cost-to-complete calculator (Epic 5.1).

For each game badge, compute what it would cost to raise it from its current level to
a target level by buying the missing cards. Fully offline — it reads only cached Store
data (catalog set sizes, inventory, badge progress, latest prices).

Design decisions (per the approving vote — fail closed, never mislead a purchase):

* Crafting one badge level consumes one full set, so reaching level ``T`` from level
  ``L`` needs ``T - L`` copies of each card. ``missing = max(0, (T-L) - owned)``.
* A badge is **complete** (safely costable) only when every card in the set is known
  *and* every still-needed card has a price. If any card name is unknown (we know the
  set size ``N`` but not all ``N`` market hash names) or any needed card is unpriced /
  unmarketable, the badge is **incomplete** — reported as "needs discovery/pricing",
  never given a fabricated cost. The greedy optimizer only ranks complete badges.
* Prices are integer cents (exact). Confidence is a coarse, deterministic High/Med/Low
  data-quality signal; incompleteness or a missing/stale price floors it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from typing import TYPE_CHECKING

from ..config import MAX_NORMAL_BADGE_LEVEL, XP_PER_BADGE_LEVEL
from ..models import Confidence, Money, PurchaseCandidate

if TYPE_CHECKING:
    from ..db import Store

__all__ = ["BadgeCost", "CostReport", "compute_costs"]

# Conservative multi-unit (order-book-walk) cost model (#15). priceoverview `lowest` is a
# single-unit ask, so buying k copies costs more than k*lowest. We price the first copy at
# `lowest` and each additional copy at a book-walk proxy: the median (the typical
# transacted price), but capped at DEPTH_CAP x lowest so a spiky median can't wildly
# over-estimate and steer users off a genuinely cheap badge. With no median, use a
# documented inflation. The estimate is always >= k*lowest (never undershoots) and
# monotonic non-decreasing in quantity.
MULTI_UNIT_DEPTH_CAP = 2.0  # an extra copy costs at most 2x the lowest ask
MULTI_UNIT_INFLATION = 1.15  # +15% per extra copy when no median proxy is available


def _multi_unit_line_cents(base_cents: int, median_cents: int | None, qty: int) -> int:
    """Conservative cost in cents for buying ``qty`` copies given the single-unit price."""
    if qty <= 0:
        return 0
    if qty == 1:
        return base_cents
    cap = ceil(base_cents * MULTI_UNIT_DEPTH_CAP)
    if median_cents is not None and median_cents >= base_cents:
        extra = min(median_cents, cap)
    else:
        extra = ceil(base_cents * MULTI_UNIT_INFLATION)
    extra = max(extra, base_cents)  # additional copies never cost less than the lowest ask
    return base_cents + (qty - 1) * extra


@dataclass(frozen=True, slots=True)
class BadgeCost:
    appid: int
    current_level: int
    target_level: int
    set_size: int
    known_cards: int
    crafts_needed: int
    candidates: list[PurchaseCandidate]
    known_cost: Money | None
    complete: bool
    ready_to_craft: bool
    confidence: Confidence
    notes: list[str] = field(default_factory=list)

    @property
    def expected_xp(self) -> int:
        return self.crafts_needed * XP_PER_BADGE_LEVEL

    @property
    def estimated_cost(self) -> Money | None:
        """The cost the optimizer may rely on — only defined for a complete badge."""
        return self.known_cost if self.complete else None


@dataclass(frozen=True, slots=True)
class CostReport:
    badges: list[BadgeCost]
    currency: str

    def complete_badges(self) -> list[BadgeCost]:
        return [b for b in self.badges if b.complete and b.crafts_needed > 0]

    def incomplete_badges(self) -> list[BadgeCost]:
        return [b for b in self.badges if not b.complete and b.crafts_needed > 0]

    def ready_to_craft(self) -> list[BadgeCost]:
        return [b for b in self.badges if b.ready_to_craft]


def _confidence(
    *, complete: bool, any_stale: bool, low_volume: bool, level_assumed: bool
) -> Confidence:
    # Completeness/assumption problems floor confidence; otherwise price quality decides.
    if not complete or level_assumed:
        return Confidence.LOW
    if any_stale or low_volume:
        return Confidence.MEDIUM
    return Confidence.HIGH


def _sum_money(amounts: list[Money], currency: str) -> Money:
    return Money(sum(a.cents for a in amounts), currency)


def compute_costs(
    store: Store,
    *,
    target_level: int = MAX_NORMAL_BADGE_LEVEL,
    currency: str = "USD",
    min_volume: int = 1,
) -> CostReport:
    """Compute per-badge completion costs from cached Store data.

    Foil badges are out of scope for now (thin markets, separate level semantics), so
    only normal cards and normal badge progress are considered — foil support lands as
    its own feature.
    """
    if not (0 <= target_level <= MAX_NORMAL_BADGE_LEVEL):
        raise ValueError(f"target_level must be 0..{MAX_NORMAL_BADGE_LEVEL}, got {target_level}")
    badges: list[BadgeCost] = []
    for badge_set in store.list_badge_sets():
        appid = badge_set.appid
        progress = store.get_badge_progress(appid)
        level_assumed = progress is None
        current_level = progress.level if progress else 0
        notes: list[str] = []
        if level_assumed:
            notes.append("badge level unknown; assumed 0 (import badge progress to refine)")

        crafts_needed = max(0, target_level - current_level)
        known = store.cards_for_app(appid, include_foil=False)
        owned = store.inventory_for_app(appid)
        known_names = {c.market_hash_name for c in known}
        cards_unknown = badge_set.set_size - len(known_names)

        candidates: list[PurchaseCandidate] = []
        line_costs: list[Money] = []
        any_stale = False
        low_volume = False
        uncostable = False
        multi_unit = False

        for card in known:
            missing = max(0, crafts_needed - owned.get(card.market_hash_name, 0))
            if missing == 0:
                continue
            if not card.marketable:
                uncostable = True
                notes.append(f"{card.market_hash_name} is unmarketable")
                continue
            snap = store.latest_price(appid, card.market_hash_name)
            unit = (snap.lowest or snap.median) if snap else None
            if unit is None:
                uncostable = True
                notes.append(f"{card.market_hash_name} has no cached price")
                continue
            if unit.currency != currency:
                # Fail closed: never sum a foreign-currency price into this total.
                uncostable = True
                notes.append(f"{card.market_hash_name} priced in {unit.currency}, not {currency}")
                continue
            if snap is not None and snap.is_stale():
                any_stale = True
            if snap is not None and (snap.volume is None or snap.volume < min_volume):
                low_volume = True
            higher = (
                snap.median.cents
                if (snap is not None and snap.lowest is not None and snap.median is not None)
                else None
            )
            line = Money(_multi_unit_line_cents(unit.cents, higher, missing), currency)
            candidates.append(
                PurchaseCandidate(
                    appid=appid,
                    market_hash_name=card.market_hash_name,
                    missing_quantity=missing,
                    estimated_unit_price=unit,
                    estimated_line_total=line,  # modeled total, so reports match the plan
                    confidence=Confidence.MEDIUM,
                )
            )
            line_costs.append(line)
            if missing > 1:
                multi_unit = True

        if multi_unit:
            notes.append(
                "multi-copy cost is a conservative model (units past the first assume a "
                "book-walk toward the median, capped) — modeled, not order-book-measured"
            )

        if cards_unknown > 0:
            notes.append(
                f"{cards_unknown} of {badge_set.set_size} card names unknown "
                "(needs inventory/card-name discovery)"
            )
        elif cards_unknown < 0:
            notes.append(
                f"{len(known_names)} known cards exceed catalog set size "
                f"{badge_set.set_size} (data mismatch); treated as incomplete"
            )

        complete = cards_unknown == 0 and not uncostable and crafts_needed > 0
        known_cost = _sum_money(line_costs, currency) if line_costs else Money(0, currency)
        # Ready to craft the next level now: full set known and at least one of each owned.
        ready_to_craft = (
            cards_unknown == 0
            and crafts_needed > 0
            and all(owned.get(c.market_hash_name, 0) >= 1 for c in known)
        )
        confidence = _confidence(
            complete=complete,
            # A floor estimate (multi-unit) is uncertain, so treat it like a
            # low-volume quote — it can't earn HIGH confidence.
            any_stale=any_stale,
            low_volume=low_volume or multi_unit,
            level_assumed=level_assumed,
        )

        badges.append(
            BadgeCost(
                appid=appid,
                current_level=current_level,
                target_level=target_level,
                set_size=badge_set.set_size,
                known_cards=len(known_names),
                crafts_needed=crafts_needed,
                candidates=candidates,
                known_cost=known_cost,
                complete=complete,
                ready_to_craft=ready_to_craft,
                confidence=confidence,
                notes=notes,
            )
        )
    return CostReport(badges=badges, currency=currency)
