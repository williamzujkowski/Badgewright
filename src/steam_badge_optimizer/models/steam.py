"""Steam application (game) domain model."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["SteamApp"]


class SteamApp(BaseModel):
    """A Steam application (game) that may have a trading-card badge set."""

    model_config = {"frozen": True}

    appid: int = Field(gt=0, description="Steam application id (positive).")
    name: str = Field(min_length=1, description="Display name of the app.")

    def market_url(self) -> str:
        """The Community Market search URL scoped to this app (read-only link)."""
        return f"https://steamcommunity.com/market/search?appid={self.appid}"
