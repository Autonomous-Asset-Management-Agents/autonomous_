"""
validation.py - Out-of-Sample Walk-Forward Validation for ai_trading_bot.
Implements time-series cross-validation tailored for trading strategies.
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def walk_forward_validate(
    df: pd.DataFrame, model_config: dict, n_splits: int = 5
) -> dict:
    """
    Performs walk-forward (out-of-sample) validation on the given dataframe.

    Args:
        df (pd.DataFrame): Historical data to validate on, strictly chronological.
        model_config (dict): Must contain 'train_eval_func', a callable that takes
                             (train_df, test_df) and returns a dict of metrics.
        n_splits (int): Number of testing periods.

    Returns:
        dict: Aggregated performance metrics of the model over the out-of-sample data.
    """
    if len(df) < n_splits * 2:
        raise ValueError(
            "Not enough data to support the requested number of splits safely."
        )

    train_eval_func = model_config.get("train_eval_func")
    if not callable(train_eval_func):
        raise ValueError("model_config must contain a callable 'train_eval_func'.")

    # Time series standard walk-forward split (expanding window)
    fold_size = len(df) // (n_splits + 1)

    folds_results = []

    for i in range(n_splits):
        train_start = 0
        train_end = fold_size * (i + 1)
        test_start = train_end
        test_end = train_end + fold_size

        if i == n_splits - 1:
            # Consume any remainder in the final fold
            test_end = len(df)

        train_df = df.iloc[train_start:train_end]
        test_df = df.iloc[test_start:test_end]

        try:
            logger.info(
                f"Fold {i+1}/{n_splits}: "
                f"Train size={len(train_df)} "
                f"Test size={len(test_df)}"
            )

            fold_metrics = train_eval_func(train_df, test_df)

            if not isinstance(fold_metrics, dict):
                fold_metrics = {"result": fold_metrics}

            fold_metrics["fold_index"] = i + 1
            folds_results.append(fold_metrics)

        except Exception as e:
            logger.error("Error during fold %s: %s", i + 1, e)
            raise

    # Aggregate results metrics
    aggregated = {"folds": folds_results}

    if folds_results:
        # Extract numeric keys from the first fold's results dynamically
        base_keys = [
            k
            for k, v in folds_results[0].items()
            if isinstance(v, (int, float, np.number)) and k != "fold_index"
        ]

        for key in base_keys:
            vals = [fold.get(key, 0.0) for fold in folds_results]
            aggregated[f"average_{key}"] = float(np.mean(vals))

    return aggregated
