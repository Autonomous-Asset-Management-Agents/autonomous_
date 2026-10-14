# core/engine/marktdaten.py
# #4249 (H-2h, ARC-E6 #3738) — umgezogen aus core/engine/trading_loop.py, wortgleich.
# Verantwortlichkeit: Marktdaten und Konto der Handelsschleife (Snapshot-Abruf in Chunks,
# Specialist-Abdeckung, Rang-Lauf bei geschlossenem Markt, Breaker-Feed, Bar-Cache).
"""Marktdaten und Konto: was die Handelsschleife je Zyklus von Markt und Broker liest.

#4249 (H-2h) zieht fuenf Methoden aus ``TradingLoopMixin`` hierher (Schnitt-Entscheidung #4184,
``docs/3738-arc-e6-gestalt/H2_SCHNITT_trading_loop.md``): den Snapshot-Abruf in Chunks, die
flaggesteuerte Specialist-Abdeckung (#2630), den Rang-Lauf ueber das ganze Universum bei
geschlossenem Markt, den Feed des kontoweiten Drawdown-Breakers (#2978, halt-only) und das
Aufwaermen des Bar-Caches.

Die Ruempfe sind wortgleich mit dem Stand vor dem Umbau. Abweichung: Die Patch-Ziele der
Tests (``get_config``, ``engine_now``, ``CompositionRoot``, ``StockSnapshotRequest``) liest das
Modul zur Laufzeit als ``_tl.<name>`` am Kernmodul, damit Patches auf
``core.engine.trading_loop`` weiter wirken (#4184 §3). ``config.ALPACA_DATA_FEED`` und
``config.get_config()`` in ``_reconcile_specialist_coverage`` bleiben wortgleich: Tests patchen
das Modul ``config`` direkt. Der ``_tl``-Import steht am Dateiende, damit der Kreis
Kern <-> Marktdaten in beiden Ladereihenfolgen traegt. ``TradingLoopMixin`` erbt
``MarktdatenMixin``; Aufrufe und Instanz-Mocks loesen ueber die MRO auf.

Plan: ``docs/4249-*/implementation_plan.md``.
"""

import asyncio
import logging
from datetime import timezone

import config
from core.engine.equity_fallback import resolve_equity


class MarktdatenMixin:
    """#4249 (H-2h): Marktdaten und Konto der Handelsschleife; Basis von ``TradingLoopMixin``."""

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
            size = int(getattr(_tl.get_config(), "SNAPSHOT_FETCH_CHUNK_SIZE", 100) or 0)
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
                request_params = _tl.StockSnapshotRequest(
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
            not _tl.get_config().FULL_UNIVERSE_TRADING_ENABLED
            and len(symbols_to_process) > 200
        ):
            symbols_to_process = symbols_to_process[:200]
        current_time_utc = _tl.engine_now(timezone.utc)
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
                _tl.get_config().LSTM_RANK_PANEL_STRATEGY_INDEPENDENT
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

    async def _update_live_account_equity(self, active_strategy) -> None:
        """#2978 (Epic #1891): feed the account-wide drawdown breaker on the LIVE
        path, once per cycle, BEFORE the trading_halted gate.

        The account-wide 7% portfolio-stop + progressive daily-drawdown circuit
        breaker live in ``RiskManager.update_account_equity`` — the only setter of
        ``trading_halted`` on this layer — but until now it was fed only by the Sim
        runner, leaving it structurally inert in live trading (MiFID II RTS 6
        pre-trade risk control that never fired). This mirrors
        ``simulation_runner``'s single ``update_account_equity`` call.

        Reads equity via the fail-safe house helper ``resolve_equity`` (live
        account equity, or ``DEFAULT_EQUITY`` with a WARNING) off the event loop
        via ``asyncio.to_thread`` (``api.get_account()`` is blocking). Calls the
        breaker with ``allow_unlock=False`` → halt-only: the live path can move
        ``trading_halted`` False→True but never auto-unlocks (EU AI Act Art. 14).

        Fail-safe (failure-direction: never crash the loop): ``rm is None`` → skip;
        any error → WARNING + continue. The breaker acts on exactly the instance
        (``active_strategy.risk_manager``) the halt gate reads two lines later.
        """
        rm = getattr(active_strategy, "risk_manager", None)
        if rm is None:
            return
        try:
            equity = await asyncio.to_thread(
                resolve_equity,
                getattr(self, "api", None),
                _tl.get_config().DEFAULT_EQUITY,
            )
            rm.update_account_equity(equity, allow_unlock=False)
        except (
            Exception
        ) as e:  # noqa: BLE001 — a breaker-feed error must not kill the loop
            logging.warning(
                "[AccountBreaker] live equity update failed; account-wide breaker "
                "not fed this cycle: %s",
                e,
                exc_info=True,
            )

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
            if not _tl.get_config().LSTM_BAR_STORE_WARMUP_ENABLED:
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
            now = _tl.CompositionRoot.get_instance().clock_port.now()
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


# #4249 (H-2h): Kern-Namen (get_config, engine_now, CompositionRoot, StockSnapshotRequest) liest
# das Modul ueber _tl, damit die Patch-Ziele der Tests wirken (#4184 §3). Am Dateiende: der
# Kreis Kern <-> Marktdaten traegt so in beiden Ladereihenfolgen (Muster
# core/engine/schleifen_stops.py).
from core.engine import trading_loop as _tl  # noqa: E402
