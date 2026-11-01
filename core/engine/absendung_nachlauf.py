# core/engine/absendung_nachlauf.py
# ARC-E6 G-1b (#3821) — Nachlauf der Absendung je Mandant, ausgezogen aus
# OrderExecutorMixin._execute_tenant_order (core/engine/order_executor.py).
"""Die Schritte der Absendung je Mandant, die NACH dem Broker laufen.

Der Dirigent ``OrderExecutorMixin._execute_tenant_order`` bleibt in
``order_executor.py``, ebenso jeder Schritt bis einschliesslich Absendung, Polling und
Storno. Hierher gezogen sind nur die fuenf Schritte des Nachlaufs: Endzustand erfassen,
SELL und BUY nachbuchen, die Ausfuehrung melden und die Fehlerbehandlung. Keiner von
ihnen ruft eine mutierende Broker-Methode, liest ``getattr(config, ...)`` oder die
Wanduhr — die eingefrorenen Vertragszeilen des Executors bleiben damit unberuehrt.

Warum ueberhaupt ein zweites Modul: Eine Extraktion in dieselbe Datei verlaengert sie
(Signaturen, Docstrings, Zustandszugriffe; gemessen +129 Zeilen), die Dateizahl im
Vertrag darf aber nur sinken (``vertrag.toml``, G-1a-Waechter in
``test_3819_uebergabe_schritte.py``).

Die Ruempfe sind wortgleich mit dem Stand im Executor, mit derselben angemeldeten
Abbildung wie in G-1a: Namen, die Tests am Modul ``order_executor`` neu binden, lesen
die Ruempfe als ``order_executor.<name>`` — sonst griffe der Patch ins Leere. Das sind
``RedisClient``, ``persist_pm_state_to_redis``, ``_audit_skipped_signal``,
``erfasse_order_endzustand``, ``_safe_bump_exec``, ``_safe_publish``, ``logging`` und
``SKIP_REASON_EXECUTION_ERROR``.

Die Importrichtung ist erzwungen: Dieses Modul importiert den Executor, nie umgekehrt.
Zusammengesetzt wird in ``BotEngine`` (``base.py``).

Plan: ``docs/3821-*/implementation_plan.md``.
"""

import asyncio
import json

from alpaca.common.exceptions import APIError

from core.composition.root import CompositionRoot

from . import order_executor
from .order_executor import _Absendung


