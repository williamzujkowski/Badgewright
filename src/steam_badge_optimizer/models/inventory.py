"""User inventory domain models: cards and non-card community holdings."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from ._types import NonBlankStr

__all__ = ["ItemKind", "UserCardInventory", "UserItemHolding"]


class UserCardInventory(BaseModel):
    """How many copies of a given card the user currently owns.

    Keyed by the card's market hash name (unique within an app). Quantity is the
    number of duplicate copies held; duplicates reduce how many cards must be bought
    to craft a badge.
    """

    model_config = {"frozen": True}

    appid: int = Field(gt=0)
    market_hash_name: NonBlankStr = Field(min_length=1)
    quantity: int = Field(ge=0, description="Copies owned (non-negative).")
    is_foil: bool = False


class ItemKind(StrEnum):
    """The kind of non-card community item a holding represents.

    Kept deliberately closed; ``OTHER`` absorbs future/unclassified marketable 753/6
    items (backgrounds, emoticons, ...) without a schema change.
    """

    BOOSTER_PACK = "booster_pack"
    SACK_OF_GEMS = "sack_of_gems"
    GEMS = "gems"  # loose gems; quantity is the gem count, not copies
    OTHER = "other"


class UserItemHolding(BaseModel):
    """A non-card community item the user holds (booster pack, gems, sack, or other).

    Separate from :class:`UserCardInventory` so cards stay card-shaped (``is_foil``,
    badge semantics) and heterogeneous items don't overload that table. For ``GEMS``,
    ``quantity`` is the number of gems; otherwise it is the number of copies held.
    """

    model_config = {"frozen": True}

    appid: int = Field(gt=0)
    market_hash_name: NonBlankStr = Field(min_length=1)
    kind: ItemKind
    quantity: int = Field(ge=0, description="Copies held, or gem count for GEMS.")
