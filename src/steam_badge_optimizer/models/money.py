"""Money value object and Steam price-string parsing.

Steam's `priceoverview` returns prices as **localized display strings**
(``"$0.03"``, ``"1.234,56 EUR"``, ``"1 234,56 RUB"``), never numbers. Parsing them
naively with ``float()`` is wrong (thousands separators, comma decimals, currency
symbols, non-breaking spaces) and is a trust-boundary input from an external source,
so it gets strict, well-tested handling here.

Money is stored as an integer number of minor units (cents) to avoid binary
floating-point error in cost arithmetic.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field

from ..config import CURRENCY_IDS

__all__ = ["Money", "PriceParseError", "parse_steam_price"]

# Everything that isn't a digit or a separator we might care about.
_KEEP = re.compile(r"[0-9.,]")


class PriceParseError(ValueError):
    """Raised when a Steam price string cannot be parsed into a Money amount."""


class Money(BaseModel):
    """An amount of money in integer minor units (e.g. cents)."""

    model_config = {"frozen": True}

    cents: int = Field(ge=0, description="Amount in minor units (cents); non-negative.")
    currency: str = Field(description="ISO-ish currency code, e.g. 'USD'.")

    def __init__(self, cents: int, currency: str = "USD", **kwargs: object) -> None:
        # Positional convenience: Money(3, "USD").
        super().__init__(cents=cents, currency=currency.upper(), **kwargs)

    @property
    def amount(self) -> Decimal:
        """The value in major units (e.g. dollars) as an exact Decimal."""
        return Decimal(self.cents) / Decimal(100)

    def __str__(self) -> str:
        return f"{self.amount:.2f} {self.currency}"


def _normalize_decimal_string(numeric: str) -> Decimal:
    """Turn the digits/separators of a price into a Decimal in major units.

    Handles both ``1,234.56`` (US) and ``1.234,56`` (EU) plus bare integers, using
    the position of the *last* separator as the decimal point.
    """
    has_comma = "," in numeric
    has_dot = "." in numeric

    if has_comma and has_dot:
        # The separator that appears last is the decimal point; the other is grouping.
        decimal_sep = "," if numeric.rfind(",") > numeric.rfind(".") else "."
        grouping_sep = "." if decimal_sep == "," else ","
        numeric = numeric.replace(grouping_sep, "").replace(decimal_sep, ".")
    elif has_comma or has_dot:
        sep = "," if has_comma else "."
        # Multiple occurrences of one separator ⇒ it's grouping (1.234.567).
        # A single occurrence with exactly 3 trailing digits ⇒ grouping (1,234).
        # Otherwise it's a decimal point.
        after = numeric.rsplit(sep, 1)[1]
        if numeric.count(sep) > 1 or len(after) == 3:
            numeric = numeric.replace(sep, "")
        else:
            numeric = numeric.replace(sep, ".")

    try:
        return Decimal(numeric)
    except InvalidOperation as exc:  # pragma: no cover - guarded by caller extraction
        raise PriceParseError(f"could not parse numeric part {numeric!r}") from exc


def parse_steam_price(text: str, currency: str) -> Money:
    """Parse a localized Steam price display string into :class:`Money`.

    ``parse_steam_price("$0.03", "USD") -> Money(3, "USD")``.
    Raises :class:`PriceParseError` on empty/garbage input or an unknown currency.
    """
    currency = currency.upper()
    if currency not in CURRENCY_IDS:
        raise PriceParseError(f"unknown currency {currency!r}; known: {sorted(CURRENCY_IDS)}")
    if text is None or not str(text).strip():
        raise PriceParseError("empty price string")

    # Trailing separators are never part of the number -- they come from a currency
    # abbreviation ending in a period (e.g. the Russian ruble suffix) that the filter
    # captured along with the digits.
    numeric = "".join(_KEEP.findall(str(text))).strip(".,")
    if not numeric or not any(ch.isdigit() for ch in numeric):
        raise PriceParseError(f"no numeric content in {text!r}")

    value = _normalize_decimal_string(numeric)
    if value < 0:
        raise PriceParseError(f"negative price {text!r}")

    cents = int((value * 100).to_integral_value(rounding="ROUND_HALF_UP"))
    return Money(cents=cents, currency=currency)
