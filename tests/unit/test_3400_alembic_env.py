import os
from unittest import mock

import pytest

from core.database.utils import get_database_url

pytestmark = pytest.mark.vc4


def test_get_database_url_from_env_postgres():
    with mock.patch.dict(
        os.environ, {"DATABASE_URL": "postgresql://user:pass@host/db"}
    ):
        url = get_database_url()
        assert url == "postgresql+asyncpg://user:pass@host/db"


def test_get_database_url_from_env_postgres_legacy():
    with mock.patch.dict(os.environ, {"DATABASE_URL": "postgres://user:pass@host/db"}):
        url = get_database_url()
        assert url == "postgresql+asyncpg://user:pass@host/db"


def test_get_database_url_from_config(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # We should get sqlite from config when no env var is set
    url = get_database_url()
    assert url.startswith("sqlite+aiosqlite:///")


def test_get_database_url_empty_config(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with mock.patch("config.get_config") as mock_get_config:
        mock_get_config.return_value.DATABASE_URL = ""
        with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
            get_database_url()
