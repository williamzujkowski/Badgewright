"""Privacy & credential-safety invariants (Epics 8.4, 1.3, 0.4, 9.5)."""

from __future__ import annotations

import importlib.util

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from steam_badge_optimizer import models as models_pkg
from steam_badge_optimizer.cli import app
from steam_badge_optimizer.db import Store
from steam_badge_optimizer.db.schema import MIGRATIONS
from steam_badge_optimizer.models import SteamApp

runner = CliRunner()

# Anything that would smell like a stored Steam credential/session secret.
FORBIDDEN_SECRET_NAMES = {
    "steamloginsecure",
    "sessionid",
    "session_id",
    "shared_secret",
    "identity_secret",
    "revocation_code",
    "password",
    "steam_guard",
    "steamguard",
    "access_token",
    "refresh_token",
}


class TestDeleteAll:
    def _seed(self, data_dir) -> None:
        from steam_badge_optimizer.config import Settings

        s = Settings.resolve(data_dir=str(data_dir))
        s.data_dir.mkdir(parents=True, exist_ok=True)
        with Store(s.db_path) as store:
            store.upsert_app(SteamApp(appid=440, name="Team Fortress 2"))

    def test_deletes_db_and_leaves_no_data(self, tmp_path) -> None:
        from steam_badge_optimizer.config import Settings

        self._seed(tmp_path)
        db = Settings.resolve(data_dir=str(tmp_path)).db_path
        assert db.is_file()
        result = runner.invoke(app, ["delete-all", "--yes", "--data-dir", str(tmp_path)])
        assert result.exit_code == 0
        # DB and every journal/WAL sidecar are gone.
        for suffix in ("", "-wal", "-shm", "-journal"):
            assert not (db.parent / f"{db.name}{suffix}").exists()
        # No trace of the imported data survives anywhere in the data dir.
        for f in tmp_path.rglob("*"):
            if f.is_file():
                assert b"Team Fortress 2" not in f.read_bytes()

    def test_nothing_to_delete_is_graceful(self, tmp_path) -> None:
        result = runner.invoke(app, ["delete-all", "--yes", "--data-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "no local data" in result.output.lower()

    def test_prompt_abort_keeps_data(self, tmp_path) -> None:
        from steam_badge_optimizer.config import Settings

        self._seed(tmp_path)
        result = runner.invoke(app, ["delete-all", "--data-dir", str(tmp_path)], input="n\n")
        assert result.exit_code == 1
        assert Settings.resolve(data_dir=str(tmp_path)).db_path.is_file()  # untouched


class TestNoStoredSecrets:
    def test_no_model_field_is_a_credential(self) -> None:
        # Structural guarantee: the domain models cannot hold a Steam credential/secret.
        checked = 0
        for name in dir(models_pkg):
            obj = getattr(models_pkg, name)
            if isinstance(obj, type) and issubclass(obj, BaseModel):
                checked += 1
                for field in obj.model_fields:
                    assert field.lower() not in FORBIDDEN_SECRET_NAMES, f"{name}.{field}"
        assert checked >= 8  # sanity: we actually inspected the models

    def test_no_schema_column_is_a_credential(self) -> None:
        ddl = " ".join(stmt for migration in MIGRATIONS for stmt in migration).lower()
        for secret in FORBIDDEN_SECRET_NAMES:
            assert secret not in ddl, f"schema references {secret!r}"


class TestEgressAudit:
    @pytest.mark.parametrize("module", ["requests", "aiohttp", "pycurl", "websockets", "tornado"])
    def test_no_extra_network_library_installed(self, module: str) -> None:
        # Egress audit: the only sanctioned network client is httpx (via SafeClient).
        # A dependency pulling in another network library would widen the egress surface.
        assert importlib.util.find_spec(module) is None, (
            f"{module} is installed — review the dependency that pulled it in"
        )
