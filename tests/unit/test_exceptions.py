"""
test_exceptions.py — Unit tests for core/exceptions.py

Verifies the exception hierarchy, inheritance, and behavior.
"""

import allure
import pytest

from core.exceptions import (
    BrokerConnectionError,
    ConfigurationError,
    DataProviderError,
    InsufficientFundsError,
    RiskLimitExceeded,
    StrategyExecutionError,
    TradingBotError,
)

# ---------------------------------------------------------------------------
# Tests: Exception Hierarchy
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestExceptionHierarchy:

    def test_all_are_trading_bot_errors(self):
        """All custom exceptions must inherit from TradingBotError."""
        for exc_class in [
            BrokerConnectionError,
            InsufficientFundsError,
            RiskLimitExceeded,
            StrategyExecutionError,
            DataProviderError,
            ConfigurationError,
        ]:
            assert issubclass(
                exc_class, TradingBotError
            ), f"{exc_class.__name__} must be a subclass of TradingBotError"

    def test_all_are_exceptions(self):
        """TradingBotError must be an Exception."""
        assert issubclass(TradingBotError, Exception)

    def test_subclasses_are_not_equal(self):
        """Each exception class must be distinct."""
        classes = [
            BrokerConnectionError,
            InsufficientFundsError,
            RiskLimitExceeded,
            StrategyExecutionError,
            DataProviderError,
            ConfigurationError,
        ]
        assert len(set(classes)) == len(classes)


# ---------------------------------------------------------------------------
# Tests: Raise and Catch
# ---------------------------------------------------------------------------


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestRaiseAndCatch:

    def test_broker_connection_error_can_be_raised(self):
        with pytest.raises(BrokerConnectionError, match="Alpaca timeout"):
            raise BrokerConnectionError("Alpaca timeout")

    def test_caught_as_base_class(self):
        """Subclass must be catchable as TradingBotError."""
        with pytest.raises(TradingBotError):
            raise RiskLimitExceeded("Daily drawdown exceeded")

    def test_caught_as_exception(self):
        """All must be catchable as generic Exception."""
        with pytest.raises(Exception):
            raise DataProviderError("No bars returned")

    def test_exception_chaining(self):
        """Exception chaining via 'from' must preserve cause."""
        original = ConnectionError("Network unreachable")
        with pytest.raises(BrokerConnectionError) as exc_info:
            try:
                raise original
            except ConnectionError as e:
                raise BrokerConnectionError("Could not reach Alpaca") from e
        assert exc_info.value.__cause__ is original

    def test_insufficient_funds_error_message(self):
        msg = "Need $5000, only $1200 available"
        exc = InsufficientFundsError(msg)
        assert str(exc) == msg

    def test_strategy_execution_error_in_handler(self):
        """Simulate an engine catching StrategyExecutionError separately."""
        strategy_errors = []

        def _execute():
            raise StrategyExecutionError("NaN in model output")

        try:
            _execute()
        except StrategyExecutionError as e:
            strategy_errors.append(str(e))
        except TradingBotError:
            pass  # Should NOT be reached

        assert len(strategy_errors) == 1
        assert "NaN" in strategy_errors[0]

    def test_configuration_error_on_missing_key(self):
        def get_config(key, env={}):
            if key not in env:
                raise ConfigurationError(f"Missing config key: {key}")
            return env[key]

        with pytest.raises(ConfigurationError, match="ALPACA_API_KEY"):
            get_config("ALPACA_API_KEY", {})
