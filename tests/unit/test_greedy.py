"""Tests for the greedy optimizer and account-XP math."""

from __future__ import annotations

import pytest

from steam_badge_optimizer.config import account_xp_between
from steam_badge_optimizer.models import Confidence, Money
from steam_badge_optimizer.optimize import build_plan
from steam_badge_optimizer.optimize.cost import BadgeCost, CostReport


def _badge(
    appid: int,
    cost_cents: int,
    crafts: int = 5,
    *,
    complete: bool = True,
    ready: bool = False,
) -> BadgeCost:
    return BadgeCost(
        appid=appid,
        current_level=0,
        target_level=5,
        set_size=1,
        known_cards=1,
        crafts_needed=crafts,
        candidates=[],
        known_cost=Money(cost_cents, "USD"),
        complete=complete,
        ready_to_craft=ready,
        confidence=Confidence.HIGH,
        notes=[],
    )


def _report(*badges: BadgeCost) -> CostReport:
    return CostReport(badges=list(badges), currency="USD")


class TestAccountXp:
    def test_first_ten_levels_100_each(self) -> None:
        assert account_xp_between(0, 10) == 1000
        assert account_xp_between(0, 1) == 100

    def test_second_band_200_each(self) -> None:
        assert account_xp_between(10, 20) == 2000
        assert account_xp_between(10, 11) == 200

    def test_no_gain_below_current(self) -> None:
        assert account_xp_between(50, 50) == 0
        assert account_xp_between(50, 40) == 0

    def test_band_crossing(self) -> None:
        # 8->9,10 cost 100 each (=200); 11,12 cost 200 each (=400). Total 8->12 = 600.
        assert account_xp_between(8, 12) == 600

    def test_single_mid_band_level(self) -> None:
        # Reaching level 25 costs 100*ceil(25/10) = 300.
        assert account_xp_between(24, 25) == 300


class TestGreedy:
    def test_ranks_cheapest_cost_per_xp_first(self) -> None:
        # b_cheap: 100c/500xp; b_pricey: 400c/500xp. Cheap first.
        cheap = _badge(1, 100)
        pricey = _badge(2, 400)
        plan = build_plan(_report(pricey, cheap))
        assert [b.appid for b in plan.chosen] == [1, 2]

    def test_budget_caps_spend_and_skips_unaffordable(self) -> None:
        plan = build_plan(
            _report(_badge(1, 100), _badge(2, 400), _badge(3, 300)),
            budget=Money(450, "USD"),
        )
        # Cheapest first: 100 (ok, total 100), 300 (ok, total 400), 400 (would be 800 > 450 skip).
        assert [b.appid for b in plan.chosen] == [1, 3]
        assert plan.total_cost == Money(400, "USD")
        assert plan.budget_remaining == Money(50, "USD")
        assert [b.appid for b in plan.skipped_over_budget] == [2]

    def test_target_xp_stops_when_reached(self) -> None:
        # Each badge = 500 XP. Target 900 -> need 2 badges.
        plan = build_plan(
            _report(_badge(1, 100), _badge(2, 100), _badge(3, 100)),
            target_xp=900,
        )
        assert plan.total_xp == 1000
        assert len(plan.chosen) == 2
        assert plan.target_reached is True

    def test_target_not_reached_notes_shortfall(self) -> None:
        plan = build_plan(_report(_badge(1, 100)), target_xp=900)
        assert plan.target_reached is False
        assert plan.total_xp == 500
        assert any("not reached" in n.lower() for n in plan.notes)

    def test_budget_blocks_target(self) -> None:
        # Target wants 2 badges but budget only affords 1.
        plan = build_plan(
            _report(_badge(1, 100), _badge(2, 100)),
            budget=Money(150, "USD"),
            target_xp=1000,
        )
        assert len(plan.chosen) == 1
        assert plan.target_reached is False

    def test_ready_and_incomplete_surfaced_not_chosen(self) -> None:
        ready = _badge(1, 0, ready=True)
        incomplete = _badge(2, 0, complete=False)
        chosen = _badge(3, 100)
        plan = build_plan(_report(ready, incomplete, chosen))
        assert [b.appid for b in plan.ready_to_craft] == [1]
        assert [b.appid for b in plan.incomplete] == [2]
        # ready-to-craft (cost 0) also qualifies as a complete badge and is chosen first.
        assert 3 in [b.appid for b in plan.chosen]
        assert 2 not in [b.appid for b in plan.chosen]  # incomplete never chosen

    def test_budget_currency_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_plan(_report(_badge(1, 100)), budget=Money(100, "EUR"))
