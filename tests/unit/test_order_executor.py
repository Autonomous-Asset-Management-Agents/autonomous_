# tests/unit/test_order_executor.py
# Epic 1.7 / PR-C — TDD Red-Phase
# Tests für OrderExecutorMixin (wird nach core/engine/order_executor.py extrahiert)
#
# Gherkin-Kriterien:
#   Given: Engine mit gemockten Alpaca-Clients, Compliance-Guardian, Tenants
#   When:  _process_signal_event / _execute_tenant_order aufgerufen
#   Then:  Korrektes Routing, Compliance-Checks, PubSub-Events

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import allure
import pytest

from core.events import SignalEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision_context(**kwargs):
    ctx = MagicMock()
    ctx.lstm_prediction = kwargs.get("lstm_prediction", 0.0)
    ctx.rl_stabilized_action = kwargs.get("rl_stabilized_action", 0)
    ctx.risk_approved = kwargs.get("risk_approved", True)
    ctx.portfolio_approved = kwargs.get("portfolio_approved", True)
    ctx.client_order_id = kwargs.get("client_order_id", "test-uuid")
    ctx.intelligence_approved = kwargs.get("intelligence_approved", True)
    ctx.current_price = kwargs.get("current_price", 150.0)
    ctx.conviction_score = kwargs.get("conviction_score", 0.8)
    ctx.alpaca_order_id = None
    return ctx


def _make_signal(action="BUY", symbol="AAPL", qty=2.0, is_simulation=False):
    ctx = _make_decision_context()
    event = MagicMock(spec=SignalEvent)
    event.action = action
    event.symbol = symbol
    event.suggested_quantity = qty
    event.decision_context = ctx
    event.is_simulation = is_simulation
    return event


def _make_executor(api=None, compliance=None):
    """Erstellt eine minimale Executor-Instanz."""
    from core.engine.order_executor import OrderExecutorMixin

    executor = OrderExecutorMixin.__new__(OrderExecutorMixin)
    executor.api = api or MagicMock()
    executor.compliance_guardian = compliance
    executor._log_strategy_thought = MagicMock()
    executor.cloud_logger = MagicMock()
    executor.cloud_logger.log_decision = MagicMock()
    executor.live_universe = []  # benötigt von _execute_tenant_order
    return executor


# ---------------------------------------------------------------------------
# 1. HOLD Signal → kein Submit
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestHoldSignalNoExecution:
    @pytest.mark.anyio
    async def test_hold_does_not_call_submit_order(self):
        """
        Given: Signal mit action=HOLD
        When:  _process_signal_event aufgerufen
        Then:  submit_order wird nie aufgerufen
        """
        from core.engine.order_executor import OrderExecutorMixin

        executor = _make_executor()
        event = _make_signal(action="HOLD")

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(event)

        executor.api.submit_order.assert_not_called()


# ---------------------------------------------------------------------------
# 2. BUY, keine Tenants → global account fallback
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestBuyFallbackGlobalAccount:
    @pytest.mark.anyio
    async def test_buy_with_no_tenants_uses_global_api(self):
        """
        Given: BUY Signal, get_active_tenant_clients gibt [] zurück
        When:  _process_signal_event aufgerufen
        Then:  engine.api.submit_order wird mit korrekten Parametern aufgerufen
        """
        from core.engine.order_executor import OrderExecutorMixin

        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="order-123")

        executor = _make_executor(api=api)
        event = _make_signal(action="BUY", qty=2.0, is_simulation=False)

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(event)

        api.submit_order.assert_called_once()
        req = api.submit_order.call_args[0][0]
        assert req.qty == 2.0


# ---------------------------------------------------------------------------
# 2b. Execution visibility (#2077 / Part B): a real fill marks the decision executed
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Visibility")
class TestExecutionVisibility:
    @pytest.mark.anyio
    async def test_buy_fallback_sets_action_executed_true(self):
        """
        Given: a BUY that submits successfully on the global/fallback path
        When:  _process_signal_event runs
        Then:  the decision context is flagged action_executed=True (was never set → console showed 0),
               and it is NOT a simulation.
        """
        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="order-777")

        executor = _make_executor(api=api)
        event = _make_signal(action="BUY", qty=1.0, is_simulation=False)

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(event)

        api.submit_order.assert_called_once()
        assert event.decision_context.action_executed is True
        assert event.decision_context.is_simulation is False

    @pytest.mark.anyio
    async def test_buy_fallback_bumps_submit_ok_counter(self):
        """
        Given: a BUY that submits successfully on the global/fallback path
        When:  _process_signal_event runs
        Then:  the exec-counter submit_ok is incremented (the fallback path was
               invisible in /engine-diagnostics — the tenant path already bumps it).
        """
        from core.engine.order_executor import get_exec_counters, reset_exec_counters

        reset_exec_counters()
        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="order-778")

        executor = _make_executor(api=api)
        event = _make_signal(action="BUY", qty=1.0, is_simulation=False)

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(event)

        assert get_exec_counters().get("submit_ok", 0) >= 1

    @pytest.mark.anyio
    async def test_shadow_fallback_flags_simulation_executed(self):
        """
        Given: SHADOW_MODE=True and the global/fallback path is taken
        When:  _process_signal_event runs
        Then:  the decision is marked executed BUT flagged is_simulation=True, so
               the surface can show it without claiming a real broker fill.
        """
        api = MagicMock()
        executor = _make_executor(api=api)
        event = _make_signal(action="BUY", qty=2.0, is_simulation=False)

        with (
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
            patch("core.engine.order_executor.config.SHADOW_MODE", True, create=True),
        ):
            await executor._process_signal_event(event)

        api.submit_order.assert_not_called()
        assert event.decision_context.action_executed is True
        assert event.decision_context.is_simulation is True


