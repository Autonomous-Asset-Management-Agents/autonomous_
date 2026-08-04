# core/engine/trading_loop.py
# Epic 1.4 / Issue #217 — LangGraph-Dispatch für Non-LSTM-Strategien
# Epic 1.7 / PR-C — Extrahiert aus core/engine.py
# Epic 2.3-Pre / PR-A — Graceful Handover via AgentRegistry
# Verantwortlichkeit: Live Trading Loop, Snapshot-Fetch, LangGraph-Task-Dispatch

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import pytz
from alpaca.common.exceptions import APIError
from alpaca.data.requests import StockSnapshotRequest

import config
from config import (  # ML-1: SIP = consolidated NBBO (MiFID II best-execution)
    BYPASS_MARKET_HOURS,
    get_config,
)
from core.cloud_logger import DecisionContext

# TODO(MOD-1): migrate BYPASS_MARKET_HOURS to RuntimeConfigState when that lands
from core.engine.loop_counters import _bump_loop_counter
from core.engine.portfolio_context import build_portfolio_context
from core.events import SignalEvent
from core.exceptions import BrokerConnectionError
from core.position_stop import plan_position_stops
from core.sim.clock import (  # #2548: SIM_MODE → sim time; else datetime.now (byte-identical)
    engine_now,
)

# Layer 2: Per-Symbol Evaluation Timeout (MiFID II Art. 17)
_SYMBOL_EVAL_TIMEOUT_SEC = 45.0


def _regime_conditioner_state_keys(market_data) -> dict:
    """#1949 (RTR-2): flag-gated "vix"/"regime" values for the SymbolEvalState seam.

    Both are now DECLARED LangGraph channels (core/orchestration/graph.py) because
    langgraph==1.0.10 silently drops undeclared input keys — the old top-level "vix"
    the loop emitted never reached any agent. To keep the dark ship byte-identical,
    the producer emits None for BOTH channels while REGIME_CONDITIONER_ENABLED is
    OFF (default): agents then observe exactly the old dropped-key behaviour
    (VIXAwareRiskAgent keeps abstaining). ON → the per-cycle MarketRegimeModel
    output (monitor_loop → current_market_data) reaches RegimeDetectionAgent (and
    VIXAwareRiskAgent — plan #1949 §12.5, documented joint consumption). Scalars
    only (§5.9). Invalid config fails safe to OFF.
    """
    try:
        # config.get_config() (not the from-import) — the same patchable seam the
        # finance-core helpers use (CODING_POLICY §2.10).
        enabled = bool(
            getattr(config.get_config(), "REGIME_CONDITIONER_ENABLED", False)
        )
    except (TypeError, ValueError):
        enabled = False
    if not enabled or not market_data:
        return {"vix": None, "regime": None}
    return {"vix": market_data.get("vix"), "regime": market_data.get("regime")}


try:
    from core.telemetry import get_tracer
except ImportError:  # pragma: no cover

    def get_tracer(name):  # type: ignore[misc]
        from contextlib import nullcontext

        class _Noop:
            def start_as_current_span(self, *a, **kw):
                return nullcontext()

        return _Noop()


try:
    from core.orchestration.graph import build_symbol_eval_graph
except ImportError:  # pragma: no cover
    build_symbol_eval_graph = None  # type: ignore[assignment]

try:
    from core.kill_switch import kill_switch
except ImportError:  # pragma: no cover
    kill_switch = None  # type: ignore[assignment]

# RTR-1 (#1948): FREE point-in-time news producer (Google-News-RSS). Flag-FIRST
# inside the producer — NEWS_SENTIMENT_NLP_ENABLED off (default) returns None
# without any network I/O, and no state key is added (byte-identical dormant path).
try:
    from core.nlp.headlines import produce_news_headlines
except ImportError:  # pragma: no cover
    produce_news_headlines = None  # type: ignore[assignment]


def _extract_ohlc_from_snapshot(snapshot_obj) -> tuple[dict, float]:
    """
    Extrahiert OHLC + Live-Preis aus einem Alpaca-Snapshot-Objekt.

    KRITISCH: Nutzt latest_trade.price als 'close' (live Intraday-Preis).
    daily_bar liefert die GESTRIGE EOD-Bar — sie darf NICHT als close verwendet
    werden, da sonst alle Zyklen identische stale Preise bekommen.

    Priorität:
        1. latest_trade.price → live 'close' (primär)
        2. daily_bar          → O/H/L-Referenz (gestern), H/L werden auf live Preis erweitert
        3. Fallback           → daily_bar.close wenn kein latest_trade vorhanden (Markt zu)
        4. Nullen             → wenn weder Bar noch Trade

    Returns:
        (ohlc_dict, price) — price == ohlc['close']
    """
    trade = getattr(snapshot_obj, "latest_trade", None)
    bar = getattr(snapshot_obj, "daily_bar", None)

    if trade is not None:
        # Live Intraday-Preis
        live_price = float(getattr(trade, "price", getattr(trade, "p", 0.0)))

        if bar is not None:
            bar_open = float(getattr(bar, "open", getattr(bar, "o", live_price)))
            bar_high = float(getattr(bar, "high", getattr(bar, "h", live_price)))
            bar_low = float(getattr(bar, "low", getattr(bar, "l", live_price)))
            bar_volume = float(getattr(bar, "volume", getattr(bar, "v", 0.0)))
            ohlc = {
                "open": bar_open,
                # H/L auf live Preis erweitern wenn er outside der EOD-Range liegt
                "high": max(bar_high, live_price),
                "low": min(bar_low, live_price),
                "close": live_price,  # ← LIVE PREIS (nicht gestern!)
                "volume": bar_volume,
            }
        else:
            # Kein daily_bar — nur latest_trade vorhanden
            vol = float(trade.size) if hasattr(trade, "size") else 0.0
            ohlc = {
                "open": live_price,
                "high": live_price,
                "low": live_price,
                "close": live_price,
                "volume": vol,
            }
        return ohlc, live_price

    elif bar is not None:
        # Kein live Trade (Markt geschlossen) → daily_bar als Fallback
        bar_close = float(getattr(bar, "close", getattr(bar, "c", 0.0)))
        ohlc = {
            "open": float(getattr(bar, "open", getattr(bar, "o", 0.0))),
            "high": float(getattr(bar, "high", getattr(bar, "h", 0.0))),
            "low": float(getattr(bar, "low", getattr(bar, "l", 0.0))),
            "close": bar_close,
            "volume": float(getattr(bar, "volume", getattr(bar, "v", 0.0))),
        }
        return ohlc, bar_close

    else:
        # Weder Trade noch Bar
        return {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "volume": 0.0}, 0.0


