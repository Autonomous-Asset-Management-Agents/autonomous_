# core/learning/engine.py
# Epic 1.7 / PR-D — Extrahiert aus core/ai_components.py
# Verantwortlichkeit: AI Learning Engine (Gemini-gestützte Trade-Analyse, Regel-Generierung)

import asyncio
import json
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import LEARNED_RULES_FILE
from core.ai_rules import AILearnedRules
from core.llm.provider import get_llm_provider
from core.utils import BackendSignals


class AILearningEngine:
    """
    Analysiert Simulations-Trade-Daten mit Gemini AI und generiert
    defensive/opportunistische Trading-Regeln.

    Ursprünglich Teil von core/ai_components.py (monolithisch).
    Extrahiert via TDD (Epic 1.7 / PR-D) — Backward-Compat-Shim in ai_components.py.
    """

    def __init__(self, signals: BackendSignals):
        self.signals = signals
        self.gemini_model = None  # resolved lazily via get_llm_provider()
        logging.info("AI Learning Engine: initialized (Gemini resolved on first use).")
        self.ai_rules = AILearnedRules()
        self.validator = None  # Injected externally when available

    def _load_simulation_data(
        self,
    ) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
        try:
            trades_df = pd.read_csv("simulation_trades.csv", parse_dates=["Timestamp"])

            if "TradeContext" in trades_df.columns:
                trades_df["TradeContext"] = (
                    trades_df["TradeContext"].fillna("{}").apply(json.loads)
                )
            else:
                logging.warning(
                    "'TradeContext' column not found. AI Learning will be limited."
                )
                trades_df["TradeContext"] = [{}] * len(trades_df)

            equity_df = pd.read_csv(
                "simulation_equity_log.csv", parse_dates=["Timestamp"]
            )

            if trades_df.empty or equity_df.empty:
                self.signals.error_message.emit(
                    "Analysis Error", "Simulation log files are empty."
                )
                return None, None, None

            equity_df["daily_pnl"] = equity_df["Equity"].diff().fillna(0)

            if trades_df["Timestamp"].dt.tz is not None:
                trades_df["Timestamp"] = trades_df["Timestamp"].dt.tz_localize(None)
            if equity_df["Timestamp"].dt.tz is not None:
                equity_df["Timestamp"] = equity_df["Timestamp"].dt.tz_localize(None)

            trades_df["Date"] = trades_df["Timestamp"].dt.date
            equity_df["Date"] = equity_df["Timestamp"].dt.date

            equity_daily = equity_df.groupby("Date").first().reset_index()
            trades_df = pd.merge(
                trades_df, equity_daily[["Date", "daily_pnl"]], on="Date", how="left"
            )
            trades_df.drop(columns=["Date"], inplace=True, errors="ignore")

            losing_trades_df = trades_df[trades_df["daily_pnl"] < 0].copy()
            winning_trades_df = trades_df[trades_df["daily_pnl"] > 0].copy()

            return trades_df, winning_trades_df, losing_trades_df

        except FileNotFoundError:
            self.signals.error_message.emit(
                "Analysis Error", "Could not find simulation logs."
            )
            return None, None, None
        except Exception as e:
            logging.error("Error loading simulation data: %s", e, exc_info=True)
            self.signals.error_message.emit(
                "Analysis Error", "Failed to load logs: %s" % e
            )
            return None, None, None

    def _build_gemini_learning_prompt(
        self, trade_samples: List[Dict], news_samples: List[Dict]
    ) -> str:
        available_features = ""
        if (
            trade_samples
            and "TradeContext" in trade_samples[0]
            and "indicators" in trade_samples[0]["TradeContext"]
            and "features" in trade_samples[0]["TradeContext"]["indicators"]
        ):
            feature_names = list(
                trade_samples[0]["TradeContext"]["indicators"]["features"].keys()
            )
            available_features = (
                "Available Feature Keys in 'indicators.features': %s" % feature_names
            )

        return (
            "You are a quantitative trading analyst. Your task is to analyze a sample of LOSING trades "
            "from the 'RLAgent' to find patterns and create new DEFENSIVE rules.\n"
            "Data:\n"
            "1. Losing Trade Samples: %s\n"
            "2. Recent News Samples (for context): %s %s\n"
            "Analysis Task:\n"
            'Analyze the "Losing Trade Samples" for patterns. Look for strong correlations between '
            "losses (\"daily_pnl\" < 0) and the 'TradeContext'.\n"
            "The strategy is always 'RLAgent'. Its context contains the agent's action and the scaled "
            "PyTorch prediction.\n"
            "**CRITICAL:** Correlate failures with all fields in the nested 'indicators.features' "
            "dictionary (e.g., 'rsi_14d', 'market_cap_log', 'adx_14d').\n"
            "Find patterns where an 'RLAgent' 'BUY' signal (action 1) *failed* and correlate it to "
            "the 'vix', 'regime', or 'indicators.torch_pred_scaled'.\n"
            "Rule Generation Task:\n"
            "Generate 1-10 new DEFENSIVE rules based ONLY on the strongest losing patterns.\n"
            "Available Rule Actions:\n"
            '1. "action": "block_trade"\n'
            '2. "action": "reduce_size" — Must include a "value" (e.g., 0.5 for 50%% size).\n'
            '3. "action": "tighten_sl" — Must include a "value" (e.g., 1.0 for 1x ATR).\n'
            "Respond ONLY with valid JSON:\n"
            '{"analysis_summary": "...", "learned_rules": [{"reason": "...", "trigger": {...}, '
            '"action": "...", "value": ...}]}\n'
            'If no strong patterns, return an empty "learned_rules" list.\n'
        ) % (
            json.dumps(trade_samples, indent=2),
            json.dumps(news_samples, indent=2),
            available_features,
        )

    def _build_gemini_opportunity_prompt(
        self, trade_samples: List[Dict], news_samples: List[Dict]
    ) -> str:
        available_features = ""
        if (
            trade_samples
            and "TradeContext" in trade_samples[0]
            and "indicators" in trade_samples[0]["TradeContext"]
            and "features" in trade_samples[0]["TradeContext"]["indicators"]
        ):
            feature_names = list(
                trade_samples[0]["TradeContext"]["indicators"]["features"].keys()
            )
            available_features = (
                "Available Feature Keys in 'indicators.features': %s" % feature_names
            )

        return (
            "You are a quantitative trading analyst. Your task is to analyze a sample of WINNING "
            "trades from the 'RLAgent' to find patterns and create new OPPORTUNITY rules.\n"
            "Data:\n"
            "1. Winning Trade Samples: %s\n"
            "2. Recent News Samples (for context): %s %s\n"
            "Rule Generation Task:\n"
            "Generate 1-10 new OPPORTUNITY rules.\n"
            "Available Rule Actions:\n"
            '1. "action": "increase_size" — Must include a "value" (e.g., 1.5).\n'
            '2. "action": "widen_sl" — Must include a "value" (e.g., 3.0).\n'
            '3. "action": "proactive_signal" — Must include "headline_keywords", "sentiment_gt", "signal_ticker".\n'
            "Respond ONLY with valid JSON:\n"
            '{"analysis_summary": "...", "learned_rules": [{"reason": "...", "trigger": {...}, '
            '"action": "...", "value": ...}]}\n'
        ) % (
            json.dumps(trade_samples, indent=2),
            json.dumps(news_samples, indent=2),
            available_features,
        )

    async def run_learning_analysis(
        self, historical_data_provider, news_processor, shutdown_event: threading.Event
    ):
        self.signals.ai_learning_update.emit("Loading simulation data...")
        if shutdown_event.is_set():
            self.signals.ai_learning_update.emit("Learning aborted.")
            return

        trades_df, winning_trades_df, losing_trades_df = self._load_simulation_data()
        if trades_df is None:
            self.signals.ai_learning_update.emit("Analysis failed: No simulation data.")
            return
        if shutdown_event.is_set():
            self.signals.ai_learning_update.emit("Learning aborted.")
            return

        self.signals.ai_learning_update.emit(
            "Fetching historical context (VIX & News)..."
        )
        try:
            start_date, end_date = (
                trades_df["Timestamp"].min().to_pydatetime(),
                trades_df["Timestamp"].max().to_pydatetime(),
            )
        except Exception:
            logging.error("Could not determine date range.")
            self.signals.ai_learning_update.emit(
                "Analysis failed: Could not read simulation dates."
            )
            return

        from datetime import timedelta  # local import to avoid circular

        vix_data = await asyncio.to_thread(
            historical_data_provider.get_data,
            "^VIX",
            end_date + timedelta(days=1),
            days=(end_date - start_date).days + 5,
        )

        if not vix_data.empty and "close" in vix_data.columns:
            vix_data = vix_data[["close"]].rename(columns={"close": "vix"})
            if vix_data.index.tz is not None:
                vix_data.index = vix_data.index.tz_localize(None)
            vix_data.index.name = "Timestamp"

            for df_name, df in [
                ("trades", trades_df),
                ("winning", winning_trades_df),
                ("losing", losing_trades_df),
            ]:
                locals()[df_name + "_df"] = pd.merge_asof(
                    df.sort_values("Timestamp"),
                    vix_data,
                    on="Timestamp",
                    direction="backward",
                )
        else:
            logging.warning("VIX data invalid. Proceeding without it.")
            for df in [trades_df, winning_trades_df, losing_trades_df]:
                df["vix"] = np.nan

        if shutdown_event.is_set():
            self.signals.ai_learning_update.emit("Learning aborted.")
            return

        news_data = await asyncio.to_thread(
            news_processor.get_historical_news,
            trades_df["Symbol"].unique().tolist(),
            start_date,
            end_date,
        )

        from datetime import datetime as dt_cls

        news_samples = []
        for item in news_data[:10]:
            item_copy = item.copy()
            if "created_at" in item_copy and isinstance(
                item_copy["created_at"], dt_cls
            ):
                item_copy["created_at"] = item_copy["created_at"].isoformat()
            news_samples.append(item_copy)

        if not (self.gemini_model or get_llm_provider()):
            self.signals.ai_learning_update.emit(
                "Analysis failed: Gemini AI not available."
            )
            self.signals.ai_learning_complete.emit({})
            return

        all_new_rules: List[Dict] = []
        final_summary = ""

        def _prepare_trade_samples(df):
            def extract_features(row):
                ctx = row.get("TradeContext", {})
                return ctx.get("indicators", {}).get("features", {})

            df = df.copy()
            df["features"] = df.apply(extract_features, axis=1)
            df["vix"] = df["vix"].fillna(0)
            df["torch_pred"] = df["TradeContext"].apply(
                lambda x: x.get("indicators", {}).get("torch_pred_scaled")
            )
            df["rl_action"] = df["TradeContext"].apply(
                lambda x: x.get("indicators", {}).get("rl_action")
            )
            df["Timestamp"] = df["Timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
            sample_cols = [
                "Symbol",
                "Side",
                "Qty",
                "Price",
                "Timestamp",
                "daily_pnl",
                "vix",
                "torch_pred",
                "rl_action",
                "features",
            ]
            final_cols = [c for c in sample_cols if c in df.columns]
            return df[final_cols]

        if not losing_trades_df.empty:
            self.signals.ai_learning_update.emit(
                "Analyzing LOSING trades with Gemini AI..."
            )
            try:
                trade_samples = (
                    _prepare_trade_samples(losing_trades_df)
                    .sample(min(len(losing_trades_df), 100))
                    .to_dict("records")
                )
                prompt = self._build_gemini_learning_prompt(trade_samples, news_samples)
                ai_result, error = await self._run_gemini_query(prompt, shutdown_event)
                if ai_result:
                    all_new_rules.extend(ai_result.get("learned_rules", []))
                    final_summary += (
                        "--- Loss Analysis ---\n"
                        + ai_result.get("analysis_summary", "No summary.")
                        + "\n\n"
                    )
                else:
                    self.signals.ai_learning_update.emit(
                        "AI analysis (losses) failed: %s" % error
                    )
            except Exception as e:
                logging.error("AI Learning (Losses) failed: %s", e, exc_info=True)
        else:
            final_summary += "--- Loss Analysis ---\nNo losing trades found.\n\n"

        if shutdown_event.is_set():
            self.signals.ai_learning_update.emit("Learning aborted.")
            return

        if not winning_trades_df.empty:
            self.signals.ai_learning_update.emit(
                "Analyzing WINNING trades with Gemini AI..."
            )
            try:
                trade_samples = (
                    _prepare_trade_samples(winning_trades_df)
                    .nlargest(100, "daily_pnl")
                    .to_dict("records")
                )
                prompt = self._build_gemini_opportunity_prompt(
                    trade_samples, news_samples
                )
                ai_result, error = await self._run_gemini_query(prompt, shutdown_event)
                if ai_result:
                    all_new_rules.extend(ai_result.get("learned_rules", []))
                    final_summary += (
                        "--- Win Analysis ---\n"
                        + ai_result.get("analysis_summary", "No summary.")
                        + "\n\n"
                    )
                else:
                    self.signals.ai_learning_update.emit(
                        "AI analysis (wins) failed: %s" % error
                    )
            except Exception as e:
                logging.error("AI Learning (Wins) failed: %s", e, exc_info=True)
        else:
            final_summary += "--- Win Analysis ---\nNo winning trades found.\n\n"

        if all_new_rules:
            self.signals.ai_learning_update.emit("Checking rule syntax...")
            structurally_valid_rules = []
            for r in all_new_rules:
                trigger = r.get("trigger")
                action = r.get("action")
                reason = r.get("reason")
                value = r.get("value")

                if not all([isinstance(trigger, dict), action, reason]):
                    logging.warning("AI Learning: Skipping invalid rule: %s", r)
                    continue

                if action == "block_trade":
                    structurally_valid_rules.append(r)
                elif action in [
                    "reduce_size",
                    "tighten_sl",
                    "increase_size",
                    "widen_sl",
                ]:
                    try:
                        float(value)
                        structurally_valid_rules.append(r)
                    except (ValueError, TypeError):
                        logging.warning(
                            "AI Learning: Skipping rule '%s' (invalid value): %s",
                            action,
                            r,
                        )
                elif action == "proactive_signal":
                    required = ["headline_keywords", "sentiment_gt", "signal_ticker"]
                    if all(f in trigger for f in required):
                        try:
                            float(trigger["sentiment_gt"])
                            structurally_valid_rules.append(r)
                        except (ValueError, TypeError):
                            logging.warning(
                                "AI Learning: Skipping 'proactive_signal' (invalid sentiment_gt): %s",
                                r,
                            )
                    else:
                        logging.warning(
                            "AI Learning: Skipping 'proactive_signal' (missing fields): %s",
                            r,
                        )
                else:
                    logging.warning(
                        "AI Learning: Skipping rule (unknown action): %s", r
                    )

            if structurally_valid_rules and self.validator:
                self.signals.ai_learning_update.emit(
                    "Running Historical Backtest & Monte Carlo Stress Test..."
                )
                validated_rules = self.validator.validate_new_rules(
                    structurally_valid_rules
                )
                rejected_count = len(structurally_valid_rules) - len(validated_rules)
                if rejected_count > 0:
                    self.signals.ai_learning_update.emit(
                        "Validator rejected %d rules." % rejected_count
                    )
                    final_summary += (
                        "\n--- Validator Rejected %d rules ---" % rejected_count
                    )
                structurally_valid_rules = validated_rules

            if structurally_valid_rules:
                self.ai_rules.save_rules(structurally_valid_rules)
                logging.info(
                    "AI Learning: Saved %d rules.", len(structurally_valid_rules)
                )
                final_summary += "\n--- Saved %d new rules to %s ---" % (
                    len(structurally_valid_rules),
                    LEARNED_RULES_FILE,
                )
            else:
                final_summary += "\n--- No rules passed validation. ---"

        self.signals.ai_learning_update.emit("AI analysis complete.")
        self.signals.ai_learning_complete.emit(
            {"analysis_summary": final_summary, "learned_rules": all_new_rules}
        )

        # Run Dynamic Constitution Calibrations
        self.update_dynamic_agent_weights()

    def update_dynamic_agent_weights(self):
        """
        Dynamic Constitution: Nightly Calibration
        Aggregates agent_trust_scores from Redis, calculates weight deltas,
        and saves updated clamped agent_weights_v2 to Redis.
        """
        try:
            import core.round_table.agents as agents_module
            from core.redis_client import RedisClient

            r = RedisClient.get_sync_redis()
            if not r:
                return

            raw_trust = r.get("agent_trust_scores")
            if not raw_trust:
                logging.info(
                    "Dynamic Constitution: No trust scores available for calibration."
                )
                return

            trust_scores = json.loads(raw_trust)

            # Fetch current weights or initialize
            current_weights = {}
            for agent in agents_module.ALL_AGENTS:
                agent_name = agent.__class__.__name__
                current_weights[agent_name] = agent.weight  # dynamic property read

            # Apply trust shifts (e.g. 1 point of trust = +1.0 weight shift)
            new_weights = {}
            for agent in agents_module.ALL_AGENTS:
                agent_name = agent.__class__.__name__
                current = current_weights.get(agent_name, agent.default_weight)
                trust_delta = trust_scores.get(agent_name, 0.0)

                # Calculate new, clamp exactly to bounds
                shifted = float(current) + float(trust_delta)
                clamped = max(agent.min_weight, min(agent.max_weight, shifted))
                new_weights[agent_name] = clamped

            # Persist to Redis Hash Map 'agent_weights_v2'
            for agent_name, weight_val in new_weights.items():
                r.hset("agent_weights_v2", agent_name, str(weight_val))

            # Reset trust scores after successful calibration
            r.set("agent_trust_scores", json.dumps({}))
            logging.info(
                f"Dynamic Constitution: Scaled {len(new_weights)} agent weights based on daily attribution."
            )
        except Exception as e:
            logging.error(
                f"Dynamic Constitution calibration failed: {e}", exc_info=True
            )

    async def _run_gemini_query(
        self, prompt: str, shutdown_event: threading.Event
    ) -> Tuple[Optional[Dict], Optional[str]]:
        response_text, retries, delay = None, 3, 3
        for i in range(retries):
            if shutdown_event.is_set():
                return None, "Aborted"
            try:
                _model = self.gemini_model or get_llm_provider()
                response_text = await asyncio.to_thread(_model.generate_content, prompt)
                if not response_text:
                    raise Exception("Empty response from Gemini")

                json_str = response_text.strip()
                start, end = json_str.find("{"), json_str.rfind("}") + 1
                if start != -1 and end != -1:
                    return json.loads(json_str[start:end]), None
                else:
                    raise ValueError("No valid JSON in Gemini response.")

            except Exception as e:
                if i < retries - 1:
                    logging.warning(
                        "Gemini learning query fail (try %d): %s. Retrying...", i + 1, e
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    logging.error("Gemini learning query failed after retries.")
                    return None, str(e)
        return None, "Unknown error after retries."
