"""Badge / card / badge-progress domain models."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from ..config import MAX_NORMAL_BADGE_LEVEL
from ._types import NonBlankStr

__all__ = ["BadgeSet", "Card", "CardGooValue", "UserBadgeProgress"]


class BadgeSet(BaseModel):
    """A game's trading-card set: how many distinct cards complete one craft."""

    model_config = {"frozen": True}

    appid: int = Field(gt=0)
    set_size: int = Field(gt=0, le=100, description="Number of distinct cards in the set.")

    def xp_to_max_normal(self, xp_per_level: int) -> int:
        """Total account XP from crafting this normal badge to level 5."""
        return xp_per_level * MAX_NORMAL_BADGE_LEVEL


class Card(BaseModel):
    """A single trading card within a set, identified by its market hash name."""

    model_config = {"frozen": True}

    appid: int = Field(gt=0)
    market_hash_name: NonBlankStr = Field(min_length=1, description="Exact Steam market hash name.")
    card_name: str | None = Field(default=None, description="Human card title, if known.")
    is_foil: bool = False
    marketable: bool = True
    tradable: bool = True


class CardGooValue(BaseModel):
    """How many gems a card yields when turned into gems ("goo value").

    ``goo_value`` is the gem count; ``item_type``/``border_color`` are the internal Steam
    parameters it was derived from (border_color 1 = foil, ~10x a normal card). Stable per
    card — cached so a re-run doesn't re-fetch.
    """

    model_config = {"frozen": True}

    appid: int = Field(gt=0)
    market_hash_name: NonBlankStr = Field(min_length=1)
    item_type: int = Field(ge=0)
    border_color: int = Field(ge=0)
    goo_value: int = Field(ge=0, description="Gems yielded by turning this card into gems.")


class UserBadgeProgress(BaseModel):
    """The user's progress on one game's badge."""

    model_config = {"frozen": True}

    appid: int = Field(gt=0)
    level: int = Field(ge=0, description="Current normal-badge level (0 = uncrafted).")
    is_foil: bool = False

    @model_validator(mode="after")
    def _check_level_bounds(self) -> UserBadgeProgress:
        # Foil badges have a single level; normal badges cap at MAX_NORMAL_BADGE_LEVEL.
        cap = 1 if self.is_foil else MAX_NORMAL_BADGE_LEVEL
        if self.level > cap:
            raise ValueError(
                f"badge level {self.level} exceeds cap {cap} "
                f"({'foil' if self.is_foil else 'normal'} badge, appid {self.appid})"
            )
        return self

    @property
    def is_maxed(self) -> bool:
        cap = 1 if self.is_foil else MAX_NORMAL_BADGE_LEVEL
        return self.level >= cap

    def remaining_normal_levels(self) -> int:
        """How many more normal levels are craftable (0 for foil/maxed)."""
        if self.is_foil:
            return 0
        return max(0, MAX_NORMAL_BADGE_LEVEL - self.level)
