"""Optimizer input/output domain models.

Only :class:`PurchaseCandidate` is defined now — the shape the cost-to-complete
calculator (Epic 5.1) will emit and the reports (Epic 7) will consume. Richer
``OptimizationRun`` / ``PurchasePlan`` aggregates land with the optimizer itself, so
they are deliberately not modeled ahead of a consumer (YAGNI).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from .money import Money

__all__ = ["Confidence", "PurchaseCandidate"]


class Confidence(StrEnum):
    """Coarse confidence in a candidate's price estimate (drives plan ranking)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PurchaseCandidate(BaseModel):
    """A single card the user would need to buy to advance a badge."""

    model_config = {"frozen": True}

    appid: int = Field(gt=0)
    market_hash_name: str = Field(min_length=1)
    missing_quantity: int = Field(gt=0, description="Copies still needed to buy.")
    estimated_unit_price: Money | None = Field(
        default=None, description="Per-copy estimate; None if price is unavailable."
    )
    confidence: Confidence = Confidence.LOW

    @property
    def estimated_total(self) -> Money | None:
        """Total estimated cost = unit price x missing quantity, if priced."""
        if self.estimated_unit_price is None:
            return None
        return Money(
            cents=self.estimated_unit_price.cents * self.missing_quantity,
            currency=self.estimated_unit_price.currency,
        )
