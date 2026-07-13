"""In-memory latest-per-symbol store of the FINAL execution outcome for a
Round-Table decision — the Iron-Dome / risk / kill-switch gate result (RQ-1
#1516). DISPLAY ONLY.

Paired with :mod:`core.round_table.recent_decisions`: the order executor records
the outcome per symbol at each gate, and the ``/round-table-decisions`` serve
path joins it onto the matching decision (symbol + a small time tolerance) so
the console + demo can show a badge ("Executed", "Blocked — order-value limit",
…). This is the answer to "approved != actually traded": a gatekeeper-approved
verdict can still be blocked/resized downstream, and this makes that visible.

NEVER on the trading path — every write is best-effort and swallows all errors,
so a display-store failure can never touch execution. BORA: a process-local
in-memory dict, identical on Desktop (LocalStateClient) and Enterprise (Redis) —
no Redis call, no edition-specific code.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Outcome codes (the display badge maps these). ────────────────────────────
EXECUTED = "executed"  # order reached the broker (paper or live)
RESIZED = "resized"  # risk-manager reduced the size but still executed
BLOCKED_RISK = "blocked:risk"  # position size resolved to 0 (risk/cash)
BLOCKED_ORDER_VALUE = "blocked:order_value"  # ComplianceGuardian max order value
BLOCKED_DAILY_LIMIT = "blocked:daily_limit"  # ComplianceGuardian daily-trade cap
BLOCKED_CHURN = "blocked:churn"  # anti-churn (recent SELL / cooldown)
BLOCKED_PORTFOLIO = "blocked:portfolio"  # portfolio-manager declined to open
BLOCKED_KILL_SWITCH = "blocked:kill_switch"  # kill-switch halt
HITL_HELD = "hitl_held"  # queued for human approval
PENDING = "pending"  # serve-time default: actionable but no outcome yet

_MAX_SYMBOLS = 2000  # defensive cap; universe is ~500

# Insertion order == recency order (re-recording moves the symbol to the end).
_outcomes: Dict[str, Dict[str, Any]] = {}
# One lock guards every read AND write — the engine loop writes while FastAPI may
# read from a thread-pool thread (mirrors recent_decisions.py's concurrency mandate).
_lock = threading.Lock()


def record_execution_outcome(
    symbol: str, code: str, reason: str = "", ts: Optional[float] = None
) -> None:
    """Store the latest execution outcome for ``symbol``. NEVER raises
    (fail-safe): a display-store failure must not be able to touch the trading
    path. ``ts`` defaults to ``time.time()`` (injectable for tests)."""
    try:
        sym = str(symbol or "").strip().upper()
        code = str(code or "").strip()
        if not sym or not code:
            return
        rec = {
            "outcome": code,
            "reason": str(reason or ""),
            "ts": float(ts if ts is not None else time.time()),
        }
        with _lock:
            _outcomes.pop(sym, None)  # latest-per-symbol (re-insert at the end)
            if len(_outcomes) >= _MAX_SYMBOLS:
                _outcomes.pop(next(iter(_outcomes)), None)  # evict oldest
            _outcomes[sym] = rec
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("execution_outcomes: record failed (display-only): %s", exc)


def get_execution_outcome(symbol: str) -> Optional[Dict[str, Any]]:
    """Latest outcome record for one symbol (copy), or None."""
    try:
        with _lock:
            rec = _outcomes.get(str(symbol or "").strip().upper())
            return dict(rec) if rec else None
    except Exception:  # pragma: no cover - defensive
        return None


def clear_execution_outcomes() -> None:
    """Test hook — wipe the store."""
    with _lock:
        _outcomes.clear()


__all__ = [
    "record_execution_outcome",
    "get_execution_outcome",
    "clear_execution_outcomes",
    "EXECUTED",
    "RESIZED",
    "BLOCKED_RISK",
    "BLOCKED_ORDER_VALUE",
    "BLOCKED_DAILY_LIMIT",
    "BLOCKED_CHURN",
    "BLOCKED_PORTFOLIO",
    "BLOCKED_KILL_SWITCH",
    "HITL_HELD",
    "PENDING",
]
