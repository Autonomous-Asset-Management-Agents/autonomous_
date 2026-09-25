"""#3361 — Regime-beyond-VIX: a credit-led risk-off composite as a BUY sizing throttle.

Evidence (Epic #2963, "VIX-blind sizing"): the largest loss-cut cluster of the 09/2026
paper weeks fell in an oil-/rates-driven risk-off while the VIX sat at ~13 ("Low
Volatility"). The regime detection is a pure function of the VIX and was blind to it. A
credit-led composite separated those days (mean 69) from the idiosyncratic ones (39;
baseline 50); the equal-weighted variant did not (49 vs 50).

What this module does (plan: docs/3361-regime-throttle/implementation_plan.md):
- **Pure signal**: five components, each as a 0..100 percentile within its own window
  (100 = most risk-off): credit (HYG 5d return, inverted), rates (TLT 5d, inverted),
  oil (USO 5d), correlation (mean pairwise correlation of the sector basket, 15d) and
  SPY drawdown from its 20d high (inverted). Weighted mean, credit-led.
- **Throttle**: when today's composite is at/above the operator's percentile of the
  composite's own history, NEW BUYs are scaled by ``REGIME_THROTTLE_SIZE_FACTOR``. It
  never sells, never blocks, never touches SELL / risk exits.
- **Fail-open everywhere**: missing/stale/short data, a missing lead component (credit),
  or any error ⇒ factor 1.0. A non-authoritative side-feed must never change trading
  market-wide because a fetch failed (same reasoning as the earnings guard, #3349).
- **Sim-safe**: ``now`` comes from the ENGINE clock; a cache stamped in the future
  relative to a simulated ``now`` is treated as stale ⇒ no throttle in replays.

Data: Alpaca daily bars through the engine's existing data provider (ETFs — no new
source, no key; FRED stays an optional enterprise-side enrichment, #3359).
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import time
from datetime import date
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# ADR-REG-01 (#3361): component weights — credit-led. Basis: the 09/2026 marking test
# (credit-led composite 69 vs 39 vs baseline 50; equal weights 49 vs 50 = no edge) and
# the literature finding that credit stress leads equity volatility. A deliberate
# setting, not a fitted optimum — kept constant (not operator-tunable) so the signal
# stays one auditable definition. Review with the first paper-measurement window.
WEIGHTS: Dict[str, float] = {
    "credit": 0.40,
    "oil": 0.20,
    "rates": 0.20,
    "correlation": 0.10,
    "spy_drawdown": 0.10,
}

CREDIT_PROXY = "HYG"  # high-yield credit ETF — price down = spreads widening
RATES_PROXY = "TLT"  # long treasuries — price down = yields up
OIL_PROXY = "USO"  # oil — price up = energy shock
MARKET_PROXY = "SPY"

# ADR-REG-02 (#3361): the correlation basket is the 11 SPDR sector ETFs — a fixed,
# book-independent set, so the reading does not move because the portfolio changed.
# (The marking test used the ~19 then-held names; with weight 0.10 this is the smallest
# component, and the dark period re-validates the basket before any promotion.)
CORRELATION_BASKET: Tuple[str, ...] = (
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
)  # fmt: skip

RETURN_DAYS = 5
CORRELATION_DAYS = 15
DRAWDOWN_DAYS = 20
# ADR-REG-03 (#3361): below 60 composite days a percentile of the composite's own
# history is not meaningful ⇒ fail-open. 4 calendar days tolerates a long weekend.
MIN_HISTORY_DAYS = 60
MAX_CACHE_AGE_DAYS = 4
HISTORY_CALENDAR_DAYS = 260  # ≈ 180 trading days, the validated window size

_CACHE_FILENAME = "regime_signal.json"


def all_symbols() -> List[str]:
    """Every symbol the producer fetches (proxies + correlation basket)."""
    return [CREDIT_PROXY, RATES_PROXY, OIL_PROXY, MARKET_PROXY, *CORRELATION_BASKET]


def _is_number(value: Any) -> bool:
    """A usable reading: a real, FINITE number. Review #3477 FINDING-03 — explicit
    instead of the ``v == v`` idiom, and stricter: ``inf``, ``bool`` and non-numeric
    values are rejected too, not only ``NaN``."""
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def riskoff_composite(
    components: Mapping[str, Optional[float]],
    weights: Mapping[str, float] = WEIGHTS,
) -> Optional[float]:
    """Weighted mean of the component percentiles (0..100); ``None`` = no reading.

    A missing component is left out and the rest is re-normalised. The LEAD component
    (credit) is mandatory and at least three components must be present — otherwise the
    number would no longer be the validated, credit-led signal ⇒ ``None`` (fail-open).
    """
    present = {
        k: float(v) for k, v in components.items() if k in weights and _is_number(v)
    }
    if "credit" not in present or len(present) < 3:
        return None
    total = sum(weights[k] for k in present)
    if total <= 0:
        return None
    return sum(weights[k] * present[k] for k in present) / total


def threshold_value(history: List[float], threshold_pct: int) -> Optional[float]:
    """The ``threshold_pct``-th percentile of the composite's own history, or ``None``
    when the history is too short to mean anything (fail-open)."""
    values = sorted(float(v) for v in history if _is_number(v))
    if len(values) < MIN_HISTORY_DAYS:
        return None
    pct = max(0.0, min(100.0, float(threshold_pct)))
    # linear interpolation between closest ranks (same result as numpy's default)
    pos = (len(values) - 1) * pct / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def throttle_factor(
    score: Optional[float],
    history: List[float],
    threshold_pct: int,
    size_factor: float,
) -> float:
    """1.0 (no throttle) unless today's composite is at/above the threshold; then the
    operator's size factor, clamped to [0.1, 1.0]. Pure."""
    if not _is_number(score):
        return 1.0
    thr = threshold_value(history, threshold_pct)
    if thr is None or float(score) < thr:
        return 1.0
    return max(0.1, min(1.0, float(size_factor)))


