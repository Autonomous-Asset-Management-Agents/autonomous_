from datetime import datetime
from unittest.mock import MagicMock

import allure
import pytest

from core.data_provider_databento import DatabentoHistoricalClient
from core.engine.trading_loop import TradingLoopMixin
from core.market_regime import MarketRegimeModel


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_market_regime_none_date():
    """Ensure MarketRegimeModel handles current_date=None gracefully without strftime crash."""
    # Mock data provider
    mock_provider = MagicMock()
    model = MarketRegimeModel(data_provider=mock_provider)

    # Should not raise AttributeError: 'NoneType' object has no attribute 'strftime'
    result = model.get_market_regime(current_date=None)

    assert isinstance(result, dict)
    assert "regime" in result
    assert "confidence" in result


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_trading_loop_none_next_open():
    """Ensure TradingLoopMixin handles clock.next_open=None gracefully."""
    # Reset kill switch singleton so this test is not affected by halted state
    # left over from previous tests (same pattern as test_kill_switch.py fixtures).
    from core.kill_switch import KillSwitch

    KillSwitch._instance = None

    class DummyEngine(TradingLoopMixin):
        def __init__(self):
            self.api = MagicMock()
            self.strategy_running = MagicMock()
            # Stop immediately after one check
            self.strategy_running.is_set.side_effect = [True, False]
            self._shutdown_event = MagicMock()
            self._shutdown_event.is_set.return_value = False
            self._skipped_symbols = set()

        async def _startup_health_check(self):
            pass

        def _log_strategy_thought(self, msg):
            self.last_thought = msg

    engine = DummyEngine()

    # Setup mock clock with next_open = None
    mock_clock = MagicMock()
    mock_clock.is_open = False
    mock_clock.next_open = None
    engine.api.get_clock.return_value = mock_clock

    # Run the loop (it will sleep, but we mock asyncio.sleep to not actually sleep)
    import asyncio

    with pytest.MonkeyPatch.context() as m:

        async def dummy_sleep(*args, **kwargs):
            pass

        m.setattr(asyncio, "sleep", dummy_sleep)
        from core.engine.trading_loop import kill_switch

        m.setattr(kill_switch, "is_halted", MagicMock(return_value=False))
        asyncio.run(engine.live_trading_loop())
    # Should log fallback text and NOT crash
    assert hasattr(engine, "last_thought")
    assert "Unknown Time" in engine.last_thought or "None" in engine.last_thought


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_databento_none_dates():
    """Ensure DatabentoHistoricalClient handles start/end=None gracefully."""
    # Mock Databento init
    with pytest.MonkeyPatch.context() as m:
        m.setenv("DATABENTO_API_KEY", "dummy_key")
        client = DatabentoHistoricalClient()
        client._client = MagicMock()

        # Should return empty DataFrame instead of raising TypeError/AttributeError
        df = client.get_bars("AAPL", start=None, end=None)
        assert df.empty

        df_batch = client.get_batch_bars(["AAPL"], start=None, end=None)
        assert df_batch == {}