# ---------------------------------------------------------------------------
# 2c. Part C (#2176, Piece 2): displacement gate armed on the single-tenant fallback
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Anti-Churn — fallback displacement")
class TestFallbackDisplacementGate:
    @pytest.mark.anyio
    async def test_fallback_buy_blocked_when_pm_declines(self):
        """should_open_new_position == (False, ...) must block the fallback BUY.
        This is what activates Increment 2's cooldown on the desktop path — the
        fallback never called should_open_new_position before."""
        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="o-1")
        executor = _make_executor(api=api)
        pm = MagicMock()
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (
            False,
            "AAPL in order cooldown (< 900s since last trade)",
            None,
        )
        executor.active_strategy = MagicMock(portfolio_manager=pm)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(event)

        api.submit_order.assert_not_called()
        pm.should_open_new_position.assert_called_once()

    @pytest.mark.anyio
    async def test_fallback_buy_displaces_weakest_before_buying(self):
        """Book full + should_open returns a symbol_to_close ⇒ the weakest holding
        is SOLD before the new BUY is submitted (mirrors the tenant path)."""
        api = MagicMock()
        api.get_account.return_value = MagicMock(multiplier="2", buying_power=1000.0)
        api.submit_order.return_value = MagicMock(id="o-2")
        api.get_open_position.return_value = MagicMock(qty="5", current_price="100.0")
        executor = _make_executor(api=api)
        pm = MagicMock()
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (True, "swap weakest", "MSFT")
        executor.active_strategy = MagicMock(portfolio_manager=pm)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(event)

        assert api.submit_order.call_count == 2
        reqs = [c.args[0] for c in api.submit_order.call_args_list]
        assert reqs[0].symbol == "MSFT" and reqs[0].side.value == "sell"
        assert reqs[1].symbol == "AAPL" and reqs[1].side.value == "buy"
        pm.record_trade.assert_any_call("MSFT", "sell")

    @pytest.mark.anyio
    async def test_fallback_buy_without_portfolio_manager_still_submits(self):
        """Null-safe: no active_strategy / PortfolioManager ⇒ unchanged behaviour."""
        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="o-3")
        executor = _make_executor(api=api)  # no active_strategy -> pm is None
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(event)

        api.submit_order.assert_called_once()

    @pytest.mark.anyio
    async def test_fallback_buy_proceeds_when_scoring_errors(self):
        """Fail-OPEN: a scoring error must NOT newly block a BUY the legacy path
        would have executed."""
        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="o-4")
        executor = _make_executor(api=api)
        pm = MagicMock()
        pm.score_opportunity.side_effect = RuntimeError("scoring boom")
        executor.active_strategy = MagicMock(portfolio_manager=pm)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(event)

        api.submit_order.assert_called_once()  # BUY still went through

    @pytest.mark.anyio
    async def test_fallback_displacement_respects_shadow_mode(self):
        """In SHADOW_MODE the displacement SELL must be simulated (never a real
        broker submit) and must sit AFTER the kill-switch gate — a paper run may
        never fire a real exit."""
        api = MagicMock()
        api.get_account.return_value = MagicMock(multiplier="2", buying_power=1000.0)
        api.get_open_position.return_value = MagicMock(qty="5", current_price="100.0")
        executor = _make_executor(api=api)
        pm = MagicMock()
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (True, "swap", "MSFT")
        executor.active_strategy = MagicMock(portfolio_manager=pm)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)

        with (
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
            patch("core.engine.order_executor.config.SHADOW_MODE", True, create=True),
        ):
            await executor._process_signal_event(event)

        # Neither the displacement SELL nor the BUY may hit the real broker.
        api.submit_order.assert_not_called()
        pm.record_trade.assert_any_call("MSFT", "sell")  # PM still tracks it

    @pytest.mark.anyio
    async def test_fallback_portfolio_decline_is_logged_to_decisions(self):
        """ADR-OBS-01: a portfolio/cooldown-declined fallback BUY must still be
        written to the decisions table (cloud_logger.log_decision) like every other
        block path — the early return must not make it silently vanish."""
        api = MagicMock()
        executor = _make_executor(api=api)
        pm = MagicMock()
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (
            False,
            "AAPL in order cooldown (< 900s since last trade)",
            None,
        )
        executor.active_strategy = MagicMock(portfolio_manager=pm)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(event)

        api.submit_order.assert_not_called()
        executor.cloud_logger.log_decision.assert_called_once()


# ---------------------------------------------------------------------------
# 2d. Part C — Redis persist/restore parity on the single-tenant fallback
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Anti-Churn — fallback Redis parity")
class TestFallbackRedisParity:
    @pytest.mark.anyio
    async def test_fallback_restores_pm_state_once(self):
        """The desktop PortfolioManager's anti-churn state is restored from Redis at
        the start of the fallback path so the cooldown survives a restart."""
        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="o")
        executor = _make_executor(api=api)
        pm = MagicMock()
        pm.user_id = "oss-single"
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (True, "ok", None)
        executor.active_strategy = MagicMock(portfolio_manager=pm)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)

        with (
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
            patch("core.engine.order_executor.RedisClient") as rc,
            patch(
                "core.engine.order_executor.restore_pm_state_from_redis",
                new=AsyncMock(),
            ) as mock_restore,
            patch(
                "core.engine.order_executor.persist_pm_state_to_redis", new=AsyncMock()
            ),
        ):
            rc.get_redis = AsyncMock(return_value=MagicMock())
            await executor._process_signal_event(event)

        mock_restore.assert_awaited()
        assert mock_restore.await_args.args[0] is pm

    @pytest.mark.anyio
    async def test_fallback_persists_pm_state_after_buy(self):
        """After a successful fallback BUY the PM state is persisted to Redis."""
        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="o")
        executor = _make_executor(api=api)
        pm = MagicMock()
        pm.user_id = "oss-single"
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (True, "ok", None)
        executor.active_strategy = MagicMock(portfolio_manager=pm)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)

        with (
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
            patch("core.engine.order_executor.RedisClient") as rc,
            patch(
                "core.engine.order_executor.restore_pm_state_from_redis",
                new=AsyncMock(),
            ),
            patch(
                "core.engine.order_executor.persist_pm_state_to_redis", new=AsyncMock()
            ) as mock_persist,
        ):
            rc.get_redis = AsyncMock(return_value=MagicMock())
            await executor._process_signal_event(event)

        mock_persist.assert_awaited()
        assert mock_persist.await_args.args[0] is pm
        assert mock_persist.await_args.args[1] == "AAPL"


