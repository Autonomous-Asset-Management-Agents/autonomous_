"""
exceptions.py — Custom Exception Hierarchy for the AI Trading Bot.

Provides typed exceptions to replace broad `except Exception` blocks
at API boundaries. These exceptions survive Epic 1.7 (Architecture
Refactoring) unchanged.

Hierarchy:
    TradingBotError (base)
    ├── BrokerConnectionError   — Alpaca API connection/timeout failures
    ├── InsufficientFundsError  — Not enough cash to place an order
    ├── RiskLimitExceeded       — Risk manager has blocked the trade
    ├── StrategyExecutionError  — Error inside a trading strategy
    ├── DataProviderError       — Market data fetch failure
    ├── ConfigurationError      — Invalid or missing configuration value
    ├── SwapInProgressError     — Another strategy swap is already pending
    └── PositionLockError       — Swap rejected due to open positions (Epic 2.3 / I-3)
"""


class TradingBotError(Exception):
    """Base exception for all AI Trading Bot errors.

    Catch this to handle any bot-specific error in one place, while
    still allowing specific subclasses to be caught individually.
    """


class BrokerConnectionError(TradingBotError):
    """Raised when the connection to the broker (Alpaca) fails.

    Examples: HTTP timeout, invalid credentials, API rate limit exceeded,
    or the broker endpoint returning a non-2xx status.
    """


class InsufficientFundsError(TradingBotError):
    """Raised when an order cannot be placed due to insufficient cash.

    Do NOT catch this silently — it indicates the position size
    calculation or order routing has a bug.
    """


class RiskLimitExceeded(TradingBotError):
    """Raised when the RiskManager blocks a trade due to limit violations.

    Examples: daily drawdown limit hit, kill switch active,
    position size exceeds MAX_POSITION_PERCENT.
    """


class StrategyExecutionError(TradingBotError):
    """Raised when an exception occurs inside a trading strategy.

    Wraps internal strategy errors to allow the engine to distinguish
    strategy bugs from infrastructure failures.
    """


class DataProviderError(TradingBotError):
    """Raised when fetching market data fails.

    Examples: Alpaca data API timeout, Polygon.io error,
    empty or malformed bar data.
    """


class ConfigurationError(TradingBotError):
    """Raised when required configuration is missing or invalid.

    Examples: missing API keys, invalid config values, environment
    variables not set.
    """


class SwapInProgressError(TradingBotError):
    """Raised when a strategy swap is requested while another swap is pending.

    The AgentRegistry enforces a single pending swap at a time to prevent
    race conditions during the Graceful Handover process (Epic 2.3-Pre).
    Callers must wait for commit_swap() to complete before requesting
    another swap.
    """


class PositionLockError(TradingBotError):
    """Raised when a strategy swap is rejected due to open broker positions.

    The Hot-Swap API (POST /api/strategy/swap) returns HTTP 423 when
    open positions exist and force=False. Prevents mid-trade strategy
    switches that could cause compliance violations (Epic 2.3 / I-3).
    Pass force=True to bypass (shadow_mode recommended).
    """
