"""Historical price-anomaly detection (Epic 6.3).

**Research, not trading advice.** Reads the append-only PriceSnapshot history and flags
cards whose recent price looks unusual, for a human to investigate. Executes nothing.

Detectors (each pure, over one card's same-currency lowest-price series):

* ``SUDDEN_DROP`` — the latest lowest is well below the trailing mean of prior points.
* ``MEAN_REVERSION`` — the latest lowest is a statistical low outlier vs its own history
  (z-score below a threshold), so it *may* revert upward.
* ``STALE_MEDIAN`` — the latest snapshot's median sits far above its live lowest ask,
  i.e. recent sale prices haven't caught up to a dropped ask.

Fail-closed: a card with fewer than :data:`MIN_SNAPSHOTS` same-currency history points is
skipped ("insufficient history") — no anomaly is fabricated from sparse data. Every
result carries a caveat and a coarse (never HIGH) confidence, because anomalies are
speculative by nature.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from ..models import Confidence, Money

if TYPE_CHECKING:
    from ..db import Store

__all__ = [
    "MIN_SNAPSHOTS",
    "Anomaly",
    "AnomalyKind",
    "detect_anomalies",
]

MIN_SNAPSHOTS = 5
DROP_FACTOR = 0.7  # latest < 70% of the trailing mean = a sudden drop
REVERSION_Z = -1.5  # z-score at/below which the latest point is a low outlier
STALE_MEDIAN_FACTOR = 1.5  # median > 1.5x the live lowest = a stale/lagging median


class AnomalyKind(StrEnum):
    SUDDEN_DROP = "sudden_drop"
    MEAN_REVERSION = "mean_reversion"
    STALE_MEDIAN = "stale_median"


@dataclass(frozen=True, slots=True)
class Anomaly:
    appid: int
    market_hash_name: str
    kind: AnomalyKind
    latest: Money
    reference: Money  # trailing mean / history mean / median, per kind
    magnitude: float  # how pronounced, in [0, 1+] (bigger = more unusual)
    confidence: Confidence
    caveat: str

    @property
    def sort_key(self) -> float:
        return self.magnitude


def _confidence(history_len: int, volume: int | None) -> Confidence:
    # Anomalies are speculative -> never HIGH. More history + volume -> MEDIUM.
    if history_len >= MIN_SNAPSHOTS + 5 and (volume or 0) >= 5:
        return Confidence.MEDIUM
    return Confidence.LOW


def _detect_for_item(appid: int, name: str, store: Store, currency: str) -> list[Anomaly]:
    history = [
        s
        for s in store.price_history(appid, name)
        if s.lowest is not None and s.lowest.currency == currency
    ]
    if len(history) < MIN_SNAPSHOTS:
        return []  # insufficient history — never fabricate an anomaly
    series = [s.lowest.cents for s in history]  # type: ignore[union-attr]
    latest_cents = series[-1]
    prior = series[:-1]
    volume = history[-1].volume
    conf = _confidence(len(history), volume)
    found: list[Anomaly] = []

    trailing_mean = statistics.fmean(prior)
    if trailing_mean > 0 and latest_cents < DROP_FACTOR * trailing_mean:
        found.append(
            Anomaly(
                appid=appid,
                market_hash_name=name,
                kind=AnomalyKind.SUDDEN_DROP,
                latest=Money(latest_cents, currency),
                reference=Money(round(trailing_mean), currency),
                magnitude=(trailing_mean - latest_cents) / trailing_mean,
                confidence=conf,
                caveat="recent drop vs trailing average — could be a real drop or a "
                "thin-market blip; research before acting",
            )
        )

    all_mean = statistics.fmean(series)
    sd = statistics.pstdev(series)
    if sd > 0:
        z = (latest_cents - all_mean) / sd
        if z <= REVERSION_Z:
            found.append(
                Anomaly(
                    appid=appid,
                    market_hash_name=name,
                    kind=AnomalyKind.MEAN_REVERSION,
                    latest=Money(latest_cents, currency),
                    reference=Money(round(all_mean), currency),
                    magnitude=min(2.0, abs(z)),
                    confidence=conf,
                    caveat="latest is a statistical low vs its own history — MAY revert "
                    "upward, but low prices can also persist; not a prediction",
                )
            )

    median = history[-1].median
    if (
        median is not None
        and median.currency == currency
        and latest_cents > 0
        and median.cents > STALE_MEDIAN_FACTOR * latest_cents
    ):
        found.append(
            Anomaly(
                appid=appid,
                market_hash_name=name,
                kind=AnomalyKind.STALE_MEDIAN,
                latest=Money(latest_cents, currency),
                reference=median,
                magnitude=(median.cents - latest_cents) / median.cents,
                confidence=conf,
                caveat="median sale price sits well above the live lowest ask — the "
                "median may be lagging a drop; verify on the market page",
            )
        )
    return found


def detect_anomalies(store: Store, *, currency: str = "USD", top: int = 50) -> list[Anomaly]:
    """Detect price anomalies across all cards with enough same-currency history."""
    if top <= 0:
        raise ValueError("top must be positive")
    out: list[Anomaly] = []
    for appid, name in store.iter_price_items():
        out.extend(_detect_for_item(appid, name, store, currency))
    out.sort(key=lambda a: (a.magnitude, a.market_hash_name), reverse=True)
    return out[:top]
