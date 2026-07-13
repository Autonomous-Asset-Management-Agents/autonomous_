# tests/unit/test_lstm_strategy.py
# Issue #938 — TDD Coverage: FeatureGenerationError Abstention Path
# ADR-SEC-03 (revised by #1878): Callers MUST return abstention (None, None) on
#             FeatureGenerationError — NOT (0.0, None) which biases the
#             ConsensusEngine toward SELL, and NOT (0.5, None) which is a
#             strong-buy in prediction space and leaked failed symbols into the
#             Top-N ranking (#1878 Fix 3).
#
# Gherkin:
#   Given: create_live_features() raises FeatureGenerationError
#   When:  LSTMDynamicStrategy._get_torch_prediction() is called
#   Then:  Returns (None, None) — abstention, no stack trace propagated

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
    Issue #938 (revised by #1878) — Verify that FeatureGenerationError produces a
    true abstention (None, None) rather than any numeric pseudo-prediction.

    ADR-SEC-03: 0.0 injected into ConsensusEngine would silently shift the weighted
    average toward SELL even when the failure was a data pipeline error, not a genuine
    bearish signal.
    #1878 Fix 3: 0.5 (the old abstention encoding) is a strong-buy value in the
    prediction space (buy threshold 0.2) and could rank a failed symbol into the
    Top-N — abstention must therefore be None, and update_lstm_rankings drops it.
    """

    def test_feature_generation_error_is_importable(self):
        """Smoke test: FeatureGenerationError must be importable from torch_model."""
        from models.torch_model import FeatureGenerationError

        assert issubclass(FeatureGenerationError, RuntimeError)


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestEvaluatePredExposure:
    """#1969 (RT-BUG-3): the evaluate-phase must expose the RAW continuous pred in
    `decision_context.lstm_prediction`, not the ordinal rank — otherwise the
    Round-Table LSTMSignalAgent/RLConfidenceAgent get a saturated/monotone-wrong
    input and the +0.067-IC signal is destroyed.
    """

    def test_get_pred_for_symbol_returns_raw_pred_not_rank(self):
        from core.strategies.lstm_strategy import LSTMDynamicStrategy

        strategy = LSTMDynamicStrategy.__new__(LSTMDynamicStrategy)
        # rank cache: (symbol, raw_pred) sorted by pred desc; ranks would be 1,2,3
        strategy._lstm_rank_cache = [("AAA", 4.2), ("BBB", -1.7), ("CCC", 0.0)]

        # Raw pred is returned (incl. negative & zero) — NOT the 1-based rank.
        assert strategy._get_pred_for_symbol("AAA") == 4.2
        assert strategy._get_pred_for_symbol("BBB") == -1.7
        assert strategy._get_pred_for_symbol("CCC") == 0.0
        # Unknown symbol → 0.0 (neutral), no crash.
        assert strategy._get_pred_for_symbol("ZZZ") == 0.0

    @pytest.mark.anyio
    async def test_abstention_returns_none_not_a_prediction(self):
        """
        Gherkin:
          Given: create_live_features() raises FeatureGenerationError
          When:  _get_torch_prediction() is called
          Then:  Returns (None, None) — NOT (0.0, None) and NOT (0.5, None)
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

        assert score is None, (
            f"FeatureGenerationError MUST return abstention None — not SELL-biased "
            f"0.0 and not 0.5 (strong-buy in prediction space, #1878). Got: {score!r}"
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
                assert result == (None, None)
            except Exception as exc:
                pytest.fail(
                    f"_get_torch_prediction must not propagate FeatureGenerationError "
                    f"to caller. Got: {type(exc).__name__}: {exc}"
                )

    @pytest.mark.anyio
    async def test_unexpected_exception_is_caught_returns_abstention(self):
        """
        Gherkin:
          Given: create_live_features() raises an unexpected ValueError
          When:  _get_torch_prediction() is called
          Then:  The outer except catches it and returns (None, None) — abstention.
                 (The outer try/except is a safety net — it logs at WARNING and
                 does NOT re-raise.)

        #1878 review Finding 2: the old (0.0, None) injected a SELL bias — a held
        position whose serve path errored would rank at the tail of the LSTM
        ranking and be force-sold by the rank-based exit. Every data-error path
        must be a true abstention (None, None).
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
            # Outer except catches ALL non-FeatureGenerationError exceptions
            score, features = await strategy._get_torch_prediction(
                "MSFT", current_date, market_data
            )

        assert score is None, (
            f"Unexpected serve errors MUST abstain (None) — got {score!r}. "
            f"0.0 ranks a held position last → forced rank-SELL (#1878 review)."
        )
        assert features is None

    @pytest.mark.anyio
    async def test_abstention_score_differs_from_sell_bias(self):
        """
        Regression guard: abstention (None) must NOT equal the old SELL-biased
        value (0.0, pre-ADR-SEC-03) nor the old strong-buy leak (0.5, pre-#1878).
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

        assert score is None, (
            "REGRESSION: numeric abstention re-introduced. 0.0 biases the "
            "ConsensusEngine toward SELL (ADR-SEC-03); 0.5 is a strong-buy in "
            "prediction space and leaks failed symbols into the Top-N (#1878). "
            "FeatureGenerationError MUST return None."
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
