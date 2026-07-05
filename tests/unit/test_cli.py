"""Smoke tests for the CLI wiring: --help works, version prints, stubs fail loudly."""

from __future__ import annotations

from typer.testing import CliRunner

from steam_badge_optimizer import __version__
from steam_badge_optimizer.cli import app

runner = CliRunner()


def test_help_lists_all_command_groups() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for group in ("catalog", "inventory", "badges", "prices", "optimize", "report", "market"):
        assert group in result.output


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_safety_command_states_the_boundary() -> None:
    result = runner.invoke(app, ["safety"])
    assert result.exit_code == 0
    assert "never automate" in result.output.lower() or "does not" in result.output.lower()
    assert "POST" not in result.output  # only read verbs are advertised


def test_init_creates_data_dir(tmp_path) -> None:
    result = runner.invoke(app, ["init", "--data-dir", str(tmp_path / "sbo")])
    assert result.exit_code == 0
    assert (tmp_path / "sbo").is_dir()


def test_unimplemented_stub_exits_nonzero() -> None:
    # market scan-weakness is still a stub (Milestone 5).
    result = runner.invoke(app, ["market", "scan-weakness", "--top", "5"])
    assert result.exit_code == 2
    assert "not implemented" in result.output.lower()


def test_optimize_on_empty_db_is_graceful(tmp_path) -> None:
    result = runner.invoke(app, ["optimize", "--budget", "50", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "no badges to plan" in result.output.lower()