# ---------------------------------------------------------------------------
# 3. ComplianceGuardian blockt Tenant-Order
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestComplianceBlocksTenantOrder:
    @pytest.mark.anyio
    async def test_compliance_check_order_blocks_execution(self):
        """
        Given: ComplianceGuardian.check_order gibt False zurück
        When:  _execute_tenant_order aufgerufen
        Then:  kein submit_order, PubSub-Rejection-Event publiziert
        """
        from core.engine.order_executor import OrderExecutorMixin

        guardian = MagicMock()
        guardian.check_order.return_value = False

        executor = _make_executor(compliance=guardian)

        tenant = {
            "user_id": "user-1",
            "client": MagicMock(),
            "equity": 10000.0,
        }
        event = _make_signal(action="BUY")

        # Patch _get_tenant_risk_manager to return a mock rm
        rm = MagicMock()
        rm.calculate_position_size.return_value = 1.0
        executor._get_tenant_risk_manager = MagicMock(return_value=rm)
        # PortfolioManager muss 3-Tuple zurückgeben (should_open, reason, swap_symbol)
        pm = MagicMock()
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (True, "OK", None)
        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

        with patch("core.engine.order_executor.RedisClient") as mock_redis:
            mock_redis.get_redis = AsyncMock(
                return_value=MagicMock(
                    publish=AsyncMock(),
                    lock=MagicMock(
                        return_value=MagicMock(
                            acquire=AsyncMock(return_value=True),
                            release=AsyncMock(return_value=True),
                        )
                    ),
                )
            )
            await executor._execute_tenant_order(tenant, event)

        tenant["client"].submit_order.assert_not_called()
        guardian.check_order.assert_called_once()


# ---------------------------------------------------------------------------
# #2065: held_qty propagation for the ComplianceGuardian exit-exemption
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestExitExemptionHeldQtyPropagation:
    @pytest.mark.anyio
    async def test_sell_global_path_passes_held_qty(self):
        """Global fallback SELL: compliance_order carries held_qty == real position qty."""
        api = MagicMock()
        api.get_open_position.return_value = MagicMock(qty="200")
        guardian = MagicMock()
        guardian.check_order.return_value = False  # block → capture order, no submit
        executor = _make_executor(api=api, compliance=guardian)
        executor.live_risk_manager = None
        event = _make_signal(action="SELL", qty=0.0)  # suggested 0 → triggers pos fetch

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(event)

        guardian.check_order.assert_called_once()
        order = guardian.check_order.call_args[0][0]
        assert order["side"] == "sell"
        assert order["held_qty"] == 200.0

    @pytest.mark.anyio
    async def test_buy_global_path_held_qty_zero(self):
        """A BUY carries held_qty == 0.0 (the exemption is SELL-only)."""
        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="o1")
        guardian = MagicMock()
        guardian.check_order.return_value = False
        executor = _make_executor(api=api, compliance=guardian)
        executor.live_risk_manager = None
        event = _make_signal(action="BUY", qty=3.0)

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(event)

        guardian.check_order.assert_called_once()
        order = guardian.check_order.call_args[0][0]
        assert order["held_qty"] == 0.0

    # -- #2553: held_qty must be an INDEPENDENT witness of the real broker holding,
    #    not an echo of the (reducible / signal-supplied) order quantity. --------------
    @pytest.mark.anyio
    async def test_sell_global_qty_gt0_uses_broker_held_qty(self):
        """A SELL whose signal carries qty>0 skips the position fetch — held_qty must STILL be the
        real broker holding (independent witness), not an echo of the order qty (else a SELL
        exceeding the position would self-exempt)."""
        api = MagicMock()
        api.get_open_position.return_value = MagicMock(qty="200")  # real holding 200
        guardian = MagicMock()
        guardian.check_order.return_value = False  # block → capture, no submit
        executor = _make_executor(api=api, compliance=guardian)
        executor.live_risk_manager = None
        event = _make_signal(
            action="SELL", qty=50.0
        )  # suggested 50 (<200): skips pos fetch today

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(event)

        guardian.check_order.assert_called_once()
        order = guardian.check_order.call_args[0][0]
        assert order["side"] == "sell"
        assert order["quantity"] == 50.0
        assert order["held_qty"] == 200.0  # RED today: echoes the order qty (50.0)

    @pytest.mark.anyio
    async def test_sell_global_held_qty_fetch_fail_is_fail_closed(self):
        """If the broker holding cannot be fetched, the exit-exemption fails CLOSED (held_qty=0 → the
        SELL is subject to the normal order-value cap, never silently self-exempted)."""
        api = MagicMock()
        api.get_open_position.side_effect = RuntimeError("network")
        guardian = MagicMock()
        guardian.check_order.return_value = False
        executor = _make_executor(api=api, compliance=guardian)
        executor.live_risk_manager = None
        event = _make_signal(action="SELL", qty=50.0)

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ):
            await executor._process_signal_event(event)

        guardian.check_order.assert_called_once()
        order = guardian.check_order.call_args[0][0]
        assert order["held_qty"] == 0.0  # RED today: echoes the order qty (50.0)

    @pytest.mark.anyio
    async def test_sell_tenant_human_approved_partial_uses_full_broker_held_qty(self):
        """A human-approved PARTIAL exit down-sizes the order, but held_qty must remain the real
        broker holding, not the reduced order size (else quantity<=held_qty is a tautology).
        """
        api = MagicMock()
        api.get_open_position.return_value = MagicMock(qty="200")  # real holding 200
        guardian = MagicMock()
        guardian.check_order.return_value = False  # block → capture, no submit
        executor = _make_executor(api=api, compliance=guardian)
        tenant = {"user_id": "u1", "client": api, "equity": 100000.0}
        event = _make_signal(
            action="SELL", symbol="AAPL", qty=120.0
        )  # human-approved 120 of 200
        rm = MagicMock()
        executor._get_tenant_risk_manager = MagicMock(return_value=rm)
        pm = MagicMock()
        pm.should_open_new_position.return_value = (True, "OK", None)
        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

        with patch("core.engine.order_executor.RedisClient") as mock_redis:
            mock_redis.get_redis = AsyncMock(
                return_value=MagicMock(
                    publish=AsyncMock(),
                    lock=MagicMock(
                        return_value=MagicMock(
                            acquire=AsyncMock(return_value=True),
                            release=AsyncMock(return_value=True),
                        )
                    ),
                )
            )
            await executor._execute_tenant_order(tenant, event, source="human_approved")

        guardian.check_order.assert_called_once()
        order = guardian.check_order.call_args[0][0]
        assert order["quantity"] == 120.0  # human-approved reduced size
        assert order["held_qty"] == 200.0  # RED today: echoes the reduced size (120.0)


