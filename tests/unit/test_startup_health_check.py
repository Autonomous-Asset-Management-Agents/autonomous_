# tests/unit/test_startup_health_check.py
# TDD Red Phase — Startup Health Check
#
# Gherkin:
#   Given: Engine startet
#   When:  _startup_health_check() aufgerufen
#   Then:  Schlägt fehl (RuntimeError) wenn Redis oder Gemini nicht erreichbar
#          Loggt WARNING wenn RL-Modell fehlt (non-critical, degraded mode)
#          Gibt None zurück wenn alle checks passen

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """Minimal BotEngine ohne echte API-Verbindungen."""
    with patch("config.GEMINI_API_KEY", "test-key"), patch(
        "core.engine.base.TradingClient", MagicMock()
    ), patch("core.engine.base.StockHistoricalDataClient", MagicMock()), patch(
        "core.engine.base.RedisClient", MagicMock()
    ), patch(
        "core.engine.base.AIMarketScanner", MagicMock()
    ), patch(
        "core.engine.base.AILearningEngine", MagicMock()
    ), patch(
        "core.engine.base.HistoricalDataProvider", MagicMock()
    ), patch(
        "core.engine.base.MarketRegimeModel", MagicMock()
    ), patch(
        "core.engine.base.NewsProcessor", MagicMock()
    ), patch(
        "core.engine.base.AILearnedRules", MagicMock()
    ), patch(
        "core.engine.base.AgentRegistry", MagicMock()
    ), patch(
        "core.engine.base.set_global_registry", MagicMock()
    ), patch(
        "core.engine.base.ComplianceGuardian", MagicMock()
    ), patch(
        "core.engine.base.get_cloud_logger", MagicMock()
    ), patch(
        "core.engine.base.threading.Thread", MagicMock()
    ):
        from core.engine.base import BotEngine

        eng = BotEngine.__new__(BotEngine)
        eng._shutdown_event = MagicMock()
        eng._shutdown_event.is_set.return_value = False
        return eng


# ---------------------------------------------------------------------------
# Tests: alle checks OK → kein Fehler
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_startup_health_check_passes_when_all_ok(engine):
    """Wenn Redis + Gemini erreichbar und RL-Modell vorhanden → kein Error."""
    with patch.object(
        engine, "_check_redis", AsyncMock(return_value=True)
    ), patch.object(
        engine, "_check_gemini", AsyncMock(return_value=True)
    ), patch.object(
        engine, "_check_model_files", return_value=True
    ):
        # Should not raise
        await engine._startup_health_check()


# ---------------------------------------------------------------------------
# Tests: kritische Fehler → RuntimeError
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_startup_health_check_fails_when_redis_down(engine):
    """Redis nicht erreichbar → RuntimeError mit 'redis' im Message."""
    with patch.object(
        engine, "_check_redis", AsyncMock(return_value=False)
    ), patch.object(
        engine, "_check_gemini", AsyncMock(return_value=True)
    ), patch.object(
        engine, "_check_model_files", return_value=True
    ):
        with pytest.raises(RuntimeError, match="redis"):
            await engine._startup_health_check()


@pytest.mark.anyio
async def test_startup_health_check_fails_when_gemini_down(engine):
    """Gemini API nicht erreichbar → RuntimeError mit 'gemini' im Message."""
    with patch.object(
        engine, "_check_redis", AsyncMock(return_value=True)
    ), patch.object(
        engine, "_check_gemini", AsyncMock(return_value=False)
    ), patch.object(
        engine, "_check_model_files", return_value=True
    ):
        with pytest.raises(RuntimeError, match="gemini"):
            await engine._startup_health_check()


@pytest.mark.anyio
async def test_startup_health_check_lists_all_failed(engine):
    """Mehrere Failures → alle in RuntimeError message."""
    with patch.object(
        engine, "_check_redis", AsyncMock(return_value=False)
    ), patch.object(
        engine, "_check_gemini", AsyncMock(return_value=False)
    ), patch.object(
        engine, "_check_model_files", return_value=True
    ), patch(
        "core.engine.base.send_slack_alert", MagicMock()
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await engine._startup_health_check()
        msg = str(exc_info.value)
        assert "redis" in msg
        assert "gemini" in msg


# ---------------------------------------------------------------------------
# Tests: RL-Modell fehlt → nur WARNING (non-critical)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_startup_health_check_model_missing_is_warning_not_fail(engine, caplog):
    """RL-Modell fehlt → kein RuntimeError, aber WARNING geloggt."""
    import logging

    with patch.object(
        engine, "_check_redis", AsyncMock(return_value=True)
    ), patch.object(
        engine, "_check_gemini", AsyncMock(return_value=True)
    ), patch.object(
        engine, "_check_model_files", return_value=False
    ):
        with caplog.at_level(logging.WARNING):
            await engine._startup_health_check()  # must NOT raise
        assert any(
            "rl_model" in r.message.lower() or "model" in r.message.lower()
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Tests: individuelle Check-Methoden
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_redis_returns_true_on_ping(engine):
    """_check_redis() → True wenn Redis PING erfolgreich."""
    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    with patch(
        "core.redis_client.RedisClient.get_redis", AsyncMock(return_value=mock_redis)
    ):
        result = await engine._check_redis()
    assert result is True


@pytest.mark.anyio
async def test_check_redis_returns_false_on_exception(engine):
    """_check_redis() → False wenn Redis Exception wirft (kein Crash)."""
    with patch(
        "core.redis_client.RedisClient.get_redis",
        AsyncMock(side_effect=Exception("Connection refused")),
    ):
        result = await engine._check_redis()
    assert result is False


def test_check_model_files_returns_true_when_file_exists(engine, tmp_path):
    """_check_model_files() → True wenn RL-Modell-Datei vorhanden."""
    rl_zip = tmp_path / "rl_agent_v5.zip"
    rl_zip.touch()
    with patch("config.DATA_DIR", str(tmp_path), create=True), patch(
        "config.RL_MODEL_VERSION", "rl_agent_v5", create=True
    ):
        result = engine._check_model_files()
    assert result is True


def test_check_model_files_returns_false_when_file_missing(engine, tmp_path):
    """_check_model_files() → False wenn RL-Modell-Datei fehlt."""
    with patch("config.DATA_DIR", str(tmp_path), create=True), patch(
        "config.RL_MODEL_VERSION", "rl_agent_v5", create=True
    ):
        result = engine._check_model_files()
    assert result is False