class AbsendungNachlaufMixin:
    """Nachlauf-Schritte von ``_execute_tenant_order``; in ``BotEngine`` zusammengesetzt."""

    async def _schritt_mandant_endzustand(self, st: _Absendung) -> None:
        """Der wahre Endzustand der Order in Pruefkette und Analyse (reine Beobachtung)."""
        context = st.context
        # #2783 Inkrement 2: der wahre Endzustand in beide Ebenen. Hier — und nur
        # hier — ist er bekannt: `live_order` traegt das Ergebnis des Pollings,
        # `order` die Broker-Antwort auf das Absenden.
        #
        # REINE BEOBACHTUNG. Die Funktion wirft nie (jede Senke einzeln gekapselt),
        # denn an dieser Stelle liegt die Order bereits beim Broker: Ein Abbruch
        # NACH der Kapitalbewegung waere deutlich schlimmer als ein fehlender
        # Datensatz.
        await order_executor.erfasse_order_endzustand(
            order=st.live_order if st.live_order is not None else st.order,
            symbol=st.symbol,
            action=st.action,
            decision_id=getattr(context, "decision_id", "") if context else "",
            # `abs(size) * curr` — dasselbe Muster, mit dem der Bestand den
            # Nominalwert an dieser Stelle bildet (:1605). `price` ist hier NICHT
            # gebunden; mein erster Entwurf benutzte es und flake8 hat es gefangen.
            order_value=abs(float(st.size or 0.0)) * float(st.curr or 0.0),
            strategy_name=getattr(self, "strategy_name", "") or "",
        )

    async def _schritt_mandant_verkauf_nachbuchen(self, st: _Absendung) -> None:
        """SELL: Portfolio nachbuchen; scheitert das, entscheidet der Broker-Bestand."""
        user_id, symbol, client = st.user_id, st.symbol, st.client
        equity, redis_client = st.equity, st.redis_client
        try:
            pm = self._get_tenant_portfolio_manager(user_id, client, equity)
            pm.record_trade(symbol, "sell")
            await order_executor.persist_pm_state_to_redis(pm, symbol, redis_client)
            pm.clear_sell_signals_after_sale(symbol)
        except Exception as e:
            # ADR-ENG-07: Post-SELL portfolio state failure is ERROR-level.
            # The broker order was submitted but internal state may be inconsistent.
            # Risk: Ghost Position — duplicate SELL on next cycle.
            # Remediation: Hard-sync against Alpaca to determine ground truth.
            order_executor.logging.error(
                "[User %s] CRITICAL: Post-SELL portfolio state update failed for %s: %s"
                " — initiating hard-sync against broker to prevent ghost position.",
                user_id,
                symbol,
                e,
            )
            try:
                # Broker is source of truth: if position no longer exists,
                # force-clear local state to prevent duplicate SELL next cycle.
                # NB: the return value is intentionally discarded — the SIGNAL is
                # the exception path below (APIError 404 = position gone = SELL
                # landed). A normal return means the position is still open, which
                # the lines beneath this call treat as a genuine inconsistency.
                await asyncio.to_thread(client.get_open_position, symbol)
                # Position still open at broker → state is genuinely inconsistent.
                order_executor.logging.error(
                    "[User %s] Hard-sync: %s position STILL OPEN at broker after SELL."
                    " Manual intervention required.",
                    user_id,
                    symbol,
                )
            except APIError as api_err:
                # ADR-ENG-07 / POLICY-01: ONLY a 404 (position-not-found)
                # or Alpaca error code 40410000 confirms the SELL landed.
                # A 429 Rate-Limit or 504 Gateway Timeout MUST NOT clear state —
                # that would create a Ghost Position on the next cycle.
                #
                # APIError.status_code → http_error.response.status_code (read-only)
                # APIError.code        → json.loads(self._error)["code"] (read-only)
                status_code = api_err.status_code  # None if no http_error
                try:
                    error_code = api_err.code  # raises if _error is not valid JSON
                except Exception:
                    error_code = None
                if status_code == 404 or error_code == 40410000:
                    order_executor.logging.warning(
                        "[User %s] Hard-sync: %s confirmed SOLD at broker "
                        "(APIError 404/40410000). Force-clearing local portfolio state.",
                        user_id,
                        symbol,
                    )
                    try:
                        pm = self._get_tenant_portfolio_manager(user_id, client, equity)
                        pm.record_trade(symbol, "sell")
                        await order_executor.persist_pm_state_to_redis(
                            pm, symbol, redis_client
                        )
                        pm.clear_sell_signals_after_sale(symbol)
                    except Exception as force_e:
                        order_executor.logging.error(
                            "[User %s] Force-clear also failed for %s: %s"
                            " — state remains inconsistent, alerting via PubSub.",
                            user_id,
                            symbol,
                            force_e,
                        )
                else:
                    # Other API errors (429 Rate-Limit, 5xx server error) MUST NOT
                    # clear local state — position may still be open at broker.
                    order_executor.logging.error(
                        "[User %s] Hard-sync check for %s returned unexpected APIError "
                        "(status=%s code=%s) — NOT treating as confirmed SELL.",
                        user_id,
                        symbol,
                        status_code,
                        error_code,
                    )
                    raise api_err

            try:
                _redis = await order_executor.RedisClient.get_redis()
                await order_executor._safe_publish(
                    _redis,
                    f"explainability:{user_id}",
                    json.dumps(
                        {
                            "type": "state_inconsistency",
                            "title": f"Portfolio State Error: {symbol}",
                            "message": f"SELL executed but post-trade bookkeeping failed: {e}",
                            "timestamp": CompositionRoot.get_instance()
                            .clock_port.now()
                            .isoformat(),
                        }
                    ),
                )
            except Exception as redis_err:
                order_executor.logging.warning(
                    "[User %s] Failed to publish state_inconsistency alert "
                    "to Redis for %s: %s",
                    user_id,
                    symbol,
                    redis_err,
                )

    async def _schritt_mandant_kauf_nachbuchen(self, st: _Absendung) -> None:
        """BUY: Portfolio nachbuchen und die Konviktion der Position fortschreiben."""
        user_id, symbol, context = st.user_id, st.symbol, st.context
        try:
            pm = self._get_tenant_portfolio_manager(user_id, st.client, st.equity)
            pm.record_trade(symbol, "buy")
            await order_executor.persist_pm_state_to_redis(pm, symbol, st.redis_client)
            conv = getattr(context, "conviction_score", 0.5) if context else 0.5
            pm.update_position_conviction(symbol, conv)
        except Exception as e:
            order_executor.logging.warning(
                "[User %s] Post-trade conviction update failed for %s: %s",
                user_id,
                symbol,
                e,
            )

    async def _schritt_mandant_melden(self, st: _Absendung) -> None:
        """Die Ausfuehrung loggen und auf dem Erklaerungs-Kanal veroeffentlichen."""
        user_id, action, symbol, size = st.user_id, st.action, st.symbol, st.size
        order_executor.logging.info(
            f"[User {user_id}] ✅ Executed {action} {size} shares for {symbol}. OrderID: {st.order.id}"
        )
        try:
            redis = await order_executor.RedisClient.get_redis()
            await order_executor._safe_publish(
                redis,
                f"explainability:{user_id}",
                json.dumps(
                    {
                        "type": "trade_executed",
                        "title": f"{action} Order Executed: {symbol}",
                        "message": f"Successfully routed {size} shares to your broker.",
                        "timestamp": CompositionRoot.get_instance()
                        .clock_port.now()
                        .isoformat(),
                    }
                ),
            )
        except Exception as redis_err:
            order_executor.logging.warning(
                "[User %s] Failed to publish trade_executed event to Redis: %s",
                user_id,
                redis_err,
            )

    async def _schritt_mandant_fehler(self, st: _Absendung, e: Exception) -> None:
        """Ein Fehler der Absendung: zaehlen, auditieren (vor dem Broker), melden."""
        user_id, action, symbol = st.user_id, st.action, st.symbol
        # PR A.2: fail-safe — count as a submit failure ONLY when the live
        # broker submit itself was reached (pre-submit guards return earlier and
        # never set the flag). Bump is first + wrapped so it cannot mask the log.
        if st.submit_attempted:
            order_executor._safe_bump_exec("submit_fail")
        order_executor.logging.error(
            f"[User {user_id}] ❌ Order Execution Failed for {symbol}: {e}"
        )
        # #2585: an approved signal that never reached the broker died in this
        # swallower with an ERROR log only — no audit-chain record. Audit it.
        # order_submitted=True means the order DID reach the market (post-trade
        # bookkeeping failed) — that is not a skip, so no entry then.
        if not st.order_submitted:
            await order_executor._audit_skipped_signal(
                symbol, action, order_executor.SKIP_REASON_EXECUTION_ERROR, str(e)
            )
        try:
            redis = await order_executor.RedisClient.get_redis()
            if st.order_submitted:
                await order_executor._safe_publish(
                    redis,
                    f"explainability:{user_id}",
                    json.dumps(
                        {
                            "type": "state_inconsistency",
                            "severity": "CRITICAL",
                            "title": f"API Error: {symbol}",
                            "message": f"Broker API rejected order: {e}",
                            "timestamp": CompositionRoot.get_instance()
                            .clock_port.now()
                            .isoformat(),
                        }
                    ),
                )
            else:
                await order_executor._safe_publish(
                    redis,
                    f"explainability:{user_id}",
                    json.dumps(
                        {
                            "type": "trade_rejected",
                            "title": f"API Error: {symbol}",
                            "message": f"Broker API rejected order: {e}",
                            "timestamp": CompositionRoot.get_instance()
                            .clock_port.now()
                            .isoformat(),
                        }
                    ),
                )
        except Exception as redis_err:
            order_executor.logging.warning(
                "[User %s] Failed to publish trade_rejected event to Redis: %s",
                user_id,
                redis_err,
            )
