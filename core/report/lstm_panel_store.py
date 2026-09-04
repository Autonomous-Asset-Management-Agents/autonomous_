"""RPT-8 (Epic #1998) — the live LSTM panel store + reader.

The report's quant view (RPT-1 :mod:`core.report.fact_set`) needs, per symbol,
the engine's REAL per-cycle LSTM output: a score *history* (raw score over time
-> score-trend + own-percentile) and a cross-sectional snapshot (-> rank /
percentile). The engine's :class:`LSTMDynamic` overwrites ``_lstm_rank_cache``
each cycle and keeps no history, and the standalone ``StockSpecialistAgent`` has
no handle to the strategy instance.

This module is the **decoupled seam** between the two (mirrors the ``model_registry``
pattern): the engine loop *writes* one line at the end of ``update_lstm_rankings``
(flag-gated, lazy-imported — see RPT-8 wiring), and the report's
:class:`EngineLstmPanelReader` *reads* it. Both live in the same engine process
(the trading loop and ``engine.specialist_registry`` share one process), so an
in-memory singleton is shared between writer and reader.

Design guarantees
-----------------
* **Writing is read-only telemetry.** The WRITER never touches the decision path:
  the engine's ``_lstm_rank_cache`` / ``_allocation_weights`` / rank helpers are
  untouched, and the store only *observes* an already-computed ranking.
* **⚠ READING is no longer telemetry-only.** ``cross_section_standing()`` is now read
  by ``LSTMSignalAgent`` when ``LSTM_VOTE_USES_CROSS_SECTIONAL_RANK`` is ON, which
  puts this module's OUTPUT on the trading-decision path. Design choices made under
  the old telemetry assumption must be re-examined before that flag is flipped, not
  after — a number that was only ever going to be printed in a report is held to a
  lower bar than one that decides a trade.
* **Dormant writer when OFF.** The single writer call is guarded by
  ``REPORT_GENERATOR_V2_ENABLED`` with a lazy import at the call site, so with the
  flag off the engine does zero extra work. ⚠ The MODULE is nevertheless imported at
  process start: ``fact_set`` imports ``cross_section_standing`` at module scope so
  the report and the vote share one definition of "rank". Importing it is inert (no
  side effects, the store is created lazily) — but the older claim that "with the
  flag off this module is never imported" is no longer true, and an auditable module
  may not carry a guarantee its own package contradicts.
* **PIT.** The reader filters a symbol's history to ``<= as_of`` and returns the
  latest cross-section snapshot ``<= as_of``. The store is live-only (every entry
  happened at/before "now"); historical / backtest reports use the FactSet
  fixture path (``InMemoryDataContext``), never this store — an honest limit.
* **Bounded.** Per-symbol history is a rolling ``deque(maxlen=SCORE_HISTORY_LEN)``
  (one point per calendar day, latest wins) and daily snapshots are capped at
  ``SNAPSHOT_HISTORY_LEN`` — no unbounded growth even as the universe churns.
* **Thread-safe.** A single lock guards every mutation and every read returns a
  copy, so a background thread reading while the engine writes can never see a
  torn structure or ``changed size during iteration``.

Known limits (honest, documented — never silent wrong numbers)
--------------------------------------------------------------
* **Single-process / single-instance.** Writer (engine trading loop) and reader
  (``engine.specialist_registry`` specialists) share one process, so the module
  singleton is shared — verified for the desktop edition. Under a horizontally
  scaled enterprise deployment (>1 engine instance) each instance has its own
  store; a report served by an instance that hasn't accrued history degrades to
  the honest gap (the FactSet falls back to the static model-card view), never a
  fabricated number.
* **In-memory only.** A restart/redeploy resets the store, so the score-trend is
  really "since last start". The report shows exactly the points it has (e.g.
  "last 3 scores"); the audit-critical reliability constants live in the
  committed model card, not here, so a short history never weakens the audit.
* **Live-only PIT.** Every entry happened at/before "now"; the reader bounds a
  symbol's series and the cross-section to ``<= as_of``. Historical / backtest
  reports use the FactSet fixture path (``InMemoryDataContext``), never this
  store. All dates are normalised to a naive calendar ``date`` on both sides.
"""

from __future__ import annotations

import logging
import statistics
import threading
from collections import OrderedDict, deque
from datetime import date, datetime
from typing import Deque, Dict, List, Mapping, Optional, Sequence, Tuple

import config

_ScoreRow = Tuple[date, float]

