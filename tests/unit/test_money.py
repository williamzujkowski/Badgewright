"""Tests for Money and localized Steam price-string parsing."""

from __future__ import annotations

import pytest

from steam_badge_optimizer.models.money import Money, PriceParseError, parse_steam_price


class TestParseSteamPrice:
    @pytest.mark.parametrize(
        ("text", "currency", "cents"),
        [
            ("$0.03", "USD", 3),
            ("$1.50", "USD", 150),
            ("$1,234.56", "USD", 123456),  # US grouping + decimal
            ("£0.09", "GBP", 9),
            ("0,03€", "EUR", 3),  # EU comma decimal, symbol suffix
            ("1.234,56€", "EUR", 123456),  # EU grouping '.' decimal ','
            ("1 234,56 pуб.", "RUB", 123456),  # noqa: RUF001 - real Cyrillic ruble text
            ("$5", "USD", 500),  # bare integer
            ("$0.10", "USD", 10),
            ("2,00€", "EUR", 200),
        ],
    )
    def test_parses_localized_formats(self, text: str, currency: str, cents: int) -> None:
        assert parse_steam_price(text, currency).cents == cents

    def test_currency_is_recorded_and_uppercased(self) -> None:
        m = parse_steam_price("$0.03", "usd")
        assert m.currency == "USD"

    def test_two_decimals_are_exact(self) -> None:
        # Steam always displays exactly two decimal places, so x100 is exact.
        assert parse_steam_price("$12.34", "USD").cents == 1234
        assert parse_steam_price("$0.99", "USD").cents == 99

    @pytest.mark.parametrize("bad", ["", "   ", "free", "$", "N/A"])
    def test_garbage_raises(self, bad: str) -> None:
        with pytest.raises(PriceParseError):
            parse_steam_price(bad, "USD")

    def test_unknown_currency_raises(self) -> None:
        with pytest.raises(PriceParseError):
            parse_steam_price("$1.00", "XYZ")


class TestMoney:
    def test_positional_construction(self) -> None:
        m = Money(150, "USD")
        assert m.cents == 150
        assert str(m) == "1.50 USD"

    def test_amount_is_exact(self) -> None:
        assert str(Money(3, "USD").amount) == "0.03"

    def test_negative_cents_rejected(self) -> None:
        with pytest.raises(ValueError):
            Money(-1, "USD")

    def test_frozen(self) -> None:
        m = Money(10, "USD")
        with pytest.raises(ValueError):
            m.cents = 20  # type: ignore[misc]