# ---------------------------------------------------------------------------
# 4. Size = 0 → Redis Rejection-Event
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestZeroSizePublishesRedis:
    @pytest.mark.anyio
    async def test_zero_size_publishes_rejection_event(self):
        """
        Given: RiskManager errechnet size=0
        When:  _execute_tenant_order aufgerufen
        Then:  kein submit_order, Redis PubSub trade_rejected publiziert
        """
        from core.engine.order_executor import OrderExecutorMixin

        executor = _make_executor()

        tenant = {
            "user_id": "user-1",
            "client": MagicMock(),
            "equity": 10000.0,
        }
        event = _make_signal(action="BUY")

        rm = MagicMock()
        rm.calculate_position_size.return_value = 0.0  # Size = 0
        executor._get_tenant_risk_manager = MagicMock(return_value=rm)
        pm = MagicMock()
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (True, "OK", None)
        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

        redis_mock = MagicMock()
        redis_mock.publish = AsyncMock()
        redis_mock.lock = MagicMock(
            return_value=MagicMock(
                acquire=AsyncMock(return_value=True),
                release=AsyncMock(return_value=True),
            )
        )

        with patch("core.engine.order_executor.RedisClient") as mock_redis:
            mock_redis.get_redis = AsyncMock(return_value=redis_mock)
            await executor._execute_tenant_order(tenant, event)

        tenant["client"].submit_order.assert_not_called()
        redis_mock.publish.assert_called_once()
        published_data = redis_mock.publish.call_args[0]
        assert "explainability:user-1" in published_data[0]


# ---------------------------------------------------------------------------
# 5. Multi-Tenant Fan-out → 3 Tenants, 3 Aufrufe
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestMultiTenantFanout:
    @pytest.mark.anyio
    async def test_three_tenants_get_three_order_attempts(self):
        """
        Given: 3 aktive Tenants
        When:  _process_signal_event mit BUY Signal
        Then:  _execute_tenant_order wird für jeden Tenant aufgerufen
        """
        from core.engine.order_executor import OrderExecutorMixin

        executor = _make_executor()
        event = _make_signal(action="BUY", is_simulation=False)

        tenants = [
            {"user_id": f"user-{i}", "client": MagicMock(), "equity": 10000.0}
            for i in range(3)
        ]

        call_count = []

        async def mock_execute_tenant(tenant, ev):
            call_count.append(tenant["user_id"])

        executor._execute_tenant_order = mock_execute_tenant

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=tenants)
        ):
            await executor._process_signal_event(event)

        assert len(call_count) == 3
        assert "user-0" in call_count
        assert "user-1" in call_count
        assert "user-2" in call_count


# ---------------------------------------------------------------------------
# 6. Epic INF-8: Shadow Mode / Dry-Run Order Proxy
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestShadowModeExecution:
    @pytest.mark.anyio
    async def test_shadow_mode_intercepts_global_order(self):
        """
        Given: SHADOW_MODE=True in config
        When:  _process_signal_event ist aufgerufen und fallback auf globale api passiert
        Then:  broker API (submit_order) wird NICHT gerufen, Order wird als Dry-Run simuliert
        """
        import config
        from core.engine.order_executor import OrderExecutorMixin

        api = MagicMock()
        executor = _make_executor(api=api)
        event = _make_signal(action="BUY", qty=2.0)

        with (
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
            patch("core.engine.order_executor.config.SHADOW_MODE", True, create=True),
        ):
            await executor._process_signal_event(event)

        # In Shadow Mode daerf die ECHTE api.submit_order nicht gerufen werden
        api.submit_order.assert_not_called()
        # event context sollte eine shadow_mode flag oder pseudo order id haben
        assert "shadow_" in getattr(event.decision_context, "alpaca_order_id", "")

    @pytest.mark.anyio
    async def test_shadow_mode_intercepts_tenant_order(self):
        """
        Given: SHADOW_MODE=True in config
        When:  _execute_tenant_order aufgerufen (Multi-Tenant)
        Then:  tenant["client"].submit_order wird NICHT gerufen, Order als Dry-Run simuliert
        """
        import config
        from core.engine.order_executor import OrderExecutorMixin

        executor = _make_executor()
        tenant_api = MagicMock()
        tenant = {
            "user_id": "user-shadow",
            "client": tenant_api,
            "equity": 10000.0,
        }
        event = _make_signal(action="BUY")

        rm = MagicMock()
        rm.calculate_position_size.return_value = 1.0
        executor._get_tenant_risk_manager = MagicMock(return_value=rm)
        pm = MagicMock()
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (True, "OK", None)
        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

        with (
            patch("core.engine.order_executor.RedisClient") as mock_redis,
            patch("core.engine.order_executor.config.SHADOW_MODE", True, create=True),
        ):
            mock_redis.get_redis = AsyncMock(
                return_value=MagicMock(
                    publish=AsyncMock(),
                    lock=MagicMock(
                        return_value=MagicMock(
                            acquire=AsyncMock(return_value=True),
                            release=AsyncMock(return_value=True),
                        )
                    ),
                )
            )
            await executor._execute_tenant_order(tenant, event)

        # Echte API darf nicht gerufen werden!
        tenant_api.submit_order.assert_not_called()

    @pytest.mark.anyio
    async def test_live_tenant_execution_sets_action_executed(self):
        """
        Given: SHADOW_MODE=False in config
        When:  _execute_tenant_order successfully submits order to broker
        Then:  tenant["client"].submit_order is called, action_executed=True,
               and is_simulation=False.
        """
        from alpaca.trading.enums import OrderStatus

        executor = _make_executor()
        tenant_api = MagicMock()
        tenant_api.submit_order.return_value = MagicMock(id="order-live-456")
        tenant_api.get_order_by_id.return_value = MagicMock(status=OrderStatus.FILLED)

        tenant = {
            "user_id": "user-live",
            "client": tenant_api,
            "equity": 10000.0,
        }
        event = _make_signal(action="BUY")

        rm = MagicMock()
        rm.calculate_position_size.return_value = 1.0
        executor._get_tenant_risk_manager = MagicMock(return_value=rm)
        pm = MagicMock()
        pm.score_opportunity.return_value = MagicMock()
        pm.should_open_new_position.return_value = (True, "OK", None)
        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

        with (
            patch("core.engine.order_executor.RedisClient") as mock_redis,
            patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
        ):
            mock_redis.get_redis = AsyncMock(
                return_value=MagicMock(
                    publish=AsyncMock(),
                    lock=MagicMock(
                        return_value=MagicMock(
                            acquire=AsyncMock(return_value=True),
                            release=AsyncMock(return_value=True),
                        )
                    ),
                )
            )
            await executor._execute_tenant_order(tenant, event)

        tenant_api.submit_order.assert_called_once()
        assert event.decision_context.alpaca_order_id == "order-live-456"
        assert event.decision_context.action_executed is True
        assert event.decision_context.is_simulation is False


