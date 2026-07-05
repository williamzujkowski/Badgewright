"""Market item and price-snapshot domain models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from .money import Money
from .provenance import SourceRecord

__all__ = ["MarketItem", "PriceSnapshot"]


class MarketItem(BaseModel):
    """A tradable market item, identified by app + market hash name."""

    model_config = {"frozen": True}

    appid: int = Field(gt=0)
    market_hash_name: str = Field(min_length=1)

    def listings_url(self) -> str:
        """Read-only Community Market listings page for manual review."""
        from urllib.parse import quote

        return (
            f"https://steamcommunity.com/market/listings/"
            f"{self.appid}/{quote(self.market_hash_name)}"
        )


class PriceSnapshot(BaseModel):
    """A point-in-time price observation for a market item.

    ``lowest``/``median`` are parsed Money (``priceoverview`` may omit either for
    thin items). ``volume`` is the reported 24h sales count. ``source`` carries the
    provenance (URL, fetch time, TTL) so staleness and confidence can be judged.
    """

    model_config = {"frozen": True}

    item: MarketItem
    lowest: Money | None = None
    median: Money | None = None
    volume: int | None = Field(default=None, ge=0, description="24h sales count (priceoverview).")
    listings: int | None = Field(
        default=None, ge=0, description="Current asks / ask-side depth (market search)."
    )
    source: SourceRecord

    @model_validator(mode="after")
    def _check_single_currency(self) -> PriceSnapshot:
        # Steam quotes lowest and median in one currency; enforce it so persistence
        # (one currency column per row) is never lossy.
        if (
            self.lowest is not None
            and self.median is not None
            and self.lowest.currency != self.median.currency
        ):
            raise ValueError(
                f"lowest ({self.lowest.currency}) and median ({self.median.currency}) "
                "must share a currency"
            )
        return self

    @property
    def has_price(self) -> bool:
        return self.lowest is not None or self.median is not None

    def is_stale(self, *, now: datetime | None = None) -> bool:
        """Delegates to the source record's TTL policy."""
        return self.source.is_stale(now=now)
