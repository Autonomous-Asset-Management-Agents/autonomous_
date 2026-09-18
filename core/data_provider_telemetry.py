"""ADR-OBS-01 / PR E: data-provider feed-health instrumentation (PURE OBSERVATION, VC-1).

Fail-safe module-level counters for the market-data feed paths, read by the
``data_providers`` subsystem of ``/engine-diagnostics``. Every mutation is
DOUBLE-guarded (the private ``_record*`` helpers swallow their own errors AND the
public ``bump_*`` entry points guard the call site) so a counter/timestamp failure
can NEVER raise into — or alter the result / fallback waterfall of — a real data
fetch on the research/data path.

MACHINE-only: source NAMES (alpaca / databento / polygon / vix / specialist source
keys), ok/fail COUNTS, last-error timestamps, booleans, ages (seconds). NEVER stores
symbols, prices, order content, or any PII — aggregate counts only.
"""

from __future__ import annotations

import time
from typing import Any, Dict

# --- 1) OHLCV waterfall per-source stats (alpaca / databento / polygon) --------
# Bounded: only the fixed waterfall source names are ever inserted (a bad name is
# ignored), so the dict can never grow unbounded.
_ALLOWED_SOURCES = ("alpaca", "databento", "polygon")

_source_stats: Dict[str, Dict[str, Any]] = {
    name: {"ok": 0, "fail": 0, "last_error_ts": None} for name in _ALLOWED_SOURCES
}


def bump_source(source: str, ok: bool) -> None:
    """Fail-safe per-source counter bump for the OHLCV waterfall.

    ``ok=True`` advances the source's ok-count; ``ok=False`` advances its fail-count
    and stamps ``last_error_ts``. Swallows EVERY error — a broken counter must never
    perturb a data fetch or its fallback. Unknown source names are ignored (bounded).
    """
    try:
        entry = _source_stats.get(source)
        if entry is None:
            return
        if ok:
            entry["ok"] = int(entry.get("ok", 0) or 0) + 1
        else:
            entry["fail"] = int(entry.get("fail", 0) or 0) + 1
            entry["last_error_ts"] = time.time()
    except Exception:  # noqa: BLE001 — a broken counter must never break a fetch
        pass


def get_data_source_stats() -> Dict[str, Dict[str, Any]]:
    """Read-only snapshot of the per-source OHLCV waterfall stats (machine-only)."""
    try:
        return {k: dict(v) for k, v in _source_stats.items()}
    except Exception:  # noqa: BLE001
        return {}


# --- 2) VIX / market-regime freshness -----------------------------------------
_regime_state: Dict[str, Any] = {"vix_present": False, "vix_updated_ts": None}


def mark_regime_update(vix_present: bool) -> None:
    """Fail-safe: record that the market regime / VIX cache was (re)computed.

    Stamps the update time and whether a real VIX value backed it. PURE OBSERVATION
    from the regime path — never a live fetch, never alters the regime result.
    """
    try:
        _regime_state["vix_present"] = bool(vix_present)
        _regime_state["vix_updated_ts"] = time.time()
    except Exception:  # noqa: BLE001 — never break the regime computation
        pass


def get_regime_freshness() -> Dict[str, Any]:
    """Read-only VIX presence + age (seconds) from cached regime state (no live fetch)."""
    try:
        ts = _regime_state.get("vix_updated_ts")
        age = round(time.time() - ts, 1) if ts is not None else None
        return {
            "vix_present": bool(_regime_state.get("vix_present", False)),
            "vix_regime_age_seconds": age,
        }
    except Exception:  # noqa: BLE001
        return {"vix_present": False, "vix_regime_age_seconds": None}


# --- 3) Symbol-universe source + count ----------------------------------------
_universe_state: Dict[str, Any] = {"universe_source": None, "universe_count": None}


def mark_universe(source: str, count: int) -> None:
    """Fail-safe: record the last resolved symbol-universe source + count.

    ``source`` is a machine label (alpaca / wikipedia / fallback); ``count`` is an
    aggregate symbol count (never the symbols themselves). PURE OBSERVATION — never
    alters the universe result.
    """
    try:
        _universe_state["universe_source"] = str(source)
        _universe_state["universe_count"] = int(count)
    except Exception:  # noqa: BLE001 — never break universe resolution
        pass


def get_universe_state() -> Dict[str, Any]:
    """Read-only last-known symbol-universe source + count (no live call)."""
    try:
        return dict(_universe_state)
    except Exception:  # noqa: BLE001
        return {"universe_source": None, "universe_count": None}


# --- 4) Specialist free-API per-source ok/fail --------------------------------
# Bounded at ~16 keys — a fixed roster of specialist source names; once the cap is
# reached no new key is created (a broken/oversized name is ignored).
_SPECIALIST_MAX_KEYS = 16
_specialist_stats: Dict[str, Dict[str, int]] = {}


def bump_specialist_source(source: str, ok: bool) -> None:
    """Fail-safe per-source ok/fail bump for the specialist's free-API fetchers.

    Bounded: no more than ``_SPECIALIST_MAX_KEYS`` distinct source names are tracked.
    PURE OBSERVATION — a counter failure never perturbs the specialist gather.
    """
    try:
        entry = _specialist_stats.get(source)
        if entry is None:
            if len(_specialist_stats) >= _SPECIALIST_MAX_KEYS:
                return
            entry = {"ok": 0, "fail": 0}
            _specialist_stats[source] = entry
        key = "ok" if ok else "fail"
        entry[key] = int(entry.get(key, 0) or 0) + 1
    except Exception:  # noqa: BLE001 — never break the specialist gather
        pass


def get_specialist_source_stats() -> Dict[str, Dict[str, int]]:
    """Read-only snapshot of the specialist free-API per-source stats (machine-only)."""
    try:
        return {k: dict(v) for k, v in _specialist_stats.items()}
    except Exception:  # noqa: BLE001
        return {}


# --- test / daily-reset helper ------------------------------------------------
def reset_data_provider_telemetry() -> None:
    """Test/daily-reset helper — zeroes every data-provider feed counter."""
    global _specialist_stats
    for name in _ALLOWED_SOURCES:
        _source_stats[name] = {"ok": 0, "fail": 0, "last_error_ts": None}
    _regime_state.update({"vix_present": False, "vix_updated_ts": None})
    _universe_state.update({"universe_source": None, "universe_count": None})
    _specialist_stats = {}