# ---------------------------------------------------------------------------
# 7. Issue #937 — TestHardSyncPostSell (POLICY-01 / ADR-ENG-07)
#
# Gherkin:
#   Given: SELL order submitted successfully to broker
#   When:  post-SELL portfolio bookkeeping (record_trade / clear_sell_signals) fails
#   Then:  Hard-sync against Alpaca determines ground truth
# ---------------------------------------------------------------------------


def _make_sell_tenant_setup(executor, api_error_on_position=None):
    """Helper: returns (tenant, event, rm, pm) configured for a SELL scenario."""
    import json as _json
    from unittest.mock import MagicMock

    from alpaca.common.exceptions import APIError as AlpacaAPIError

    def _make_api_error(code, status=None):
        """Build an AlpacaAPIError with correct JSON string error and optional http_error."""
        error_json = _json.dumps({"message": "position does not exist", "code": code})
        http_error = None
        if status is not None:
            http_error = MagicMock()
            http_error.response.status_code = status
        return AlpacaAPIError(error=error_json, http_error=http_error)

    tenant_api = MagicMock()
    # SELL submitted successfully
    tenant_api.submit_order.return_value = MagicMock(id="order-sell-123")
    from alpaca.trading.enums import OrderStatus

    tenant_api.get_order_by_id.return_value = MagicMock(status=OrderStatus.FILLED)

    if api_error_on_position == "not_found":
        # Simulates Alpaca 404: position gone → SELL confirmed (via error_code 40410000)
        tenant_api.get_open_position.side_effect = _make_api_error(40410000)
    elif api_error_on_position == "still_open":
        # Position still exists at broker → inconsistent state
        tenant_api.get_open_position.return_value = MagicMock(
            qty="5", avg_entry_price="150"
        )
    elif api_error_on_position == "unexpected":
        # Internal / network error during hard-sync check
        tenant_api.get_open_position.side_effect = RuntimeError("network timeout")

    tenant = {"user_id": "user-sell", "client": tenant_api, "equity": 10000.0}

    event = _make_signal(action="SELL", symbol="AAPL", qty=5.0)

    # RiskManager: SELL path uses get_open_position, not calculate_position_size
    # so we don't need to mock it here — the SELL branch reads qty from pos.

    rm = MagicMock()
    executor._get_tenant_risk_manager = MagicMock(return_value=rm)

    pm = MagicMock()
    # First call to record_trade always raises (triggers hard-sync path)
    pm.record_trade.side_effect = Exception("db write failed")
    executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

    return tenant, event, rm, pm, tenant_api, _make_api_error


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestHardSyncPostSell:
    """
    Issue #937 — Unit tests for the post-SELL broker hard-sync mechanism.
    Verifies POLICY-01 (ADR-ENG-07): only AlpacaAPIError triggers force-clear;
    unexpected errors re-raise rather than being silently treated as confirmed SELLs.
    """

    @pytest.mark.anyio
    async def test_scenario1_sell_confirmed_force_clear_succeeds(self):
        """
        Scenario 1:
          Given: SELL submitted; record_trade() fails; get_open_position() raises AlpacaAPIError
          When:  hard-sync runs
          Then:  WARNING logged, force-clear attempted, no exception propagated
        """
        executor = _make_executor()
        tenant, event, rm, pm, tenant_api, _make_api_error = _make_sell_tenant_setup(
            executor,
            api_error_on_position=None,  # we override side_effect manually below
        )

        # Second call to record_trade (force-clear) succeeds
        pm.record_trade.side_effect = [
            Exception("db write failed"),  # first call fails → triggers hard-sync
            None,  # force-clear succeeds
        ]

        redis_mock = MagicMock()
        redis_mock.publish = AsyncMock()
        redis_mock.lock = MagicMock(
            return_value=MagicMock(
                acquire=AsyncMock(return_value=True),
                release=AsyncMock(return_value=True),
            )
        )

        with (
            patch("core.engine.order_executor.RedisClient") as mock_redis,
            patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
            patch("core.engine.order_executor.kill_switch") as ks,
        ):
            mock_redis.get_redis = AsyncMock(return_value=redis_mock)
            ks.check_halt = MagicMock()
            # Position qty lookup (before order submission) succeeds;
            # hard-sync: AlpacaAPIError with code 40410000 confirms SELL
            tenant_api.get_open_position.side_effect = [
                MagicMock(qty="5"),  # pre-SELL qty check → success
                _make_api_error(40410000),  # hard-sync → confirms SELL (code 40410000)
            ]
            await executor._execute_tenant_order(tenant, event)

        # SELL was submitted
        tenant_api.submit_order.assert_called_once()
        # Force-clear attempted (2nd record_trade call)
        assert pm.record_trade.call_count == 2

    @pytest.mark.anyio
    async def test_scenario2_sell_submitted_position_still_open(self):
        """
        Scenario 2:
          Given: SELL submitted; record_trade() fails; get_open_position() returns a position
          When:  hard-sync runs
          Then:  ERROR logged ('STILL OPEN'), no force-clear, PubSub state_inconsistency event
        """
        executor = _make_executor()
        tenant, event, rm, pm, tenant_api, _ = _make_sell_tenant_setup(
            executor, api_error_on_position="still_open"
        )

        redis_mock = MagicMock()
        redis_mock.publish = AsyncMock()
        redis_mock.lock = MagicMock(
            return_value=MagicMock(
                acquire=AsyncMock(return_value=True),
                release=AsyncMock(return_value=True),
            )
        )

        with (
            patch("core.engine.order_executor.RedisClient") as mock_redis,
            patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
            patch("core.engine.order_executor.kill_switch") as ks,
        ):
            mock_redis.get_redis = AsyncMock(return_value=redis_mock)
            ks.check_halt = MagicMock()
            # Pre-SELL: qty lookup succeeds; hard-sync: position still open
            tenant_api.get_open_position.side_effect = [
                MagicMock(qty="5"),  # pre-SELL qty
                MagicMock(qty="5"),  # hard-sync: still open
            ]
            await executor._execute_tenant_order(tenant, event)

        # Only 1 record_trade call (no force-clear)
        assert pm.record_trade.call_count == 1
        # PubSub state_inconsistency published — it's the FIRST publish call
        # (trade_executed is published afterwards at the bottom of execute_order)
        redis_mock.publish.assert_called()
        published_body = redis_mock.publish.call_args_list[0][0][1]
        import json

        assert json.loads(published_body)["type"] == "state_inconsistency"

    @pytest.mark.anyio
    async def test_scenario3_hard_sync_raises_unexpected_error(self):
        """
        Scenario 3:
          Given: SELL submitted; record_trade() fails; get_open_position() raises RuntimeError
          When:  hard-sync runs (POLICY-01: unexpected errors are not treated as confirmed SELLs)
          Then:  RuntimeError is logged + re-raised within the hard-sync block.
                 The fan-out loop's outer except catches it at tenant level — by design,
                 a single-symbol failure must not crash other tenants. The key assertion
                 is that force-clear (2nd record_trade call) was NOT triggered.
        """
        executor = _make_executor()
        tenant, event, rm, pm, tenant_api, _ = _make_sell_tenant_setup(
            executor, api_error_on_position="unexpected"
        )

        redis_mock = MagicMock()
        redis_mock.publish = AsyncMock()
        redis_mock.lock = MagicMock(
            return_value=MagicMock(
                acquire=AsyncMock(return_value=True),
                release=AsyncMock(return_value=True),
            )
        )

        with (
            patch("core.engine.order_executor.RedisClient") as mock_redis,
            patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
            patch("core.engine.order_executor.kill_switch") as ks,
        ):
            mock_redis.get_redis = AsyncMock(return_value=redis_mock)
            ks.check_halt = MagicMock()
            # Pre-SELL: qty OK; hard-sync: RuntimeError
            tenant_api.get_open_position.side_effect = [
                MagicMock(qty="5"),  # pre-SELL qty
                RuntimeError("network timeout"),  # hard-sync unexpected error
            ]
            # The RuntimeError propagates to asyncio.gather via the outer except
            # — _execute_tenant_order should NOT swallow it silently
            try:
                await executor._execute_tenant_order(tenant, event)
            except Exception as exc:
                # The outer except Exception in _execute_tenant_order catches + logs it —
                # it does NOT re-raise at the top level (by design: single-symbol failure
                # must not crash the fan-out loop). What matters is force-clear was NOT called.
                pass

        # Crucially: NO force-clear (record_trade called only once — first failure)
        assert pm.record_trade.call_count == 1

    @pytest.mark.anyio
    async def test_scenario4_rate_limit_429_does_not_clear_state(self):
        """
        Scenario 4 — Ghost-Position Regression Guard (INF-14 / ADR-ENG-07):
          Given: SELL submitted; record_trade() fails; get_open_position() raises
                 AlpacaAPIError with status_code=429 (Rate Limit)
          When:  hard-sync runs
          Then:  state MUST NOT be cleared (force-clear NOT triggered).
                 A 429 does NOT confirm the position was sold — it only means
                 Alpaca throttled us. Clearing state here would create a Ghost
                 Position on the next trading cycle.

        This test directly verifies the fix for the Ghost-Position exploit
        identified in the Archon Gatekeeper review.
        """
        import json as _json

        from alpaca.common.exceptions import APIError as AlpacaAPIError

        executor = _make_executor()

        tenant_api = MagicMock()
        tenant_api.submit_order.return_value = MagicMock(id="order-sell-429")
        from alpaca.trading.enums import OrderStatus

        tenant_api.get_order_by_id.return_value = MagicMock(status=OrderStatus.FILLED)

        tenant = {"user_id": "user-ratelimit", "client": tenant_api, "equity": 10000.0}
        event = _make_signal(action="SELL", symbol="NVDA", qty=3.0)

        rm = MagicMock()
        executor._get_tenant_risk_manager = MagicMock(return_value=rm)

        pm = MagicMock()
        # First record_trade fails → triggers hard-sync
        pm.record_trade.side_effect = [Exception("db write failed")]
        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

        # Simulate Alpaca 429 Rate-Limit error using correct APIError construction.
        # status_code is a read-only property derived from http_error.response.status_code.
        http_error_429 = MagicMock()
        http_error_429.response.status_code = 429
        rate_limit_err = AlpacaAPIError(
            error=_json.dumps({"message": "too many requests", "code": 42900000}),
            http_error=http_error_429,
        )

        redis_mock = MagicMock()
        redis_mock.publish = AsyncMock()
        redis_mock.lock = MagicMock(
            return_value=MagicMock(
                acquire=AsyncMock(return_value=True),
                release=AsyncMock(return_value=True),
            )
        )

        with (
            patch("core.engine.order_executor.RedisClient") as mock_redis,
            patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
            patch("core.engine.order_executor.kill_switch") as ks,
        ):
            mock_redis.get_redis = AsyncMock(return_value=redis_mock)
            ks.check_halt = MagicMock()
            tenant_api.get_open_position.side_effect = [
                MagicMock(qty="3"),  # pre-SELL qty lookup
                rate_limit_err,  # hard-sync: 429 Rate Limit
            ]
            # The 429 is re-raised out of hard-sync → caught by outer except
            # _execute_tenant_order logs it but does NOT re-raise at top level
            await executor._execute_tenant_order(tenant, event)

        # CRITICAL assertion: force-clear was NOT triggered.
        # record_trade was called exactly once (the failed initial call).
        # If it were called twice, a Ghost Position would have been created.
        assert pm.record_trade.call_count == 1, (
            "GHOST POSITION EXPLOIT: force-clear was triggered on a 429 Rate-Limit error. "
            "This would delete local state even though the position may still be open at broker."
        )
        pm.clear_sell_signals_after_sale.assert_not_called()