# Per-symbol rolling score history — one point per calendar day. 20 trading days
# (~4 weeks) is enough for the report's score-trend + own-percentile read.
SCORE_HISTORY_LEN = 20
# Daily cross-section snapshots retained for the rank/percentile view. Kept EQUAL
# to the history window so churn-pruning (dropping per-symbol points older than
# the oldest retained snapshot) never truncates history to a shorter horizon.
SNAPSHOT_HISTORY_LEN = 20


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError(f"Unsupported date type: {type(value)!r}")


class LstmPanelStore:
    """In-memory, bounded, thread-safe store of the engine's live LSTM panel."""

    def __init__(
        self,
        history_len: int = SCORE_HISTORY_LEN,
        snapshot_len: int = SNAPSHOT_HISTORY_LEN,
    ) -> None:
        self._lock = threading.Lock()
        self._history: Dict[str, Deque[_ScoreRow]] = {}
        # date -> {symbol: score}; ordered so eviction is FIFO by insertion.
        self._snapshots: "OrderedDict[date, Dict[str, float]]" = OrderedDict()
        self._history_len = int(history_len)
        self._snapshot_len = int(snapshot_len)
        # #2683: optional durability. INJECTED, never constructed here — so unit tests and
        # the sim run without a database. Concrete adapter: lstm_panel_persistence.py.
        self._persistence = None

    # ---- #2683 durability ---------------------------------------------------
    def attach_persistence(self, adapter) -> None:
        """Attach a ``save(snapshot_date, rows)`` / ``load(max_dates)`` adapter."""
        with self._lock:
            self._persistence = adapter

    def _persistence_enabled(self) -> bool:
        try:
            return bool(
                getattr(config.get_config(), "LSTM_PANEL_PERSISTENCE_ENABLED", False)
            )
        except Exception:  # noqa: BLE001 — telemetry must not need a readable config
            return False

    def snapshot_dates(self) -> List[date]:
        """The snapshot dates currently in the window, oldest first.

        Observability for #2683: the smoothing can be ARMED and still be a no-op when the
        window holds a single date (fresh process). That has to be readable, not guessed.
        """
        with self._lock:
            return sorted(self._snapshots)

    def hydrate_from(self, rows) -> int:
        """Load ``(snapshot_date, symbol, score)`` rows into the window; returns #dates.

        DATE-ordered by construction — never insertion order. A backfill or an out-of-order
        query result must not decide which days the median spans (the same trap #2680
        documents for ``cross_section_smoothed``). Capped to the ``snapshot_len`` NEWEST
        dates, idempotent, and malformed rows are skipped individually so one bad row cannot
        void the hydration. Also rebuilds the per-symbol history the report's score-trend reads.
        """
        grouped: Dict[date, Dict[str, float]] = {}
        for row in rows or ():
            try:
                d, sym, score = row[0], str(row[1]), row[2]
                if score is None:
                    continue
                grouped.setdefault(_as_date(d), {})[sym] = float(score)
            except (TypeError, ValueError, IndexError):
                continue
        if not grouped:
            return 0
        keep = sorted(grouped)[-self._snapshot_len :]
        with self._lock:
            for d in keep:
                self._snapshots[d] = dict(grouped[d])
            # Re-order the whole window chronologically so eviction (FIFO by insertion) and
            # every order-sensitive reader agree even after a partial load.
            for d in sorted(self._snapshots):
                self._snapshots.move_to_end(d)
            while len(self._snapshots) > self._snapshot_len:
                self._snapshots.popitem(last=False)
            self._history.clear()
            for d in sorted(self._snapshots):
                for sym, score in self._snapshots[d].items():
                    dq = self._history.get(sym)
                    if dq is None:
                        dq = deque(maxlen=self._history_len)
                        self._history[sym] = dq
                    dq.append((d, float(score)))
        return len(keep)

    def hydrate_from_persistence(self) -> int:
        """Load the retained window from the attached adapter — best-effort and quiet-safe.

        A missing adapter, a disabled flag or a failing query leaves an EMPTY but perfectly
        working store: the smoothing then degrades to point-in-time, which is exactly the
        pre-#2683 behaviour — never a crash, never a fabricated window.

        SKIPPED under ``SIM_MODE``: a sim builds its own deterministic panel from the pinned
        corpus (``core/sim/runner._seed_lstm_panel``); inheriting the operator's live
        snapshots would make a sim run depend on whatever that engine happened to observe.
        """
        if self._persistence is None or not self._persistence_enabled():
            return 0
        try:
            if bool(getattr(config.get_config(), "SIM_MODE", False)):
                return 0
        except Exception:  # noqa: BLE001
            pass
        try:
            rows = self._persistence.load(self._snapshot_len)
        except (
            Exception
        ) as exc:  # noqa: BLE001 — durability is telemetry, never a blocker
            logging.warning(
                "LSTM panel hydration failed (%s) — continuing with an empty window; the "
                "trend smoothing degrades to point-in-time until the panel refills (§5.6).",
                exc,
            )
            return 0
        n = self.hydrate_from(rows)
        logging.info(
            "LSTM panel hydrated from persistence: %d snapshot date(s) — the trend median "
            "spans that many days (1 means it is effectively point-in-time).",
            n,
        )
        return n

    def record_cycle(self, as_of, ranked: Sequence[Tuple[str, float]]) -> None:
        """Observe one engine cycle. ``ranked`` is the ``_lstm_rank_cache`` shape:
        a sequence of ``(symbol, score)``. Idempotent within a calendar day
        (a later same-day cycle replaces that day's point). Never raises on
        malformed rows — a bad row is skipped (telemetry must not crash the loop).
        """
        d = _as_date(as_of)
        snap: Dict[str, float] = {}
        with self._lock:
            for row in ranked or ():
                try:
                    sym = str(row[0])
                    score = float(row[1])
                except (TypeError, ValueError, IndexError):
                    continue
                snap[sym] = score
                dq = self._history.get(sym)
                if dq is None:
                    dq = deque(maxlen=self._history_len)
                    self._history[sym] = dq
                if dq and dq[-1][0] == d:
                    dq[-1] = (d, score)  # same day -> latest wins
                else:
                    dq.append((d, score))
            self._snapshots[d] = snap
            self._snapshots.move_to_end(d)
            while len(self._snapshots) > self._snapshot_len:
                evicted, _ = self._snapshots.popitem(last=False)
                self._prune_history_before(evicted)
            adapter = self._persistence
            retained = sorted(self._snapshots)

        # #2683: durability, OUTSIDE the lock (a DB round-trip must not hold the panel lock
        # that every reader contends on) and strictly best-effort — a failing write costs
        # telemetry, never the trading cycle. The in-memory write above already stands.
        if adapter is not None and snap and self._persistence_enabled():
            try:
                adapter.save(d, list(snap.items()))
                prune_before = retained[0] if retained else None
                if prune_before is not None and hasattr(adapter, "prune_before"):
                    adapter.prune_before(prune_before)
            except Exception as exc:  # noqa: BLE001
                logging.warning(
                    "LSTM panel persistence write failed for %s (%s) — the in-memory panel "
                    "is unaffected; the trend window will be shorter after a restart (§5.6).",
                    d.isoformat(),
                    exc,
                )

    def _prune_history_before(self, cutoff: date) -> None:
        """Drop per-symbol points older than the oldest retained snapshot and
        forget symbols with no remaining history (bounds the symbol dict against
        delisted/churned tickers). Caller holds the lock."""
        oldest = next(iter(self._snapshots), None)
        if oldest is None:
            return
        for sym in list(self._history.keys()):
            dq = self._history[sym]
            while dq and dq[0][0] < oldest:
                dq.popleft()
            if not dq:
                del self._history[sym]

    def history(self, symbol: str) -> List[_ScoreRow]:
        with self._lock:
            dq = self._history.get(str(symbol))
            return list(dq) if dq else []

    def latest_snapshot_date(self) -> Optional[date]:
        """Date of the most recent cross-section snapshot, or None if the store is empty.

        Consumers (e.g. the rank-cache funnel) use this to refuse a STALE panel — a populated but
        outdated snapshot (the rank producer stopped) would otherwise silently pin decisions to an old
        top-K. `cross_section_at` alone cannot detect this; it only reports thinness, not age.
        """
        with self._lock:
            return max(self._snapshots) if self._snapshots else None

    def cross_section_at(self, as_of) -> Dict[str, float]:
        d = _as_date(as_of)
        with self._lock:
            best_d: Optional[date] = None
            best: Optional[Dict[str, float]] = None
            for sd, snap in self._snapshots.items():
                if sd <= d and (best_d is None or sd > best_d):
                    best_d, best = sd, snap
            return dict(best) if best else {}

    def cross_section_smoothed(
        self,
        as_of,
        window_days: int = 10,
        min_coverage: int = 5,
    ) -> Dict[str, float]:
        """#2680 "Patient AI" — the TREND cross-section: per-symbol MEDIAN over the
        last ``window_days`` snapshot dates at or before ``as_of``.

        Why this exists: ``cross_section_at`` is a point-in-time photo, recomputed
        every cycle. Measured consequence (2026-08-05): IVZ was bought while
        point-in-time top-10 at 14:00 UTC and rotated out at point-in-time rank
        242/501 at 17:09 UTC — ~230 places in three hours. A one-day spike both
        bought and sold the name. The smoothed table is what a *patient* selector
        must rank on.

        Design decisions, each one a defect the naive moving average had:

        * **MEDIAN, not mean** — the mean lets a single extreme day carry the score
          (0.1/0.1/0.1/0.1/9.0 averages to 1.88 and out-ranks a steady 0.5), which
          re-imports exactly the spike sensitivity this method removes.
        * **window selected BY DATE** — ``_snapshots`` is an OrderedDict in WRITE
          order; ``reversed()`` over it returns the newest *written*, not the newest
          *dated*, snapshot. A backfill / out-of-order ``record_cycle`` would silently
          smooth the wrong days. ``cross_section_at`` already scans by date; so does
          this. ``window_days`` therefore counts the last N snapshot DATES the panel
          actually recorded (trading days), not calendar days.
        * **coverage floor** — a symbol seen on fewer than ``min_coverage`` of those
          dates is NOT ranked at all. Otherwise a freshly covered name is "smoothed"
          over its single high sample and tops the book — the spike, one layer down.
          The floor scales down on a COLD START (fewer dates than the window exist),
          so a fresh engine smooths over what it has instead of blocking.
        * **PIT-honest** — dates after ``as_of`` never enter the window.

        Returns ``{}`` when no snapshot at or before ``as_of`` exists; every caller
        treats an empty cross-section as "no ranking" (the vote abstains, the funnel
        falls back to the full universe, rotation skips = fail-safe HOLD).
        """
        d = _as_date(as_of)
        try:
            window = max(1, int(window_days))
        except (TypeError, ValueError):
            window = 10
        try:
            floor = max(1, int(min_coverage))
        except (TypeError, ValueError):
            floor = 5

        with self._lock:
            # BY DATE, newest first — never insertion order (see docstring).
            dates = sorted((sd for sd in self._snapshots if sd <= d), reverse=True)[
                :window
            ]
            if not dates:
                return {}
            samples: Dict[str, List[float]] = {}
            for sd in dates:
                for sym, score in self._snapshots[sd].items():
                    if score is None:
                        continue
                    try:
                        samples.setdefault(sym, []).append(float(score))
                    except (TypeError, ValueError):
                        continue  # a malformed row drops out, never voids the panel

        # Cold start: the floor can never exceed the history that exists.
        effective_floor = min(floor, len(dates))
        out: Dict[str, float] = {}
        for sym, values in samples.items():
            if len(values) < effective_floor:
                continue
            out[sym] = statistics.median(values)
        return out

    def reset(self) -> None:
        with self._lock:
            self._history.clear()
            self._snapshots.clear()


