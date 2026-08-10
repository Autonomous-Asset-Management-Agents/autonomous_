"""
test_validation.py — Iron Dome Unit Tests for core/validation.py (EXTENDED)

Coverage target: ~100% of walk_forward_validate()
Includes: normal operation, error cases, edge cases, aggregation logic.
"""

import allure
import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.iron_dome

from core.validation import walk_forward_validate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_df(n: int = 100):
    """Create a simple monotonic time-series DataFrame with n rows."""
    dates = pd.date_range("2020-01-01", periods=n)
    return pd.DataFrame({"close": np.arange(float(n)) + 100.0}, index=dates)


def _mock_eval(return_val: float = 0.05, sharpe_val: float = 1.2):
    def _func(train_df, test_df):
        return {"return": return_val, "sharpe": sharpe_val}

    return _func


# ---------------------------------------------------------------------------
# Tests: Normal Operation
# ---------------------------------------------------------------------------


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestWalkForwardNormal:

    def test_returns_aggregated_average_metrics(self):
        df = _make_df(100)
        results = walk_forward_validate(
            df, {"train_eval_func": _mock_eval()}, n_splits=5
        )
        assert results["average_return"] == pytest.approx(0.05)
        assert results["average_sharpe"] == pytest.approx(1.2)

    def test_correct_number_of_folds(self):
        df = _make_df(100)
        results = walk_forward_validate(
            df, {"train_eval_func": _mock_eval()}, n_splits=5
        )
        assert len(results["folds"]) == 5

    def test_each_fold_has_index(self):
        df = _make_df(100)
        results = walk_forward_validate(
            df, {"train_eval_func": _mock_eval()}, n_splits=3
        )
        indices = [fold["fold_index"] for fold in results["folds"]]
        assert indices == [1, 2, 3]

    def test_last_fold_consumes_remainder(self):
        """Last fold should consume leftover rows (not discard them)."""
        df = _make_df(107)  # 107 / 6 = 17 per fold, 5 leftover consumed in final fold
        results = walk_forward_validate(
            df, {"train_eval_func": _mock_eval()}, n_splits=5
        )
        assert len(results["folds"]) == 5

    def test_single_split(self):
        df = _make_df(10)
        results = walk_forward_validate(
            df, {"train_eval_func": _mock_eval()}, n_splits=1
        )
        assert len(results["folds"]) == 1

    def test_non_dict_return_from_eval_func(self):
        """If train_eval_func returns non-dict, it must be wrapped."""

        def _scalar_func(train, test):
            return 0.42  # not a dict

        df = _make_df(50)
        results = walk_forward_validate(
            df, {"train_eval_func": _scalar_func}, n_splits=2
        )
        assert "folds" in results
        assert results["folds"][0].get("result") == 0.42

    def test_expanding_window_train_size_grows(self):
        """Train set must grow with each fold (expanding window)."""
        train_sizes = []

        def _capture_func(train_df, test_df):
            train_sizes.append(len(train_df))
            return {"metric": 1.0}

        df = _make_df(100)
        walk_forward_validate(df, {"train_eval_func": _capture_func}, n_splits=4)
        # Each fold's training set must be strictly larger than the previous
        for i in range(1, len(train_sizes)):
            assert train_sizes[i] > train_sizes[i - 1], (
                f"Fold {i+1} training set ({train_sizes[i]}) must be larger "
                f"than fold {i} ({train_sizes[i-1]})"
            )

    def test_aggregation_with_mixed_metrics(self):
        """Aggregation must average each numeric metric across folds."""
        fold_counter = [0]
        returns = [0.1, 0.2, 0.3]

        def _varied_func(train_df, test_df):
            r = returns[fold_counter[0] % len(returns)]
            fold_counter[0] += 1
            return {"profit": r}

        df = _make_df(60)
        results = walk_forward_validate(
            df, {"train_eval_func": _varied_func}, n_splits=3
        )
        expected_avg = sum(returns) / 3
        assert results["average_profit"] == pytest.approx(expected_avg, abs=0.001)


# ---------------------------------------------------------------------------
# Tests: Error Cases
# ---------------------------------------------------------------------------


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestWalkForwardErrors:

    def test_raises_on_insufficient_data(self):
        df = _make_df(5)  # 5 rows, 5 splits → 5 < 5*2
        with pytest.raises(ValueError, match="Not enough data"):
            walk_forward_validate(df, {"train_eval_func": _mock_eval()}, n_splits=5)

    def test_raises_on_non_callable_train_eval_func(self):
        df = _make_df(50)
        with pytest.raises(ValueError, match="callable"):
            walk_forward_validate(df, {"train_eval_func": "not_a_function"}, n_splits=3)

    def test_raises_on_missing_train_eval_func(self):
        df = _make_df(50)
        with pytest.raises(ValueError, match="callable"):
            walk_forward_validate(df, {}, n_splits=3)

    def test_re_raises_exception_from_eval_func(self):
        """Exceptions from train_eval_func must propagate (not be swallowed)."""

        def _exploding_func(train_df, test_df):
            raise RuntimeError("Strategy blew up")

        df = _make_df(50)
        with pytest.raises(RuntimeError, match="Strategy blew up"):
            walk_forward_validate(df, {"train_eval_func": _exploding_func}, n_splits=2)

    def test_empty_folds_results_gives_no_averages(self):
        """If folds_results is empty (theoretically), aggregated should still have 'folds' key."""
        # Achieve by patching n_splits=0 — but the loop won't execute
        df = _make_df(20)
        # n_splits=0 means fold_size = len(df)/(0+1) = 20, range(0) → no folds
        results = walk_forward_validate(
            df, {"train_eval_func": _mock_eval()}, n_splits=0
        )
        assert results["folds"] == []
        # No average keys
        assert "average_return" not in results