# ---------------------------------------------------------------------------
# 8. Epic EXC-1: Order Lifecycle Polling & Cancellation
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestOrderLifecyclePolling:
    @pytest.mark.anyio
    async def test_order_polling_timeout_and_cancel(self):
        """
        Given: Order wird gesendet
        When: Polling-Loop erreicht Timeout (OrderStatus.NEW bleibt bestehen)
        Then: cancel_order_by_id wird aufgerufen und pm.record_trade wird NICHT aufgerufen.
        """
        from alpaca.trading.enums import OrderStatus

        from core.engine.order_executor import OrderExecutorMixin

        executor = _make_executor()
        tenant_api = MagicMock()
        tenant_api.submit_order.return_value = MagicMock(id="order-polling-timeout")

        # get_order_by_id returns NEW always
        tenant_api.get_order_by_id.return_value = MagicMock(status=OrderStatus.NEW)

        tenant = {"user_id": "user-polling", "client": tenant_api, "equity": 10000.0}
        event = _make_signal(action="BUY", symbol="AAPL", qty=1.0)

        rm = MagicMock()
        rm.calculate_position_size.return_value = 1.0
        executor._get_tenant_risk_manager = MagicMock(return_value=rm)
        pm = MagicMock()
        pm.should_open_new_position.return_value = (True, "OK", None)
        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

        with (
            patch("core.engine.order_executor.RedisClient") as mock_redis,
            patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
            patch("core.engine.order_executor.kill_switch") as ks,
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_redis.get_redis = AsyncMock(
                return_value=MagicMock(
                    publish=AsyncMock(),
                    lock=MagicMock(
                        return_value=MagicMock(
                            acquire=AsyncMock(return_value=True),
                            release=AsyncMock(return_value=True),
                        )
                    ),
                )
            )
            ks.check_halt = MagicMock()

            # Execute
            await executor._execute_tenant_order(tenant, event)

        # Assertions
        tenant_api.submit_order.assert_called_once()
        # Sleep called max_wait_seconds / poll_interval times (120/2 = 60)
        # Background tasks (like latency watchdog) may also call sleep, so we assert >= 60
        assert mock_sleep.call_count >= 60
        # Cancel should be called
        tenant_api.cancel_order_by_id.assert_called_once_with("order-polling-timeout")
        # record_trade MUST NOT be called because it timed out
        pm.record_trade.assert_not_called()


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestOrderExecutorAntiChurn:
    @pytest.mark.anyio
    async def test_sell_blocked_by_anti_churn(self):
        """If PortfolioManager.can_sell_position returns False, order is blocked and consecutive sells recorded."""
        executor = _make_executor()
        tenant = {"user_id": "user-churn", "client": MagicMock(), "equity": 10000.0}
        event = _make_signal(action="SELL", symbol="AAPL", qty=10.0)

        # Mock PortfolioManager to reject the sale
        pm = MagicMock()
        pm.can_sell_position.return_value = (False, "Blocked by hold period")
        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

        with patch("core.engine.order_executor.RedisClient") as mock_redis:
            mock_redis.get_redis = AsyncMock(
                return_value=MagicMock(
                    publish=AsyncMock(),
                    lock=MagicMock(
                        return_value=MagicMock(
                            acquire=AsyncMock(return_value=True),
                            release=AsyncMock(return_value=True),
                        )
                    ),
                )
            )
            # Execute
            await executor._execute_tenant_order(tenant, event)

        # Assertions: order should NOT be submitted, and record_sell_signal should be called
        tenant["client"].submit_order.assert_not_called()
        pm.record_sell_signal.assert_called_once_with("AAPL")
        mock_redis.get_redis.return_value.publish.assert_called_once()

    @pytest.mark.anyio
    async def test_buy_swap_blocked_by_anti_churn_after_restart(self):
        """
        Regression: BUY signal after restart with full portfolio.

        Scenario (audit finding — "First-BUY-Blindspot"):
          Given: Bot restarts. First signal is BUY on NVDA.
          And:   Portfolio is full (max_positions). PM proposes to SWAP (close IWM).
          And:   Redis holds recent trade history for IWM (bought 5 min ago).
          When:  _execute_tenant_order processes BUY for NVDA.
          Then:  restore_pm_state_from_redis IS called before should_open_new_position.
          And:   The SWAP is blocked because IWM violates the hold period.
          And:   submit_order is NOT called.

        Verifies: restore_pm_state_from_redis is hoisted ABOVE the SELL/BUY branch,
                  so the BUY path (debate_position_swap) also operates on restored state.
        """
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, MagicMock, patch

        from core.engine.order_executor import restore_pm_state_from_redis

        executor = _make_executor()
        tenant = {"user_id": "user-buy-swap", "client": MagicMock(), "equity": 10000.0}
        event = _make_signal(action="BUY", symbol="NVDA", qty=5.0)

        # PM: portfolio is full → should_open_new_position proposes to swap out IWM
        pm = MagicMock()
        pm.user_id = "user-buy-swap"
        pm._trade_history = {}  # starts empty (simulating fresh restart)
        pm._consecutive_sell_signals = {}

        # After restore, IWM trade history will be populated; should_open_new_position
        # will call _can_trade_symbol which checks _trade_history.
        # We simulate this by making should_open_new_position blocked
        # (the swap is refused because the PM sees the restored trade history).
        pm.should_open_new_position.return_value = (
            False,
            "SWAP blocked: IWM in hold period",
            None,
        )
        pm.score_opportunity.return_value = MagicMock()

        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

        # Track whether restore was called in BUY path
        restore_calls = []
        original_restore = restore_pm_state_from_redis

        async def spy_restore(pm_arg, r_arg, pm_restored_arg):
            restore_calls.append({"pm": pm_arg, "r": r_arg})
            await original_restore(pm_arg, r_arg, pm_restored_arg)

        with (
            patch("core.engine.order_executor.RedisClient") as mock_redis,
            patch(
                "core.engine.order_executor.restore_pm_state_from_redis", spy_restore
            ),
            patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
            patch("core.engine.order_executor.kill_switch") as ks,
        ):
            ks.check_halt = MagicMock()
            tenant["client"].get_account.return_value = MagicMock(cash="50000")
            mock_redis.get_redis = AsyncMock(
                return_value=MagicMock(
                    publish=AsyncMock(),
                    get=AsyncMock(return_value=None),
                    set=AsyncMock(),
                    lock=MagicMock(
                        return_value=MagicMock(
                            acquire=AsyncMock(return_value=True),
                            release=AsyncMock(return_value=True),
                        )
                    ),
                )
            )

            await executor._execute_tenant_order(tenant, event)

        # CRITICAL: restore must be called even for BUY actions
        assert len(restore_calls) == 1, (
            "restore_pm_state_from_redis was NOT called in the BUY path — "
            "anti-churn state is blind after restart!"
        )
        # SWAP must be blocked — submit_order must not be called
        tenant["client"].submit_order.assert_not_called()


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestDesktopFallbackRecordTrade:
    @pytest.mark.anyio
    async def test_fallback_buy_records_trade_on_active_strategy_pm(self):
        """
        Given: BUY Signal, get_active_tenant_clients returns [] (Desktop fallback)
        And:   active_strategy has a portfolio_manager
        When:  _process_signal_event is processed
        Then:  pm.record_trade("AAPL", "buy") is called
        And:   pm.update_position_conviction("AAPL", 0.8) is called
        """
        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="order-fallback-buy")

        executor = _make_executor(api=api)
        pm = MagicMock()
        strategy = MagicMock()
        strategy.portfolio_manager = pm
        executor.active_strategy = strategy

        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)
        event.decision_context.conviction_score = 0.8

        with (
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
            patch("core.engine.order_executor.config.SHADOW_MODE", True, create=True),
            patch("core.engine.order_executor.kill_switch") as ks,
        ):
            ks.check_halt = MagicMock()
            await executor._process_signal_event(event)

        pm.record_trade.assert_called_once_with("AAPL", "buy")
        pm.update_position_conviction.assert_called_once_with("AAPL", 0.8)

    @pytest.mark.anyio
    async def test_fallback_sell_records_trade_on_active_strategy_pm(self):
        """
        Given: SELL Signal, get_active_tenant_clients returns [] (Desktop fallback)
        And:   active_strategy has a portfolio_manager
        When:  _process_signal_event is processed
        Then:  pm.record_trade("AAPL", "sell") is called
        And:   pm.clear_sell_signals_after_sale("AAPL") is called
        """
        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="order-fallback-sell")

        executor = _make_executor(api=api)
        pm = MagicMock()
        strategy = MagicMock()
        strategy.portfolio_manager = pm
        executor.active_strategy = strategy

        event = _make_signal(action="SELL", symbol="AAPL", qty=2.0)

        with (
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
            patch("core.engine.order_executor.config.SHADOW_MODE", True, create=True),
            patch("core.engine.order_executor.kill_switch") as ks,
        ):
            ks.check_halt = MagicMock()
            await executor._process_signal_event(event)

        pm.record_trade.assert_called_once_with("AAPL", "sell")
        pm.clear_sell_signals_after_sale.assert_called_once_with("AAPL")

    @pytest.mark.anyio
    async def test_fallback_failsafe_without_strategy_or_pm(self):
        """
        Given: BUY Signal, get_active_tenant_clients returns []
        And:   active_strategy or portfolio_manager is None (fail-safe test)
        When:  _process_signal_event is processed
        Then:  execution succeeds without raising any exceptions
        """
        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="order-fallback-failsafe")

        executor = _make_executor(api=api)
        executor.active_strategy = None  # None strategy

        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)

        with (
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
            patch("core.engine.order_executor.config.SHADOW_MODE", True, create=True),
            patch("core.engine.order_executor.kill_switch") as ks,
        ):
            ks.check_halt = MagicMock()
            await executor._process_signal_event(event)

        # Verified: no exceptions raised
        api.submit_order.assert_not_called()  # Shadow mode submit on DryRunOrderProxy


