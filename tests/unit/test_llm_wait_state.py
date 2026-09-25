"""B11: Ohne Ollama-Daemon wartet die Handelsschleife, statt stumm aufzugeben.

Bisher war der LLM-Check ein kritischer Startup-Check: nicht erreichbar, RuntimeError,
``live_trading_loop`` bricht ab, /health meldet "healthy" mit ``strategy_running=False``
und die Oberflaeche zeigt nur "Idle". Auf einem frischen Mac ohne Ollama ist das der
Normalfall, und ein spaeter installiertes Modell hilft ohne Neustart nicht.

Neu: Ein fehlgeschlagener LLM-Check ist ein Wartezustand. Alle 30 s wird erneut geprueft,
die Schleife startet, sobald der Provider antwortet. /health traegt ``llm_ready`` und
``startup_blocked_reason``, damit die Oberflaeche den Grund zeigen kann. Redis bleibt ein
harter Abbruch wie bisher.
"""

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod

pytestmark = [
    pytest.mark.unit,
    pytest.mark.vc0,
]  # vc0: Plattform-Querschnitt (Engine-Start)


@pytest.fixture
def engine():
    """Minimal BotEngine ohne echte API-Verbindungen (wie test_startup_health_check)."""
    with (
        patch("config.GEMINI_API_KEY", "test-key"),
        patch("core.engine.base.TradingClient", MagicMock()),
        patch("core.engine.base.StockHistoricalDataClient", MagicMock()),
        patch("core.engine.base.RedisClient", MagicMock()),
        patch("core.engine.base.AIMarketScanner", MagicMock()),
        patch("core.engine.base.AILearningEngine", MagicMock()),
        patch("core.engine.base.HistoricalDataProvider", MagicMock()),
        patch("core.engine.base.MarketRegimeModel", MagicMock()),
        patch("core.engine.base.NewsProcessor", MagicMock()),
        patch("core.engine.base.AILearnedRules", MagicMock()),
        patch("core.engine.base.AgentRegistry", MagicMock()),
        patch("core.engine.base.set_global_registry", MagicMock()),
        patch("core.engine.base.ComplianceGuardian", MagicMock()),
        patch("core.engine.base.get_cloud_logger", MagicMock()),
        patch("core.engine.base.threading.Thread", MagicMock()),
    ):
        from core.engine.base import BotEngine

        eng = BotEngine.__new__(BotEngine)
        eng._shutdown_event = threading.Event()
        eng.strategy_running = threading.Event()
        eng.strategy_running.set()
        return eng


def _checks(engine, llm_side_effect, redis_ok=True):
    """Patch-Buendel: Redis, LLM (Sequenz), RL-Modell, Slack, und der Schlaf im Wartezustand."""
    sleep = AsyncMock()
    return (
        patch.object(engine, "_check_redis", AsyncMock(return_value=redis_ok)),
        patch.object(engine, "_check_llm", AsyncMock(side_effect=llm_side_effect)),
        patch.object(engine, "_check_model_files", return_value=True),
        patch("core.engine.base.send_slack_alert", MagicMock()),
        patch("core.engine.base.asyncio.sleep", sleep),
        sleep,
    )


# ---------------------------------------------------------------------------
# Wartezustand
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_llm_down_waits_and_starts_once_reachable(engine):
    """LLM erst nicht erreichbar, dann erreichbar: kein Abbruch, Zustand sichtbar, dann bereit."""
    seen_while_waiting = []

    async def llm_probe():
        seen_while_waiting.append((engine._llm_ready, engine._startup_blocked_reason))
        return len(seen_while_waiting) >= 3

    p_redis, _, p_model, p_slack, p_sleep, _ = _checks(engine, None)
    with (
        p_redis,
        p_model,
        p_slack,
        p_sleep,
        patch.object(
            engine, "_check_llm", AsyncMock(side_effect=llm_probe)
        ) as check_llm,
    ):
        await engine._startup_health_check()  # darf NICHT werfen

    assert check_llm.await_count == 3
    # Waehrend des Wartens war der Grund gesetzt und llm_ready False.
    assert seen_while_waiting[1] == (False, "llm_unreachable")
    assert seen_while_waiting[2] == (False, "llm_unreachable")
    assert engine._llm_ready is True
    assert engine._startup_blocked_reason is None


@pytest.mark.anyio
async def test_wait_state_reprobes_every_30_seconds(engine):
    """Zwischen zwei Proben liegen 30 s Wartezeit (in kleinen, abbrechbaren Schritten)."""
    p_redis, p_llm, p_model, p_slack, p_sleep, sleep = _checks(
        engine, [False, False, True]
    )
    with p_redis, p_llm, p_model, p_slack, p_sleep:
        await engine._startup_health_check()

    slept = sum(call.args[0] for call in sleep.await_args_list)
    assert slept == pytest.approx(60.0)  # zwei Wartefenster a 30 s
    assert max(call.args[0] for call in sleep.await_args_list) <= 1.0


@pytest.mark.anyio
async def test_wait_state_aborts_when_strategy_is_stopped(engine):
    """Stop waehrend des Wartens: RuntimeError wie bisher, kein Endlos-Warten."""

    async def stop_after_first_sleep(_seconds):
        engine.strategy_running.clear()

    p_redis, p_llm, p_model, p_slack, _, _ = _checks(engine, [False, False, False])
    with (
        p_redis,
        p_llm,
        p_model,
        p_slack,
        patch(
            "core.engine.base.asyncio.sleep",
            AsyncMock(side_effect=stop_after_first_sleep),
        ) as sleep,
    ):
        with pytest.raises(RuntimeError, match="llm"):
            await engine._startup_health_check()

    assert sleep.await_count == 1
    assert engine._llm_ready is False
    assert engine._startup_blocked_reason == "llm_unreachable"


