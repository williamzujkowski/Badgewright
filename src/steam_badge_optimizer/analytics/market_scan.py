"""Market-intelligence scoring (Story 4 / Epic 6).

**Research, not trading advice.** These functions read cached price data and surface
weak-looking prices and set-level quirks for a human to investigate. Badgewright never
executes or recommends trades.

Discipline (per the approving vote):

* The lowest-vs-median signal is called ``ask_vs_median_gap``, **not** "spread" —
  ``priceoverview`` has no buy orders, so a true bid/ask spread is not computable.
* Everything is **liquidity-weighted**: a low-volume quote is unreliable noise, so it
  is flagged LOW-CONFIDENCE and can never rank as a top opportunity on a price gap
  alone (its liquidity weight drives the score toward zero).
* Volatility is only reported when the local price history has at least
  :data:`MIN_SNAPSHOTS_FOR_VOLATILITY` points; otherwise it is ``None`` ("insufficient
  history") — never fabricated from one or two points.
* Prices in a currency other than the scan currency are skipped (never mixed).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models import Confidence, Money

if TYPE_CHECKING:
    from ..db import Store

__all__ = [
    "DEFAULT_MIN_VOLUME",
    "DOMINANCE_FLAG_THRESHOLD",
    "MIN_SNAPSHOTS_FOR_VOLATILITY",
    "CardWeakness",
    "SetSignal",
    "scan_sets",
    "scan_weakness",
]

MIN_SNAPSHOTS_FOR_VOLATILITY = 5
DEFAULT_MIN_VOLUME = 5
DOMINANCE_FLAG_THRESHOLD = 0.4  # one card >= 40% of set cost = bottleneck risk


@dataclass(frozen=True, slots=True)
class CardWeakness:
    appid: int
    market_hash_name: str
    lowest: Money | None
    median: Money | None
    volume: int | None
    ask_vs_median_gap: float | None  # (median - lowest) / median, in [0, 1]
    volatility: float | None  # coefficient of variation, or None = insufficient history
    confidence: Confidence
    score: float
    signals: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SetSignal:
    appid: int
    set_size: int
    complete: bool
    total_cost: Money | None
    card_dominance: float | None  # max card cost / total, in [0, 1]
    signals: list[str] = field(default_factory=list)


def _gap(lowest: Money | None, median: Money | None) -> float | None:
    if lowest is None or median is None or median.cents <= 0:
        return None
    return max(0.0, (median.cents - lowest.cents) / median.cents)


def _volatility(history_cents: list[int]) -> float | None:
    if len(history_cents) < MIN_SNAPSHOTS_FOR_VOLATILITY:
        return None
    mean = statistics.fmean(history_cents)
    if mean <= 0:
        return None
    return statistics.pstdev(history_cents) / mean


def scan_weakness(
    store: Store,
    *,
    currency: str = "USD",
    min_volume: int = DEFAULT_MIN_VOLUME,
    top: int = 50,
) -> list[CardWeakness]:
    """Rank cards by a liquidity-weighted price-weakness score (research only)."""
    if top <= 0:
        raise ValueError("top must be positive")
    rows: list[CardWeakness] = []
    for appid, name in store.iter_price_items():
        snap = store.latest_price(appid, name)
        if snap is None or not snap.has_price:
            continue
        priced = snap.lowest or snap.median
        if priced is not None and priced.currency != currency:
            continue  # never mix currencies in one scan

        gap = _gap(snap.lowest, snap.median)
        volume = snap.volume or 0
        history = [s.lowest.cents for s in store.price_history(appid, name) if s.lowest is not None]
        volatility = _volatility(history)

        signals: list[str] = []
        # Liquidity weight in [0, 1]: below the threshold the quote is unreliable, so
        # its weight (and thus its score) collapses toward zero — it can't top the list.
        liquidity_weight = min(1.0, volume / min_volume) if min_volume > 0 else 1.0
        low_volume = volume < min_volume
        if low_volume:
            signals.append(f"low volume ({volume} < {min_volume}) — unreliable, risky")
        if snap.is_stale():
            signals.append("price is stale")
            liquidity_weight *= 0.5
        if volatility is None:
            signals.append("insufficient history for volatility")
        elif volatility > 0.25:
            signals.append(f"volatile (cv={volatility:.2f})")
        if gap is not None and gap > 0:
            signals.append(f"lowest ask {gap * 100:.0f}% below median")

        score = (gap or 0.0) * liquidity_weight
        confidence = (
            Confidence.LOW
            if (low_volume or snap.is_stale() or gap is None)
            else (Confidence.MEDIUM if volatility is None or volatility > 0.25 else Confidence.HIGH)
        )
        rows.append(
            CardWeakness(
                appid=appid,
                market_hash_name=name,
                lowest=snap.lowest,
                median=snap.median,
                volume=snap.volume,
                ask_vs_median_gap=gap,
                volatility=volatility,
                confidence=confidence,
                score=score,
                signals=signals,
            )
        )
    rows.sort(key=lambda r: (r.score, r.volume or 0), reverse=True)
    return rows[:top]


def scan_sets(store: Store, *, currency: str = "USD") -> list[SetSignal]:
    """Per-set signals: single-set cost (when fully known+priced) and card dominance."""
    signals_out: list[SetSignal] = []
    for badge_set in store.list_badge_sets():
        appid = badge_set.appid
        cards = store.cards_for_app(appid, include_foil=False)
        notes: list[str] = []
        unit_cents: list[int] = []
        priced_all = len(cards) == badge_set.set_size and badge_set.set_size > 0
        for card in cards:
            snap = store.latest_price(appid, card.market_hash_name)
            unit = (snap.lowest or snap.median) if snap else None
            if unit is None or unit.currency != currency:
                priced_all = False
                continue
            unit_cents.append(unit.cents)

        if priced_all and unit_cents:
            total = sum(unit_cents)
            dominance = max(unit_cents) / total if total > 0 else None
            if dominance is not None and dominance >= DOMINANCE_FLAG_THRESHOLD:
                notes.append(f"one card is {dominance * 100:.0f}% of set cost (bottleneck risk)")
            signals_out.append(
                SetSignal(
                    appid=appid,
                    set_size=badge_set.set_size,
                    complete=True,
                    total_cost=Money(total, currency),
                    card_dominance=dominance,
                    signals=notes,
                )
            )
        else:
            notes.append("set not fully known/priced (needs card discovery + prices)")
            signals_out.append(
                SetSignal(
                    appid=appid,
                    set_size=badge_set.set_size,
                    complete=False,
                    total_cost=None,
                    card_dominance=None,
                    signals=notes,
                )
            )
    return signals_out
