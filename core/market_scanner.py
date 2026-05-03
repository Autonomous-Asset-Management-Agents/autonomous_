# core/market_scanner.py
# Epic 1.7 / PR-A — Extracted from ai_components.py
# Contains: AIMarketScanner (async market scan, Gemini-assisted strategy recommendation).

import asyncio
import json
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from config import (
    DEFAULT_SYMBOLS,
    ENABLE_GEMINI_IN_SIMULATION,
    GEMINI_MAX_RETRIES,
)
from core import strategies
from core.data_provider import HistoricalDataProvider
from core.gemini_client import get_gemini_instance
from core.news_processor import NewsProcessor
from core.utils import BackendSignals, ta

# Max concurrent API calls during market scan to avoid connection pool exhaustion
SCAN_CONCURRENCY = 20


class AIMarketScanner:
    def __init__(
        self,
        signals: BackendSignals,
        data_provider: HistoricalDataProvider,
        news_processor: NewsProcessor,
        shutdown_event: threading.Event,
    ):
        self.signals = signals
        self.data_provider = data_provider
        self.news_processor = news_processor
        self.shutdown_event = shutdown_event
        self.running = False
        self.gemini_model = None  # resolved lazily via get_gemini_instance()
        self._is_simulation = False
        self._active_tasks: set = set()
        # Semaphore is created lazily per-event-loop to avoid cross-loop conflicts
        # when the simulation runs in a new thread with its own loop.
        self._scan_semaphore: Optional[asyncio.Semaphore] = None
        self._scan_semaphore_loop: Optional[asyncio.AbstractEventLoop] = None
        logging.info("AI Market Scanner: initialized (Gemini resolved on first scan.).")

    def set_simulation_mode(self, is_simulation: bool):
        self._is_simulation = is_simulation
        if hasattr(self.news_processor, "_is_simulation"):
            self.news_processor._is_simulation = is_simulation

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Return a Semaphore bound to the current running event loop.

        Creates a fresh Semaphore when first called or when the event loop
        has changed (e.g. simulation thread with asyncio.new_event_loop()).
        """
        loop = asyncio.get_running_loop()
        if self._scan_semaphore is None or self._scan_semaphore_loop is not loop:
            self._scan_semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)
            self._scan_semaphore_loop = loop
        return self._scan_semaphore

    def _is_valid_dataframe(self, df) -> bool:
        return df is not None and isinstance(df, pd.DataFrame) and not df.empty

    def _calculate_score(
        self, symbol: str, current_date: datetime, sim_client: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Calculates basic indicators (RSI, ADX, Volatility) for the scanner."""
        data = None
        if sim_client:
            data = sim_client.get_bars(symbol, "1d", limit=300)
        else:
            data = self.data_provider.get_data(symbol, current_date, days=300)

        if not self._is_valid_dataframe(data) or len(data) < 51:
            logging.debug(
                "[%s] Score calc failed: Insufficient data length (%d < 51).",
                symbol,
                len(data) if data is not None else 0,
            )
            return {}

        req_cols = ["open", "high", "low", "close", "volume"]
        if not all(c in data.columns for c in req_cols):
            logging.debug(
                "[%s] Score calc failed: Missing columns. Have: %s",
                symbol,
                list(data.columns),
            )
            return {}

        data = data[req_cols].dropna()
        if len(data) < 51:
            logging.debug(
                "[%s] Score calc failed: Insufficient data after dropna (%d < 51).",
                symbol,
                len(data),
            )
            return {}

        scores: Dict[str, Any] = {"symbol": symbol}

        try:
            rsi = ta.rsi(data["close"], length=14)
            adx = ta.adx(data["high"], data["low"], data["close"], length=14)
            volatility = data["close"].pct_change().rolling(20).std()

            if rsi is not None:
                scores["rsi"] = rsi.iloc[-1]
            if adx is not None:
                adx_col = next(
                    (col for col in adx.columns if col.startswith("ADX_")), None
                )
                if adx_col:
                    scores["adx"] = adx[adx_col].iloc[-1]
            if volatility is not None:
                scores["volatility"] = volatility.iloc[-1]

            scores["Ranging_conf"] = 0.0
            scores["Trending_conf"] = 0.0
            scores["High Volatility_conf"] = scores.get("volatility", 0.0) * 10

        except Exception as e:
            logging.warning(
                "[%s] Indicator calculation failed: %s", symbol, e, exc_info=True
            )
            return {}

        logging.debug("[%s] Scan Results: %s", symbol, scores)
        return scores

    async def _calculate_score_async(
        self, symbol: str, current_date: datetime, sim_client: Optional[Any] = None
    ) -> Dict[str, Any]:
        async with self._get_semaphore():
            try:
                return await asyncio.to_thread(
                    self._calculate_score, symbol, current_date, sim_client
                )
            except Exception as e:
                logging.error("Error calc score async %s: %s", symbol, e)
                return {}

    async def scan_market(
        self,
        current_date: datetime,
        market_regime: Dict[str, Any],
        sim_client: Optional[Any] = None,
        live_symbols: Optional[List[str]] = None,
    ):
        if self.shutdown_event.is_set():
            return None
        self.running = True

        assets: List[str] = []
        if sim_client:
            assets = sim_client.available_symbols
        elif live_symbols:
            assets = live_symbols
        else:
            assets = self.data_provider.get_available_symbols()

        logging.info(
            "AI Scanner: Analyzing %d assets for %s...",
            len(assets),
            current_date.strftime("%Y-%m-%d"),
        )

        total_assets = len(assets)

        # Use asyncio.gather instead of create_task to avoid binding coroutines
        # to a specific event loop at scheduling time — safe across thread loops.
        coros = [
            self._calculate_score_async(symbol, current_date, sim_client)
            for symbol in assets
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        all_scores = [r for r in results if isinstance(r, dict) and r]

        if self.signals and not self.shutdown_event.is_set():
            self.signals.scanner_progress.emit(len(all_scores), total_assets)

        if self.shutdown_event.is_set():
            logging.info("Scanner finishing early after score calculation.")
            self.running = False
            return None

        recommended_strategy = "RLAgent"
        recommendation_confidence = "high"

        use_gemini = (
            all_scores
            and (not self._is_simulation or ENABLE_GEMINI_IN_SIMULATION)
            and get_gemini_instance() is not None
        )
        gemini = get_gemini_instance() if use_gemini else None

        top_stocks: List[Dict[str, Any]] = []

        if not use_gemini:
            logging.warning(
                "AI Scanner: Using fallback (Gemini unavailable or simulation mode)."
            )
            candidates = [s for s in all_scores if s.get("volatility", 0) > 0.01]
            top_stocks = sorted(
                candidates, key=lambda x: x.get("volatility", 0), reverse=True
            )[:10]

        else:
            logging.info("AI Scanner: Using Gemini AI for analysis.")
            try:
                market_summary = "Market-Wide Signal Summary:\n"
                for s in all_scores[:20]:
                    market_summary += (
                        f"- {s.get('symbol')}: RSI={s.get('rsi', 'N/A'):.1f}, "
                        f"ADX={s.get('adx', 'N/A'):.1f}, Vol={s.get('volatility', 'N/A'):.4f}\n"
                    )

                prompt = self._build_gemini_scanner_prompt(
                    market_regime, market_summary
                )

                response_text, retries, delay = None, GEMINI_MAX_RETRIES, 2
                for i in range(retries):
                    if self.shutdown_event.is_set():
                        break
                    try:
                        response_text = await asyncio.to_thread(
                            gemini.generate_content, prompt
                        )
                        if response_text:
                            break
                    except Exception as e:
                        if i < retries - 1:
                            logging.warning(
                                "Gemini scan fail (try %d): %s. Retrying...", i + 1, e
                            )
                            await asyncio.sleep(delay)
                            delay *= 2
                        else:
                            logging.error("Gemini scan failed after retries.")
                            raise e

                if self.shutdown_event.is_set():
                    logging.info("Scanner interrupted during Gemini call.")
                    self.running = False
                    return None
                if response_text is None:
                    raise Exception("Gemini response None.")

                json_str = response_text.strip()
                start, end = json_str.find("{"), json_str.rfind("}") + 1
                if start != -1 and end != 0:
                    ai_rec = json.loads(json_str[start:end])
                    recommended_strategy = "RLAgent"
                    recommendation_confidence = ai_rec.get("confidence", "medium")

                    candidates = [
                        s for s in all_scores if s.get("volatility", 0) > 0.01
                    ]
                    top_stocks = sorted(
                        candidates, key=lambda x: x.get("volatility", 0), reverse=True
                    )[:10]
                else:
                    raise ValueError("No JSON found in Gemini response")
            except Exception as e:
                logging.error(
                    "Gemini scan failed: %s. Using fallback.", e, exc_info=True
                )
                candidates = [s for s in all_scores if s.get("volatility", 0) > 0.01]
                top_stocks = sorted(
                    candidates, key=lambda x: x.get("volatility", 0), reverse=True
                )[:10]

        if not top_stocks:
            logging.warning("Scanner: No stocks identified. Using default symbols.")
            top_stocks = [{"symbol": s} for s in DEFAULT_SYMBOLS[:10]]

        if not self.shutdown_event.is_set():
            adapted_top_stocks = [
                {
                    "symbol": s_data["symbol"],
                    "trending_score": s_data.get("adx", 0.0),
                    "ranging_score": s_data.get("rsi", 0.0),
                    "breakout_score": s_data.get("volatility", 0.0),
                    "rsi": s_data.get("rsi", 50.0),
                }
                for s_data in top_stocks
            ]

            result = {
                "date": current_date,
                "recommended_strategy": recommended_strategy,
                "recommendation_confidence": recommendation_confidence,
                "top_stocks": adapted_top_stocks,
                "market_regime": market_regime,
            }
            if self.signals:
                self.signals.scanner_complete.emit(adapted_top_stocks)

            logging.info(
                "Scanner Complete: Strategy='%s', Top Stocks: %s",
                recommended_strategy,
                [s["symbol"] for s in top_stocks],
            )
            self.running = False
            return result

        self.running = False
        return None

    def _build_gemini_scanner_prompt(
        self, market_regime: Dict[str, Any], market_summary: str
    ) -> str:
        strategy_list = json.dumps(list(strategies.STRATEGY_CLASSES.keys()))
        regime = market_regime.get("regime", "Ranging")
        confidence = market_regime.get("confidence", 0.5)

        return (
            "You are an expert quantitative trading analyst.\n"
            "The bot's primary strategy is a trained Reinforcement Learning agent ('RLAgent').\n"
            "Your task is to confirm if this is the correct strategy or if a manual override is needed.\n\n"
            "Here is the data:\n"
            f'1.  **Market Regime:** "{regime}" (Confidence: {confidence:.2f})\n'
            f"2.  **Available Strategies:** {strategy_list}\n\n"
            "**CRITICAL INSTRUCTIONS:**\n"
            '1.  99% of the time, the "RLAgent" is the correct choice.\n'
            '2.  Only if the Market Regime is "Extreme Panic" or "Black Swan" (VIX > 50) should you recommend "HOLD_ALL".\n'
            '3.  Assign a "confidence" (high, medium, low).\n'
            "4.  Provide a brief reason for your choice.\n\n"
            "Respond ONLY with valid JSON in this exact format:\n"
            '{"recommended_strategy": "RLAgent", "confidence": "high", "reason": "Reason..."}'
        )
