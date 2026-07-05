"""Badgewright domain models.

All models are validated pydantic types and compose with :class:`SourceRecord`
provenance. They are the shapes the ingestion, persistence, optimizer, and report
layers serialize and round-trip.
"""

from .badge import BadgeSet, Card, UserBadgeProgress
from .inventory import UserCardInventory
from .market import MarketItem, PriceSnapshot
from .money import Money, PriceParseError, parse_steam_price
from .optimization import Confidence, PurchaseCandidate
from .provenance import SourceKind, SourceRecord
from .steam import SteamApp

__all__ = [
    "BadgeSet",
    "Card",
    "Confidence",
    "MarketItem",
    "Money",
    "PriceParseError",
    "PriceSnapshot",
    "PurchaseCandidate",
    "SourceKind",
    "SourceRecord",
    "SteamApp",
    "UserBadgeProgress",
    "UserCardInventory",
    "parse_steam_price",
]