def active_cross_section(store: "LstmPanelStore", as_of) -> Dict[str, float]:
    """#2680 — the ONE cross-section seam every consumer reads.

    Resolved at CALL time (never at import: a module-level constant would freeze
    the flag before the engine env is applied — the same trap that made the
    operator-set exit envs inert on 2026-08-05).

    ``LSTM_RANK_SMOOTHING_ENABLED`` OFF (**default**) → ``cross_section_at``,
    byte-identical to today. ON → ``cross_section_smoothed`` with the configured
    window / coverage floor.

    Deliberately ONE helper for all four consumers — the LSTM vote
    (``agents.py``), the rotation SELL trigger and the rank funnel
    (``trading_loop.py``) and the report card (``api_routes.py``). The first #2680
    revision smoothed only the funnel, which picks WHICH symbols get evaluated
    and always includes the holdings: both deciding seams kept reading the raw
    point-in-time rank, so the churn it was written against stayed. Routing every
    consumer through here also preserves the invariant this module was built on —
    a report can never show a different standing than the board voted on
    (``cross_section_standing`` below).

    Fail-safe: any config error falls back to the point-in-time table + WARNING
    (§5.6) — never a silent switch of the ranking basis.
    """
    try:
        cfg = config.get_config()
        enabled = bool(getattr(cfg, "LSTM_RANK_SMOOTHING_ENABLED", False))
        window = int(getattr(cfg, "LSTM_RANK_SMOOTHING_WINDOW_DAYS", 10) or 10)
        floor = int(getattr(cfg, "LSTM_RANK_SMOOTHING_MIN_COVERAGE", 5) or 5)
    except (
        Exception
    ):  # noqa: BLE001 — a broken config must not change the ranking basis
        logging.warning(
            "LSTM rank smoothing: config unreadable — falling back to the "
            "point-in-time cross-section (§5.6)."
        )
        return store.cross_section_at(as_of)
    if not enabled:
        return store.cross_section_at(as_of)
    # Duck-typed store variants exist (the module docstring's EngineLstmPanelReader
    # adapter is one: it carries `cross_section`, not `cross_section_at`). A store
    # without the smoothed method must DEGRADE to the point-in-time table, never raise:
    # the rotation lever wraps this call in a broad try/except, so an AttributeError
    # here would silently disable exits altogether — the fail-silent class this
    # codebase keeps getting bitten by (#2585). Loud + degraded, never quiet + dead.
    if not hasattr(store, "cross_section_smoothed"):
        logging.warning(
            "LSTM rank smoothing armed but %s has no cross_section_smoothed() — "
            "falling back to the point-in-time cross-section (§5.6).",
            type(store).__name__,
        )
        return store.cross_section_at(as_of)
    return store.cross_section_smoothed(as_of, window_days=window, min_coverage=floor)