# ---------------------------------------------------------------------------
# INC-6 (continuous operation): the explicit market-open gate at the ORDER step.
# Research/analysis runs continuously even when the market is closed, but order
# PLACEMENT stays market-gated — never submit while closed unless
# BYPASS_MARKET_HOURS. See docs INC-6 Dual-Design (Option A).
# ---------------------------------------------------------------------------


@allure.feature("VC-3 Trading & Execution")
@allure.story("Market-Hours Order Gate")
class TestMarketClosedOrderGate:
    @pytest.mark.anyio
    async def test_closed_market_blocks_order_and_records_outcome(self):
        """
        Given: market CLOSED (clock.is_open=False) and BYPASS_MARKET_HOURS off
        When:  _process_signal_event handles a live BUY
        Then:  NO broker submission AND outcome 'blocked:market_closed' recorded
        """
        import config
        from core.round_table.execution_outcomes import (
            clear_execution_outcomes,
            get_execution_outcome,
        )

        clear_execution_outcomes()

        api = MagicMock()
        clock = MagicMock()
        clock.is_open = False
        api.get_clock.return_value = clock

        executor = _make_executor(api=api)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0, is_simulation=False)

        with (
            patch.object(config.get_config(), "BYPASS_MARKET_HOURS", False),
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
        ):
            await executor._process_signal_event(event)

        api.submit_order.assert_not_called()
        rec = get_execution_outcome("AAPL")
        assert rec is not None, "expected an execution-outcome record for AAPL"
        assert rec["outcome"] == "blocked:market_closed"

    @pytest.mark.anyio
    async def test_closed_market_blocks_sell_order(self):
        """A closed-market SELL must ALSO be blocked (conservative: no submit)."""
        import config
        from core.round_table.execution_outcomes import (
            clear_execution_outcomes,
            get_execution_outcome,
        )

        clear_execution_outcomes()

        api = MagicMock()
        clock = MagicMock()
        clock.is_open = False
        api.get_clock.return_value = clock

        executor = _make_executor(api=api)
        event = _make_signal(action="SELL", symbol="MSFT", qty=3.0, is_simulation=False)

        with (
            patch.object(config.get_config(), "BYPASS_MARKET_HOURS", False),
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
        ):
            await executor._process_signal_event(event)

        api.submit_order.assert_not_called()
        rec = get_execution_outcome("MSFT")
        assert rec is not None and rec["outcome"] == "blocked:market_closed"

    @pytest.mark.anyio
    async def test_bypass_market_hours_allows_order_when_closed(self):
        """
        Given: market CLOSED but BYPASS_MARKET_HOURS=True (test override)
        When:  _process_signal_event handles a live BUY
        Then:  the order IS submitted to the broker (gate bypassed)
        """
        import config

        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="order-bypass-1")
        clock = MagicMock()
        clock.is_open = False
        api.get_clock.return_value = clock

        executor = _make_executor(api=api)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0, is_simulation=False)

        with (
            patch.object(config.get_config(), "BYPASS_MARKET_HOURS", True),
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
        ):
            await executor._process_signal_event(event)

        api.submit_order.assert_called_once()

    @pytest.mark.anyio
    async def test_clock_error_fails_open_and_submits(self):
        """A clock-check error must FAIL-OPEN (mirror the trading loop) — a
        transient clock outage must never freeze an otherwise-open book."""
        import config

        api = MagicMock()
        api.submit_order.return_value = MagicMock(id="order-failopen-1")
        api.get_clock.side_effect = RuntimeError("clock service down")

        executor = _make_executor(api=api)
        event = _make_signal(action="BUY", symbol="AAPL", qty=2.0, is_simulation=False)

        with (
            patch.object(config.get_config(), "BYPASS_MARKET_HOURS", False),
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
        ):
            await executor._process_signal_event(event)

        api.submit_order.assert_called_once()
