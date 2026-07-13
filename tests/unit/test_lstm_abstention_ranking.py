# tests/unit/test_lstm_abstention_ranking.py
# Issue #1878 (MLA-4) — Fix 3: abstention is None, never a prediction value.
#
# ADR-SEC-03 (revised by #1878): the old abstention encoding (0.5) was correct in
# the Round-Table VOTE space [0..1] but leaked into the PREDICTION space (predicted
# scaled 5d return), where 0.5 is a strong-buy value (buy threshold 0.2). A symbol
# whose feature generation failed could therefore rank into the Top-N and be BOUGHT.
#
# Gherkin (issue acceptance):
#   Given create_live_features raises FeatureGenerationError for symbol X
#   When update_lstm_rankings runs over [X, A, B]
#   Then X is never in the LSTM ranking nor the allocation weights (never Top-N)
#   And the abstention is logged at WARNING
#
# Exit safety (verified against core/smart_exit.py:111): an abstained in-position
# symbol has lstm_rank=None -> the rank-based SELL does NOT fire; stop-loss and
# take-profit remain active.

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest


def _make_strategy():
    """Minimal LSTMDynamicStrategy double (heavy ML init bypassed, real numpy)."""
    import numpy as np

    from core.strategies.lstm_strategy import LSTMDynamicStrategy

    strategy = LSTMDynamicStrategy.__new__(LSTMDynamicStrategy)
    strategy.torch_model = MagicMock()
    strategy.scaler_x = MagicMock()
    strategy.torch = MagicMock()
    strategy.np = np  # update_lstm_rankings does real array math
    strategy.pd = MagicMock()
    strategy.scaler_y = None
    strategy.features_list = ["close", "volume", "rsi_14"]
    strategy._initialized = True
    strategy.device = "cpu"
    strategy.client = MagicMock()
    strategy.data_provider = MagicMock()
    strategy.client.__class__ = MagicMock  # not SimulationAdapter
    strategy._lstm_rank_cache = []
    strategy._allocation_weights = {}
    return strategy


def _now():
    return datetime(2024, 6, 1, tzinfo=timezone.utc)


def _snapshots(symbols):
    snaps = {}
    for s in symbols:
        snap = MagicMock()
        snap.latest_trade.p = 100.0
        snaps[s] = snap
    return snaps


# ---------------------------------------------------------------------------
# _get_torch_prediction — abstention contract
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestAbstentionIsNone:
    @pytest.mark.anyio
    async def test_feature_error_returns_none_not_a_prediction(self):
        """FeatureGenerationError -> (None, None). NEVER 0.5 (strong-buy in
        prediction space) and NEVER 0.0 (sell-bias)."""
        import pandas as pd

        from models.torch_model import FeatureGenerationError

        strategy = _make_strategy()
        dates = pd.date_range("2024-01-01", periods=300)
        hist = pd.DataFrame(
            {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1e6},
            index=dates,
        )

        with patch(
            "models.torch_model.create_live_features",
            side_effect=FeatureGenerationError("missing column"),
        ), patch("core.strategies.lstm_strategy.SEQUENCE_LENGTH", 10), patch(
            "asyncio.get_running_loop"
        ) as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=hist)
            score, features = await strategy._get_torch_prediction(
                "AAPL", _now(), {"vix": 20.0, "latest_news_sentiment": 0.0}
            )

        assert score is None, (
            f"Abstention MUST be None — got {score!r}. 0.5 is a strong-buy in "
            f"prediction space (#1878 leak), 0.0 a sell-bias (ADR-SEC-03)."
        )
        assert features is None


