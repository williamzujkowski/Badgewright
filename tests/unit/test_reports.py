"""Tests for purchase-plan reports — CSV-injection and inert-HTML are safety-critical."""

from __future__ import annotations

import csv as csvmod
from datetime import UTC, datetime

import pytest

from steam_badge_optimizer.db import Store
from steam_badge_optimizer.models import (
    BadgeSet,
    Card,
    MarketItem,
    Money,
    PriceSnapshot,
    SourceKind,
    SourceRecord,
)
from steam_badge_optimizer.optimize import build_plan, compute_costs
from steam_badge_optimizer.reports import (
    InertHtmlError,
    assert_inert_html,
    neutralize_formula,
    render_html,
    write_csv,
    write_html,
)


def _seed(store: Store, *, card_name: str = "100-A", game: str = "Cheap Game") -> None:
    from steam_badge_optimizer.models import SteamApp

    store.upsert_app(SteamApp(appid=100, name=game))
    store.upsert_badge_set(BadgeSet(appid=100, set_size=1))
    store.upsert_card(Card(appid=100, market_hash_name=card_name))
    store.add_price_snapshot(
        PriceSnapshot(
            item=MarketItem(appid=100, market_hash_name=card_name),
            lowest=Money(100, "USD"),
            volume=500,
            source=SourceRecord(
                kind=SourceKind.STEAM_MARKET,
                url="https://steamcommunity.com/market/priceoverview/",
                fetched_at=datetime.now(UTC),
                parser_version="1",
                raw_sha256=SourceRecord.sha256_of(card_name.encode()),
                cache_ttl_seconds=86400,
            ),
        )
    )


def _plan(store: Store):
    return build_plan(compute_costs(store, target_level=5, currency="USD"))


class TestCsvInjection:
    @pytest.mark.parametrize("payload", ["=cmd()", "+1", "-2", "@SUM(A1)", "|calc", "\t=evil"])
    def test_formula_triggers_are_quoted(self, payload: str) -> None:
        assert neutralize_formula(payload).startswith("'")

    def test_leading_whitespace_then_trigger_covered(self) -> None:
        assert neutralize_formula("   =danger").startswith("'")

    def test_safe_values_untouched(self) -> None:
        assert neutralize_formula("Half-Life 2") == "Half-Life 2"
        assert neutralize_formula("100-Heavy") == "100-Heavy"  # interior '-' fine

    def test_written_csv_neutralizes_malicious_card_name(self, tmp_path) -> None:
        with Store.in_memory() as store:
            _seed(store, card_name="=HYPERLINK(evil)")
            write_csv(_plan(store), store, tmp_path / "p.csv")
            with open(tmp_path / "p.csv", encoding="utf-8") as fh:
                rows = list(csvmod.DictReader(fh))
        assert rows[0]["card"].startswith("'=")  # formula neutralized in the file


class TestInertHtml:
    def test_valid_report_passes_inertness(self, tmp_path) -> None:
        with Store.in_memory() as store:
            _seed(store)
            write_html(_plan(store), store, tmp_path / "p.html")
            doc = (tmp_path / "p.html").read_text()
        assert "Content-Security-Policy" in doc
        assert_inert_html(doc)  # no raise

    def test_malicious_card_name_is_escaped_not_executable(self) -> None:
        with Store.in_memory() as store:
            _seed(store, card_name="<script>alert(1)</script>", game='"><img onerror=x>')
            doc = render_html(_plan(store), store)
        # Rendered doc must remain inert and contain the escaped payload, not live markup.
        assert_inert_html(doc)
        assert "<script>alert(1)</script>" not in doc
        assert "&lt;script&gt;" in doc

    def test_colon_scheme_lookalike_name_still_renders(self, tmp_path) -> None:
        # Steam titles commonly contain colons; a name that merely contains "steam:" or
        # "data:" as plain text must NOT fail the inert check (it's escaped text, not a link).
        with Store.in_memory() as store:
            _seed(store, card_name="Data: Recovery", game="Portal 2: steam:cake")
            write_html(_plan(store), store, tmp_path / "p.html")  # must not raise
            doc = (tmp_path / "p.html").read_text()
        assert_inert_html(doc)
        assert "Portal 2: steam:cake".replace(":", "") in doc.replace(":", "")

    def test_attacker_quote_in_href_context_is_safe(self) -> None:
        # A card name with a double-quote can't break out of the href attribute:
        # listings_url() urllib-quotes it and html.escape(quote=True) escapes it.
        with Store.in_memory() as store:
            _seed(store, card_name='" onmouseover="alert(1)')
            doc = render_html(_plan(store), store)
        assert_inert_html(doc)
        assert 'onmouseover="alert(1)"' not in doc

    @pytest.mark.parametrize(
        "bad",
        [
            "<html><head><meta http-equiv='Content-Security-Policy' content='x'></head>"
            "<body><script>x</script></body></html>",
            "<html><body onload='x'>hi</body></html>",
            '<html><body><a href="javascript:alert(1)">x</a></body></html>',
            '<html><body><a href="steam://run/440">x</a></body></html>',
            "<html><body>no csp here</body></html>",
            '<html><head><meta http-equiv="Content-Security-Policy" content="x"></head>'
            '<body><a href="https://steamcommunity.com/market/sellitem/1">x</a></body></html>',
        ],
    )
    def test_assert_inert_rejects_violations(self, bad: str) -> None:
        with pytest.raises(InertHtmlError):
            assert_inert_html(bad)
