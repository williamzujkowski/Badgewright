"""Tests for the inventory-value CSV/HTML export (#99), incl. injection defenses."""

from __future__ import annotations

import csv
from datetime import UTC, datetime

import pytest

from steam_badge_optimizer.analytics.inventory_valuation import HoldingValue, InventoryValuation
from steam_badge_optimizer.models import Money
from steam_badge_optimizer.reports import assert_inert_html, write_inventory_value
from steam_badge_optimizer.reports.inventory_report import (
    build_inventory_rows,
    render_inventory_html,
)

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _holding(
    appid: int, name: str, qty: int, cents: int | None, *, foil: bool = False, sig=None, kind="card"
):
    unit = Money(cents, "USD") if cents is not None else None
    line = Money(cents * qty, "USD") if cents is not None else None
    return HoldingValue(
        appid=appid,
        market_hash_name=name,
        quantity=qty,
        is_foil=foil,
        unit_price=unit,
        line_value=line,
        kind=kind,
        signals=sig or [],
    )


def _valuation(holdings, *, total: int, priced: int, unpriced: int) -> InventoryValuation:
    return InventoryValuation(
        currency="USD",
        total_value=Money(total, "USD"),
        priced_count=priced,
        unpriced_count=unpriced,
        holdings=holdings,
    )


class TestBuildRows:
    def test_projects_fields_and_injects_timestamp(self) -> None:
        v = _valuation([_holding(220, "220-A", 2, 50)], total=100, priced=1, unpriced=0)
        rows = build_inventory_rows(v, {220: "Half-Life 2"}, now=NOW)
        r = rows[0]
        assert r.rank == 1 and r.game == "Half-Life 2" and r.appid == 220
        assert r.quantity == 2 and r.unit_price == "0.50" and r.line_value == "1.00"
        assert r.as_of == "2026-01-02T03:04:05+00:00"

    def test_unpriced_and_foil_render_blank_and_yes(self) -> None:
        v = _valuation(
            [_holding(1, "1-A", 1, None, foil=True, sig=["unpriced (no cached USD market price)"])],
            total=0,
            priced=0,
            unpriced=1,
        )
        r = build_inventory_rows(v, {}, now=NOW)[0]
        assert r.unit_price == "" and r.line_value == ""
        assert r.foil == "yes" and r.game == "App 1"
        assert "unpriced" in r.notes


class TestKindColumn:
    def test_kind_in_csv_and_html(self, tmp_path) -> None:
        v = _valuation(
            [_holding(753, "753-Gems", 1000, 5, kind="gems")], total=5, priced=1, unpriced=0
        )
        rows = build_inventory_rows(v, {}, now=NOW)
        assert rows[0].kind == "gems"
        doc = render_inventory_html(v, rows, currency="USD")
        assert "gems" in doc and "<th>kind</th>" in doc
        assert_inert_html(doc)


class TestCsvExport:
    def test_writes_columns_and_rows(self, tmp_path) -> None:
        p = tmp_path / "out.csv"
        v = _valuation([_holding(220, "220-A", 2, 50)], total=100, priced=1, unpriced=0)
        n = write_inventory_value(v, {220: "Half-Life 2"}, p, now=NOW)
        assert n == 1
        with p.open() as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["game"] == "Half-Life 2"
        assert rows[0]["line_value"] == "1.00"

    def test_formula_injection_neutralized_in_game_and_card(self, tmp_path) -> None:
        p = tmp_path / "evil.csv"
        v = _valuation([_holding(1, "=cmd|'/c calc'!A1", 1, 10)], total=10, priced=1, unpriced=0)
        write_inventory_value(v, {1: "=HYPERLINK(evil)"}, p, now=NOW)
        with p.open() as fh:
            row = next(csv.DictReader(fh))
        assert row["game"].startswith("'=")  # neutralized
        assert row["card"].startswith("'=")

    def test_delimiter_chars_stay_in_one_cell(self, tmp_path) -> None:
        p = tmp_path / "delim.csv"
        v = _valuation([_holding(7, "7-A", 1, 10)], total=10, priced=1, unpriced=0)
        write_inventory_value(v, {7: 'A, "B"\nC'}, p, now=NOW)
        with p.open() as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1 and rows[0]["appid"] == "7"
        assert rows[0]["game"] == 'A, "B"\nC'


class TestHtmlExport:
    def test_is_inert_and_shows_total(self, tmp_path) -> None:
        p = tmp_path / "out.html"
        v = _valuation([_holding(220, "220-A", 2, 50)], total=100, priced=1, unpriced=0)
        write_inventory_value(v, {220: "Half-Life 2"}, p, now=NOW)
        doc = p.read_text()
        assert_inert_html(doc)
        assert "Content-Security-Policy" in doc
        assert "1.00 USD" in doc  # the total
        assert "Half-Life 2" in doc

    def test_escapes_malicious_card_name(self) -> None:
        v = _valuation(
            [_holding(1, "<script>alert('x')</script>", 1, 10)], total=10, priced=1, unpriced=0
        )
        doc = render_inventory_html(v, build_inventory_rows(v, {}, now=NOW), currency="USD")
        assert "<script>" not in doc
        assert "&lt;script&gt;" in doc
        assert_inert_html(doc)

    def test_escapes_malicious_notes(self) -> None:
        v = _valuation(
            [_holding(1, "1-A", 1, None, sig=["<img src=x onerror=alert(1)>"])],
            total=0,
            priced=0,
            unpriced=1,
        )
        doc = render_inventory_html(v, build_inventory_rows(v, {}, now=NOW), currency="USD")
        assert "<img" not in doc
        assert "&lt;img" in doc
        assert_inert_html(doc)


class TestWrite:
    def test_empty_is_valid(self, tmp_path) -> None:
        p = tmp_path / "empty.html"
        v = _valuation([], total=0, priced=0, unpriced=0)
        assert write_inventory_value(v, {}, p, now=NOW) == 0
        assert_inert_html(p.read_text())

    def test_unsupported_extension_rejected(self, tmp_path) -> None:
        v = _valuation([_holding(1, "1-A", 1, 10)], total=10, priced=1, unpriced=0)
        with pytest.raises(ValueError):
            write_inventory_value(v, {}, tmp_path / "x.pdf", now=NOW)


class TestCli:
    def test_writes_and_rejects_bad_extension(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app
        from steam_badge_optimizer.config import Settings
        from steam_badge_optimizer.db import Store
        from steam_badge_optimizer.models import SteamApp, UserCardInventory

        s = Settings.resolve(data_dir=str(tmp_path))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with Store(s.db_path) as store:
            store.upsert_app(SteamApp(appid=220, name="Half-Life 2"))
            store.upsert_inventory(
                UserCardInventory(appid=220, market_hash_name="220-A", quantity=1)
            )
        runner = CliRunner()
        bad = runner.invoke(
            app,
            [
                "report",
                "inventory-value",
                "--out",
                str(tmp_path / "x.pdf"),
                "--data-dir",
                str(tmp_path),
            ],
        )
        assert bad.exit_code == 2
        out = tmp_path / "r.csv"
        ok = runner.invoke(
            app, ["report", "inventory-value", "--out", str(out), "--data-dir", str(tmp_path)]
        )
        assert ok.exit_code == 0
        assert out.is_file()
