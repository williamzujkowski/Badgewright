"""User card-inventory domain model."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["UserCardInventory"]


class UserCardInventory(BaseModel):
    """How many copies of a given card the user currently owns.

    Keyed by the card's market hash name (unique within an app). Quantity is the
    number of duplicate copies held; duplicates reduce how many cards must be bought
    to craft a badge.
    """

    model_config = {"frozen": True}

    appid: int = Field(gt=0)
    market_hash_name: str = Field(min_length=1)
    quantity: int = Field(ge=0, description="Copies owned (non-negative).")
    is_foil: bool = False