def cross_section_standing(
    xsec: Mapping[str, float], symbol: str
) -> Tuple[Optional[float], Optional[int], Optional[int]]:
    """Where ``symbol`` stands in the cross-section — ``(percentile, rank, n)``.

    THE single definition of "rank" in this codebase, deliberately. Both the
    auditable report (FactSet ``lstm_xsec_percentile`` / ``lstm_xsec_rank``) and
    the Round Table's LSTM vote read it, so a report can never tell the user one
    standing while the board votes on another. Returns ``(None, None, None)`` when
    the symbol is absent from the panel — the caller must abstain, never guess.

    ``percentile`` = share of the universe scoring BELOW the symbol (0..100);
    ``rank`` counts from the top (1 = best).
    """
    # Defense-in-depth: the live ingest (``record_cycle``) float-coerces and drops
    # bad rows, so production scores are always float. But this helper now feeds the
    # board VOTE, not just telemetry — a malformed panel with a ``None`` score must
    # abstain, never raise ``TypeError`` on ``s < base`` into the round table. A
    # None-scored peer is not comparable, so it drops out of the ranking entirely.
    vals = [v for v in xsec.values() if v is not None]
    if symbol not in xsec or xsec.get(symbol) is None or not vals:
        return None, None, None
    base = xsec[symbol]
    n = len(vals)
    pct = 100.0 * sum(1 for s in vals if s < base) / n
    rank = 1 + sum(1 for s in vals if s > base)
    return pct, rank, n


