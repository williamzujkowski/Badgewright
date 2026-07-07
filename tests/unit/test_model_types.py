"""Tests for shared model field types — NonBlankStr rejects whitespace-only input (#27)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from steam_badge_optimizer.models import Card, MarketItem, SteamApp, UserCardInventory


class TestNonBlankStr:
    @pytest.mark.parametrize("bad", ["", " ", "   ", "\t", "\n", " \t \n "])
    def test_market_hash_name_rejects_blank(self, bad: str) -> None:
        # Field(min_length=1) alone accepts a lone space; NonBlankStr closes that gap.
        with pytest.raises(ValidationError):
            MarketItem(appid=753, market_hash_name=bad)

    @pytest.mark.parametrize(
        "factory",
        [
            lambda v: Card(appid=1, market_hash_name=v),
            lambda v: UserCardInventory(appid=1, market_hash_name=v, quantity=1),
            lambda v: SteamApp(appid=1, name=v),
        ],
    )
    def test_models_reject_whitespace_only(self, factory) -> None:
        with pytest.raises(ValidationError):
            factory("   ")

    def test_valid_name_accepted(self) -> None:
        assert MarketItem(appid=753, market_hash_name="440-Heavy").market_hash_name == "440-Heavy"

    def test_value_is_validated_not_stripped(self) -> None:
        # A name with surrounding spaces but real content is preserved verbatim (exact Steam
        # hash names matter — we validate, we do not trim).
        item = MarketItem(appid=753, market_hash_name=" 440-Heavy ")
        assert item.market_hash_name == " 440-Heavy "
