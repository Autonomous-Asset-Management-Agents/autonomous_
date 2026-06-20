# Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# core/news_processor.py
# Epic 1.7 / PR-A — Extracted from ai_components.py
# Contains: NewsProcessor (sentiment analysis via Gemini + Polygon news fetch).

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import requests

from config import (
    ENABLE_GEMINI_IN_SIMULATION,
    GEMINI_MAX_RETRIES,
    GEMINI_RATE_LIMIT_DELAY,
    POLYGON_API_KEY,
)
from core.llm.provider import get_llm_provider


class NewsProcessor:
    """Handles news fetching and AI-powered sentiment analysis."""

    def __init__(self):
        self.api_token = POLYGON_API_KEY
        self.sentiment_model = None  # resolved lazily via get_llm_provider()
        self.rate_limit_lock = threading.Lock()
        self.last_api_call = 0
        self.min_delay = GEMINI_RATE_LIMIT_DELAY
        self._is_simulation = False
        self.sentiment_cache: Dict[str, dict] = {}
        logging.info("News Processor: initialized (Gemini resolved on first use).")

    def _clear_cache_if_large(self):
        if len(self.sentiment_cache) > 2000:
            logging.info("Clearing news sentiment cache (over 2000 items).")
            self.sentiment_cache.clear()

    def analyze_sentiments_batch(self, headlines: List[str]) -> Dict[str, dict]:
        self._clear_cache_if_large()

        headlines_to_fetch = list(
            {h for h in headlines if h not in self.sentiment_cache}
        )
        results_map = {
            h: self.sentiment_cache[h] for h in headlines if h in self.sentiment_cache
        }

        if not headlines_to_fetch:
            return results_map

        if self._is_simulation and not ENABLE_GEMINI_IN_SIMULATION:
            sim_result = {
                "sentiment": "neutral",
                "score": 0.0,
                "reason": "Simulation mode - Gemini disabled",
            }
            for h in headlines_to_fetch:
                results_map[h] = sim_result
            return results_map

        _model = self.sentiment_model or get_llm_provider()
        if not _model:
            neut_result = {
                "sentiment": "neutral",
                "score": 0.0,
                "reason": "AI model unavailable.",
            }
            for h in headlines_to_fetch:
                results_map[h] = neut_result
            return results_map

        default_result = {
            "sentiment": "neutral",
            "score": 0.0,
            "reason": "AI analysis failed.",
        }

        batch_size = 50

        for i in range(0, len(headlines_to_fetch), batch_size):
            batch_headlines = headlines_to_fetch[i : i + batch_size]
            logging.info(
                "Analyzing headline batch %d/%d...",
                i // batch_size + 1,
                (len(headlines_to_fetch) // batch_size) + 1,
            )

            with self.rate_limit_lock:
                elapsed = time.time() - self.last_api_call
                if elapsed < self.min_delay:
                    time.sleep(self.min_delay - elapsed)
                self.last_api_call = time.time()

            headlines_json_list = json.dumps(batch_headlines)
            prompt = (
                f"Analyze the sentiment for each headline in this list.\n"
                f"Headline List: {headlines_json_list}\n\n"
                "Respond ONLY with a valid JSON list, where each object corresponds to a headline:\n"
                '[\n  {"headline": "...", "sentiment": "positive|negative|neutral", "score": float, "reason": "..."},\n  ...\n]'
            )

            try:
                last_exception = None
                for attempt in range(GEMINI_MAX_RETRIES):
                    try:
                        _model = self.sentiment_model or get_llm_provider()
                        response_text = _model.generate_content(prompt)

                        if not response_text:
                            raise Exception("Empty response from Gemini")

                        json_str = (
                            response_text.strip()
                            .replace("```json", "")
                            .replace("```", "")
                        )
                        start, end = json_str.find("["), json_str.rfind("]") + 1
                        if start == -1 or end == 0:
                            raise ValueError("No JSON list found in Gemini response.")

                        batch_data = json.loads(json_str[start:end])
                        api_results_map = {
                            item.get("headline"): item for item in batch_data
                        }

                        for h in batch_headlines:
                            if h in api_results_map:
                                item = api_results_map[h]
                                sentiment = item.get("sentiment", "neutral").lower()
                                score = max(
                                    -1.0, min(1.0, float(item.get("score", 0.0)))
                                )
                                reason = item.get("reason", "N/A")
                                valid_result = {
                                    "sentiment": sentiment,
                                    "score": score,
                                    "reason": reason,
                                }
                                results_map[h] = valid_result
                                self.sentiment_cache[h] = valid_result
                            else:
                                logging.warning(
                                    "Gemini batch response missing headline: %s", h
                                )
                                results_map[h] = default_result

                        last_exception = None
                        break

                    except Exception as e:
                        last_exception = e
                        if attempt < GEMINI_MAX_RETRIES - 1:
                            delay = self.min_delay * (2**attempt)
                            logging.warning(
                                "Gemini batch sentiment failed (attempt %d): %s. Retrying in %ss...",
                                attempt + 1,
                                e,
                                delay,
                            )
                            time.sleep(delay)
                        else:
                            logging.error(
                                "Gemini batch sentiment failed after %d attempts: %s",
                                GEMINI_MAX_RETRIES,
                                e,
                            )

                if last_exception:
                    with self.rate_limit_lock:
                        self.last_api_call = time.time()
                    raise last_exception

            except Exception as e:
                logging.error(
                    "Failed AI batch sentiment: %s. Defaulting %d headlines.",
                    e,
                    len(batch_headlines),
                )
                for h in batch_headlines:
                    results_map[h] = default_result

        return results_map

    def analyze_sentiment(self, headline: str) -> dict:
        if not headline:
            return {"sentiment": "neutral", "score": 0.0, "reason": "Headline empty."}
        batch_result = self.analyze_sentiments_batch([headline])
        return batch_result.get(
            headline,
            {"sentiment": "neutral", "score": 0.0, "reason": "Analysis failed."},
        )

    def get_historical_news(
        self, symbols: List[str], start_date: datetime, end_date: datetime
    ) -> List[dict]:
        """Gets historical news, handling pagination for long date ranges."""
        if not self.api_token or not symbols:
            return []

        all_news, seen_ids, articles_to_process = [], set(), []
        symbol_str = ",".join(symbols)
        current_end_date = end_date

        logging.info(
            "Fetching historical news for %s from %s to %s...",
            symbols,
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
        )

        while current_end_date > start_date:
            if (
                self.sentiment_model is None and get_llm_provider() is None
            ) and not ENABLE_GEMINI_IN_SIMULATION:
                logging.warning("Gemini disabled, stopping historical news fetch.")
                break

            try:
                end_str = current_end_date.strftime("%Y-%m-%d")
                start_str = (current_end_date - timedelta(days=90)).strftime("%Y-%m-%d")
                if pd.to_datetime(start_str) < start_date:
                    start_str = start_date.strftime("%Y-%m-%d")

                logging.info(
                    "NewsProcessor: Fetching chunk %s to %s...", start_str, end_str
                )

                url = (
                    f"https://api.polygon.io/v2/reference/news"
                    f"?published_utc.gte={start_str}"
                    f"&published_utc.lte={end_str}"
                    f"&ticker.any_of={symbol_str}"
                    f"&limit=1000&order=desc&apiKey={self.api_token}"
                )

                time.sleep(13)  # Polygon rate limit: 5 calls/min

                response = requests.get(url, timeout=45)
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])

                for article in results:
                    article_id = article.get("id")
                    headline = article.get("title")
                    if (
                        article_id
                        and article.get("published_utc")
                        and headline
                        and article_id not in seen_ids
                    ):
                        articles_to_process.append(article)
                        seen_ids.add(article_id)

                current_end_date = pd.to_datetime(start_str) - timedelta(days=1)

                if pd.to_datetime(start_str) <= start_date:
                    break

            except requests.exceptions.HTTPError as e:
                logging.error("HTTP Error fetching hist news chunk: %s", e)
                if e.response.status_code == 429:
                    logging.warning("Rate limit hit, sleeping for 60s...")
                    time.sleep(60)
                else:
                    break
            except Exception as e:
                logging.error("Error fetching hist news chunk: %s", e, exc_info=False)
                break

        if not articles_to_process:
            logging.warning("No historical news articles found.")
            return []

        logging.info(
            "Analyzing sentiment for %d historical articles...",
            len(articles_to_process),
        )
        headlines = [a["title"] for a in articles_to_process]
        sentiment_map = self.analyze_sentiments_batch(headlines)
        logging.info("Batch sentiment analysis complete.")

        for article in articles_to_process:
            try:
                headline = article["title"]
                sentiment_data = sentiment_map.get(
                    headline,
                    {"sentiment": "neutral", "score": 0.0, "reason": "N/A"},
                )
                dt = datetime.fromisoformat(
                    article["published_utc"].replace("Z", "+00:00")
                )
                processed = {
                    "created_at": dt,
                    "headline": headline,
                    "snippet": article.get("description", ""),
                    "symbols": article.get("tickers", []),
                    "sentiment": sentiment_data.get("sentiment", "neutral"),
                    "sentiment_score": sentiment_data.get("score", 0.0),
                }
                all_news.append(processed)
            except Exception as e:
                logging.warning("Error processing article %s: %s", article.get("id"), e)

        logging.info("Total historical news processed: %d", len(all_news))
        return all_news

    def set_simulation_mode(self, is_simulation: bool):
        self._is_simulation = is_simulation
