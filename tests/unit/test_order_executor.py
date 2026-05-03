# tests/unit/test_order_executor.py
# Epic 1.7 / PR-C — TDD Red-Phase
# Tests für OrderExecutorMixin (wird nach core/engine/order_executor.py extrahiert)
#
# Gherkin-Kriterien:
#   Given: Engine mit gemockten Alpaca-Clients, Compliance-Guardian, Tenants
#   When:  _process_signal_event / _execute_tenant_order aufgerufen
#   Then:  Korrektes Routing, Compliance-Checks, PubSub-Events

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call

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
# 3. ComplianceGuardian blockt Tenant-Order
# ---------------------------------------------------------------------------


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
                return_value=MagicMock(publish=AsyncMock())
            )
            await executor._execute_tenant_order(tenant, event)

        tenant["client"].submit_order.assert_not_called()
        guardian.check_order.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Size = 0 → Redis Rejection-Event
# ---------------------------------------------------------------------------


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


class TestShadowModeExecution:
    @pytest.mark.anyio
    async def test_shadow_mode_intercepts_global_order(self):
        """
        Given: SHADOW_MODE=True in config
        When:  _process_signal_event ist aufgerufen und fallback auf globale api passiert
        Then:  broker API (submit_order) wird NICHT gerufen, Order wird als Dry-Run simuliert
        """
        from core.engine.order_executor import OrderExecutorMixin
        import config

        api = MagicMock()
        executor = _make_executor(api=api)
        event = _make_signal(action="BUY", qty=2.0)

        with patch.object(
            executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
        ), patch("core.engine.order_executor.config.SHADOW_MODE", True, create=True):
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
        from core.engine.order_executor import OrderExecutorMixin
        import config

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

        with patch("core.engine.order_executor.RedisClient") as mock_redis, patch(
            "core.engine.order_executor.config.SHADOW_MODE", True, create=True
        ):
            mock_redis.get_redis = AsyncMock(
                return_value=MagicMock(publish=AsyncMock())
            )
            await executor._execute_tenant_order(tenant, event)

        # Echte API darf nicht gerufen werden!
        tenant_api.submit_order.assert_not_called()
