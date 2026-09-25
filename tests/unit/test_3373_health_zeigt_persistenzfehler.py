"""#3373 — ein stiller Persistenz-Ausfall muss in ``/health`` sichtbar sein.

Sieben Tage lang scheiterte jeder ``decisions``-Insert. Der ``CloudLogger`` zaehlte das
brav mit (``stats["errors"]``, ``stats["fallback_writes"]``) — aber niemand las die Zaehler.
Auf dem Desktop gibt es kein Engine-Log auf Platte; ``/health`` ist dort die einzige Stelle,
an der ein Betreiber so etwas sehen kann.

Reine Beobachtung: Der Status bleibt ``healthy``. Ob aus den Zahlen ein Alarm wird,
entscheidet die Oberflaeche — nicht dieser Endpunkt.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    return TestClient(api_routes_mod.app)


def _health(client, stats=None, wirft=False):
    logger = MagicMock()
    if wirft:
        logger.get_stats.side_effect = RuntimeError("kaputt")
    else:
        logger.get_stats.return_value = stats
    with (
        patch(
            "core.engine.api_routes.RedisClient.check_health",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("core.engine.api_routes.engine") as engine,
        patch("core.cloud_logger.get_cloud_logger", return_value=logger),
    ):
        engine.strategy_running.is_set.return_value = True
        return client.get("/health").json()


def test_health_zeigt_abgewiesene_schreibvorgaenge(client):
    data = _health(
        client, {"errors": 412, "fallback_writes": 3926, "decisions_logged": 0}
    )

    assert data["persistence"] == {
        "errors": 412,
        "fallback_writes": 3926,
        "decisions_logged": 0,
    }
    assert data["status"] == "healthy", "Beobachtung, kein Urteil"


def test_health_ueberlebt_einen_kaputten_logger(client):
    data = _health(client, wirft=True)

    assert data["status"] == "healthy"
    assert data["persistence"] is None
