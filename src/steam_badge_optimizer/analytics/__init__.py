"""Market-intelligence analytics (research only — never trades)."""

from .anomalies import Anomaly, AnomalyKind, detect_anomalies
from .cheapest_badges import BadgeSetCost, rank_cheapest_badges
from .market_scan import CardWeakness, SetSignal, scan_sets, scan_weakness

__all__ = [
    "Anomaly",
    "AnomalyKind",
    "BadgeSetCost",
    "CardWeakness",
    "SetSignal",
    "detect_anomalies",
    "rank_cheapest_badges",
    "scan_sets",
    "scan_weakness",
]
