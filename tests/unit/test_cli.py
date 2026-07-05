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


def test_command_needing_args_fails_cleanly_not_with_traceback() -> None:
    # badges import with no source: a clean guidance message + exit 2, no traceback.
    result = runner.invoke(app, ["badges", "import"])
    assert result.exit_code == 2
    assert "provide --file" in result.output.lower()
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_no_command_advertises_unimplemented() -> None:
    # Every wired command now does real work — nothing should say "not implemented".
    result = runner.invoke(app, ["--help"])
    assert "not implemented" not in result.output.lower()


def test_optimize_on_empty_db_is_graceful(tmp_path) -> None:
    result = runner.invoke(app, ["optimize", "--budget", "50", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "no badges to plan" in result.output.lower()
