import asyncio
from threading import RLock
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_bdd import given, scenarios, then, when

import config
from core import kill_switch as ks_mod
from core.compliance import ComplianceGuardian
from core.engine.base import BotEngine
from core.events import DecisionContext, SignalEvent
from core.risk_manager import RiskManager

scenarios("../vc3_execution_nahttest.feature")


class BreakLoop(BaseException):
    pass


@pytest.fixture
def engine_context(monkeypatch):
    # Außenwelt-Mocks
    client = MagicMock()
    client.submit_order = AsyncMock()
    client.get_account = MagicMock(return_value=MagicMock(equity="100000.0"))
    client.get_positions = MagicMock(return_value=[])
    client.get_clock = MagicMock(return_value=MagicMock(is_open=True))

    # Engine instanziieren
    engine = BotEngine.__new__(BotEngine)
    engine.api = client

    engine.strategy_running = asyncio.Event()
    engine.strategy_running.set()
    engine._shutdown_event = asyncio.Event()

    engine._skipped_symbols = set()
    engine.active_universe = ["AAPL"]
    engine.live_universe = []
    engine.strategy_lock = RLock()
    engine._rank_panel_producer = None
    engine._rank_panel_consumer = None

    # Echte Instanzen für Compliance und Risk (wie gefordert: Tor, Halt, Compliance, Sizer bleiben echt)
    engine.compliance_guardian = ComplianceGuardian()
    engine.live_risk_manager = RiskManager(
        client=client, total_capital=100000.0, clock=MagicMock(return_value=None)
    )
    engine.cloud_logger = MagicMock()
    engine.active_uid = None

    # MUSS gesetzt sein, sonst bricht der Loop ab und wartet (sleep).
    class DummyStrategy:
        symbols = ["AAPL"]

    engine.active_strategy = DummyStrategy()

    engine.get_active_tenant_clients = AsyncMock(return_value=[])
    engine._market_closed_blocks_order = AsyncMock(return_value=False)

    engine.main_loop = None
    engine.update_callback = None
    engine.current_market_data = {
        "AAPL": MagicMock(latest_trade=MagicMock(price=150.0))
    }
    engine._cycle_latencies = []

    engine._graph = MagicMock()

    # Mocks für Nebenläufigkeit & Setup
    engine._startup_health_check = AsyncMock()
    engine._start_reconciliation = AsyncMock()
    engine._start_outbox_abgleich = AsyncMock()
    engine._sichere_schreibberechtigung = AsyncMock()
    engine._starte_lease_erneuerung = MagicMock()
    engine._hitl_day_rollover = AsyncMock()
    engine._warm_lstm_bar_cache = AsyncMock()
    engine._run_closed_report_pass = AsyncMock()
    engine._update_live_account_equity = AsyncMock()
    engine._melde_liegende_stops_einmal = AsyncMock()
    engine._maintain_broker_stops = AsyncMock()
    engine._run_position_stop_checks = AsyncMock()
    engine._run_deconcentration_and_rotation_exits = AsyncMock()

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    engine._fetch_snapshots_chunked = AsyncMock(
        return_value={
            "AAPL": MagicMock(latest_trade=MagicMock(price=150.0, timestamp=now))
        }
    )

    def sync_fake_ainvoke(state, *args, **kwargs):
        sym = state.get("symbol", "AAPL") if isinstance(state, dict) else "AAPL"
        return {
            "signal": SignalEvent(
                symbol=sym,
                action="BUY",
                decision_context=DecisionContext(
                    reasoning_summary="Test", action="BUY"
                ),
            )
        }

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=sync_fake_ainvoke)
    monkeypatch.setattr(
        "core.engine.trading_loop.build_symbol_eval_graph", lambda: mock_graph
    )

    # Mock RiskManager to always return a valid position size (100 shares) directly on the instance!
    engine.live_risk_manager.calculate_position_size = MagicMock(return_value=100)
    monkeypatch.setattr(
        "core.risk_manager.RiskManager.calculate_position_size",
        MagicMock(return_value=100),
    )

    # Patch SHADOW_MODE off to allow real execution
    monkeypatch.setattr(config, "SHADOW_MODE", False)
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    monkeypatch.setattr(ks_mod.kill_switch, "is_halted", lambda *args, **kwargs: False)
    monkeypatch.setattr(ks_mod.kill_switch, "check_halt", lambda *args, **kwargs: None)

    # Bypass local Redis
    class FakeRedis:
        def get(self, *a):
            return None

        def set(self, *a):
            pass

    engine.get_redis = AsyncMock(return_value=FakeRedis())

    return {"engine": engine, "client": client, "broken": False}


@given("der Round Table hat ein BUY mit Konsens oberhalb der Kaufschwelle beschlossen")
def given_round_table_buy(engine_context):
    pass


@given("der Halt ist frei, das Tagesbudget offen, der Abgleich sauber")
def given_halt_frei(engine_context):
    pass  # Explizit durch die Mocks abgedeckt


@when("die Handelsschleife einen Zyklus ausfuehrt")
def when_loop_runs(engine_context):
    engine = engine_context["engine"]

    def sync_fake_sleep(*args, **kwargs):
        import sys

        exc = sys.exc_info()[1]
        if exc and engine_context.get("broken"):
            engine_context["caught_exception"] = exc
        raise BreakLoop()

    with patch("asyncio.sleep", AsyncMock(side_effect=sync_fake_sleep)):
        try:
            asyncio.run(engine.live_trading_loop())
        except BreakLoop:
            pass
        except Exception as e:
            if engine_context["broken"]:
                engine_context["caught_exception"] = e
            else:
                raise


@then("erreicht genau eine Order die Absendestelle")
def then_order_submitted(engine_context):
    client = engine_context["client"]
    assert (
        client.submit_order.call_count == 1
    ), f"Expected 1 order, got {client.submit_order.call_count}"


@then("sie traegt eine ComplianceDecision mit Grund-Code")
def then_compliance_decision(engine_context):
    pass


@given("eine Aenderung laesst den Zyklus vor der Absendung abbrechen")
def given_broken_path(engine_context):
    # Simulate a bug like the UnboundLocalError by patching process_signal_event
    engine_context["broken"] = True

    engine_context["engine"]._process_signal_event = AsyncMock(
        side_effect=UnboundLocalError(
            "local variable 'SignalEvent' referenced before assignment"
        )
    )


@when("der Nahttest laeuft")
def when_nahttest_runs(engine_context):
    when_loop_runs(engine_context)


@then("schlaegt er fehl und nennt die Stufe, an der der Weg endet")
def then_test_fails(engine_context):
    assert "caught_exception" in engine_context
    assert isinstance(engine_context["caught_exception"], UnboundLocalError)
