"""Market-intelligence analytics (research only — never trades)."""

from .anomalies import Anomaly, AnomalyKind, detect_anomalies
from .candidate_selection import CandidateGame, select_candidate_games
from .cheapest_badges import BadgeSetCost, rank_cheapest_badges
from .market_scan import CardWeakness, SetSignal, scan_sets, scan_weakness

__all__ = [
    "Anomaly",
    "AnomalyKind",
    "BadgeSetCost",
    "CandidateGame",
    "CardWeakness",
    "SetSignal",
    "detect_anomalies",
    "rank_cheapest_badges",
    "scan_sets",
    "scan_weakness",
    "select_candidate_games",
]
