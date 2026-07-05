"""Shared row model for purchase-plan exports.

Turns an :class:`OptimizationPlan` into flat, format-agnostic rows (one per card to
buy) that the CSV and HTML writers render. Enriches each card with its game name,
informational market-listings URL, and price age by reading the Store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..models import MarketItem

if TYPE_CHECKING:
    from ..db import Store
    from ..optimize import OptimizationPlan

__all__ = ["PlanRow", "build_rows"]


@dataclass(frozen=True, slots=True)
class PlanRow:
    priority: int
    appid: int
    game: str
    badge_current_level: int
    badge_target_level: int
    card: str
    missing_qty: int
    unit_price: str
    total_price: str
    market_hash_name: str
    market_url: str
    price_age: str
    confidence: str
    notes: str


def _age_str(fetched_at: datetime, now: datetime) -> str:
    seconds = max(0.0, (now - fetched_at).total_seconds())
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def build_rows(
    plan: OptimizationPlan, store: Store, *, now: datetime | None = None
) -> list[PlanRow]:
    """Flatten the chosen badges into one row per card to buy."""
    reference = now or datetime.now(UTC)
    app_names = {a.appid: a.name for a in store.list_apps()}
    rows: list[PlanRow] = []
    priority = 0
    for badge in plan.chosen:
        note = "; ".join(badge.notes)
        for cand in badge.candidates:
            priority += 1
            item = MarketItem(appid=cand.appid, market_hash_name=cand.market_hash_name)
            unit = cand.estimated_unit_price
            total = cand.estimated_total
            snap = store.latest_price(cand.appid, cand.market_hash_name)
            age = _age_str(snap.source.fetched_at, reference) if snap else "n/a"
            rows.append(
                PlanRow(
                    priority=priority,
                    appid=cand.appid,
                    game=app_names.get(cand.appid, f"App {cand.appid}"),
                    badge_current_level=badge.current_level,
                    badge_target_level=badge.target_level,
                    card=cand.market_hash_name,
                    missing_qty=cand.missing_quantity,
                    unit_price=f"{unit.amount:.2f}" if unit else "",
                    total_price=f"{total.amount:.2f}" if total else "",
                    market_hash_name=cand.market_hash_name,
                    market_url=item.listings_url(),
                    price_age=age,
                    confidence=cand.confidence.value,
                    notes=note,
                )
            )
    return rows