def compute_regime_state(closes: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the composite from daily closes (``symbol -> pandas Series``, date index).

    Returns ``{"score", "components", "history", "asof"}`` — ``score`` is ``None`` when
    the data does not support a reading. Pure apart from pandas; no I/O, no clock.
    """
    import pandas as pd

    def series(sym: str) -> Optional["pd.Series"]:
        s = closes.get(sym)
        if s is None or len(s) == 0:
            return None
        s = pd.Series(s).dropna().astype(float)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        return s if len(s) > DRAWDOWN_DAYS else None

    def pct_rank(s: "pd.Series", invert: bool) -> "pd.Series":
        # percentile of each day within the series' own window; 100 = most risk-off
        ranked = s.rank(pct=True) * 100.0
        return 100.0 - ranked if invert else ranked

    parts: Dict[str, "pd.Series"] = {}
    hyg, tlt, uso, spy = (
        series(CREDIT_PROXY),
        series(RATES_PROXY),
        series(OIL_PROXY),
        series(MARKET_PROXY),
    )
    if hyg is not None:
        parts["credit"] = pct_rank((hyg / hyg.shift(RETURN_DAYS) - 1).dropna(), True)
    if tlt is not None:
        parts["rates"] = pct_rank((tlt / tlt.shift(RETURN_DAYS) - 1).dropna(), True)
    if uso is not None:
        parts["oil"] = pct_rank((uso / uso.shift(RETURN_DAYS) - 1).dropna(), False)
    if spy is not None:
        dd = (spy / spy.rolling(DRAWDOWN_DAYS).max() - 1).dropna()
        parts["spy_drawdown"] = pct_rank(dd, True)

    basket = {s: series(s) for s in CORRELATION_BASKET}
    basket = {k: v for k, v in basket.items() if v is not None}
    if len(basket) >= 5:
        rets = pd.DataFrame(basket).sort_index().pct_change()
        corr_values = {}
        for i in range(CORRELATION_DAYS, len(rets)):
            window = rets.iloc[i - CORRELATION_DAYS + 1 : i + 1].dropna(
                axis=1, how="any"
            )
            if window.shape[1] < 5:
                continue
            c = window.corr().values
            n = c.shape[0]
            upper = [c[a][b] for a in range(n) for b in range(a + 1, n)]
            if upper:
                corr_values[rets.index[i]] = sum(upper) / len(upper)
        if corr_values:
            parts["correlation"] = pct_rank(pd.Series(corr_values), False)

    if not parts:
        return {"score": None, "components": {}, "history": [], "asof": None}

    table = pd.DataFrame(parts).sort_index()
    history: List[float] = []
    last_score: Optional[float] = None
    last_components: Dict[str, Optional[float]] = {}
    last_day = None
    for day, row in table.iterrows():
        comps = {k: (None if pd.isna(v) else float(v)) for k, v in row.items()}
        value = riskoff_composite(comps)
        if value is None:
            continue
        history.append(round(value, 2))
        last_score, last_components, last_day = value, comps, day
    asof = None
    if last_day is not None:
        asof = (last_day.date() if hasattr(last_day, "date") else last_day).isoformat()
    return {
        "score": None if last_score is None else round(last_score, 2),
        "components": {
            k: (None if v is None else round(v, 1)) for k, v in last_components.items()
        },
        "history": history,
        "asof": asof,
    }


# --------------------------------------------------------------------------- #
# Cache — written once per day by the producer, READ by the order path (the order
# path itself never fetches anything).
# --------------------------------------------------------------------------- #


def default_cache_path() -> Optional[str]:
    try:
        from core.report.financials_feed import DEFAULT_CACHE_DIR

        return os.path.join(DEFAULT_CACHE_DIR, _CACHE_FILENAME)
    except Exception:  # noqa: BLE001 — no cache location ⇒ treated as cold (fail-open)
        return None


# Serialises the once-per-day refresh: the trading loop and an operator opening the
# Console preview can ask at the same moment. Without it both would fetch all bars.
_REFRESH_LOCK = threading.Lock()
# ADR-REG-05: after a refresh that produced no reading, wait 15 minutes before the next
# attempt. Rationale: the loop asks every cycle; during a data outage that would be
# ~15 bar requests per cycle for nothing. 15 min keeps the same-day recovery prompt.
_RETRY_AFTER_SEC = 900.0
_last_failed_attempt: Dict[str, float] = {}
# One lock for every READ and the swap of the cache file (same finding as review #3480
# FINDING-01): on Windows ``os.replace`` fails with PermissionError while the target is
# open, and the order path (reader) and the refresh thread (writer) share this process.
# Separate from _REFRESH_LOCK, which is held across the network fetch.
_FILE_LOCK = threading.Lock()
# ADR-REG-06: up to 5 swap attempts, 50 ms apart — the lock covers this process only;
# a virus scanner or backup tool can still hold the file for a moment.
_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_SEC = 0.05


def _atomic_write_json(path: str, payload: Mapping[str, Any]) -> bool:
    """Write-then-swap (review #3477 FINDING-01). The order path reads this file while
    the loop thread or the preview endpoint may be writing it; a plain ``open(..., "w")``
    exposes a truncated file, which reads as "no throttle" — a silent bypass. The JSON
    goes to a UNIQUE temp file in the same directory (two writers never share one) and
    ``os.replace`` swaps it in atomically. On failure the old cache stays untouched."""
    tmp_path = None
    try:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".regime_signal.", suffix=".tmp", dir=directory
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
            fh.flush()
            os.fsync(fh.fileno())
        last_exc: Optional[BaseException] = None
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                with _FILE_LOCK:
                    os.replace(tmp_path, path)
                return True
            except PermissionError as exc:
                last_exc = exc
                if attempt + 1 < _REPLACE_ATTEMPTS:
                    time.sleep(_REPLACE_BACKOFF_SEC)
        raise last_exc if last_exc else OSError("cache swap failed")
    except Exception as exc:  # noqa: BLE001 — a cache write must never break the cycle
        logger.warning(
            "RegimeSignal: cache write failed (%s) — previous cache kept.",
            exc,
            exc_info=True,
        )
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                logger.warning(
                    "RegimeSignal: temp file %s not removed.", tmp_path, exc_info=True
                )
        return False


def refresh_regime_cache(
    fetch_closes: Callable[[str], Any],
    cache_file: Optional[str] = None,
    computed_on: Optional[date] = None,
) -> Dict[str, Any]:
    """Fetch the daily closes, compute the state, write the cache. Never raises: a
    symbol whose fetch fails is simply missing (the composite re-normalises or, without
    its lead component, reads ``None``)."""
    closes: Dict[str, Any] = {}
    for sym in all_symbols():
        try:
            data = fetch_closes(sym)
            if data is not None and len(data) > 0:
                closes[sym] = data
        except Exception as exc:  # noqa: BLE001 — a data gap must not break the cycle
            logger.warning(
                "RegimeSignal: no closes for %s (%s).", sym, exc, exc_info=True
            )
    try:
        state = compute_regime_state(closes)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "RegimeSignal: compute failed (%s) — no reading.", exc, exc_info=True
        )
        state = {"score": None, "components": {}, "history": [], "asof": None}
    if computed_on is not None:
        state["computed_on"] = computed_on.isoformat()
    if state.get("score") is None:
        # No reading ⇒ do NOT write: an empty state stamped with today's
        # ``computed_on`` would replace a still-fresh reading and mark the day as done,
        # switching the throttle off until tomorrow after ONE transient data gap.
        logger.warning(
            "RegimeSignal: no reading computed (%d/%d symbols with data) — cache left "
            "as it is; retried later.",
            len(closes),
            len(all_symbols()),
        )
        return state
    path = cache_file or default_cache_path()
    if path:
        _atomic_write_json(path, state)
    return state


def provider_fetch(data_provider: Any, now: Any) -> Callable[[str], Any]:
    """Adapt the engine's data provider to ``fetch_closes`` — the SAME Alpaca daily-bar
    pipe ``/stock-history`` uses (no new source). Returns the ``close`` column."""

    def fetch(symbol: str) -> Any:
        df = data_provider.get_data(symbol, now, days=HISTORY_CALENDAR_DAYS)
        if df is None or getattr(df, "empty", True):
            return None
        return df.sort_index()["close"]

    return fetch


def ensure_fresh_state(
    fetch_closes: Callable[[str], Any],
    today: date,
    cache_file: Optional[str] = None,
    retry_after_sec: float = _RETRY_AFTER_SEC,
) -> Dict[str, Any]:
    """Recompute at most ONCE per calendar day; otherwise return the cached state. The
    ``computed_on`` stamp (not ``asof``, which is the last BAR date) drives the refresh.
    Single-flight; a refresh without a reading is retried after ``retry_after_sec``.
    """
    key = cache_file or default_cache_path() or ""
    with _REFRESH_LOCK:
        state = load_regime_state(cache_file)
        if state and state.get("computed_on") == today.isoformat():
            return state
        last_failed = _last_failed_attempt.get(key)
        if last_failed is not None and time.monotonic() - last_failed < retry_after_sec:
            return {"score": None, "components": {}, "history": [], "asof": None}
        state = refresh_regime_cache(fetch_closes, cache_file, computed_on=today)
        if state.get("score") is None:
            _last_failed_attempt[key] = time.monotonic()
        else:
            _last_failed_attempt.pop(key, None)
        return state


def load_regime_state(cache_file: Optional[str] = None) -> Optional[Dict[str, Any]]:
    path = cache_file or default_cache_path()
    if not path or not os.path.exists(path):
        return None
    try:
        with _FILE_LOCK:  # never hold the file open while a refresh swaps it
            with open(path, encoding="utf-8") as fh:
                blob = json.load(fh)
        return blob if isinstance(blob, dict) else None
    except Exception:  # noqa: BLE001 — unreadable cache ⇒ cold (fail-open)
        logger.warning(
            "RegimeSignal: cache unreadable at %s — no throttle.", path, exc_info=True
        )
        return None


def is_fresh(state: Optional[Mapping[str, Any]], now: date) -> bool:
    """A reading counts only if it is from the last few calendar days AND not from the
    future relative to ``now`` (a simulated ``now`` must never see today's cache)."""
    if not state or not state.get("asof"):
        return False
    try:
        age = (now - date.fromisoformat(str(state["asof"]))).days
    except (TypeError, ValueError):
        return False
    return 0 <= age <= MAX_CACHE_AGE_DAYS


def regime_reading(
    now: date,
    threshold_pct: int,
    size_factor: float,
    state: Optional[Mapping[str, Any]] = None,
    cache_file: Optional[str] = None,
) -> Dict[str, Any]:
    """The one summary both the order path and the Console preview use.

    ``would_throttle`` / ``factor`` say what the throttle does at the given settings —
    independent of whether ``REGIME_THROTTLE_ENABLED`` is on (the preview shows the
    dark reading; the order path applies it only when enabled).
    """
    st = state if state is not None else load_regime_state(cache_file)
    fresh = is_fresh(st, now)
    score = st.get("score") if (st and fresh) else None
    history = list(st.get("history") or []) if (st and fresh) else []
    factor = throttle_factor(score, history, threshold_pct, size_factor)
    return {
        "available": score is not None,
        "asof": st.get("asof") if st else None,
        "score": score,
        "threshold": threshold_value(history, threshold_pct),
        "threshold_percentile": int(threshold_pct),
        "components": dict(st.get("components") or {}) if (st and fresh) else {},
        "weights": dict(WEIGHTS),
        "history_days": len(history),
        "would_throttle": factor < 1.0,
        "factor": factor,
    }


def active_throttle_reading(now: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """The reading the engine ACTS on: ``None`` while the throttle is dark, else the
    :func:`regime_reading` at the configured settings.

    One gate for every consumer (order size AND the top-up target), so they can never
    disagree about whether the throttle is on. Dark ⇒ returns before the cache is
    touched ⇒ byte-identical. ``now`` defaults to the ENGINE clock (replay-safe).
    """
    import config as _cfg

    cfg = _cfg.get_config()
    if not getattr(cfg, "REGIME_THROTTLE_ENABLED", False):
        return None
    if now is None:
        from datetime import timezone

        from core.sim.clock import engine_now

        now = engine_now(timezone.utc).date()
    return regime_reading(
        now,
        int(getattr(cfg, "REGIME_RISKOFF_PERCENTILE", 75)),
        float(getattr(cfg, "REGIME_THROTTLE_SIZE_FACTOR", 0.5)),
    )


def active_throttle_factor(now: Optional[date] = None) -> float:
    """Factor in [0.1, 1.0] the engine applies right now; 1.0 while dark, without a
    fresh reading, or on any error (fail-open, WARNING)."""
    try:
        reading = active_throttle_reading(now)
        if not reading:
            return 1.0
        factor = float(reading.get("factor", 1.0))
        return factor if 0.0 < factor < 1.0 else 1.0
    except Exception as exc:  # noqa: BLE001 — a side-feed must never break trading
        logger.warning(
            "RegimeThrottle: no usable reading (%s) — factor 1.0.", exc, exc_info=True
        )
        return 1.0
