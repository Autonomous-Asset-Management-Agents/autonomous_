"""In-memory latest-per-symbol store for Round Table decisions (G1a, #1050).

Display source for the desktop console routes (G1b: `/round-table-decisions`,
`/round-table/<symbol>`) — NOT a compliance record (that remains the
LocalJSONAuditLogger / SenateProtocol chain) and NOT on the trading path
(read-only for the API layer; the single producer is `run_round_table`, which
records the same `SenateSession` it already logs to the protocol).

Ported from the bundle's battle-tested pattern under main's Round-Table
nomenclature, including its latest-per-symbol fix: one busy symbol must
REPLACE its own entry, never evict other symbols (the bundle's original
rolling deque had exactly that bug).

Memory bound: one entry per symbol → naturally bounded by the universe size
(~500); `_MAX_SYMBOLS` is a defensive cap against pathological symbol churn.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_SYMBOLS = 2000  # defensive cap; universe is ~500

# Insertion order == recency order (re-recording moves the symbol to the end).
_recent: Dict[str, Dict[str, Any]] = {}
# PR-review P0-1 (concurrency mandate): the engine loop writes while FastAPI
# may read from a thread-pool thread (sync routes) — an unguarded
# `list(_recent.values())` during a concurrent pop/insert raises
# "RuntimeError: dictionary changed size during iteration". One lock guards
# every read AND write; operations are O(1)/O(n-copy), never blocking I/O.
_lock = threading.Lock()

# RQ-1 (#1516): attach the FINAL execution outcome (Iron-Dome / risk / kill-switch
# result) to each decision at serve time. Only actionable (BUY/SELL) decisions can
# have an execution outcome; a HOLD never reaches the executor. An outcome is
# joined by symbol only if it was recorded within this window of the decision —
# guards against a stale cross-cycle outcome lingering on a busy symbol.
_OUTCOME_TOLERANCE_S = 5.0


def record_round_table_decision(session: Any) -> None:
    """Store the latest decision for a symbol. NEVER raises (fail-safe):
    a display-store failure must not be able to touch the trading path."""
    try:
        if is_dataclass(session) and not isinstance(session, type):
            entry = asdict(session)
        elif isinstance(session, dict):
            entry = dict(session)
        else:
            return
        symbol = str(entry.get("symbol") or "").strip().upper()
        if not symbol:
            return
        with _lock:
            # Latest-per-symbol: drop the old slot first so re-insertion lands
            # at the end of the dict (= newest in recency order).
            _recent.pop(symbol, None)
            if len(_recent) >= _MAX_SYMBOLS:
                _recent.pop(next(iter(_recent)), None)  # evict oldest (defensive)
            _recent[symbol] = entry
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("recent_decisions: record failed (display-only): %s", exc)


def _decision_epoch(entry: Dict[str, Any]) -> Optional[float]:
    """Best-effort ISO-8601 ``timestamp`` -> epoch seconds; None if unparseable."""
    ts = entry.get("timestamp")
    if not ts:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _attach_execution_outcomes(items: List[Dict[str, Any]]) -> None:
    """Join the latest execution outcome onto each ACTIONABLE (BUY/SELL) decision,
    by symbol + a time tolerance. HOLD/no-action decisions get
    ``execution_outcome=None`` (no order was ever attempted); an actionable
    decision with no fresh outcome gets ``"pending"``. Best-effort — never raises;
    mutates the passed-in copies only (the store stays a pure record)."""
    try:
        from core.round_table.execution_outcomes import PENDING, get_execution_outcome
    except Exception:  # pragma: no cover - defensive
        return
    for it in items:
        try:
            action = str(it.get("signal_action") or it.get("action") or "").upper()
            if action not in ("BUY", "SELL"):
                it["execution_outcome"] = None
                it["execution_outcome_reason"] = ""
                continue
            rec = get_execution_outcome(it.get("symbol") or "")
            dt = _decision_epoch(it)
            if (
                rec
                and dt is not None
                and abs(float(rec.get("ts", 0.0)) - dt) <= _OUTCOME_TOLERANCE_S
            ):
                it["execution_outcome"] = rec.get("outcome")
                it["execution_outcome_reason"] = rec.get("reason", "")
            else:
                it["execution_outcome"] = PENDING
                it["execution_outcome_reason"] = ""
        except Exception:  # pragma: no cover - defensive
            it["execution_outcome"] = "pending"
            it["execution_outcome_reason"] = ""


def get_recent_round_table_decisions(
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """All latest decisions, newest first, each with its final ``execution_outcome``
    joined on (display-only; see execution_outcomes.py)."""
    with _lock:
        # Copy so the serve-time outcome join never mutates the stored record.
        items = [dict(v) for v in list(_recent.values())[::-1]]
    _attach_execution_outcomes(items)
    return items[:limit] if limit else items


def get_round_table_decision(symbol: str) -> Optional[Dict[str, Any]]:
    """Latest decision for one symbol, or None."""
    with _lock:
        return _recent.get(str(symbol or "").strip().upper())


def clear_recent_round_table_decisions() -> None:
    """Test hook — wipe the store."""
    with _lock:
        _recent.clear()


__all__ = [
    "record_round_table_decision",
    "get_recent_round_table_decisions",
    "get_round_table_decision",
    "clear_recent_round_table_decisions",
]