# ---------------------------------------------------------------------------
# update_lstm_rankings — failed symbol never in Top-N (issue acceptance test)
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestFailedSymbolNeverInTopN:
    @pytest.mark.anyio
    async def test_failed_symbol_excluded_from_ranking_and_weights(self, caplog):
        strategy = _make_strategy()
        preds = {"AAA": 0.7, "BBB": 0.3}

        async def fake_prediction(symbol, current_time, market_data):
            if symbol == "FAIL":
                return None, None  # abstention (feature failure)
            return preds[symbol], None

        strategy._get_torch_prediction = AsyncMock(side_effect=fake_prediction)

        symbols = ["FAIL", "AAA", "BBB"]
        with caplog.at_level(logging.WARNING):
            await strategy.update_lstm_rankings(
                symbols, _snapshots(symbols), {"vix": 20.0}, _now()
            )

        ranked = [s for s, _ in strategy._lstm_rank_cache]
        assert "FAIL" not in ranked, "abstained symbol must never enter the ranking"
        assert set(ranked) == {"AAA", "BBB"}
        assert (
            "FAIL" not in strategy._allocation_weights
        ), "abstained symbol must never receive an allocation weight (Top-N buy list)"
        assert set(strategy._allocation_weights) == {"AAA", "BBB"}
        assert any(
            "FAIL" in r.getMessage() and r.levelno == logging.WARNING
            for r in caplog.records
        ), "abstention must be logged at WARNING (CODING_POLICY §5.6)"

    @pytest.mark.anyio
    async def test_all_symbols_failing_yields_empty_ranking(self):
        strategy = _make_strategy()
        strategy._get_torch_prediction = AsyncMock(return_value=(None, None))

        symbols = ["X", "Y"]
        await strategy.update_lstm_rankings(
            symbols, _snapshots(symbols), {"vix": 20.0}, _now()
        )

        assert strategy._lstm_rank_cache == []
        assert strategy._allocation_weights == {}

    @pytest.mark.anyio
    async def test_short_history_held_position_gets_no_rank_sell(self, caplog):
        """#1878 review Finding 2 acceptance: a held position whose history goes
        short (data outage) must ABSTAIN — with the old (0.0, None) the position
        entered the ranking at the tail and the rank-based exit force-sold it.

        End-to-end through the real _get_torch_prediction: 30 rows with the v1
        window (seq=20) sit below the feature warm-up floor (50) → abstention →
        symbol not ranked → rank None → should_sell_smart does not SELL.
        """
        import pandas as pd

        from core.smart_exit import should_sell_smart

        strategy = _make_strategy()
        strategy.sequence_length = 20  # v1 metadata window

        dates = pd.date_range("2024-01-01", periods=30)
        short_hist = pd.DataFrame(
            {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1e6},
            index=dates,
        )

        symbols = ["HELD"]
        with patch("models.torch_model.create_live_features") as mock_create, patch(
            "asyncio.get_running_loop"
        ) as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=short_hist)
            with caplog.at_level(logging.WARNING):
                await strategy.update_lstm_rankings(
                    symbols, _snapshots(symbols), {"vix": 20.0}, _now()
                )

        mock_create.assert_not_called()  # below warm-up floor: no placeholder features
        assert "HELD" not in [s for s, _ in strategy._lstm_rank_cache], (
            "short history must be an abstention — a 0.0 'prediction' would rank "
            "the held position last and force a rank-based SELL"
        )
        assert "HELD" not in strategy._allocation_weights

        rank, in_top_n = strategy._get_rank_and_in_top_n("HELD")
        assert rank is None
        decision = should_sell_smart(
            symbol="HELD",
            entry_price=100.0,
            current_price=101.0,  # no stop-loss / take-profit trigger
            high_water_mark=101.0,
            hours_held=1.0,
            in_top_n=in_top_n,
            lstm_rank=rank,
            top_n_size=10,
        )
        assert (
            decision.action != "SELL"
        ), "data outage on a held position must never trigger a rank-based SELL"
        assert any(
            r.levelno == logging.WARNING and "HELD" in r.getMessage()
            for r in caplog.records
        ), "abstention on short history must be logged at WARNING (CODING_POLICY §5.6)"

    def test_abstained_symbol_rank_is_none_so_no_forced_rank_exit(self):
        """Exit safety: _get_rank_and_in_top_n returns (None, False) for a symbol
        missing from the cache; should_sell_smart's rank exit requires a non-None
        rank (core/smart_exit.py:111) -> no forced SELL on data failure."""
        from core.smart_exit import should_sell_smart

        strategy = _make_strategy()
        strategy._lstm_rank_cache = [("AAA", 0.7), ("BBB", 0.3)]

        rank, in_top_n = strategy._get_rank_and_in_top_n("FAIL")
        assert rank is None
        assert in_top_n is False

        decision = should_sell_smart(
            symbol="FAIL",
            entry_price=100.0,
            current_price=101.0,  # no stop-loss / take-profit trigger
            high_water_mark=101.0,
            hours_held=1.0,
            in_top_n=in_top_n,
            lstm_rank=rank,
            top_n_size=10,
        )
        assert decision.action != "SELL"
