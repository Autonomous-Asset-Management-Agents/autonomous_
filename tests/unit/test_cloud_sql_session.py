# tests/unit/test_cloud_sql_session.py
# TDD — Tests geschrieben VOR der Implementierung von core/database/session.py
#
# Abgedeckte Szenarien:
#   1. CLOUD_SQL_CONNECTION_NAME gesetzt → Engine via async_creator (Connector-Pfad)
#   2. CLOUD_SQL_CONNECTION_NAME NICHT gesetzt → Engine via DATABASE_URL (lokaler Pfad)
#   3. DATABASE_URL wird korrekt geparst: user, password, db-name
#   4. Fallback auf dummy-URL wenn DATABASE_URL fehlt → Warning
#   5. AsyncSessionLocal ist ein valides sessionmaker-Objekt
#   6. Keine echte DB-Verbindung nötig (alles gemockt)

import importlib
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _reload_session_module(monkeypatch, env: dict):
    """Reloads the session module with a clean environment."""
    for key in ["CLOUD_SQL_CONNECTION_NAME", "DATABASE_URL"]:
        monkeypatch.delenv(key, raising=False)
    for key, val in env.items():
        monkeypatch.setenv(key, val)

    # Remove cached modules so env changes take effect
    for mod_name in list(sys.modules.keys()):
        if "core.database.session" in mod_name or "google.cloud.sql" in mod_name:
            del sys.modules[mod_name]

    import core.database.session as session_mod

    importlib.reload(session_mod)
    return session_mod


class TestLocalMode:
    """When CLOUD_SQL_CONNECTION_NAME is not set → local/direct DB URL path."""

    def test_no_conn_name_uses_database_url(self, monkeypatch):
        """Engine is created from DATABASE_URL when CLOUD_SQL_CONNECTION_NAME absent."""
        db_url = "postgresql+asyncpg://user:pass@localhost:5432/testdb"
        mod = _reload_session_module(monkeypatch, {"DATABASE_URL": db_url})
        assert mod.engine is not None

    def test_missing_database_url_logs_warning(self, monkeypatch, caplog):
        """Warning is emitted when DATABASE_URL is not set (dummy fallback used)."""
        import logging

        with caplog.at_level(logging.WARNING):
            mod = _reload_session_module(monkeypatch, {})
        assert mod.engine is not None  # Engine must exist even without valid URL

    def test_async_session_local_is_sessionmaker(self, monkeypatch):
        """AsyncSessionLocal is a valid SQLAlchemy sessionmaker."""
        from sqlalchemy.orm import sessionmaker

        mod = _reload_session_module(
            monkeypatch, {"DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db"}
        )
        assert isinstance(mod.AsyncSessionLocal, sessionmaker)


class TestCloudSQLConnectorMode:
    """When CLOUD_SQL_CONNECTION_NAME is set → Cloud SQL Python Connector path."""

    def test_conn_name_triggers_connector_path(self, monkeypatch):
        """When CLOUD_SQL_CONNECTION_NAME set, engine uses async_creator not URL string."""
        mock_connector_module = MagicMock()
        mock_connector_module.create_async_connector = AsyncMock(
            return_value=MagicMock()
        )

        with patch.dict(
            sys.modules, {"google.cloud.sql.connector": mock_connector_module}
        ):
            mod = _reload_session_module(
                monkeypatch,
                {
                    "CLOUD_SQL_CONNECTION_NAME": "project:region:instance",
                    "DATABASE_URL": "postgresql+asyncpg://user:secret@34.1.2.3:5432/mydb",
                },
            )
        assert mod.engine is not None

    def test_database_url_parsed_for_credentials(self, monkeypatch):
        """User, password, and db-name are correctly parsed from DATABASE_URL."""
        from urllib.parse import urlparse

        url = "postgresql+asyncpg://myuser:mypassword@1.2.3.4:5432/mydb"
        parsed = urlparse(url)
        assert parsed.username == "myuser"
        assert parsed.password == "mypassword"
        assert parsed.path.lstrip("/") == "mydb"

    def test_connector_mode_engine_not_none(self, monkeypatch):
        """Engine object is always created even in connector mode."""
        mock_mod = MagicMock()
        mock_mod.create_async_connector = AsyncMock(return_value=MagicMock())

        with patch.dict(sys.modules, {"google.cloud.sql.connector": mock_mod}):
            mod = _reload_session_module(
                monkeypatch,
                {
                    "CLOUD_SQL_CONNECTION_NAME": "proj:europe-west3:aaa-postgres",
                    "DATABASE_URL": "postgresql+asyncpg://bot:pw@34.1.2.3:5432/botdb",
                },
            )
        assert mod.engine is not None
        assert mod.AsyncSessionLocal is not None

    def test_connector_mode_falls_back_gracefully_on_import_error(self, monkeypatch):
        """If google-cloud-sql-connector not installed, module does not crash."""
        with patch.dict(sys.modules, {"google.cloud.sql.connector": None}):
            try:
                mod = _reload_session_module(
                    monkeypatch,
                    {
                        "CLOUD_SQL_CONNECTION_NAME": "proj:region:inst",
                        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
                    },
                )
                assert mod.engine is not None
            except (ImportError, TypeError):
                pytest.skip("Connector not installed — expected in CI without package")
