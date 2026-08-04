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

import threading
from collections import OrderedDict, deque
from datetime import date, datetime
from typing import Deque, Dict, List, Mapping, Optional, Sequence, Tuple

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

    def reset(self) -> None:
        with self._lock:
            self._history.clear()
            self._snapshots.clear()


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


def get_store() -> LstmPanelStore:
    """The shared engine-process panel store (writer and readers use the same)."""
    global _store_singleton
    if _store_singleton is None:
        with _singleton_lock:
            if _store_singleton is None:
                _store_singleton = LstmPanelStore()
    return _store_singleton
