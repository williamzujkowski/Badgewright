"""CLI-level tests for `sbo optimize` input validation and safety of the spend plan."""

from __future__ import annotations

from datetime import UTC, datetime

from typer.testing import CliRunner

from steam_badge_optimizer.cli import app
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

runner = CliRunner()


def _seed(data_dir) -> None:
    from steam_badge_optimizer.config import Settings

    settings = Settings.resolve(data_dir=str(data_dir))
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with Store(settings.db_path) as store:
        store.upsert_badge_set(BadgeSet(appid=100, set_size=1))
        store.upsert_card(Card(appid=100, market_hash_name="100-A"))
        store.add_price_snapshot(
            PriceSnapshot(
                item=MarketItem(appid=100, market_hash_name="100-A"),
                lowest=Money(100, "USD"),
                volume=500,
                source=SourceRecord(
                    kind=SourceKind.STEAM_MARKET,
                    url="https://steamcommunity.com/market/priceoverview/",
                    fetched_at=datetime.now(UTC),
                    parser_version="1",
                    raw_sha256=SourceRecord.sha256_of(b"a"),
                    cache_ttl_seconds=86400,
                ),
            )
        )


def test_badge_level_over_max_rejected(tmp_path) -> None:
    result = runner.invoke(app, ["optimize", "--badge-level", "99", "--data-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "badge-level must be" in result.output


def test_negative_budget_rejected_cleanly(tmp_path) -> None:
    result = runner.invoke(app, ["optimize", "--budget", "-5", "--data-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "budget must be" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_target_level_without_current_rejected(tmp_path) -> None:
    result = runner.invoke(app, ["optimize", "--target-level", "10", "--data-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "needs --current-level" in result.output


def test_current_level_without_target_warns(tmp_path) -> None:
    _seed(tmp_path)
    result = runner.invoke(app, ["optimize", "--current-level", "5", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "only used with --target-level" in result.output


def test_plan_never_exceeds_budget(tmp_path) -> None:
    _seed(tmp_path)  # one badge, 5 crafts * $1.00 = $5.00 total
    result = runner.invoke(app, ["optimize", "--budget", "3", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0
    # $5 badge doesn't fit a $3 budget -> not chosen, total stays $0, budget intact.
    assert "Total: 0.00 USD" in result.output
    assert "Budget remaining: 3.00 USD" in result.output