class EngineLstmPanelReader:
    """Adapts :class:`LstmPanelStore` to the FactSet ``lstm_reader`` contract
    (``.series(symbol)`` / ``.cross_section(as_of)``), PIT-bounding the series to
    the report's ``as_of``."""

    def __init__(self, store: LstmPanelStore, as_of) -> None:
        self._store = store
        self._as_of = _as_date(as_of)

    def series(self, symbol: str) -> List[_ScoreRow]:
        return [(d, s) for (d, s) in self._store.history(symbol) if d <= self._as_of]

    def cross_section(self, as_of) -> Mapping[str, float]:
        return self._store.cross_section_at(as_of)


# --- process singleton -------------------------------------------------------
_store_singleton: Optional[LstmPanelStore] = None
_singleton_lock = threading.Lock()


def _hydrate_singleton(store: "LstmPanelStore") -> None:
    """#2683: attach the SQLAlchemy adapter and load the retained window — ONCE, on the
    first access anywhere in the process.

    Deliberately here rather than at an engine boot site: `get_store()` is the single door
    every entry point (engine, monitor loop, CLI, sim) already walks through, so there is no
    boot wiring left to forget. Fully best-effort — an unavailable DB leaves the store empty
    and working (pre-#2683 behaviour), never raises into a caller.
    """
    try:
        from core.report.lstm_panel_persistence import SqlPanelPersistence

        store.attach_persistence(SqlPanelPersistence())
        store.hydrate_from_persistence()
    except Exception as exc:  # noqa: BLE001 — durability must never block the panel
        logging.warning(
            "LSTM panel persistence unavailable (%s) — memory-only for this process; the "
            "trend smoothing spans only the days this process observes (§5.6).",
            exc,
        )


def get_store() -> LstmPanelStore:
    """The shared engine-process panel store (writer and readers use the same)."""
    global _store_singleton
    if _store_singleton is None:
        with _singleton_lock:
            if _store_singleton is None:
                store = LstmPanelStore()
                # Hydrate BEFORE publishing the singleton so no reader can observe a
                # half-filled window, and exactly once per process.
                _hydrate_singleton(store)
                _store_singleton = store
    return _store_singleton
