from datetime import datetime
from unittest.mock import patch

import pytest

try:
    from core.sim.broker import VirtualLiveBroker
    from core.sim.clock import SimClock
    from core.sim.data_client import SimDataClient
    from core.sim.fill_engine import SimFillEngine
except ImportError:
    from ai_trading_bot.core.sim.broker import VirtualLiveBroker
    from ai_trading_bot.core.sim.clock import SimClock
    from ai_trading_bot.core.sim.data_client import SimDataClient
    from ai_trading_bot.core.sim.fill_engine import SimFillEngine


CLOCK_MOD = SimClock.__module__


def test_sim_clock_advances_time():
    with patch(f"{CLOCK_MOD}.get_config") as mock_config:
        mock_config.return_value.SIM_DATE = ""
        mock_config.return_value.SIM_WINDOW = "09:30-10:30 ET"
        mock_config.return_value.SIM_CADENCE = "1m"

        clock = SimClock()
        initial_time = clock.current_time
        clock.advance()
        assert clock.current_time > initial_time
        assert clock.is_open is True


class DummyOrder:
    def __init__(self, symbol, qty, side):
        self.symbol = symbol
        self.qty = qty
        self.side = side
        self.status = "pending"


def test_sim_fill_engine_processes_orders():
    # Setup mock broker and dummy order
    from alpaca.trading.enums import OrderSide

    broker = VirtualLiveBroker()
    engine = broker.fill_engine

    # Just a simple sanity check - engine should not crash when processing an empty order list
    filled = engine.process_orders([])
    assert len(filled) == 0


def test_virtual_live_broker_initialization():
    broker = VirtualLiveBroker()
    assert broker is not None


def test_sim_data_client_initialization():
    client = SimDataClient()
    assert client is not None
