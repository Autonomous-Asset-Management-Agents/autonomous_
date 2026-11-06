import logging
from typing import Any

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient

try:
    from config import get_config
except ImportError:
    from ai_trading_bot.config import get_config

try:
    from core.sim.safety import assert_not_sim
except ImportError:
    from ai_trading_bot.core.sim.safety import assert_not_sim

logger = logging.getLogger(__name__)


def create_trading_client(*args, **kwargs) -> Any:
    """
    Central factory for creating the Alpaca TradingClient.
    If SIM_MODE is enabled, returns the VirtualLiveBroker duck-typed client.
    Otherwise, performs the assert_not_sim() safety check and returns the real client.  # noqa: E501
    """
    config = get_config()
    if getattr(config, "SIM_MODE", False) is True:
        logger.info(
            "SIM_MODE enabled: Instantiating VirtualLiveBroker instead of real TradingClient."  # noqa: E501
        )
        # Lazy import to avoid circular dependencies
        try:
            from core.sim.broker import VirtualLiveBroker
        except ImportError:
            from ai_trading_bot.core.sim.broker import VirtualLiveBroker

        return VirtualLiveBroker()

    # Fail-closed safety check
    assert_not_sim()
    return TradingClient(*args, **kwargs)


def create_data_client(*args, **kwargs) -> Any:
    """
    Central factory for creating the Alpaca StockHistoricalDataClient.
    If SIM_MODE is enabled, returns the SimDataClient duck-typed client.
    Otherwise, performs the assert_not_sim() safety check and returns the real client.  # noqa: E501
    """
    config = get_config()
    if getattr(config, "SIM_MODE", False) is True:
        logger.info(
            "SIM_MODE enabled: Instantiating SimDataClient instead of real StockHistoricalDataClient."  # noqa: E501
        )
        # Lazy import to avoid circular dependencies
        try:
            from core.sim.data_client import SimDataClient
        except ImportError:
            from ai_trading_bot.core.sim.data_client import SimDataClient

        return SimDataClient()

    # Fail-closed safety check
    assert_not_sim()
    return StockHistoricalDataClient(*args, **kwargs)
