# tests/unit/test_lstm_strategy.py
# Issue #938 — TDD Coverage: FeatureGenerationError Abstention Path
# ADR-SEC-03: Callers MUST return abstention (0.5, None) on FeatureGenerationError,
#             NOT (0.0, None) which biases ConsensusEngine toward SELL.
#
# Gherkin:
#   Given: create_live_features() raises FeatureGenerationError
#   When:  LSTMDynamicStrategy._get_torch_prediction() is called
#   Then:  Returns (0.5, None) — neutral abstention, no stack trace propagated

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy():
    """Creates a minimal LSTMDynamicStrategy instance bypassing heavy ML init.

    Sets torch_model and scaler_x to truthy mocks so the early-return guard
    (if not self.torch_model or not self.scaler_x: return 0.0, None) is bypassed.
    """
    from core.strategies.lstm_strategy import LSTMDynamicStrategy

    strategy = LSTMDynamicStrategy.__new__(LSTMDynamicStrategy)
    # Required attrs for _get_torch_prediction (lazy ML guards)
    strategy.torch_model = MagicMock()  # truthy → bypasses early-return guard
    strategy.scaler_x = MagicMock()  # truthy → bypasses early-return guard
    strategy.torch = MagicMock()
    strategy.np = MagicMock()
    strategy.pd = MagicMock()
    strategy.scaler_y = None
    strategy.features_list = ["close", "volume", "rsi_14"]
    strategy._initialized = True
    strategy.device = "cpu"
    # Client and data_provider needed for the data-fetch branch
    strategy.client = MagicMock()
    strategy.data_provider = MagicMock()
    strategy.client.__class__ = MagicMock  # not SimulationAdapter
    return strategy


