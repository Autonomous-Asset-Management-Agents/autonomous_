try:
    from config import get_config
except ImportError:
    from ai_trading_bot.config import get_config


def assert_not_sim():
    """
    Ensures that real trading clients (like AlpacaTradingClient) are not instantiated
    when the system is running in SIM_MODE.
    Fail-closed security design.
    """
    config = get_config()
    if getattr(config, "SIM_MODE", False):
        raise RuntimeError(
            "CRITICAL: Attempted to construct real Alpaca Client while in SIM_MODE."
        )
