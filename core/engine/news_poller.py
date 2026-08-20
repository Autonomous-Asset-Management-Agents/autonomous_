# core/engine/news_poller.py
# Epic 1.7 / PR-C — Extrahiert aus core/engine.py
# Verantwortlichkeit: Polygon News Polling, Proactive-Rules-Check

import logging
import time
from datetime import datetime, timedelta, timezone

import requests as http_requests
from alpaca.common.exceptions import APIError

import config
from core.engine.equity_fallback import resolve_equity


class NewsPollerMixin:
    """
    Mixin für BotEngine: Polygon.io News-Polling und proaktive Trading-Signale.
    """

    def _start_alpaca_news_polling(self):
        import threading

        if not config.POLYGON_API_KEY:
            logging.warning("Polygon Key missing. News polling disabled.")
            return
        self.news_running.set()
        self.news_thread = threading.Thread(
            target=self._news_polling_loop,
            args=(self._shutdown_event,),
            daemon=True,
            name="NewsThread",
        )
        self.news_thread.start()

    def _check_proactive_rules(self, article: dict):  # noqa: C901
        if self.is_simulation:
            return
        active_rules = self.ai_rules.get_rules()
        # BUG-AI-S01 (#1232): never size off a hardcoded fictional equity.
        current_equity = resolve_equity(self.api, config.get_config().DEFAULT_EQUITY)

        for rule in active_rules:
            if rule.get("action") == "proactive_signal":
                trigger = rule.get("trigger", {})
                reason = rule.get("reason", "Proactive Buy")
                keywords = [k.lower() for k in trigger.get("headline_keywords", [])]
                sentiment_gt = float(trigger.get("sentiment_gt", 1.0))
                signal_ticker = trigger.get("signal_ticker")

                if signal_ticker not in article.get("symbols", []):
                    continue
                if article.get("score", 0.0) < sentiment_gt:
                    continue
                headline_lower = article.get("headline", "").lower()
                if not any(k in headline_lower for k in keywords):
                    continue

                self._log_strategy_thought(
                    f"🔥 PROACTIVE SIGNAL: {reason} for {signal_ticker}!"
                )
                try:
                    snapshot = self.api.get_snapshot(signal_ticker)
                    current_price = snapshot.latest_trade.p
                except Exception:
                    continue
                if current_price <= 0:
                    continue

                capital_to_risk = current_equity * 0.01
                dollar_risk_per_share = current_price * 0.05 * 3.0
                if dollar_risk_per_share <= 0:
                    dollar_risk_per_share = 0.01
                trade_qty = capital_to_risk / dollar_risk_per_share
                max_position_value = current_equity * 0.10
                max_shares_by_capital = max_position_value / current_price
                min_shares = 1.0 / current_price
                final_qty = round(
                    min(max(min_shares, trade_qty), max_shares_by_capital), 6
                )

                try:
                    existing_position = self.api.get_open_position(signal_ticker)
                    if existing_position:
                        continue
                except Exception as e:
                    is_404 = False
                    if isinstance(e, APIError) and (
                        getattr(e, "status_code", None) == 404
                        or getattr(e, "code", None) == 40410000
                    ):
                        is_404 = True
                    if not is_404:
                        logging.warning(
                            "NewsPoller position check failed for %s: %s",
                            signal_ticker,
                            e,
                        )
                        continue

                logging.warning(
                    "PROACTIVE BUY %s shares of %s", final_qty, signal_ticker
                )

                if self.compliance_guardian:
                    proactive_order = {
                        "symbol": signal_ticker,
                        "side": "buy",
                        "quantity": final_qty,
                        "price": current_price,
                        "strategy_id": "proactive_news_signal",
                        "timestamp": time.time(),
                    }
                    if not self.compliance_guardian.check_order(proactive_order):
                        logging.warning(
                            f"PROACTIVE BUY for {signal_ticker} BLOCKED by ComplianceGuardian."
                        )
                        break
                    if not self.compliance_guardian.check_trade(proactive_order):
                        logging.warning(
                            f"PROACTIVE BUY for {signal_ticker} BLOCKED (daily trade limit)."
                        )
                        break
                # 🚨 OSS-Hardening: News-Poller bypassed the LangGraph router and Iron Dome
                # by talking to the broker directly. This is a severe compliance violation.
                logging.warning(
                    "OSS-COMPLIANCE: News-triggered order for %s suppressed. "
                    "Direct broker calls from NewsPoller are disabled in OSS mode. "
                    "Signal discarded — LangGraph Round Table handles market analysis independently.",
                    signal_ticker,
                )
                break

    def _news_polling_loop(self, shutdown_event):
        logging.info("News polling loop started.")
        last_fetch_time = datetime.now(timezone.utc) - timedelta(minutes=15)
        while self.news_running.is_set() and not shutdown_event.is_set():
            try:
                now_utc = datetime.now(timezone.utc)
                start_str = (last_fetch_time + timedelta(seconds=1)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                end_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                poly_key = config.get_secret_str(config.POLYGON_API_KEY)
                url = (
                    f"https://api.polygon.io/v2/reference/news?published_utc.gte={start_str}"
                    f"&published_utc.lte={end_str}&limit=50&apiKey={poly_key}"
                )
                response = http_requests.get(url, timeout=30)
                response.raise_for_status()
                self._interruptible_pause(
                    12
                )  # #1232: throttle, interruptible on shutdown
                data = response.json()
                results = data.get("results", [])
                if results:
                    processed = []
                    articles_to_process = [
                        a for a in reversed(results) if a.get("title")
                    ]
                    headlines_to_analyze = [a["title"] for a in articles_to_process]
                    if articles_to_process:
                        sentiment_map = self.news_processor.analyze_sentiments_batch(
                            headlines_to_analyze
                        )
                        for article in articles_to_process:
                            if shutdown_event.is_set():
                                break
                            headline = article["title"]
                            sentiment_data = sentiment_map.get(
                                headline, {"sentiment": "neut", "score": 0.0}
                            )
                            item = {
                                "timestamp": article["published_utc"],
                                "headline": headline,
                                "symbols": article.get("tickers", []),
                                "sentiment": sentiment_data.get("sentiment"),
                                "score": sentiment_data.get("score"),
                            }
                            processed.append(item)
                            if self.api:
                                self._check_proactive_rules(item)
                    if processed:
                        self._send_update_threadsafe(
                            "news_update", {"articles": processed}
                        )
                        self._recent_news_cache.extend(processed)
                        if len(self._recent_news_cache) > self._recent_news_cache_max:
                            self._recent_news_cache = self._recent_news_cache[
                                -self._recent_news_cache_max :
                            ]
                last_fetch_time = now_utc
            except Exception as e:
                logging.error("News loop error: %s", e)
            shutdown_event.wait(config.NEWS_POLLING_INTERVAL_SECONDS)
