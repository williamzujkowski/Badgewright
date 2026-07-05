"""Market-intelligence analytics (research only — never trades)."""

from .anomalies import Anomaly, AnomalyKind, detect_anomalies
from .market_scan import CardWeakness, SetSignal, scan_sets, scan_weakness

__all__ = [
    "Anomaly",
    "AnomalyKind",
    "CardWeakness",
    "SetSignal",
    "detect_anomalies",
    "scan_sets",
    "scan_weakness",
]
