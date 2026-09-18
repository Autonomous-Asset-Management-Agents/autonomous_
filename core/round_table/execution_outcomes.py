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
BLOCKED_MARKET_CLOSED = (
    "blocked:market_closed"  # INC-6 order gate: market closed (no bypass)
)
HITL_HELD = "hitl_held"  # queued for human approval
CLOSED = "closed"  # SELL on an already-flat position (Alpaca 404/40410000): nothing to sell, not an error (#2118)
PENDING = "pending"  # serve-time default: actionable but no outcome yet

_MAX_SYMBOLS = 2000  # defensive cap; universe is ~500

# Insertion order == recency order (re-recording moves the symbol to the end).
_outcomes: Dict[str, Dict[str, Any]] = {}
# One lock guards every read AND write — the engine loop writes while FastAPI may
# read from a thread-pool thread (mirrors recent_decisions.py's concurrency mandate).
_lock = threading.Lock()


def emit_block_span(symbol: str, reason_code: str, detail: str = "") -> None:
    """#2069 (INF-13): best-effort, fail-safe — emit ONE scrubbed OTel span for an
    execution/compliance block so it reaches the diagnose-export (the span runs
    through the LocalScrubbingSpanExporter into ``telemetry.jsonl``, which the
    desktop export already reads — zero Electron diff).

    Flag-gated (``EXECUTION_BLOCK_TELEMETRY_ENABLED``, default OFF = byte-identical);
    NEVER raises into the trading path. ``reason_code`` is a machine string (no
    PII); ``symbol`` is a public ticker; both are additionally scrubbed centrally
    by the exporter (DSGVO/TDDDG, analog #1833)."""
    try:
        import config

        if not getattr(config, "EXECUTION_BLOCK_TELEMETRY_ENABLED", False):
            return
        from core.telemetry import get_tracer

        with get_tracer("execution.block").start_as_current_span(
            "execution.block"
        ) as sp:
            sp.set_attribute("block.reason_code", str(reason_code or ""))
            sp.set_attribute("block.symbol", str(symbol or ""))
            if detail:
                sp.set_attribute("block.detail", str(detail))
    except Exception as exc:  # noqa: BLE001 — observation must never touch execution
        logger.warning(
            "execution_outcomes: block-span emit failed (observation-only): %s", exc
        )


def record_execution_outcome(
    symbol: str,
    code: str,
    reason: str = "",
    ts: Optional[float] = None,
    decision_id: Optional[str] = None,
) -> None:
    """Store the latest execution outcome for ``symbol``. NEVER raises
    (fail-safe): a display-store failure must not be able to touch the trading
    path. ``ts`` defaults to ``time.time()`` (injectable for tests).

    ``decision_id`` (#2113, optional): when the emitting seam knows the decision,
    the record is stamped with it so the durable decision_outcomes capture can
    join per decision_id instead of the symbol+time serve-heuristic. Legacy call
    sites stay unchanged — their record shape is byte-identical (no key added)."""
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
        if decision_id:
            rec["decision_id"] = str(decision_id)
        with _lock:
            _outcomes.pop(sym, None)  # latest-per-symbol (re-insert at the end)
            if len(_outcomes) >= _MAX_SYMBOLS:
                _outcomes.pop(next(iter(_outcomes)), None)  # evict oldest
            _outcomes[sym] = rec
        # #2069: the executor CHOKE-POINT — every blocked:*/error outcome emits one
        # scrubbed telemetry span (flag-gated inside; fail-safe: still inside this
        # try, and emit_block_span itself swallows every error).
        if code.startswith("blocked:") or code == "error":
            emit_block_span(sym, code, reason)
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
    "emit_block_span",
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
    "BLOCKED_MARKET_CLOSED",
    "HITL_HELD",
    "CLOSED",
    "PENDING",
]
