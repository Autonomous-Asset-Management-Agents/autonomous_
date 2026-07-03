from unittest.mock import MagicMock, patch

import allure
import pytest

from core.risk_manager import RiskManager
from core.telemetry import get_tracer


@allure.feature("VC-6 Reporting & Client Servicing")
@allure.story("Reporting & Auditing")
class TestOTelBCETraces:
    @patch("core.risk_manager.tracer")
    def test_risk_evaluate_trade_trace(self, mock_tracer):
        """Verifiziert, dass Risk Limit Evaluation getraced wird."""
        mock_span = MagicMock()
        mock_context = MagicMock()
        mock_context.__enter__.return_value = mock_span
        mock_tracer.start_as_current_span.return_value = mock_context

        rm = RiskManager(client=MagicMock(), total_capital=10000, user_id="test_user")

        market_data = {"indicators": {"features": {}}, "vix": 20.0}

        # kill_switch.is_halted() must return False — otherwise evaluate_new_trade
        # returns early (before the tracer span on line 591 is reached).
        with patch("core.kill_switch.kill_switch.is_halted", return_value=False):
            rm.evaluate_new_trade("AAPL", "BUY", market_data, 3.0)

        mock_tracer.start_as_current_span.assert_called_with("risk.evaluate_trade")
        mock_span.set_attribute.assert_any_call("symbol", "AAPL")
        mock_span.set_attribute.assert_any_call("trade.side", "BUY")

    @patch("core.engine.order_executor.tracer")
    def test_order_execution_live_trace(self, mock_tracer):
        """Verifiziert, dass Live Orders (Alpaca) trace-wrapped sind."""
        from core.engine.order_executor import OrderExecutorMixin
        from core.events import SignalEvent

        mock_span = MagicMock()
        mock_context = MagicMock()
        mock_context.__enter__.return_value = mock_span
        mock_tracer.start_as_current_span.return_value = mock_context

        executor = OrderExecutorMixin()
        executor.live_universe = ["TSLA"]
        executor.compliance_guardian = None

        # Mock dependencies in the mixin
        rm_mock = MagicMock()
        rm_mock.calculate_position_size.return_value = 1.0
        executor._get_tenant_risk_manager = MagicMock(return_value=rm_mock)

        pm_mock = MagicMock()
        pm_mock.score_opportunity.return_value = MagicMock()
        pm_mock.should_open_new_position.return_value = (True, "OK", None)
        executor._get_tenant_portfolio_manager = MagicMock(return_value=pm_mock)

        import config

        config.SHADOW_MODE = False

        tenant = {"user_id": "test_user", "client": MagicMock(), "equity": 10000}
        ctx = MagicMock()
        ctx.current_price = 100.0
        event = SignalEvent(
            symbol="TSLA",
            action="BUY",
            decision_context=ctx,
            suggested_quantity=1.0,
            is_simulation=False,
        )

        import asyncio

        asyncio.run(executor._execute_tenant_order(tenant, event))

        mock_tracer.start_as_current_span.assert_called_with("broker.submit_order.live")
        mock_span.set_attribute.assert_any_call("trade.action", "BUY")

    @patch("core.strategies.lstm_strategy.tracer")
    def test_lstm_model_inference_trace(self, mock_tracer):
        """Verifiziert, dass Model Inference im LSTM-Strategy trace-wrapped ist."""
        import numpy as np
        import pandas as pd

        from core.strategies.lstm_strategy import LSTMDynamicStrategy

        mock_span = MagicMock()
        mock_context = MagicMock()
        mock_context.__enter__.return_value = mock_span
        mock_tracer.start_as_current_span.return_value = mock_context

        with patch.object(LSTMDynamicStrategy, "__init__", lambda self: None):
            strategy = LSTMDynamicStrategy()
            strategy.torch_model = MagicMock()
            strategy.scaler_x = MagicMock()
            strategy.features_list = ["a"]
            strategy.device = "cpu"
            strategy.torch = MagicMock()
            strategy.np = np
            strategy.pd = pd
            strategy.joblib = MagicMock()
            strategy.client = MagicMock()

            mock_data = pd.DataFrame(np.random.rand(300, 1), columns=["close"])
            strategy.data_provider = MagicMock()
            strategy.data_provider.get_data.return_value = mock_data

            mock_pred = MagicMock()
            mock_pred.cpu.return_value.numpy.return_value = [[0.85]]
            strategy.torch_model.return_value = mock_pred

            strategy.scaler_x.transform.return_value = np.zeros((60, 1))

            # Fake data
            fake_market_data = {"vix": 25.0}
            from datetime import datetime

            try:
                import asyncio

                with patch(
                    "models.torch_model.create_live_features",
                    return_value=pd.DataFrame(np.random.rand(100, 1), columns=["a"]),
                ):
                    asyncio.run(
                        strategy._get_torch_prediction(
                            "MSFT", datetime.now(), fake_market_data
                        )
                    )
            except Exception as e:
                pass  # Accept pipeline break later

            mock_tracer.start_as_current_span.assert_called_with("model.inference")
            mock_span.set_attribute.assert_any_call("symbol", "MSFT")
            mock_span.set_attribute.assert_any_call("market.vix", 25.0)