class TradingLoopMixin:
    """
    Mixin für BotEngine: async Live-Trading-Loop.
    Alle Methoden waren ursprünglich Teil von engine.py.
    """

    def run_strategy_async_wrapper(self):
        """Thread-Target: Erstellt asyncio-Event-Loop und führt live_trading_loop aus."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.live_trading_loop())
        except Exception as e:
            logging.error(f"Async wrapper exception: {e}", exc_info=True)
        finally:
            try:
                loop.close()
            except Exception as e:
                logging.error("Error closing async loop: %s", e)

    async def _startup_health_check(self) -> None:
        """
        Startzeit-Dependency-Check.
        No-op Basisimplementierung — wird in BotEngine mit Redis/Gemini Checks überschrieben.
        Direkte TradingLoopMixin-Instanzen (z.B. in Unit Tests) überspringen den Check.
        """

    async def _drain_hitl_approvals(self) -> int:
        """Execute every human-approved order waiting in the HITL queue (PR-0a-ii-5b, Art. 14).

        Called once per trading cycle (C3). Dormant unless HITL_ENABLED. Each approved payload
        is CLAIMED (approved→inflight) then executed via execute_approved_order, which BYPASSES
        the gate (N1) so an approved order is never re-queued, and acked once it reaches a
        definitive outcome. Crash-safe: a mid-execution crash leaves an inflight marker that is
        surfaced (not silently lost) next cycle. Best-effort: a bad payload is logged, never
        crashes the loop. Returns the count executed.
        """
        if not get_config().HITL_ENABLED:
            return 0
        from core.hitl_queue import HitlQueue

        # Surface any approval claimed-but-never-acked last time — i.e. the engine crashed or
        # redeployed mid-execution. Loud, operator-verifies-at-broker; NOT auto-re-executed
        # (a blind re-run could place the order twice without broker-side idempotency).
        try:
            await HitlQueue.recover_orphaned_inflight()
        except Exception as exc:
            logging.error("[HITL] drain: recover_orphaned_inflight failed: %s", exc)

        try:
            approved = await HitlQueue.claim_approved()
        except Exception as exc:
            logging.error("[HITL] drain: claim_approved failed: %s", exc)
            return 0
        drained = 0
        for payload in approved:
            approval_id = payload.get("approval_id", "")
            try:
                await self.execute_approved_order(payload)
                drained += 1
            except Exception as exc:
                logging.error(
                    "[HITL] drain: execute failed for %s: %s",
                    payload.get("symbol", "?"),
                    exc,
                )
            finally:
                # Definitive outcome reached (submitted, Iron-Dome-rejected, or a handled
                # error) → clear the inflight marker. A hard crash before this point leaves the
                # marker for recover_orphaned_inflight() next cycle.
                try:
                    await HitlQueue.ack_inflight(approval_id)
                except Exception as exc:
                    logging.error(
                        "[HITL] drain: ack_inflight failed for %s: %s", approval_id, exc
                    )
        if drained:
            logging.info("[HITL] drained %d approved order(s) this cycle.", drained)
        return drained

    async def _hitl_day_rollover(self, previous_day) -> None:
        """On an NY-date change, clear YESTERDAY's HITL day-notional key (N3 — never today's).

        Dormant unless HITL_ENABLED; no-op on first boot (previous_day None), so a cold start
        cannot wipe today's accumulated autonomous budget.
        """
        if previous_day is None or not get_config().HITL_ENABLED:
            return
        from core.hitl_day_notional import HitlDayNotional

        try:
            await HitlDayNotional.rollover(previous_day.isoformat())
        except Exception as exc:
            logging.error("[HITL] day-notional rollover failed: %s", exc)

    async def _hitl_symbol_pending(self, symbol: str) -> bool:
        """True if `symbol` already has a live human-approval pending for the active user.

        The per-cycle dispatch skips such a symbol (C4) to avoid a wasted ~45s analysis for a
        signal the queue would dedup. Dormant unless HITL_ENABLED.
        """
        if not get_config().HITL_ENABLED:
            return False
        from core.hitl_queue import HitlQueue

        try:
            user_id = getattr(self, "active_uid", None) or "global"
            return await HitlQueue.has_pending(symbol, user_id)
        except Exception as exc:
            logging.error("[HITL] has_pending check failed for %s: %s", symbol, exc)
            return False

    async def _reconcile_active_strategy_entry_time(self) -> None:
        """#2046: reconcile the ACTIVE strategy's PortfolioManager entry-time once per
        session from Alpaca fills.

        The #2042 durable-entry-time reconcile is only hooked on the tenant path
        (order_executor.py:434 / ``_execute_tenant_order``), which the desktop/OSS
        fallback path never enters — so on desktop ``_trade_history`` stays empty and
        ``days_held`` stays 0. Here we reconcile the reachable canonical desktop PM
        (``active_strategy.portfolio_manager``, which carries a broker ``client``,
        rl_strategy.py:284). Fail-safe: no-op without a PM/client; once per PM instance
        (a strategy swap re-reconciles the new PM). Read-only vs the broker.
        """
        strat = getattr(self, "active_strategy", None)
        pm = getattr(strat, "portfolio_manager", None)
        if pm is None or getattr(pm, "client", None) is None:
            return
        if not hasattr(self, "_strategy_pm_reconciled"):
            self._strategy_pm_reconciled: set = set()
        if id(pm) in self._strategy_pm_reconciled:
            return
        self._strategy_pm_reconciled.add(id(pm))
        from core.engine.entry_time_reconcile import reconcile_entry_time_from_alpaca

        await reconcile_entry_time_from_alpaca(pm)

    def _ratchet_high_water_marks(self, positions):
        """#desktop-exit: maintain a per-symbol high-water-mark (the peak price seen while we hold it)
        so the per-cycle trailing stop can measure a REAL drawdown from the peak — the fix for held
        winners never being trimmed (evaluate_position_stop otherwise defaults hwm=current → drawdown 0).

        Flag-gated (``POSITION_EXIT_HWM_TRAILING_ENABLED``): OFF → returns ``None`` → plan_position_stops
        falls back to today's byte-identical behaviour. Ratchets UP only (``max``) and rebuilds from the
        currently-held set each cycle, so a symbol no longer held is evicted (no leak, no stale peak).
        In-memory (reseeds from current price on restart = the safe direction — no false sell after a boot).
        """
        if not getattr(get_config(), "POSITION_EXIT_HWM_TRAILING_ENABLED", False):
            return None
        prev = getattr(self, "_position_high_water_marks", None) or {}
        current = {}
        for p in positions or []:
            sym = p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", None)
            raw = (
                p.get("current_price")
                if isinstance(p, dict)
                else getattr(p, "current_price", None)
            )
            try:
                price = float(raw)
            except (TypeError, ValueError):
                price = 0.0
            if sym and price > 0:
                current[sym] = max(float(prev.get(sym, 0.0)), price)
        self._position_high_water_marks = current
        return current

    def _stopout_reentry_locked(self, just_exited, now=None):
        """#2554: keep a name locked out of BUY re-entry for a cooldown after it was
        stopped out / harvested, so the round table cannot re-buy a just-sold name within
        minutes (the churn carousel). The existing brakes are BUY-side cooldowns that
        never key on a stop-out; this is the exit-side, stop-out-keyed lockout.

        Arms/refreshes the lockout for every freshly exited symbol, evicts expired
        entries, and returns the set of currently-locked symbols (the caller removes them
        from the consensus candidates). Flag-gated ``STOPOUT_REENTRY_COOLDOWN_MIN``
        (minutes; ``<= 0`` → disabled → empty set, byte-identical to today). Exit-side
        only: never blocks an exit, only a re-entry BUY. In-memory (a restart clears it —
        the safe direction: it can never trap capital, only permit an earlier re-entry).
        """
        try:
            cooldown_min = float(
                getattr(get_config(), "STOPOUT_REENTRY_COOLDOWN_MIN", 0) or 0
            )
        except (TypeError, ValueError):
            cooldown_min = 0.0
        if cooldown_min <= 0:
            return set()
        now = now or datetime.now(timezone.utc)
        lock = getattr(self, "_stopout_lockout", None) or {}
        for sym in just_exited or ():
            if sym:
                lock[sym] = now + timedelta(minutes=cooldown_min)
        lock = {s: t for s, t in lock.items() if t > now}
        self._stopout_lockout = lock
        return set(lock)

    def _ratchet_entry_times(self, positions, trade_history):
        """#2555: maintain the CURRENT holding's entry-time per symbol so the per-cycle
        stop measures ``hours_held`` from the fresh (re)buy — not ``min(_trade_history)``,
        which returns the OLDEST trade in the 30-day window and goes stale across a sold-
        then-rebought symbol (``[old_entry, sell, new_buy]`` → ``min`` = old_entry →
        inflated hours_held → panic-protection bypassed + loss-cut tightened on what is
        really a fresh position).

        Flag-gated (``POSITION_EXIT_ENTRY_TIME_RECONCILE_ENABLED``): OFF → returns ``None``
        → plan_position_stops falls back to ``min(_trade_history)`` (byte-identical to
        today). Locks each symbol's entry at first observation and holds it while the
        position is continuously held; a symbol no longer held is evicted, so a later
        rebuy re-stamps a fresh entry. On the FIRST cycle the held set is seeded from
        ``trade_history`` (the #1994 fill-reconcile has written the true entry there);
        every later new appearance is an in-session (re)open → stamped ``now()``. No
        broker calls; never mutates ``_trade_history``.
        """
        if not getattr(
            get_config(), "POSITION_EXIT_ENTRY_TIME_RECONCILE_ENABLED", False
        ):
            return None
        prev = getattr(self, "_position_entry_times", None)
        first_cycle = prev is None
        prev = prev or {}
        now = datetime.now(timezone.utc)
        current = {}
        for p in positions or []:
            sym = p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", None)
            if not sym:
                continue
            if sym in prev:
                current[sym] = prev[sym]
            elif first_cycle:
                times = (trade_history or {}).get(sym)
                current[sym] = min(times) if times else now
            else:
                current[sym] = now
        self._position_entry_times = current
        return current

    async def _fetch_snapshots_chunked(
        self, symbols, *, per_chunk_timeout: float = 15.0
    ) -> dict:
        """Fetch snapshots for ``symbols`` in independent chunks so a single failing /
        timing-out request can no longer zero the WHOLE panel — an empty ``snapshots``
        makes ``update_lstm_rankings`` skip every symbol (``if symbol not in snapshots:
        continue``) → empty ranking → all-HOLD. Each chunk is its own
        ``StockSnapshotRequest``; a chunk that fails / times out drops only ITS symbols
        this cycle while the others still populate.

        The chunks run **concurrently** via ``asyncio.gather`` (Archon review): total
        wall-clock is bounded by ~ONE ``per_chunk_timeout``, not the sum over chunks, so
        a slow provider cannot serialise the fetch into a multi-minute loop stall.

        Logging (AGENTS.md Rule 5): a partial chunk failure is a GRACEFUL fallback (the
        other chunks still populate the panel) → **WARNING**. ``ERROR`` is reserved for a
        TOTAL outage — every chunk failed / returned nothing this cycle.

        ``SNAPSHOT_FETCH_CHUNK_SIZE`` <= 0 (or >= ``len(symbols)``) => one request,
        byte-identical to the pre-fix single-request path. Fail-safe (CODING_POLICY §5.6):
        NEVER raises; returns whatever merged successfully (possibly ``{}``)."""
        symbols = list(symbols or [])
        if not symbols:
            return {}
        try:
            size = int(getattr(get_config(), "SNAPSHOT_FETCH_CHUNK_SIZE", 100) or 0)
        except Exception:  # noqa: BLE001 — a bad flag must never break the fetch
            size = 100
        if size <= 0 or size >= len(symbols):
            chunks = [symbols]
        else:
            chunks = [symbols[i : i + size] for i in range(0, len(symbols), size)]

        async def _fetch_one(idx: int, chunk: list) -> dict:
            """One chunk. Never raises: a partial failure/timeout logs at WARNING (a
            graceful fallback — the other chunks recover the cycle) and returns {}."""
            try:
                request_params = StockSnapshotRequest(
                    symbol_or_symbols=chunk,
                    feed=config.ALPACA_DATA_FEED,
                )
                part = await asyncio.wait_for(
                    asyncio.to_thread(self.data_api.get_stock_snapshot, request_params),
                    timeout=per_chunk_timeout,
                )
                return part or {}
            except asyncio.TimeoutError:
                logging.warning(
                    "FETCH_CHUNK_TIMEOUT: snapshot chunk %d/%d (%d symbols) exceeded "
                    "%.0fs — other chunks unaffected.",
                    idx + 1,
                    len(chunks),
                    len(chunk),
                    per_chunk_timeout,
                )
                return {}
            except (
                Exception
            ) as e:  # noqa: BLE001 — one bad chunk must not zero the panel
                logging.warning(
                    "FETCH_CHUNK_ERROR: snapshot chunk %d/%d (%d symbols) failed (%s) — "
                    "other chunks unaffected.",
                    idx + 1,
                    len(chunks),
                    len(chunk),
                    e,
                )
                return {}

        # return_exceptions=True is belt-and-braces: _fetch_one already swallows every
        # error, so gather can never raise here (§5.6 — a fetch must never break a cycle).
        results = await asyncio.gather(
            *[_fetch_one(i, c) for i, c in enumerate(chunks)],
            return_exceptions=True,
        )
        merged: dict = {}
        for r in results:
            if isinstance(r, dict):
                merged.update(r)
        if not merged:
            # TOTAL outage — every chunk failed/empty this cycle. This is the only
            # ERROR-worthy case; the panel will be empty and the cycle degrades to HOLD.
            logging.error(
                "FETCH_TOTAL_OUTAGE: all %d snapshot chunk(s) returned nothing this "
                "cycle — panel empty for %d symbols.",
                len(chunks),
                len(symbols),
            )
        return merged

    async def _reconcile_specialist_coverage(self, base_watchlist=None) -> None:
        """Flag-gated: retarget the specialist registry's HIGH-PRIORITY set to
        base ∪ held positions ∪ top-N by round-table consensus_score, so the symbols we
        actually hold and the highest-conviction decisions always have a fresh report
        (Epic #1998 RPT-GOLD, sub #2630). Fail-open — never break the cycle.

        ``base_watchlist`` is captured ONCE (lazily, from the registry's startup high-priority
        set) so the union stays bounded and closed positions / stale top-N do not accumulate
        across cycles. Tests pass an explicit list. Reads config via the patchable
        ``config.get_config()`` seam; broker I/O runs off the event loop via ``asyncio.to_thread``.
        """
        cfg = config.get_config()
        if not getattr(cfg, "SPECIALIST_COVERAGE_DYNAMIC", False):
            return
        reg = self.specialist_registry
        if reg is None:
            return
        try:
            from core.engine.coverage_selector import select_specialist_coverage

            # Capture the ORIGINAL high-priority base once (before any dynamic addition).
            if base_watchlist is None:
                if getattr(self, "_specialist_base_watchlist", None) is None:
                    self._specialist_base_watchlist = list(
                        getattr(reg, "_high_priority", []) or []
                    )
                base_watchlist = self._specialist_base_watchlist

            positions: list = []
            if (
                getattr(cfg, "SPECIALIST_COVER_POSITIONS", True)
                and self.api is not None
            ):
                try:
                    # Alpaca get_all_positions is a BLOCKING HTTP call → off the event loop
                    # (same pattern as _run_position_stop_checks), never a bare sync call here.
                    positions_raw = await asyncio.to_thread(self.api.get_all_positions)
                    positions = [p.symbol for p in positions_raw]
                except (
                    Exception
                ) as exc:  # noqa: BLE001 — degrade, never crash the cycle
                    logging.warning(
                        "Coverage: get_all_positions failed (%s) — positions skipped.",
                        exc,
                    )

            target = select_specialist_coverage(
                positions,
                list(getattr(self, "_last_round_table_state", None) or []),
                base_watchlist,
                int(getattr(cfg, "SPECIALIST_TOP_N_CONVICTION", 20)),
                bool(getattr(cfg, "SPECIALIST_COVER_POSITIONS", True)),
            )
            existing = set(getattr(reg, "_symbols", []))
            for sym in target:
                if sym not in existing:
                    reg.add_symbol(sym)
            reg.update_priority(target)
            logging.info(
                "Coverage: specialist high-priority retargeted to %d symbols "
                "(%d positions, top-%d consensus).",
                len(target),
                len(positions),
                int(getattr(cfg, "SPECIALIST_TOP_N_CONVICTION", 20)),
            )
        except Exception as exc:  # noqa: BLE001 — boot/cycle resilience
            logging.warning(
                "Coverage: reconcile failed (%s) — keeping current registry priority.",
                exc,
            )

    async def _run_closed_report_pass(
        self, local_active_strategy, symbols_to_process
    ) -> bool:
        """ONE full-universe LSTM rank/panel refresh for the market-closed report-only mode.

        Fetches snapshots for the WHOLE universe and runs the rank producer once, so the
        on-demand reports stay COMPLETE 24/7 while the market is closed. No round-table
        deep-eval, no orders. Mirrors the open-cycle fetch + rank-producer block (incl. the
        FULL_UNIVERSE 200-cap, so the closed report ranks the SAME universe as the open path).

        Returns True when the pass completed (a rank was produced, or no ranker is configured —
        a structural gap retrying cannot fix); False on a TRANSIENT failure (empty symbols /
        snapshot-fetch exception / rank exception). The caller latches ``_closed_panel_refreshed``
        only on True, so a transient failure — the full-universe snapshot fetch is a documented
        RemoteDisconnect risk — retries on the next closed wake (<=300s) instead of latching an
        empty report for the whole closed period, mirroring the open path's advance-on-success.
        """
        if not symbols_to_process:
            return False
        # Mirror the open path's universe exactly (trading_loop FULL_UNIVERSE branch): with the
        # flag OFF both cap at 200, so the report's "Platz X von N" denominator matches.
        if (
            not get_config().FULL_UNIVERSE_TRADING_ENABLED
            and len(symbols_to_process) > 200
        ):
            symbols_to_process = symbols_to_process[:200]
        current_time_utc = engine_now(timezone.utc)
        snapshots = await self._fetch_snapshots_chunked(
            symbols_to_process, per_chunk_timeout=15.0
        )
        if not snapshots:
            # Every chunk failed (a transient full outage) — retry on the next closed
            # wake rather than latch an empty report for the whole closed period.
            logging.warning(
                "Closed report pass: snapshot fetch returned nothing; retry next wake."
            )
            return False
        try:
            if hasattr(local_active_strategy, "update_lstm_rankings"):
                await local_active_strategy.update_lstm_rankings(
                    symbols_to_process,
                    snapshots,
                    self.current_market_data,
                    current_time_utc,
                )
            elif (
                get_config().LSTM_RANK_PANEL_STRATEGY_INDEPENDENT
                and self._rank_panel_producer is not None
            ):
                await self._rank_panel_producer.update_lstm_rankings(
                    symbols_to_process,
                    snapshots,
                    self.current_market_data,
                    current_time_utc,
                )
        except Exception:  # noqa: BLE001
            logging.warning(
                "Closed report pass: panel rank failed; report rank will be an honest gap.",
                exc_info=True,
            )
            return False
        # #2630: market-closed carryover — research positions ∪ the last session's top-N consensus
        await self._reconcile_specialist_coverage()
        return True

    async def _run_position_stop_checks(self) -> set:
        """#2066: pre-loop per-position risk-exit on the live (round-table) path.

        Fetch held broker positions and, for each that trips a stop
        (``plan_position_stops`` → ``evaluate_position_stop`` on the broker-authoritative
        ``avg_entry_price`` + reconciled ``entry_time`` #2046), dispatch a SELL through the
        compliance-gated executor (``triggered_by_stop=True`` → the #2065 exit-exemption
        lets a large exit through) and return the set of stopped symbols so the consensus
        pass skips them this cycle. Fail-safe: any error returns an empty set — a stop-check
        failure must never block the trading loop.
        """
        api = getattr(self, "api", None)
        if api is None:
            return set()
        try:
            positions = await asyncio.to_thread(api.get_all_positions)
        except Exception as e:  # noqa: BLE001 — never let a fetch error kill the loop
            logging.warning("[StopLoss] get_all_positions failed: %s", e)
            return set()

        strat = getattr(self, "active_strategy", None)
        pm = getattr(strat, "portfolio_manager", None)
        trade_history = getattr(pm, "_trade_history", {}) if pm is not None else {}
        high_water_marks = self._ratchet_high_water_marks(positions)
        entry_times = self._ratchet_entry_times(positions, trade_history)
        plans = plan_position_stops(
            positions or [],
            trade_history or {},
            high_water_marks=high_water_marks,
            entry_times=entry_times,
        )

        stopped: set = set()
        for p in plans:
            ctx = DecisionContext(
                symbol=p["symbol"],
                action="SELL",
                current_price=p["current_price"],
                in_position=True,
                position_qty=p["qty"],
                position_avg_price=p["avg_entry_price"],
                triggered_by_stop=True,
                stop_type=(p["reason"] or "")[:64],
                risk_approved=True,
                portfolio_approved=True,
                intelligence_approved=True,
            )
            event = SignalEvent(
                symbol=p["symbol"],
                action="SELL",
                decision_context=ctx,
                suggested_quantity=0.0,
            )
            await self._process_signal_event(event)
            stopped.add(p["symbol"])
            logging.info(
                "[StopLoss] %s: risk-exit fired → SELL dispatched (%s)",
                p["symbol"],
                p["reason"],
            )
        return stopped

    async def _run_deconcentration_and_rotation_exits(
        self, already_acted: set, now=None
    ) -> set:
        """#sell-decisions: flag-gated per-cycle exit hook — PRODUCES SELL decisions.

        A pre-consensus pass (right after ``_run_position_stop_checks``) with two
        independent, flag-gated levers that both dispatch through the verified-sound
        ``_process_signal_event`` seam and only ever REDUCE exposure:

        * **ROTATION** (``ROTATION_EXIT_ENABLED``): a held name that dropped out of the
          live LSTM top-N past hysteresis / min-hold is FULLY exited (qty 0 → the SELL
          branch fetches the whole open position). Fail-safe: rank ``None`` (symbol absent
          from the panel) → HOLD; a STALE panel (older than ``ROTATION_PANEL_MAX_AGE_DAYS``)
          skips the lever entirely (date-normalized — never ``date − datetime``).
        * **TRIM** (``DECONCENTRATION_TRIM_ENABLED``): an overweight name is PARTIALLY
          reduced by a dollar-fraction of its market value
          (``held_qty * min(1, |adjustment|/market_value)`` — no price division,
          ``market_value <= 0`` skipped, clamped to held so it can never oversell).

        Both SELLs carry ``triggered_by_stop=False`` (a rotation/trim is NOT a price
        stop → no WORM-audit contamination); execution is unaffected (that flag gates
        no order path — churn is BUY-only, the compliance exit-exemption keys on
        side + held_qty). Rotation runs first, then trim on what remains; a symbol is
        acted on at most once per cycle (shared ``already_acted`` set, unioned with this
        hook's own exits). Returns the exited-symbols set so the caller excludes them
        from the consensus (the round table cannot re-buy a just-sold name).

        Flags OFF ⇒ byte-identical no-op (early return; no panel/recommender call, no
        SELL). Fail-safe: each lever is wrapped so a lever error is logged and swallowed
        — a hook failure never kills the loop.
        """
        exited: set = set()
        cfg = get_config()
        rotation_on = bool(getattr(cfg, "ROTATION_EXIT_ENABLED", False))
        trim_on = bool(getattr(cfg, "DECONCENTRATION_TRIM_ENABLED", False))
        if not rotation_on and not trim_on:
            return exited  # byte-identical no-op

        already_acted = set(already_acted or set())
        if now is None:
            now = datetime.now(timezone.utc)

        strat = getattr(self, "active_strategy", None)
        pm = getattr(strat, "portfolio_manager", None)
        if pm is None:
            return exited

        # ---- Lever A: ROTATION (thesis-invalidation first) ------------------
        if rotation_on:
            try:
                from core.report.lstm_panel_store import (
                    cross_section_standing,
                    get_store,
                )

                store = get_store()
                snap = store.latest_snapshot_date()  # a datetime.date (or None)
                max_age = int(getattr(cfg, "ROTATION_PANEL_MAX_AGE_DAYS", 3))
                # Refuse a STALE / empty panel — date-normalized (never date − datetime).
                if snap is not None and (now.date() - snap).days <= max_age:
                    try:
                        from core.strategies.lstm_strategy import LSTM_DYNAMIC_TOP_N
                    except Exception:  # pragma: no cover — constant fallback
                        LSTM_DYNAMIC_TOP_N = 10
                    min_hold_days = float(getattr(cfg, "SMART_EXIT_MIN_HOLD_DAYS", 5.0))
                    hysteresis = float(
                        getattr(cfg, "SMART_EXIT_EXIT_RANK_HYSTERESIS", 3.0)
                    )
                    xsec = store.cross_section_at(now)
                    await asyncio.to_thread(pm.refresh_positions)
                    # Per-cycle cap (2026-07-28 incident): bound how many FULL exits this
                    # lever fires in ONE cycle so a ranking shift cannot flush the whole
                    # book at once. FLOORS to 1 (owner directive 2026-08-03): a value <=0 is
                    # NOT "unlimited" — that convention was a footgun (zeroing the cap re-opened
                    # the single-cycle flush). Disable rotation via ROTATION_EXIT_ENABLED, never
                    # by zeroing the cap.
                    rot_cap = int(getattr(cfg, "ROTATION_MAX_EXITS_PER_CYCLE", 1) or 1)
                    if rot_cap < 1:
                        rot_cap = 1
                    # Per-SESSION ceiling (2026-08-03): the per-cycle cap alone does NOT bound
                    # a whole-book flush — rotation exits are risk-reducing SELLs (exempt from
                    # the daily-trades cap) and the loop runs ~390 cycles/session, so a sustained
                    # ranking shift would drain the book one name per cycle over the day. This
                    # day-scoped counter caps CUMULATIVE rotation exits per trading day; it
                    # resets on date rollover. Protective stop-losses are unaffected. <=0 =
                    # unlimited (byte-identical rollback). Instance-attr (lazy: the mixin may be
                    # built via __new__, so read defensively and never assume __init__ ran).
                    session_cap = int(
                        getattr(cfg, "ROTATION_MAX_EXITS_PER_SESSION", 3) or 0
                    )
                    _today = now.date()
                    if getattr(self, "_rotation_session_date", None) != _today:
                        self._rotation_session_date = _today
                        self._rotation_exits_session = 0
                    rot_exits = 0
                    for sym, score in list(getattr(pm, "_position_scores", {}).items()):
                        if sym in already_acted or sym in exited:
                            continue
                        _pct, rank, n = cross_section_standing(xsec, sym)
                        if rank is None:
                            continue  # fail-safe HOLD — never a false rotation sell
                        in_top_n = rank <= LSTM_DYNAMIC_TOP_N
                        days_held = float(getattr(score, "days_held", 0) or 0)
                        min_hold_ok = days_held >= min_hold_days
                        rank_collapsed = rank > LSTM_DYNAMIC_TOP_N * hysteresis
                        if (
                            not in_top_n
                            and rank > LSTM_DYNAMIC_TOP_N
                            and (min_hold_ok or rank_collapsed)
                        ):
                            if rot_cap > 0 and rot_exits >= rot_cap:
                                logging.warning(
                                    "[Rotation] per-cycle cap %d reached — deferring "
                                    "further eligible exits to the next cycle.",
                                    rot_cap,
                                )
                                break
                            if session_cap > 0 and (
                                int(getattr(self, "_rotation_exits_session", 0) or 0)
                                >= session_cap
                            ):
                                logging.warning(
                                    "[Rotation] per-session cap %d reached — no further "
                                    "rotation exits today (resets next trading day). "
                                    "Protective stop-losses are unaffected.",
                                    session_cap,
                                )
                                break
                            ctx = DecisionContext(
                                symbol=sym,
                                action="SELL",
                                current_price=float(
                                    getattr(score, "current_price", 0.0) or 0.0
                                ),
                                in_position=True,
                                position_qty=float(getattr(score, "qty", 0.0) or 0.0),
                                position_avg_price=float(
                                    getattr(score, "avg_entry", 0.0) or 0.0
                                ),
                                triggered_by_stop=False,
                                portfolio_reason=(
                                    f"rotation: dropped to rank {rank}/{n} "
                                    f"(out of top-{LSTM_DYNAMIC_TOP_N})"
                                ),
                            )
                            event = SignalEvent(
                                symbol=sym,
                                action="SELL",
                                decision_context=ctx,
                                suggested_quantity=0.0,  # full exit
                            )
                            await self._process_signal_event(event)
                            exited.add(sym)
                            rot_exits += 1
                            self._rotation_exits_session = (
                                int(getattr(self, "_rotation_exits_session", 0) or 0)
                                + 1
                            )
                            try:
                                pm.record_trade(sym, "sell")
                            except Exception:  # noqa: BLE001 — bookkeeping only
                                # CLAUDE.md §5.6: never swallow silently. This is
                                # cooldown/freshness bookkeeping AFTER the SELL was
                                # already dispatched — the exit stands; only the
                                # anti-churn cooldown may be missed, so log + continue.
                                logging.warning(
                                    "[Rotation] failed to record trade for %s "
                                    "(cooldown bookkeeping; SELL already dispatched)",
                                    sym,
                                    exc_info=True,
                                )
                            logging.info(
                                "[Rotation] %s: rank %s/%s out of top-%s → "
                                "full SELL dispatched",
                                sym,
                                rank,
                                n,
                                LSTM_DYNAMIC_TOP_N,
                            )
            except (
                Exception
            ) as e:  # noqa: BLE001 — a lever failure must not kill the loop
                logging.warning("[Rotation] exit lever failed: %s", e, exc_info=True)

        # ---- Lever B: TRIM (de-concentrate what remains) --------------------
        if trim_on:
            try:
                acted = already_acted | exited
                min_order_value = float(getattr(cfg, "MIN_ORDER_VALUE_USD", 50.0))
                recs = await asyncio.to_thread(pm.get_rebalance_recommendations)
                scores = getattr(pm, "_position_scores", {})
                for rec in recs or ():
                    if rec.get("action") != "REDUCE":
                        continue
                    sym = rec.get("symbol")
                    if sym in acted or sym in exited:
                        continue
                    mv = float(rec.get("market_value", 0.0) or 0.0)
                    if mv <= 0:
                        continue  # no price division; skip on non-positive market value
                    score = scores.get(sym)
                    held_qty = (
                        float(getattr(score, "qty", 0.0) or 0.0) if score else 0.0
                    )
                    if held_qty <= 0:
                        continue
                    adjustment = abs(float(rec.get("adjustment_value", 0.0) or 0.0))
                    # clamp to held → can never oversell (executor honors a partial verbatim)
                    trim_qty = held_qty * min(1.0, adjustment / mv)
                    if trim_qty <= 0:
                        continue
                    # Dust-skip only where a price is safely available.
                    price = (
                        float(getattr(score, "current_price", 0.0) or 0.0)
                        if score
                        else 0.0
                    )
                    if price > 0 and trim_qty * price < min_order_value:
                        continue
                    ctx = DecisionContext(
                        symbol=sym,
                        action="SELL",
                        current_price=price,
                        in_position=True,
                        position_qty=held_qty,
                        position_avg_price=(
                            float(getattr(score, "avg_entry", 0.0) or 0.0)
                            if score
                            else 0.0
                        ),
                        triggered_by_stop=False,
                        portfolio_reason=(
                            f"deconcentration_trim: drift "
                            f"{rec.get('drift_pct', 0.0):+.1f}% → target"
                        ),
                    )
                    event = SignalEvent(
                        symbol=sym,
                        action="SELL",
                        decision_context=ctx,
                        suggested_quantity=trim_qty,  # partial
                    )
                    await self._process_signal_event(event)
                    exited.add(sym)
                    try:
                        pm.record_trade(sym, "sell")
                    except Exception:  # noqa: BLE001 — bookkeeping only
                        # CLAUDE.md §5.6: never swallow silently. Cooldown/freshness
                        # bookkeeping AFTER the SELL was already dispatched — the exit
                        # stands; only the anti-churn cooldown may be missed → log.
                        logging.warning(
                            "[Trim] failed to record trade for %s "
                            "(cooldown bookkeeping; SELL already dispatched)",
                            sym,
                            exc_info=True,
                        )
                    logging.info(
                        "[Trim] %s: trim %.4f of %.4f held (mv=%.2f adj=%.2f) → "
                        "partial SELL",
                        sym,
                        trim_qty,
                        held_qty,
                        mv,
                        adjustment,
                    )
            except (
                Exception
            ) as e:  # noqa: BLE001 — a lever failure must not kill the loop
                logging.warning(
                    "[Trim] de-concentration lever failed: %s", e, exc_info=True
                )

        return exited

    async def _warm_lstm_bar_cache(self, strat) -> None:
        """LSTM-panel-starvation fix (Part 2) — warm the daily-bar cache ONCE per
        trading day, OFF the hot path, so ``update_lstm_rankings``' per-symbol
        ``get_data`` reads become cache hits instead of a ~500-symbol live fetch
        storm that rate-limits the free IEX feed and empties the panel (-> all-HOLD).

        Flag-gated (``LSTM_BAR_STORE_WARMUP_ENABLED``) and fully fail-safe — a
        warm-up failure must NEVER break the loop; the per-symbol fetch path stays
        intact. The blocking batched fetch runs in a thread executor so it never
        blocks the event loop. This only WRITES the cache the read path already
        reads — ``update_lstm_rankings``' read loop is untouched.
        """
        try:
            if not get_config().LSTM_BAR_STORE_WARMUP_ENABLED:
                return
            if strat is None:
                return
            symbols = list(getattr(strat, "symbols", None) or [])
            if not symbols:
                return
            provider = getattr(strat, "data_provider", None) or getattr(
                self, "data_provider", None
            )
            if provider is None or not hasattr(provider, "warm_universe_cache"):
                return
            # SAME ``days`` _get_torch_prediction passes to get_data
            # (lstm_strategy.py:279) so the warmed cache keys match the per-cycle
            # read keys exactly.
            from core.strategies.lstm_strategy import SEQUENCE_LENGTH

            seq_len = getattr(strat, "sequence_length", None) or SEQUENCE_LENGTH
            days = seq_len + 200
            now = datetime.now(timezone.utc)
            loop = asyncio.get_running_loop()
            warmed = await loop.run_in_executor(
                None, provider.warm_universe_cache, symbols, now, days
            )
            logging.info(
                "LSTM bar-store warm-up: %s/%d symbols warmed (days=%d).",
                warmed,
                len(symbols),
                days,
            )
        except Exception:  # noqa: BLE001 — warm-up must never break the trading loop
            logging.warning(
                "LSTM bar-store warm-up skipped (non-fatal).", exc_info=True
            )

    async def live_trading_loop(self):  # noqa: C901
        """
        Hauptschleife für Live-Trading:
        1. Kill-Switch prüfen
        2. Markt-Stunden prüfen (sleep 300s wenn zu)
        3. Snapshots fetchen
        4a. LSTMDynamic: sequenziell in Rank-Reihenfolge (Cash-Deduplication)
        4b. Andere Strategien: LangGraph-Dispatch via asyncio.gather (parallel)
        5. Latenz messen
        """
        logging.info("Live trading loop started.")
        self._skipped_symbols.clear()

        # ADR: Startup Health Check — verhindert degradierten Start (Watermelon Effect).
        # Kritische Dependencies (Redis, Gemini) werden vor dem ersten Cycle geprüft.
        # Bei Failure: RuntimeError wird geworfen → except Exception in run_strategy_async_wrapper
        # fängt diesen und stoppt den Bot sauber.
        try:
            await self._startup_health_check()
        except RuntimeError as e:
            logging.critical(
                "🚨 Startup health check failed — aborting trading loop: %s", e
            )
            self.strategy_running.clear()
            return

        while self.strategy_running.is_set() and not self._shutdown_event.is_set():
            # Calendar day check in NY Timezone (FIND-01)
            ny_date_rolled = False
            try:
                ny_tz = pytz.timezone("America/New_York")
                current_ny_date = engine_now(ny_tz).date()
                if getattr(self, "_last_trading_day", None) != current_ny_date:
                    ny_date_rolled = True
                    previous_trading_day = getattr(self, "_last_trading_day", None)
                    self._last_trading_day = current_ny_date
                    if (
                        hasattr(self, "compliance_guardian")
                        and self.compliance_guardian is not None
                    ):
                        self.compliance_guardian.reset_daily_limit()
                        logging.info(
                            "Engine: Daily limit reset for new NY calendar day."
                        )
                    # FIND-01 / N3: clear YESTERDAY's HITL day-notional key on the NY-date
                    # change (dormant unless HITL_ENABLED; no-op on first boot).
                    await self._hitl_day_rollover(previous_trading_day)
            except Exception:
                logging.exception("Error checking NY timezone calendar day")

            # LSTM-panel-starvation fix (Part 2): warm the daily-bar cache ONCE per
            # trading day, off the hot path. ``_last_trading_day`` is unset at boot, so
            # the FIRST iteration rolls -> this is also the engine-start warm-up; every
            # later NY-date change re-warms for the new day. Flag-gated + fail-safe
            # inside the helper (a warm-up failure never breaks the loop). Resolve the
            # active strategy's universe the same way the cycle does (:787-790).
            if ny_date_rolled:
                _lock = getattr(self, "strategy_lock", None)
                if _lock is not None:
                    with _lock:
                        _warm_strat = self.active_strategy
                else:
                    _warm_strat = getattr(self, "active_strategy", None)
                await self._warm_lstm_bar_cache(_warm_strat)

            # Kill Switch
            if kill_switch is not None and kill_switch.is_halted():
                logging.error("Kill Switch is HALTED. Stopping engine loop.")
                self._shutdown_event.set()
                self.strategy_running.clear()
                break

            # Market Hours Check
            # INC-6 (continuous operation, Dual-Design Option A): a CLOSED market no
            # longer sleep-SKIPS the whole cycle (the old `sleep(300); continue`).
            # Research/analysis/report-evaluation runs CONTINUOUSLY — only ORDER
            # PLACEMENT stays market-gated, enforced downstream at the order step
            # (order_executor._market_closed_blocks_order → records
            # `blocked:market_closed`, never submits to the broker unless
            # BYPASS_MARKET_HOURS). Here we merely flag the closed cycle so we (a) run
            # the analysis at a MODEST cadence (300s end-of-cycle sleep, not 60s) and
            # (b) skip the HITL-approval drain (approvals execute only in an open cycle).
            cycle_market_closed = False
            # F3: True only after a successful get_clock. A clock-read failure leaves
            # cycle_market_closed False too — it must NOT be mistaken for a confirmed open
            # (which would re-arm the once-per-closed-period report latch mid-closure).
            clock_ok = False
            try:
                clock = await asyncio.to_thread(self.api.get_clock)
                clock_ok = True
                # PR B (fail-safe): cache market-open so /engine-diagnostics can read
                # it WITHOUT a live broker call. Pure observation — never gates flow.
                try:
                    self._last_market_open = bool(clock.is_open)
                except Exception:
                    pass
                if not clock.is_open and not BYPASS_MARKET_HOURS:
                    cycle_market_closed = True
                    next_open = (
                        clock.next_open.strftime("%Y-%m-%d %H:%M:%S UTC")
                        if clock.next_open
                        else "Unknown Time"
                    )
                    self._log_strategy_thought(
                        f"🌙 Market is CLOSED. Next open: {next_open}. Running "
                        "research/analysis only — order placement is gated until open."
                    )
            except Exception as e:
                logging.warning("Failed to check market clock: %s", e)

            if self._shutdown_event.is_set():
                break

            # Drain HITL-approved orders every cycle (PR-0a-ii-5b / C3). After the kill-switch
            # + market-hours checks above, so approvals execute only in a live, open cycle.
            # Dormant unless HITL_ENABLED. INC-6: the closed-market cycle no longer
            # `continue`s, so gate the drain explicitly — a human-approved order is an
            # ORDER, hence market-gated exactly like the autonomous path.
            # Re-arm the once-per-closed-period report pass ONLY on a CONFIRMED open (F3:
            # gate on clock_ok + is_open, not `not cycle_market_closed`, so a transient
            # clock-read blip mid-closure never re-arms → never triggers a 2nd panel pass).
            if clock_ok and clock.is_open:
                self._closed_panel_refreshed = False
            if not cycle_market_closed:
                await self._drain_hitl_approvals()

            local_active_strategy = None
            symbols_to_process = []

            with self.strategy_lock:
                if self.active_strategy:
                    local_active_strategy = self.active_strategy
                    symbols_to_process = list(self.active_strategy.symbols)

            if not local_active_strategy:
                await asyncio.sleep(5)
                continue

            # #2046: durable entry-time on desktop. The #2042 reconcile hook runs only on the
            # tenant path (order_executor), which the desktop/OSS fallback never enters — so
            # days_held would stay 0. Reconcile the active strategy's PM once/session. It MUST run
            # HERE — BEFORE the market-closed report-only `continue` below — otherwise a restart
            # while the market is closed never reconciles, leaving EVERY position at "0 days" until
            # the next open. Read-only vs the broker + once/session-guarded → safe + idempotent on
            # both the open and the closed path.
            await self._reconcile_active_strategy_entry_time()

            # --- Market-closed report-only mode (owner 2026-07-26) -------------------------
            # Trade only during market hours. When the market is closed, run exactly ONE
            # full-universe LSTM rank/panel refresh (so the on-demand reports stay COMPLETE
            # 24/7) then idle until near open — the round-table deep-eval + the whole order/
            # trade machinery below (risk-exits, consensus, execution, HITL) are skipped, so
            # the GPU rests. Flag-gated; OFF (MARKET_CLOSED_REPORT_ONLY=False) or
            # BYPASS_MARKET_HOURS restores INC-6 continuous market-closed analysis.
            from core.engine.closed_market_mode import (
                closed_idle_seconds,
                should_refresh_closed_panel,
                should_run_report_only,
            )

            if should_run_report_only(
                cycle_market_closed,
                get_config().MARKET_CLOSED_REPORT_ONLY,
                BYPASS_MARKET_HOURS,
            ):
                if should_refresh_closed_panel(
                    getattr(self, "_closed_panel_refreshed", False)
                ):
                    # F1: latch (and log) ONLY when the pass actually produced a rank — a
                    # transient full-universe fetch failure (RemoteDisconnect) must retry on
                    # the next closed wake, not latch an empty report for the whole closure.
                    produced = await self._run_closed_report_pass(
                        local_active_strategy, symbols_to_process
                    )
                    if produced:
                        self._closed_panel_refreshed = True
                        self._log_strategy_thought(
                            "🌙 Markt geschlossen — EIN vollständiger Report-/Panel-Pass "
                            "gelaufen; Reports bleiben 24/7 live, Trading startet automatisch "
                            "zum nächsten Open."
                        )
                # F2: keep the loop-liveness timestamp fresh while legitimately idle, so the
                # stall monitor (evaluate_stall suppresses on market_closed) does not fire a
                # spurious loop_stalled CRITICAL from a stale timestamp at the market-open
                # transition. Advancing it each closed wake bounds the age to the idle cadence.
                self._last_cycle_details = {
                    **(getattr(self, "_last_cycle_details", None) or {}),
                    "timestamp": time.time(),
                }
                _sto = None
                try:
                    _no = getattr(clock, "next_open", None)
                    if _no is not None:
                        _sto = float((_no - datetime.now(timezone.utc)).total_seconds())
                except Exception:
                    _sto = None
                await asyncio.sleep(closed_idle_seconds(_sto))
                continue
            # ------------------------------------------------------------------------------

            rm = getattr(local_active_strategy, "risk_manager", None)
            if rm and rm.trading_halted:
                await asyncio.sleep(60)
                continue

            # #2066: per-position risk-exit BEFORE the consensus pass. Held positions that
            # trip a stop are sold via the compliance-gated executor and excluded from the
            # consensus this cycle (so the round table can't re-buy a stopped-out loser).
            try:
                stopped_symbols = await self._run_position_stop_checks()
            except (
                Exception
            ) as e:  # noqa: BLE001 — a stop-check failure must not kill the loop
                logging.warning(
                    "[StopLoss] pre-loop stop check failed: %s", e, exc_info=True
                )
                stopped_symbols = set()
            if stopped_symbols:
                symbols_to_process = [
                    s for s in symbols_to_process if s not in stopped_symbols
                ]

            # #sell-decisions: flag-gated per-cycle exit hook (ROTATION + TRIM). Runs
            # AFTER the price-stop pass and shares its acted-set (a stopped name is never
            # also rotated/trimmed); its exited names are excluded from the consensus so
            # the round table cannot re-buy a just-sold position. Fail-safe: a hook
            # failure is logged and swallowed — it must never kill the loop.
            try:
                exited_symbols = await self._run_deconcentration_and_rotation_exits(
                    already_acted=stopped_symbols, now=datetime.now(timezone.utc)
                )
            except (
                Exception
            ) as e:  # noqa: BLE001 — an exit-hook failure must not kill the loop
                logging.warning(
                    "[ExitHook] deconcentration/rotation hook failed: %s",
                    e,
                    exc_info=True,
                )
                exited_symbols = set()
            if exited_symbols:
                symbols_to_process = [
                    s for s in symbols_to_process if s not in exited_symbols
                ]

            # #2554: keep just-stopped/harvested names out of BUY re-entry for a cooldown
            # (STOPOUT_REENTRY_COOLDOWN_MIN; default 0 = disabled) — the churn-carousel
            # brake the BUY-side cooldowns (which never key on a stop-out) don't provide.
            reentry_locked = self._stopout_reentry_locked(
                set(stopped_symbols or ()) | set(exited_symbols or ()),
                now=datetime.now(timezone.utc),
            )
            if reentry_locked:
                symbols_to_process = [
                    s for s in symbols_to_process if s not in reentry_locked
                ]

            if not symbols_to_process:
                self._log_strategy_thought("Waiting for symbols from scanner...")
                await asyncio.sleep(10)
                continue

            # Latency measurement
            t_start = time.perf_counter()
            t_data_fetched = t_start
            t_strategy_done = t_start

            _ = getattr(local_active_strategy, "strategy_name", "unknown")
            _ = get_tracer("aaa-engine")
            try:
                current_time_utc = engine_now(timezone.utc)
                # Phase B: this 200-truncation is a slice of an UNRANKED, scanner-ordered
                # list. With the full universe it would silently make "the whole universe"
                # mean "whichever 200 arrived first" — so it must not survive when the
                # flag is ON. The fan-out it incidentally bounded is replaced by the
                # ADR-FU01 semaphore below, never simply dropped. Flag OFF -> identical
                # truncation, identical constant, byte-identical behaviour.
                if not get_config().FULL_UNIVERSE_TRADING_ENABLED:
                    if len(symbols_to_process) > 200:
                        symbols_to_process = symbols_to_process[:200]

                logging.info(
                    f"Engine: Processing {len(symbols_to_process)} symbols: {symbols_to_process[:5]}..."
                )

                logging.warning(
                    "FETCH_START: Requesting snapshots for %s...",
                    symbols_to_process[:5],
                )
                snapshots = await self._fetch_snapshots_chunked(
                    symbols_to_process, per_chunk_timeout=15.0
                )
                t_data_fetched = time.perf_counter()
                logging.warning("FETCH_SUCCESS: Fetched %d snapshots.", len(snapshots))

                if hasattr(local_active_strategy, "update_lstm_rankings"):
                    await local_active_strategy.update_lstm_rankings(
                        symbols_to_process,
                        snapshots,
                        self.current_market_data,
                        current_time_utc,
                    )
                elif (
                    get_config().LSTM_RANK_PANEL_STRATEGY_INDEPENDENT
                    and self._rank_panel_producer is not None
                ):
                    # The keystone of the auditable report: produce the cross-section
                    # panel even though the ACTIVE strategy cannot.
                    #
                    # The panel's only writer is LSTMDynamic.update_lstm_rankings, and
                    # ACTIVE_STRATEGY defaults to RLAgent — so the rank the report's
                    # recommendation LEADS WITH renders "Platz n/a von n/a" for every
                    # symbol in the shipped build. Not an edge case: the default.
                    #
                    # Dormancy is structural, not preserved: this `elif` can only run
                    # where the `if` above is FALSE — i.e. where zero code runs today.
                    # There is no old behaviour at this site to keep byte-identical.
                    #
                    # The producer RANKS, it never trades: run_for_symbol is never
                    # called on it, it is registered set_active=False so get_active()
                    # can never return it, and its _bought_this_window /
                    # high_water_marks / _entry_time stay empty by construction.
                    #
                    # Failure here must never cost a cycle: the panel is telemetry for
                    # the report, and the trading path below does not read it.
                    # Increment 2: re-rank the full universe at most once per
                    # ROUND_TABLE_RANK_REFRESH_MINUTES (0 = every cycle, byte-identical). The panel
                    # store retains the last ranking between refreshes, so the funnel keeps reading it.
                    from core.engine.rank_funnel import should_refresh_rank

                    _rank_now = time.monotonic()
                    if should_refresh_rank(
                        getattr(self, "_last_rank_refresh", None),
                        _rank_now,
                        get_config().ROUND_TABLE_RANK_REFRESH_MINUTES,
                    ):
                        try:
                            await self._rank_panel_producer.update_lstm_rankings(
                                symbols_to_process,
                                snapshots,
                                self.current_market_data,
                                current_time_utc,
                            )
                            # advance the timer only on SUCCESS → a failed rank retries next cycle
                            # rather than leaving the panel stale for a full interval.
                            self._last_rank_refresh = _rank_now
                        except (
                            Exception
                        ):  # noqa: BLE001 — a ranker must not break trading
                            logging.warning(
                                "Rank-panel producer failed this cycle — the report's rank "
                                "will be an honest gap; trading is unaffected.",
                                exc_info=True,
                            )

                # LSTMDynamic: process in rank order
                symbols_order = symbols_to_process
                if (
                    getattr(local_active_strategy, "strategy_name", None)
                    == "LSTMDynamic"
                ):
                    rank_cache = getattr(local_active_strategy, "_lstm_rank_cache", [])
                    if rank_cache:
                        rank_order = [s for s, _ in rank_cache]
                        symbols_order = [
                            s for s in rank_order if s in symbols_to_process
                        ]
                        symbols_order += [
                            s for s in symbols_to_process if s not in symbols_order
                        ]

                is_lstm = (
                    getattr(local_active_strategy, "strategy_name", None)
                    == "LSTMDynamic"
                )

                # --- GAP9: ONE ComplianceGatekeeper portfolio snapshot per cycle ---
                # ARMED by default (GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED=True since #1962).
                # The snapshot is built ONCE here (not per symbol) and injected into every
                # symbol's state, so all parallel evaluations share one consistent portfolio
                # view. When the flag is OFF, the runner sees an empty context = today's
                # byte-identical behaviour. build_portfolio_context never raises (fail-open to None).
                _portfolio_context = None
                if get_config().GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED:
                    _portfolio_context = await build_portfolio_context(
                        getattr(self, "api", None),
                        getattr(self, "compliance_guardian", None),
                    )

                # Round-table-v2 rank-cache funnel: cap the deep-eval set to [holdings ∪ top-K
                # LSTM-ranked] so the ~500-symbol round-table graph does not saturate the GPU every
                # cycle. ROUND_TABLE_TOP_K_EVAL<=0 (default) → no-op → byte-identical. It falls back to
                # the FULL universe on ANY doubt — holdings unconfirmed (fetch failed / no api), or a
                # cold / thin / STALE panel — so a held position is never dropped from the exit eval and
                # the engine never keeps trading a shrunken set on an outdated ranking.
                _top_k = int(get_config().ROUND_TABLE_TOP_K_EVAL or 0)
                if _top_k > 0:
                    try:
                        from core.engine.rank_funnel import apply_rank_cache_funnel
                        from core.report.lstm_panel_store import get_store

                        # Holdings: funnel ONLY with confirmed holdings; any doubt → None → skip.
                        _held = None
                        _api = getattr(self, "api", None)
                        if _api is not None:
                            try:
                                _positions = await asyncio.to_thread(
                                    _api.get_all_positions
                                )
                                _held = set()
                                for _p in _positions or []:
                                    _s = getattr(_p, "symbol", None)
                                    if _s:
                                        _held.add(_s)
                            except (
                                Exception
                            ) as _e:  # noqa: BLE001 — must not starve exits
                                logging.warning(
                                    "[RankFunnel] get_all_positions failed (%s) — full universe.",
                                    _e,
                                )
                                _held = None

                        # Freshness: refuse a stale panel (producer stopped) → full universe + WARN.
                        _store = get_store()
                        _latest = _store.latest_snapshot_date()
                        _max_age = int(
                            get_config().ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS or 0
                        )
                        _stale = (
                            _latest is None
                            or (datetime.now(timezone.utc).date() - _latest).days
                            > _max_age
                        )

                        if _held is not None and not _stale:
                            _xsec = _store.cross_section_at(datetime.now(timezone.utc))
                            _before = len(symbols_order)
                            symbols_order = apply_rank_cache_funnel(
                                symbols_order, _held, _xsec, _top_k
                            )
                            if len(symbols_order) < _before:
                                logging.info(
                                    "[RankFunnel] deep-eval %d/%d (top-%d ∪ %d held); %d deferred.",
                                    len(symbols_order),
                                    _before,
                                    _top_k,
                                    len(_held),
                                    _before - len(symbols_order),
                                )
                        elif _stale and _latest is not None:
                            logging.warning(
                                "[RankFunnel] LSTM panel stale (latest %s > %dd) — full universe; "
                                "is the rank producer running?",
                                _latest,
                                _max_age,
                            )
                    except (
                        Exception
                    ) as _e:  # noqa: BLE001 — the funnel must never kill the cycle
                        logging.warning(
                            "[RankFunnel] skipped this cycle (%s) — full universe.", _e
                        )

                # Snapshots → SymbolEvalState-Dicts (nur Skalare, kein DataFrame)
                graph_states = []
                for symbol in symbols_order:
                    if self._shutdown_event.is_set():
                        break

                    # C4: skip a symbol already awaiting human approval — avoids a wasted
                    # analysis for a signal the queue would dedup. Dormant unless HITL_ENABLED.
                    if await self._hitl_symbol_pending(symbol):
                        continue

                    if symbol not in snapshots:
                        if symbol not in self._skipped_symbols:
                            self._log_strategy_thought(
                                f"❌ {symbol}: Not in snapshots response"
                            )
                            self._skipped_symbols.add(symbol)
                        continue

                    snapshot_obj = snapshots[symbol]
                    if (
                        not hasattr(snapshot_obj, "latest_trade")
                        or snapshot_obj.latest_trade is None
                    ):
                        if symbol not in self._skipped_symbols:
                            self._log_strategy_thought(
                                f"❌ {symbol}: No latest_trade data"
                            )
                            self._skipped_symbols.add(symbol)
                        continue

                    ohlc, price = _extract_ohlc_from_snapshot(snapshot_obj)
                    logging.debug(
                        "%s: live_price=%.2f (open=%.2f high=%.2f low=%.2f vol=%.0f)",
                        symbol,
                        price,
                        ohlc["open"],
                        ohlc["high"],
                        ohlc["low"],
                        ohlc["volume"],
                    )

                    # --- Epic 3: Data Integrity Guard (Fail-Fast) ---
                    # Flat-Candle (O=H=L=C) mit Volumen kann nicht real sein.
                    # Verhindert, dass korrupte Alpaca-Daten in den Round Table fließen.
                    if ohlc["high"] == ohlc["low"] and ohlc["volume"] > 0 and price > 0:
                        logging.warning(
                            f"[{symbol}] Flat-Candle detektiert (O=H=L=C={price:.2f}) "
                            f"mit vol={ohlc['volume']:.0f}. Symbol wird für diesen Zyklus übersprungen."
                        )
                        if symbol not in self._skipped_symbols:
                            self._skipped_symbols.add(symbol)
                        continue

                    logging.info("✅ %s: Valid data - Price $%.2f", symbol, price)
                    _symbol_state = {
                        "symbol": symbol,
                        "ohlc": ohlc,
                        "market_data_keys": [],
                        "current_time": current_time_utc.isoformat(),
                        "signal": None,
                        "error": None,
                        # #1949: "vix"/"regime" are declared LangGraph channels;
                        # values are flag-gated (OFF → None = old dropped-key
                        # behaviour, byte-identical).
                        **_regime_conditioner_state_keys(
                            getattr(self, "current_market_data", None)
                        ),
                        # GAP9: same per-cycle snapshot for every symbol (None when the
                        # feature is off → runner falls back to {} = today's behaviour).
                        "_portfolio_context": _portfolio_context,
                    }
                    # RTR-1 (#1948, dormant default OFF): point-in-time news channel.
                    # The producer is flag-FIRST (returns None without network I/O
                    # while NEWS_SENTIMENT_NLP_ENABLED is off) → the key is only
                    # added when the feature is armed; the dormant state dict stays
                    # byte-identical to today. [] (flag on, fetch failed/empty) is
                    # passed through so NewsSentimentAgent abstains transparently.
                    if produce_news_headlines is not None:
                        try:
                            _news_headlines = await produce_news_headlines(
                                symbol, current_time_utc
                            )
                        except Exception:  # noqa: BLE001 — news must never cost a cycle
                            logging.warning(
                                "News headlines producer failed for %s — no news "
                                "this cycle (agent will abstain).",
                                symbol,
                                exc_info=True,
                            )
                            # None (not []): a producer crash must not add the state
                            # key — keeps the dormant path byte-identical even then.
                            # When armed, the agent abstains on the missing channel
                            # with its own WARNING (fail-transparent either way).
                            _news_headlines = None
                        if _news_headlines is not None:
                            _symbol_state["news_headlines"] = _news_headlines
                    graph_states.append(_symbol_state)

                    if symbol in self._skipped_symbols:
                        self._skipped_symbols.remove(symbol)

                logging.info(
                    "Engine: %d graph_states prepared (shutdown=%s)",
                    len(graph_states),
                    self._shutdown_event.is_set(),
                )
                if self._shutdown_event.is_set():
                    break

                if graph_states:

                    if is_lstm:
                        # LSTMDynamic: sequenziell — jeder Buy sieht aktuelle Buying Power
                        for state in graph_states:
                            if self._shutdown_event.is_set():
                                break
                            try:
                                res = await local_active_strategy.run_for_symbol(
                                    state["symbol"],
                                    state["ohlc"],
                                    self.current_market_data,
                                    current_time_utc,
                                )
                                if isinstance(res, SignalEvent):
                                    await self._process_signal_event(res)
                            except Exception as e:
                                logging.error(
                                    "Error in sequential strategy task for %s: %s",
                                    state["symbol"],
                                    e,
                                )
                    else:
                        # Non-LSTM: LangGraph-Dispatch parallel via asyncio.gather
                        # Layer 2: Per-Symbol timeout (MiFID II Art. 17)
                        _graph = (
                            build_symbol_eval_graph()
                            if build_symbol_eval_graph
                            else None
                        )
                        results = []  # default empty for CycleWatchdog
                        if _graph is not None:
                            logging.warning(
                                "🚀 TRADING LOOP: EXECUTION GRAPH FOR %d SYMBOLS STARTED!",
                                len(graph_states),
                            )
                            if get_config().FULL_UNIVERSE_TRADING_ENABLED:
                                # Phase B (ADR-FU01): with the full universe (~500) the
                                # gather below would open ~500 concurrent graph
                                # invocations x 9 round-table agents (~4,500 in-flight
                                # LLM calls) against ONE local Ollama and exhaust its
                                # connection pool — an outage, not a cost. Today's
                                # 200-truncation is the only thing bounding it, and Phase B
                                # removes that, so the bound must be replaced, not dropped.
                                # Mirrors the scanner's proven per-loop semaphore.
                                # The per-symbol timeout starts AFTER a slot is acquired,
                                # so a queued symbol is never timed out for merely waiting.
                                _sem = asyncio.Semaphore(
                                    max(
                                        1,
                                        int(
                                            get_config().FULL_UNIVERSE_EVAL_CONCURRENCY
                                        ),
                                    )
                                )

                                # _sem and _graph are bound as defaults so the closure
                                # captures THIS cycle's objects, not whatever the names
                                # point at when the coroutine finally runs (flake8 B023).
                                # Benign today — the gather completes inside this
                                # iteration — but a late-binding closure over a loop
                                # variable is a trap that only shows up once someone
                                # moves the await, and then every symbol would evaluate
                                # against the last cycle's graph.
                                async def _bounded_eval(
                                    state, _sem=_sem, _graph=_graph
                                ):
                                    async with _sem:
                                        return await asyncio.wait_for(
                                            _graph.ainvoke(
                                                state,
                                                config={
                                                    "configurable": {
                                                        "market_data": self.current_market_data
                                                    }
                                                },
                                            ),
                                            timeout=_SYMBOL_EVAL_TIMEOUT_SEC,
                                        )

                                results = await asyncio.gather(
                                    *[_bounded_eval(state) for state in graph_states],
                                    return_exceptions=True,
                                )
                            else:
                                results = await asyncio.gather(
                                    *[
                                        asyncio.wait_for(
                                            _graph.ainvoke(
                                                state,
                                                config={
                                                    "configurable": {
                                                        "market_data": self.current_market_data
                                                    }
                                                },
                                            ),
                                            timeout=_SYMBOL_EVAL_TIMEOUT_SEC,
                                        )
                                        for state in graph_states
                                    ],
                                    return_exceptions=True,
                                )
                        else:
                            # Fallback: direktes gather wenn LangGraph nicht verfügbar
                            results = await asyncio.gather(
                                *[
                                    asyncio.wait_for(
                                        local_active_strategy.run_for_symbol(
                                            s["symbol"],
                                            s["ohlc"],
                                            self.current_market_data,
                                            current_time_utc,
                                        ),
                                        timeout=_SYMBOL_EVAL_TIMEOUT_SEC,
                                    )
                                    for s in graph_states
                                ],
                                return_exceptions=True,
                            )

                        for i, res in enumerate(results):
                            if isinstance(res, asyncio.TimeoutError):
                                _sym = (
                                    graph_states[i]["symbol"]
                                    if i < len(graph_states)
                                    else "?"
                                )
                                logging.warning(
                                    "MIFID_AUDIT[%s] SYMBOL_TIMEOUT: Evaluation "
                                    "exceeded %.0fs — skipped",
                                    _sym,
                                    _SYMBOL_EVAL_TIMEOUT_SEC,
                                )
                            elif isinstance(res, Exception):
                                logging.error(
                                    "Error in graph dispatch idx %s: %s", i, res
                                )
                            elif isinstance(res, dict) and isinstance(
                                res.get("signal"), SignalEvent
                            ):
                                await self._process_signal_event(res["signal"])
                                if res.get("round_table_scores"):
                                    self._last_round_table_state = res[
                                        "round_table_scores"
                                    ]
                            elif isinstance(res, SignalEvent):
                                await self._process_signal_event(res)

                            # Cache the Round Table state even if no signal was generated (e.g. HOLD/VETO)
                            if isinstance(res, dict) and res.get("round_table_scores"):
                                self._last_round_table_state = res["round_table_scores"]
                                # #2630: retarget specialist reports to positions ∪ top-N consensus
                                await self._reconcile_specialist_coverage()

                t_strategy_done = time.perf_counter()

                # Record Latency
                total_cycle_ms = (t_strategy_done - t_start) * 1000
                data_fetch_ms = (t_data_fetched - t_start) * 1000
                strategy_exec_ms = (t_strategy_done - t_data_fetched) * 1000

                self._cycle_latencies.append(total_cycle_ms)
                self._last_cycle_details = {
                    "total_ms": round(total_cycle_ms, 2),
                    "data_fetch_ms": round(data_fetch_ms, 2),
                    "strategy_exec_ms": round(strategy_exec_ms, 2),
                    "symbols_processed": len(symbols_to_process),
                    "timestamp": time.time(),
                }

                self.cloud_logger.log_latency_metric(
                    total_ms=total_cycle_ms,
                    data_fetch_ms=data_fetch_ms,
                    strategy_exec_ms=strategy_exec_ms,
                    symbol_count=len(symbols_to_process),
                )

                logging.info(
                    f"⏱️ Cycle Latency: {total_cycle_ms:.1f}ms (Data: {data_fetch_ms:.1f}ms, "
                    f"Exec: {strategy_exec_ms:.1f}ms) for {len(symbols_to_process)} symbols"
                )

                if total_cycle_ms > 2000:
                    logging.warning(
                        f"⚠️ HIGH LATENCY: Cycle took {total_cycle_ms:.1f}ms (>2000ms)"
                    )
                    # PR B (fail-safe): pure-observation high-latency counter.
                    _bump_loop_counter(self, "_high_latency_cycles")

                # CycleWatchdog: Track whether the cycle completed any evaluations
                # HOLD/VETO = healthy (system working). Empty = timeout/crash.
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    if not is_lstm and graph_states:
                        _completed_evals = sum(
                            1 for r in results if isinstance(r, dict) and "signal" in r
                        )
                        if _completed_evals == 0:
                            self._cycle_watchdog.record_empty_cycle(len(graph_states))
                        else:
                            self._cycle_watchdog.record_successful_cycle()
                    elif is_lstm:
                        # LSTM sequential path always completes
                        self._cycle_watchdog.record_successful_cycle()

                # PR B (fail-safe): monotone trading-cycle liveness counter, bumped
                # once per completed cycle. Pure observation — never gates flow.
                _bump_loop_counter(self, "_cycles_completed")

            except APIError as e:
                logging.error("Alpaca API Error live loop: %s", e)
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    self._cycle_watchdog.record_empty_cycle(
                        len(graph_states) if graph_states else 0
                    )
                await asyncio.sleep(20)
            except BrokerConnectionError as e:
                logging.error("Broker connection lost in live loop: %s", e)
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    self._cycle_watchdog.record_empty_cycle(
                        len(graph_states) if graph_states else 0
                    )
                await asyncio.sleep(30)
            except Exception as e:
                logging.error("Unexpected error live loop: %s", e, exc_info=True)
                if hasattr(self, "_cycle_watchdog") and self._cycle_watchdog:
                    self._cycle_watchdog.record_empty_cycle(
                        len(graph_states) if graph_states else 0
                    )
                await asyncio.sleep(30)

            if self._shutdown_event.is_set():
                break

            # --- Cycle-Boundary: Graceful Handover prüfen ---
            registry = getattr(self, "agent_registry", None)
            if registry is not None and registry.has_pending_swap():
                await self._perform_graceful_handover()

            # #2548 sim driver: under SIM_MODE advance the virtual clock one cadence step and
            # terminate at the window's end (flat-out — no real sleep). Byte-identical when off.
            if getattr(get_config(), "SIM_MODE", False):
                from core.sim.clock import get_sim_clock

                sim_clock = get_sim_clock()
                sim_clock.advance()
                # Count completed sim cycles (surfaced as SimResult.n_cycles).
                self._sim_cycles = getattr(self, "_sim_cycles", 0) + 1
                if not sim_clock.is_open:
                    self.strategy_running.clear()
                    # #2630 sim decision-grade: signal TRUE window completion on a DEDICATED
                    # event. The runner must not wait on strategy_running — the monitor's initial
                    # strategy switch (None→RLAgent) transiently clears it, which the runner would
                    # otherwise mistake for "sim done" (→ the 0-cycle premature exit).
                    ev = getattr(self, "_sim_complete", None)
                    if ev is not None:
                        ev.set()
                await asyncio.sleep(0)
            else:
                # INC-6: modest cadence when the market is closed (research still ran this
                # cycle, but there is nothing time-critical to react to) — 300s vs the
                # normal 60s live cadence.
                await asyncio.sleep(300 if cycle_market_closed else 60)

        logging.info("Live trading loop exited.")

    async def _perform_graceful_handover(self) -> None:
        """
        Führt den ausstehenden Strategie-Swap am Cycle-Ende durch.

        Ablauf:
            1. Offene Positionen vom Broker holen
            2. Neue Strategy über on_positions_received() informieren
            3. commit_swap() in Registry ausführen (atomarer Wechsel)
            4. active_strategy-Shim synchronisieren (Backward-Compat)
            5. Slack-Benachrichtigung

        Fehler-Isolation: Exception → Logging, KEIN Swap-Commit (Kapitalschutz).
        """
        registry = getattr(self, "agent_registry", None)
        if registry is None or not registry.has_pending_swap():
            return

        old_strategy = registry.get_active()
        old_name = getattr(old_strategy, "strategy_name", "unknown")

        try:
            # 1. Offene Positionen vom Broker holen
            open_positions = []
            if getattr(self, "api", None) is not None:
                try:
                    open_positions = await asyncio.to_thread(self.api.get_all_positions)
                except Exception as e:
                    logging.warning(
                        "Graceful Handover: get_all_positions failed: %s", e
                    )

            # 2. Pending Strategy ermitteln
            pending_name = registry._pending_name  # noqa: SLF001
            pending_strategy = registry._strategies.get(pending_name)  # noqa: SLF001

            # 3. Neue Strategy über Positionen informieren
            if pending_strategy is not None and open_positions:
                if hasattr(pending_strategy, "on_positions_received"):
                    pending_strategy.on_positions_received(open_positions)
                    logging.info(
                        "Graceful Handover: %d offene Positionen an '%s' übergeben.",
                        len(open_positions),
                        pending_name,
                    )

            # 4. Tatsächlicher Swap (atomar in Registry)
            registry.commit_swap()

            # 5. Backward-Compat: active_strategy-Shim synchronisieren
            if hasattr(self, "strategy_lock"):
                with self.strategy_lock:
                    self.active_strategy = registry.get_active()

            new_name = getattr(registry.get_active(), "strategy_name", pending_name)
            self._log_strategy_thought(
                f"🔄 Graceful Handover abgeschlossen: '{old_name}' → '{new_name}' "
                f"({len(open_positions)} offene Positionen übergeben)"
            )

        except Exception as e:
            # Kapitalschutz: Bei Fehler KEIN commit — System bleibt auf alter Strategy
            logging.error(
                "Graceful Handover FEHLGESCHLAGEN für '%s': %s. "
                "Aktive Strategy bleibt '%s'.",
                getattr(registry, "_pending_name", "?"),
                e,
                old_name,
                exc_info=True,
            )
            # _pending_name bleibt gesetzt — nächster Cycle versucht erneut
