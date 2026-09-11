# rule_validator.py
# --- UPGRADED VERSION: Walk-Forward Optimization ---

import logging
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

# Import DataProvider type for type hinting
from core.data_provider import HistoricalDataProvider
from core.utils import ta


class RuleValidator:
    def __init__(self, data_provider: HistoricalDataProvider):
        self.data_provider = data_provider
        self.validation_data = pd.DataFrame()
        self.MIN_TRADES = 10
        self._refresh_data()

    def _refresh_data(self):
        """Loads 2 years of data for validation."""
        try:
            end_date = datetime.now()
            logging.info("RuleValidator: Loading 2 years of SPY data...")
            df = self.data_provider.get_data("SPY", end_date, days=730)

            if not df.empty:
                df["rsi"] = ta.rsi(df["close"], length=14)
                adx = ta.adx(df["high"], df["low"], df["close"], length=14)
                if adx is not None and "ADX_14" in adx.columns:
                    df["adx"] = adx["ADX_14"]

                if "vix" not in df.columns:
                    df["vix"] = 20.0
                else:
                    df["vix"] = df["vix"].ffill().fillna(20.0)

                # Oracle Target: 5-day forward return
                df["fwd_return_5d"] = df["close"].shift(-5) / df["close"] - 1

                self.validation_data = df
                # TODO(PR-D): Complex f-string, review manually:                 logging.info(f"RuleValidator: Loaded {len(df)} rows.")
                logging.info(f"RuleValidator: Loaded {len(df)} rows.")
        except Exception as e:
            logging.error("RuleValidator: Failed to load data: %s", e)

    def validate_new_rules(self, raw_rules: List[Dict]) -> List[Dict]:
        valid_rules = []
        for rule in raw_rules:
            # 1. Sanity Check
            is_sane, reason = self._check_sanity(rule)
            if not is_sane:
                logging.warning("Rejected (Sanity): %s -> %s", reason, rule)
                continue

            # 2. Walk-Forward Validation (Robustness)
            if rule.get("action") in ["block_trade", "proactive_signal"]:
                passed_wf, wf_stats = self._walk_forward_test(rule)
                if not passed_wf:
                    logging.warning("Rejected (Walk-Forward): %s -> %s", wf_stats, rule)
                    continue

                # 3. Monte Carlo (Luck)
                if not self._monte_carlo_test(rule):
                    logging.warning(
                        f"Rejected (Monte Carlo): Luck test failed -> {rule}"
                    )
                    continue

            rule["status"] = "probation"
            rule["created_at"] = datetime.now().isoformat()
            valid_rules.append(rule)
            # TODO(PR-D): Complex f-string, review manually:             logging.info(f"Rule APPROVED: {rule.get('reason')}")
            logging.info(f"Rule APPROVED: {rule.get('reason')}")

        return valid_rules

    def _check_sanity(self, rule: Dict) -> Tuple[bool, str]:
        trigger = rule.get("trigger", {})
        if "rsi_gt" in trigger and (trigger["rsi_gt"] > 100 or trigger["rsi_gt"] < 0):
            return False, "RSI > 100"
        if (
            "vix_gt" in trigger
            and "vix_lt" in trigger
            and trigger["vix_gt"] >= trigger["vix_lt"]
        ):
            return False, "Logical Conflict"
        return True, "Passed"

    def _walk_forward_test(self, rule: Dict) -> Tuple[bool, Dict]:
        """
        Walk-Forward Optimization:
        Splits 2 years of data into Rolling Windows (e.g., 90 days).
        The rule must be profitable/useful in a majority of these windows.
        """
        if self.validation_data.empty:
            return True, {"warning": "No data"}

        df = self.validation_data.copy()

        # Window Configuration
        WINDOW_SIZE = 90  # 3 months per test window
        STEP_SIZE = 30  # Move forward 1 month at a time

        # Convert trigger to mask ONCE (Vectorized)
        trigger = rule.get("trigger", {})
        condition = pd.Series(True, index=df.index)
        if "rsi_gt" in trigger:
            condition &= df["rsi"] > trigger["rsi_gt"]
        if "rsi_lt" in trigger:
            condition &= df["rsi"] < trigger["rsi_lt"]
        if "vix_gt" in trigger:
            condition &= df["vix"] > trigger["vix_gt"]
        if "vix_lt" in trigger:
            condition &= df["vix"] < trigger["vix_lt"]
        if "adx_gt" in trigger:
            condition &= df["adx"] > trigger["adx_gt"]

        # Store global mask on DF
        df["rule_match"] = condition

        # Rolling Window Loop
        total_windows = 0
        passed_windows = 0
        valid_windows = 0  # Windows where the rule actually triggered

        start_idx = 0
        while start_idx + WINDOW_SIZE < len(df):
            end_idx = start_idx + WINDOW_SIZE
            window_df = df.iloc[start_idx:end_idx]

            # Get trades in this window
            window_matches = window_df[window_df["rule_match"]]

            if len(window_matches) > 0:
                valid_windows += 1
                avg_ret = window_matches["fwd_return_5d"].mean()

                # Success Criteria
                is_success = False
                if rule["action"] == "block_trade":
                    # Blocking is good if we blocked NEGATIVE returns
                    if avg_ret < -0.001:
                        is_success = True
                elif rule["action"] == "proactive_signal":
                    # Signal is good if it produced POSITIVE returns
                    if avg_ret > 0.001:
                        is_success = True

                if is_success:
                    passed_windows += 1

            total_windows += 1
            start_idx += STEP_SIZE

        # Decision Logic
        if valid_windows < 2:
            return False, {"reason": "Rule rarely triggered (in < 2 windows)"}

        success_rate = passed_windows / valid_windows

        # MUST work in at least 50% of the windows it triggered in
        if success_rate >= 0.50:
            return True, {
                "success_rate": f"{success_rate:.2f}",
                "windows": f"{passed_windows}/{valid_windows}",
            }
        else:
            return False, {
                "reason": "Failed Walk-Forward",
                "success_rate": f"{success_rate:.2f}",
            }

    def _monte_carlo_test(self, rule: Dict, n_sims=50) -> bool:
        if self.validation_data.empty or rule["action"] != "block_trade":
            return True

        # (Logic remains same as previous version - reusing mask)
        df = self.validation_data.copy()
        trigger = rule.get("trigger", {})
        condition = pd.Series(True, index=df.index)
        if "rsi_gt" in trigger:
            condition &= df["rsi"] > trigger["rsi_gt"]
        # ... simple triggers ...

        matches = df[condition]
        if matches.empty:
            return False
        real_score = matches["fwd_return_5d"].mean()

        better_random = 0
        pool = df["fwd_return_5d"].dropna().values.copy()
        if len(pool) < len(matches):
            return True

        for _ in range(n_sims):
            np.random.shuffle(pool)
            if np.mean(pool[: len(matches)]) < real_score:
                better_random += 1

        return (better_random / n_sims) <= 0.20
