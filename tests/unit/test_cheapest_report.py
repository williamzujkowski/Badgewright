"""Tests for the cheapest-badges CSV/HTML export (#70), incl. injection defenses."""

from __future__ import annotations

import csv
from datetime import UTC, datetime

import pytest

from steam_badge_optimizer.analytics import BadgeSetCost
from steam_badge_optimizer.models import Confidence, Money
from steam_badge_optimizer.reports import assert_inert_html, render_cheapest_html, write_cheapest
from steam_badge_optimizer.reports.cheapest_report import build_cheapest_rows

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _badge(appid: int, cents: int, *, liquid: bool = True, signals=None) -> BadgeSetCost:
    return BadgeSetCost(
        appid=appid,
        set_size=6,
        total_cost=Money(cents, "USD"),
        cost_per_xp_cents=cents / 100,
        min_liquidity=100,
        liquid=liquid,
        bottleneck_fraction=0.5,
        confidence=Confidence.MEDIUM if liquid else Confidence.LOW,
        signals=signals or [],
    )


class TestBuildRows:
    def test_projects_fields_and_injects_timestamp(self) -> None:
        rows = build_cheapest_rows(
            [_badge(220, 321)], {220: "Half-Life 2"}, currency="USD", now=NOW
        )
        r = rows[0]
        assert r.rank == 1 and r.game == "Half-Life 2" and r.appid == 220
        assert r.total_cost == "3.21" and r.buyable == "yes"
        assert r.bottleneck_pct == "50"
        assert r.as_of == "2026-01-02T03:04:05+00:00"  # injected, deterministic

    def test_thin_badge_marked(self) -> None:
        rows = build_cheapest_rows([_badge(1, 5, liquid=False)], {}, currency="USD", now=NOW)
        assert rows[0].buyable == "thin"
        assert rows[0].game == "App 1"  # fallback when name unknown

    def test_no_bottleneck_gives_empty_pct(self) -> None:
        from dataclasses import replace

        b = replace(_badge(1, 10), bottleneck_fraction=None)
        assert build_cheapest_rows([b], {}, currency="USD", now=NOW)[0].bottleneck_pct == ""


class TestCsvExport:
    def test_writes_columns_and_rows(self, tmp_path) -> None:
        p = tmp_path / "out.csv"
        n = write_cheapest([_badge(220, 321)], {220: "Half-Life 2"}, p, currency="USD", now=NOW)
        assert n == 1
        with p.open() as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["game"] == "Half-Life 2"
        assert rows[0]["total_cost"] == "3.21"
        assert rows[0]["as_of"] == "2026-01-02T03:04:05+00:00"

    def test_formula_injection_neutralized(self, tmp_path) -> None:
        # A malicious game name starting with '=' must be prefixed with a quote.
        p = tmp_path / "evil.csv"
        write_cheapest([_badge(1, 10)], {1: "=cmd|'/c calc'!A1"}, p, currency="USD", now=NOW)
        text = p.read_text()
        assert "'=cmd" in text  # neutralized, not a live formula

    def test_notes_cell_is_neutralized(self, tmp_path) -> None:
        # signals (notes) are Steam-derived text too — must be formula-neutralized.
        p = tmp_path / "notes.csv"
        write_cheapest(
            [_badge(1, 10, signals=["=HYPERLINK(evil)"])], {}, p, currency="USD", now=NOW
        )
        with p.open() as fh:
            row = next(csv.DictReader(fh))
        assert row["notes"].startswith("'=")  # neutralized

    def test_leading_whitespace_formula_neutralized(self, tmp_path) -> None:
        p = tmp_path / "ws.csv"
        write_cheapest([_badge(1, 10)], {1: "\t=cmd"}, p, currency="USD", now=NOW)
        with p.open() as fh:
            row = next(csv.DictReader(fh))
        assert row["game"].startswith("'")  # tab-then-= still neutralized

    def test_delimiter_chars_stay_in_one_cell(self, tmp_path) -> None:
        # A game name with a comma/quote/newline must not break the column layout.
        p = tmp_path / "delim.csv"
        write_cheapest([_badge(7, 10)], {7: 'A, "B"\nC'}, p, currency="USD", now=NOW)
        with p.open() as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1 and rows[0]["appid"] == "7"  # no column/row breakout
        assert rows[0]["game"] == 'A, "B"\nC'


class TestHtmlExport:
    def test_is_inert(self, tmp_path) -> None:
        p = tmp_path / "out.html"
        write_cheapest([_badge(220, 321)], {220: "Half-Life 2"}, p, currency="USD", now=NOW)
        doc = p.read_text()
        assert_inert_html(doc)  # no throw
        assert "Content-Security-Policy" in doc
        assert "Half-Life 2" in doc

    def test_escapes_malicious_game_name(self) -> None:
        # A Steam-sourced game name with markup must be escaped and the doc stay inert.
        doc = render_cheapest_html(
            build_cheapest_rows(
                [_badge(1, 10)], {1: "<script>alert('x')</script>"}, currency="USD", now=NOW
            ),
            currency="USD",
        )
        assert "<script>" not in doc  # escaped
        assert "&lt;script&gt;" in doc
        assert_inert_html(doc)  # still provably inert

    def test_escapes_malicious_notes(self) -> None:
        # signals (notes) render into HTML too and must be escaped + inert.
        doc = render_cheapest_html(
            build_cheapest_rows(
                [_badge(1, 10, signals=["<img src=x onerror=alert(1)>"])],
                {},
                currency="USD",
                now=NOW,
            ),
            currency="USD",
        )
        assert "<img" not in doc  # the tag is escaped to &lt;img (inert text, no live handler)
        assert "&lt;img" in doc
        assert_inert_html(doc)  # provably inert

    def test_game_name_with_scheme_like_text_stays_inert(self) -> None:
        # "Portal: data" as escaped text (not a link) must not trip the URL-scheme check.
        doc = render_cheapest_html(
            build_cheapest_rows([_badge(1, 10)], {1: "data:evil Portal"}, currency="USD", now=NOW),
            currency="USD",
        )
        assert_inert_html(doc)


class TestWriteCheapest:
    def test_empty_ranking_is_valid(self, tmp_path) -> None:
        p = tmp_path / "empty.html"
        assert write_cheapest([], {}, p, currency="USD", now=NOW) == 0
        assert_inert_html(p.read_text())

    def test_unsupported_extension_rejected(self, tmp_path) -> None:
        with pytest.raises(ValueError):
            write_cheapest([_badge(1, 10)], {}, tmp_path / "x.pdf", currency="USD", now=NOW)


class TestCli:
    def test_writes_and_rejects_bad_extension(self, tmp_path) -> None:
        from typer.testing import CliRunner

        from steam_badge_optimizer.cli import app
        from steam_badge_optimizer.config import Settings
        from steam_badge_optimizer.db import Store
        from steam_badge_optimizer.models import SteamApp

        s = Settings.resolve(data_dir=str(tmp_path))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with Store(s.db_path) as store:
            store.upsert_app(SteamApp(appid=220, name="Half-Life 2"))
        runner = CliRunner()
        bad = runner.invoke(
            app,
            [
                "report",
                "cheapest-badges",
                "--out",
                str(tmp_path / "x.pdf"),
                "--data-dir",
                str(tmp_path),
            ],
        )
        assert bad.exit_code == 2
        out = tmp_path / "r.csv"
        ok = runner.invoke(
            app, ["report", "cheapest-badges", "--out", str(out), "--data-dir", str(tmp_path)]
        )
        assert ok.exit_code == 0
        assert out.is_file()
