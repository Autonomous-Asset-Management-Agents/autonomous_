from unittest.mock import MagicMock, patch

import pytest

try:
    from core.client_factory import create_data_client, create_trading_client
    from core.sim.broker import VirtualLiveBroker
    from core.sim.data_client import SimDataClient
    from core.sim.safety import assert_not_sim
except ImportError:
    from ai_trading_bot.core.client_factory import (
        create_data_client,
        create_trading_client,
    )
    from ai_trading_bot.core.sim.broker import VirtualLiveBroker
    from ai_trading_bot.core.sim.data_client import SimDataClient
    from ai_trading_bot.core.sim.safety import assert_not_sim

CLIENT_FACTORY_MOD = create_trading_client.__module__
BROKER_MOD = VirtualLiveBroker.__module__
SIM_DATA_CLIENT_MOD = SimDataClient.__module__


def test_create_trading_client_returns_virtual_in_sim_mode():
    with patch(f"{CLIENT_FACTORY_MOD}.get_config") as mock_config:
        mock_config.return_value.SIM_MODE = True

        # We also mock the VirtualLiveBroker
        with patch(f"{BROKER_MOD}.VirtualLiveBroker") as MockVirtualLiveBroker:
            client = create_trading_client()
            assert client == MockVirtualLiveBroker.return_value


def test_create_trading_client_returns_real_when_not_sim_mode():
    with patch(f"{CLIENT_FACTORY_MOD}.get_config") as mock_config:
        mock_config.return_value.SIM_MODE = False

        with patch(f"{CLIENT_FACTORY_MOD}.TradingClient") as MockTradingClient:
            client = create_trading_client("api_key", "secret_key", paper=True)
            assert client == MockTradingClient.return_value
            MockTradingClient.assert_called_once_with(
                "api_key", "secret_key", paper=True
            )


def test_create_data_client_returns_sim_in_sim_mode():
    with patch(f"{CLIENT_FACTORY_MOD}.get_config") as mock_config:
        mock_config.return_value.SIM_MODE = True

        with patch(f"{SIM_DATA_CLIENT_MOD}.SimDataClient") as MockSimDataClient:
            client = create_data_client()
            assert client == MockSimDataClient.return_value


def test_create_data_client_returns_real_when_not_sim_mode():
    with patch(f"{CLIENT_FACTORY_MOD}.get_config") as mock_config:
        mock_config.return_value.SIM_MODE = False

        with patch(
            f"{CLIENT_FACTORY_MOD}.StockHistoricalDataClient"
        ) as MockStockDataClient:
            client = create_data_client("api_key", "secret_key")
            assert client == MockStockDataClient.return_value
            MockStockDataClient.assert_called_once_with("api_key", "secret_key")
