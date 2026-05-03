import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from core.engine import BotEngine
from core.events import SignalEvent
from core.cloud_logger import DecisionContext, CloudLogger


@pytest.fixture
def mock_engine(monkeypatch):
    import config as cfg

    monkeypatch.setattr(cfg, "GEMINI_API_KEY", "test-key-for-ci")
    mock_trading_client = MagicMock()
    mock_data_client = MagicMock()
    with patch(
        "core.engine.base.HistoricalDataProvider", return_value=MagicMock()
    ), patch("core.engine.base.NewsProcessor", return_value=MagicMock()), patch(
        "core.engine.base.MarketRegimeModel", return_value=MagicMock()
    ), patch(
        "core.engine.base.AIMarketScanner", return_value=MagicMock()
    ), patch(
        "core.engine.base.AILearningEngine", return_value=MagicMock()
    ), patch(
        "core.engine.base.AgentRegistry", return_value=MagicMock()
    ), patch(
        "core.engine.base.set_global_registry"
    ):
        engine = BotEngine(
            trading_client=mock_trading_client, data_client=mock_data_client
        )
    engine.api = mock_trading_client
    # Replace default cloud_logger with a mock
    engine.cloud_logger = MagicMock(spec=CloudLogger)
    engine.compliance_guardian = MagicMock()
    return engine


def test_process_signal_event_buy_success(mock_engine):
    """Test that a BUY SignalEvent is correctly executed and logged."""

    # Mock compliance guardian to approve everything
    mock_engine.compliance_guardian.check_order.return_value = True
    mock_engine.compliance_guardian.check_trade.return_value = True

    # Mock API order
    mock_order = MagicMock()
    mock_order.id = "mock-alpaca-order-id-123"
    mock_engine.api.submit_order.return_value = mock_order

    context = DecisionContext(
        symbol="AAPL",
        action="BUY",
        current_price=150.0,
        lstm_prediction=0.8,
        rl_raw_action=1,
        rl_stabilized_action=1,
        model_version_id="model-v1",
    )

    event = SignalEvent(
        symbol="AAPL",
        action="BUY",
        suggested_quantity=10,
        decision_context=context,
        is_simulation=False,
    )

    # Patch get_active_tenant_clients to return [] (no tenants) so the global
    # fallback path runs and compliance_guardian.check_order is called.
    # Without this, asyncpg (not installed locally) causes an exception before
    # check_order is ever reached.
    with patch.object(
        mock_engine, "get_active_tenant_clients", new=AsyncMock(return_value=[])
    ):
        asyncio.run(mock_engine._process_signal_event(event))

    # 1. Verify ComplianceGuardian checked the limits
    mock_engine.compliance_guardian.check_order.assert_called_once()
    mock_engine.compliance_guardian.check_trade.assert_called_once()

    # 2. Verify API order was placed
    mock_engine.api.submit_order.assert_called_once()

    # 3. Verify Alpaca order ID was saved in the context
    assert context.alpaca_order_id == "mock-alpaca-order-id-123"

    # 4. Verify CloudLogger was called to log the decision
    mock_engine.cloud_logger.log_decision.assert_called_once_with(context)


def test_process_signal_event_spam_hold(mock_engine):
    """Test that a trivial HOLD SignalEvent is NOT logged to save db space."""

    context = DecisionContext(
        symbol="AAPL",
        action="HOLD",
        current_price=150.0,
        lstm_prediction=0.1,  # Trivial confidence
        rl_raw_action=0,
        rl_stabilized_action=0,
        model_version_id="model-v1",
    )

    # Approvals are True by default in dataclass

    event = SignalEvent(
        symbol="AAPL",
        action="HOLD",
        suggested_quantity=0,
        decision_context=context,
        is_simulation=False,
    )

    asyncio.run(mock_engine._process_signal_event(event))

    # Ensure CloudLogger was NOT called
    mock_engine.cloud_logger.log_decision.assert_not_called()
    mock_engine.api.submit_order.assert_not_called()


def test_process_signal_event_significant_hold(mock_engine):
    """Test that a significant HOLD SignalEvent (Boundary Collision) is logged."""

    context = DecisionContext(
        symbol="AAPL",
        action="HOLD",
        current_price=150.0,
        lstm_prediction=0.9,  # Strong BUY signal from LSTM
        rl_raw_action=0,  # But RL chose HOLD
        rl_stabilized_action=0,
        model_version_id="model-v1",
    )

    event = SignalEvent(
        symbol="AAPL",
        action="HOLD",
        suggested_quantity=0,
        decision_context=context,
        is_simulation=False,
    )

    asyncio.run(mock_engine._process_signal_event(event))

    # Ensure CloudLogger WAS called to capture the trace of why it held
    mock_engine.cloud_logger.log_decision.assert_called_once_with(context)
