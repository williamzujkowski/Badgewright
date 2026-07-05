"""Greedy badge-completion planner (Epic 5.2).

Given a :class:`CostReport`, pick the cheapest badges to complete under a spend budget
and/or an account-XP target. Ranking is by **cost per expected XP** (ascending), which
is the right objective because every badge craft yields the same 100 XP — so the
cheapest cost-per-XP badges are the most efficient way to buy levels.

Only ``complete`` badges (fully known + priced) are ranked; incomplete and
ready-to-craft badges are surfaced separately so the human sees the whole picture but
the plan never rests on a fabricated cost.

Granularity note: this ranks whole badges. Per-*craft* granularity (a badge's first
craft is cheaper when duplicates are owned) is a later refinement (#15); for whole-badge
selection under a budget this greedy order is the standard, explainable choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from ..models import Money
from .cost import BadgeCost, CostReport

__all__ = ["OptimizationPlan", "build_plan"]


@dataclass(frozen=True, slots=True)
class OptimizationPlan:
    chosen: list[BadgeCost]
    skipped_over_budget: list[BadgeCost]
    incomplete: list[BadgeCost]
    ready_to_craft: list[BadgeCost]
    total_cost: Money
    total_xp: int
    currency: str
    budget: Money | None = None
    target_xp: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def budget_remaining(self) -> Money | None:
        if self.budget is None:
            return None
        return Money(max(0, self.budget.cents - self.total_cost.cents), self.currency)

    @property
    def target_reached(self) -> bool:
        return self.target_xp is None or self.total_xp >= self.target_xp


def _cost_per_xp(badge: BadgeCost) -> Fraction:
    # Exact ratio (integer cents / integer XP); ready-to-craft (cost 0) sorts first.
    cost = badge.known_cost.cents if badge.known_cost is not None else 0
    xp = badge.expected_xp or 1
    return Fraction(cost, xp)


def build_plan(
    report: CostReport,
    *,
    budget: Money | None = None,
    target_xp: int | None = None,
) -> OptimizationPlan:
    """Build a greedy purchase plan. Fills cheapest-cost-per-XP badges first, stopping
    at the XP target (if any) and never exceeding the budget (if any)."""
    if budget is not None and budget.currency != report.currency:
        raise ValueError(f"budget currency {budget.currency} != report {report.currency}")

    ranked = sorted(
        report.complete_badges(),
        key=lambda b: (_cost_per_xp(b), b.known_cost.cents if b.known_cost else 0),
    )

    chosen: list[BadgeCost] = []
    skipped: list[BadgeCost] = []
    total = 0
    xp = 0
    for badge in ranked:
        if target_xp is not None and xp >= target_xp:
            break
        cost = badge.known_cost.cents if badge.known_cost is not None else 0
        if budget is not None and total + cost > budget.cents:
            skipped.append(badge)  # cannot afford within budget
            continue
        chosen.append(badge)
        total += cost
        xp += badge.expected_xp

    notes: list[str] = []
    if target_xp is not None and xp < target_xp:
        shortfall = target_xp - xp
        notes.append(
            f"Target XP not reached: {xp}/{target_xp} ({shortfall} short) — "
            "raise the budget, or more badges need card discovery/pricing."
        )

    return OptimizationPlan(
        chosen=chosen,
        skipped_over_budget=skipped,
        incomplete=report.incomplete_badges(),
        ready_to_craft=report.ready_to_craft(),
        total_cost=Money(total, report.currency),
        total_xp=xp,
        currency=report.currency,
        budget=budget,
        target_xp=target_xp,
        notes=notes,
    )