def _make_current_date():
    return datetime(2024, 6, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# TestFeatureGenerationErrorAbstention (Issue #938)
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestFeatureGenerationErrorAbstention:
    """
    Issue #938 — Verify that FeatureGenerationError produces a neutral abstention
    score (0.5, None) rather than a SELL-biased 0.0.

    ADR-SEC-03: 0.0 injected into ConsensusEngine would silently shift the weighted
    average toward SELL even when the failure was a data pipeline error, not a genuine
    bearish signal.
    """

    def test_feature_generation_error_is_importable(self):
        """Smoke test: FeatureGenerationError must be importable from torch_model."""
        from models.torch_model import FeatureGenerationError

        assert issubclass(FeatureGenerationError, RuntimeError)

    @pytest.mark.anyio
    async def test_abstention_score_is_0_5_not_0_0(self):
        """
        Gherkin:
          Given: create_live_features() raises FeatureGenerationError
          When:  _get_torch_prediction() is called
          Then:  Returns (0.5, None) — NOT (0.0, None)
        """
        from models.torch_model import FeatureGenerationError

        strategy = _make_strategy()
        current_date = _make_current_date()
        market_data = {"vix": 20.0, "latest_news_sentiment": 0.0}

        # Build a mock hist DataFrame with enough rows
        import pandas as pd

        dates = pd.date_range("2024-01-01", periods=300)
        mock_hist = pd.DataFrame(
            {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1e6},
            index=dates,
        )

        with patch(
            "models.torch_model.create_live_features",
            side_effect=FeatureGenerationError("missing column: rsi_14"),
        ), patch("core.strategies.lstm_strategy.SEQUENCE_LENGTH", 10), patch(
            "asyncio.get_running_loop"
        ) as mock_loop:
            # data_provider.get_data is called via loop.run_in_executor
            mock_executor = MagicMock()
            mock_executor.return_value = mock_hist
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_hist)

            score, features = await strategy._get_torch_prediction(
                "AAPL", current_date, market_data
            )

        assert score == pytest.approx(0.5), (
            f"FeatureGenerationError MUST return abstention score 0.5, "
            f"not SELL-biased 0.0 or any other value. Got: {score}"
        )
        assert (
            features is None
        ), "FeatureGenerationError abstention must return None for features."

    @pytest.mark.anyio
    async def test_abstention_does_not_propagate_stack_trace(self):
        """
        Gherkin:
          Given: create_live_features() raises FeatureGenerationError
          When:  _get_torch_prediction() is called
          Then:  No exception propagates to the caller
        """
        from models.torch_model import FeatureGenerationError

        strategy = _make_strategy()
        current_date = _make_current_date()
        market_data = {"vix": 20.0}

        import pandas as pd

        dates = pd.date_range("2024-01-01", periods=300)
        mock_hist = pd.DataFrame(
            {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1e6},
            index=dates,
        )

        with patch(
            "models.torch_model.create_live_features",
            side_effect=FeatureGenerationError("test error"),
        ), patch("core.strategies.lstm_strategy.SEQUENCE_LENGTH", 10), patch(
            "asyncio.get_running_loop"
        ) as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_hist)
            # Must NOT raise — FeatureGenerationError is caught internally
            try:
                result = await strategy._get_torch_prediction(
                    "TSLA", current_date, market_data
                )
                assert result == (0.5, None)
            except Exception as exc:
                pytest.fail(
                    f"_get_torch_prediction must not propagate FeatureGenerationError "
                    f"to caller. Got: {type(exc).__name__}: {exc}"
                )

    @pytest.mark.anyio
    async def test_unexpected_exception_is_caught_returns_zero(self):
        """
        Gherkin:
          Given: create_live_features() raises an unexpected ValueError
          When:  _get_torch_prediction() is called
          Then:  The outer except in _get_torch_prediction catches it and returns (0.0, None).
                 (The outer try/except is a safety net — it logs + returns 0.0, does NOT re-raise.)

        Note: Only FeatureGenerationError triggers the 0.5 abstention path.
              All other exceptions fall into the outer except → (0.0, None) with logging.
        """
        strategy = _make_strategy()
        current_date = _make_current_date()
        market_data = {"vix": 20.0}

        import pandas as pd

        dates = pd.date_range("2024-01-01", periods=300)
        mock_hist = pd.DataFrame(
            {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1e6},
            index=dates,
        )

        with patch(
            "models.torch_model.create_live_features",
            side_effect=ValueError("unexpected internal error"),
        ), patch("core.strategies.lstm_strategy.SEQUENCE_LENGTH", 10), patch(
            "asyncio.get_running_loop"
        ) as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_hist)
            # Outer except catches ALL non-FeatureGenerationError exceptions → (0.0, None)
            score, features = await strategy._get_torch_prediction(
                "MSFT", current_date, market_data
            )

        assert score == pytest.approx(
            0.0
        ), "Non-FeatureGenerationError exceptions are caught by outer except → (0.0, None)"
        assert features is None

    @pytest.mark.anyio
    async def test_abstention_score_differs_from_sell_bias(self):
        """
        Regression guard: abstention score (0.5) must NOT equal the old SELL-biased
        value (0.0) that existed before ADR-SEC-03 was applied.
        """
        from models.torch_model import FeatureGenerationError

        strategy = _make_strategy()
        current_date = _make_current_date()
        market_data = {"vix": 20.0}

        import pandas as pd

        dates = pd.date_range("2024-01-01", periods=300)
        mock_hist = pd.DataFrame(
            {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1e6},
            index=dates,
        )

        with patch(
            "models.torch_model.create_live_features",
            side_effect=FeatureGenerationError("data error"),
        ), patch("core.strategies.lstm_strategy.SEQUENCE_LENGTH", 10), patch(
            "asyncio.get_running_loop"
        ) as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_hist)
            score, _ = await strategy._get_torch_prediction(
                "NVDA", current_date, market_data
            )

        assert score != 0.0, (
            "REGRESSION: Score 0.0 re-introduced. This biases ConsensusEngine toward SELL. "
            "FeatureGenerationError MUST return 0.5 (neutral abstention). ADR-SEC-03."
        )


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestMinRowsLiveConstraint:
    """Verify that in live mode, the min_rows check does not block execution on valid history length."""

    @pytest.mark.anyio
    async def test_min_rows_live_mode_is_sequence_length(self):
        """Verify that in live mode (client not SimulationAdapter), min_rows is set to SEQUENCE_LENGTH (60), not 260."""
        strategy = _make_strategy()
        current_date = _make_current_date()
        market_data = {"vix": 20.0, "latest_news_sentiment": 0.0}

        import pandas as pd

        # Provide 70 rows of data (sufficient for SEQUENCE_LENGTH=60, but less than 260)
        dates = pd.date_range("2024-01-01", periods=70)
        mock_hist = pd.DataFrame(
            {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1e6},
            index=dates,
        )

        with patch(
            "models.torch_model.create_live_features",
            return_value=None,  # We return None to cleanly stop execution
        ) as mock_create_features, patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_hist)

            score, features = await strategy._get_torch_prediction(
                "AAPL", current_date, market_data
            )

            # If min_rows is 260, the method returns early and mock_create_features is NEVER called.
            # If min_rows is SEQUENCE_LENGTH (60), mock_create_features IS called!
            mock_create_features.assert_called_once()