@pytest.mark.anyio
async def test_wait_state_aborts_on_shutdown_event(engine):
    """Auch das Shutdown-Event beendet das Warten."""

    async def shutdown_after_first_sleep(_seconds):
        engine._shutdown_event.set()

    p_redis, p_llm, p_model, p_slack, _, _ = _checks(engine, [False, False])
    with (
        p_redis,
        p_llm,
        p_model,
        p_slack,
        patch(
            "core.engine.base.asyncio.sleep",
            AsyncMock(side_effect=shutdown_after_first_sleep),
        ),
    ):
        with pytest.raises(RuntimeError, match="llm"):
            await engine._startup_health_check()


@pytest.mark.anyio
async def test_redis_down_still_aborts_immediately(engine):
    """Redis bleibt kritisch: sofortiger Abbruch, kein Warten, beide Namen in der Meldung."""
    p_redis, p_llm, p_model, p_slack, p_sleep, sleep = _checks(
        engine, [False], redis_ok=False
    )
    with p_redis, p_llm as check_llm, p_model, p_slack, p_sleep:
        with pytest.raises(RuntimeError) as exc_info:
            await engine._startup_health_check()

    assert "redis" in str(exc_info.value)
    assert "llm" in str(exc_info.value)
    assert check_llm.await_count == 1
    assert sleep.await_count == 0
    assert engine._llm_ready is False
    assert engine._startup_blocked_reason == "redis_unreachable"


@pytest.mark.anyio
async def test_all_ok_marks_llm_ready_without_waiting(engine):
    p_redis, p_llm, p_model, p_slack, p_sleep, sleep = _checks(engine, [True])
    with p_redis, p_llm, p_model, p_slack, p_sleep:
        await engine._startup_health_check()

    assert sleep.await_count == 0
    assert engine._llm_ready is True
    assert engine._startup_blocked_reason is None


# ---------------------------------------------------------------------------
# Stall-Monitor: im Wartezustand sind keine Zyklen zu erwarten
# ---------------------------------------------------------------------------


def test_stall_monitor_expects_no_cycles_while_waiting_for_llm(engine):
    engine._startup_blocked_reason = "llm_unreachable"
    assert engine._loop_expected_to_cycle() is False

    engine._startup_blocked_reason = None
    assert engine._loop_expected_to_cycle() is True

    engine.strategy_running.clear()
    assert engine._loop_expected_to_cycle() is False


# ---------------------------------------------------------------------------
# /health traegt die Felder
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(api_routes_mod.app)


def _health(client, llm_ready, reason):
    with (
        patch(
            "core.engine.api_routes.RedisClient.check_health",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("core.engine.api_routes.engine") as eng,
    ):
        # Im Wartezustand ist strategy_running bereits gesetzt (start_live_strategy setzt es
        # vor dem Thread-Start); die Oberflaeche muss den Grund ueber die Flagge stellen.
        eng.strategy_running.is_set.return_value = True
        eng._llm_ready = llm_ready
        eng._startup_blocked_reason = reason
        return client.get("/health").json()


def test_health_shows_llm_wait_state(client):
    data = _health(client, False, "llm_unreachable")
    assert data["status"] == "healthy", "Beobachtung, kein Urteil"
    assert data["strategy_running"] is True
    assert data["llm_ready"] is False
    assert data["startup_blocked_reason"] == "llm_unreachable"


def test_health_shows_llm_ready(client):
    data = _health(client, True, None)
    assert data["llm_ready"] is True
    assert data["startup_blocked_reason"] is None


def test_health_coerces_unknown_engine_fields(client):
    """Ein Engine-Objekt ohne die Felder (aelterer Stand, Mock) bricht /health nicht."""
    with (
        patch(
            "core.engine.api_routes.RedisClient.check_health",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("core.engine.api_routes.engine") as eng,
    ):
        eng.strategy_running.is_set.return_value = True
        data = client.get("/health").json()
    assert data["llm_ready"] is False
    assert data["startup_blocked_reason"] is None


def test_health_starting_body_carries_llm_fields(client):
    with (
        patch("core.engine.api_routes.engine", None),
        patch("core.engine.api_routes.engine_init_error", None),
    ):
        data = client.get("/health").json()
    assert data["status"] == "starting"
    assert data["llm_ready"] is False
    assert data["startup_blocked_reason"] is None


def test_stop_strategy_resets_wait_state(engine):
    """Ein manueller Stop waehrend des Wartens darf 'llm_unreachable' nicht sticky lassen."""
    engine._llm_ready = False
    engine._startup_blocked_reason = "llm_unreachable"
    engine.monitor_running = threading.Event()
    engine.is_simulation = False
    engine.strategy_thread = None
    engine.monitor_thread = None
    engine.strategy_lock = threading.Lock()
    engine.active_strategy = object()
    engine.specialist_registry = None
    engine._send_update_threadsafe = MagicMock()
    with patch("core.latency_watchdog.latency_watchdog", MagicMock()):
        engine.stop_strategy()
    assert engine.strategy_running.is_set() is False
    assert engine._llm_ready is False
    assert engine._startup_blocked_reason is None
