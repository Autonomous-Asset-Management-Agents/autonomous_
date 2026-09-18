"""#2113 — build + enqueue the durable ``decision_outcomes`` capture row.

Design (plan Rev 2, §5):
* Flag-gated: ``DECISION_CAPTURE_ENABLED`` (default OFF) — flag off ⇒ every
  function here is a no-op and the shipped behaviour is byte-identical to main.
* PURE OBSERVATION: every entry point swallows all errors — a capture failure
  can never raise into the trading path (mirrors execution_outcomes.py).
* NO feature compute: the row COPIES the (possibly #2389-enriched, possibly
  default) DecisionContext scalars verbatim — never calls
  compute_technical_features (plan §4: Konsolidierung mit #2389).
* Execution outcome joins per ``decision_id`` (NOT the symbol+5s
  serve-heuristic): a decision_id-stamped display-store record must match this
  decision; a legacy (unstamped) record is accepted only when it was recorded
  at/after this decision's creation — deterministic, because the executor
  processes signals serially within one engine.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

# Rate-limit the fail-open "default features" WARNING to once per process —
# §5.6 demands WARNING (never DEBUG), but one line per decision would flood
# 8000+ warnings per day on the desktop.
_warned_default_features = False


def _enabled() -> bool:
    try:
        import config as _config

        return bool(getattr(_config.get_config(), "DECISION_CAPTURE_ENABLED", False))
    except Exception:  # noqa: BLE001 — an unreadable flag must behave as OFF
        return False


def attach_round_table_capture_fields(
    ctx: Any,
    *,
    session_id: str,
    votes: Any,
    consensus_score: Optional[float],
    gatekeeper_approved: Optional[bool],
    gatekeeper_reason: str,
    ohlc: Optional[dict],
) -> None:
    """Attach the already-serialized Round-Table detail + per-cycle eval inputs
    to the DecisionContext (plan §5.3 — R-A umgangen, nicht umgebaut: the
    Enterprise SenateProtocol / LocalJSONAuditLogger split stays untouched).

    Flag-gated + fail-safe: flag OFF ⇒ the context is not touched at all
    (byte-identical `decisions` payload); errors are swallowed."""
    try:
        if not _enabled() or ctx is None:
            return
        ctx.session_id = str(session_id or "")
        ctx.round_table_votes = votes
        ctx.consensus_score = consensus_score
        ctx.gatekeeper_approved = gatekeeper_approved
        ctx.gatekeeper_reason = str(gatekeeper_reason or "")
        _ohlc = ohlc or {}
        ctx.cycle_open = _ohlc.get("open")
        ctx.cycle_high = _ohlc.get("high")
        ctx.cycle_low = _ohlc.get("low")
        ctx.cycle_close = _ohlc.get("close")
    except Exception as exc:  # noqa: BLE001 — observation must never raise
        logger.warning(
            "decision-capture: attach failed for %s (row degrades to context "
            "defaults): %s",
            getattr(ctx, "symbol", "?"),
            exc,
        )


def resolve_execution_outcome(ctx: Any) -> Tuple[Optional[str], Optional[str]]:
    """Resolve THIS decision's execution outcome from the display store —
    per decision_id, not symbol+tolerance (plan R-C).

    Accept rules (deterministic within the serially awaited executor):
    * record stamped with a decision_id → must equal ``ctx.decision_id``;
    * legacy record (no stamp — today's emit sites) → accepted only when
      recorded at/after this decision's creation time (a stale record from a
      previous cycle for the same symbol is rejected).
    Returns ``(None, None)`` when no outcome belongs to this decision (e.g. a
    HOLD that never reached the execution path)."""
    try:
        from core.round_table.execution_outcomes import get_execution_outcome

        rec = get_execution_outcome(getattr(ctx, "symbol", ""))
        if not rec:
            return None, None
        rec_did = rec.get("decision_id")
        if rec_did is not None:
            if rec_did == getattr(ctx, "decision_id", None):
                return rec.get("outcome"), rec.get("reason")
            return None, None
        decision_time = getattr(ctx, "decision_time", None)
        if not isinstance(decision_time, datetime):
            return None, None
        if float(rec.get("ts", 0.0)) >= decision_time.timestamp():
            return rec.get("outcome"), rec.get("reason")
        return None, None
    except Exception as exc:  # noqa: BLE001 — observation must never raise
        logger.warning("decision-capture: outcome resolve failed: %s", exc)
        return None, None


def build_outcome_row(ctx: Any) -> dict:
    """Build the decision_outcomes row dict from the DecisionContext — COPY
    only, no feature computation (plan §4). Caller checks the flag."""
    from core.decision_capture.lineage import (
        compute_config_sha,
        compute_feature_list_hash,
        get_git_sha,
    )

    decision_time = getattr(ctx, "decision_time", None)
    if isinstance(decision_time, datetime):
        decision_time = decision_time.isoformat()
    execution_outcome, execution_reason = resolve_execution_outcome(ctx)
    return {
        "decision_id": ctx.decision_id,
        "session_id": getattr(ctx, "session_id", "") or "",
        "symbol": ctx.symbol,
        "decision_time": decision_time,
        "consensus_score": getattr(ctx, "consensus_score", None),
        "signal_action": getattr(ctx, "action", None),
        "gatekeeper_approved": getattr(ctx, "gatekeeper_approved", None),
        "gatekeeper_reason": getattr(ctx, "gatekeeper_reason", "") or "",
        "votes_json": getattr(ctx, "round_table_votes", None),
        "voted_price": getattr(ctx, "current_price", None),
        "cycle_open": getattr(ctx, "cycle_open", None),
        "cycle_high": getattr(ctx, "cycle_high", None),
        "cycle_low": getattr(ctx, "cycle_low", None),
        "cycle_close": getattr(ctx, "cycle_close", None),
        "vix_level": getattr(ctx, "vix_level", None),
        "market_regime": getattr(ctx, "market_regime", None),
        "execution_outcome": execution_outcome,
        "execution_reason": execution_reason,
        # labels (pnl / pnl_pct / hold_duration_hours / exit_reason /
        # forward_return_5d) stay NULL until the Inc-2 attribution job fills them
        "model_version_id": getattr(ctx, "model_version_id", None),
        "git_sha": get_git_sha(),
        "config_sha": compute_config_sha(),
        "feature_list_hash": compute_feature_list_hash(),
        "is_simulation": bool(getattr(ctx, "is_simulation", False)),
    }


def capture_decision_outcome(ctx: Any) -> None:
    """Enqueue one durable decision_outcomes row for this decision. Flag-gated,
    fail-safe, PURE OBSERVATION — never raises into the trading path."""
    global _warned_default_features
    try:
        if not _enabled() or ctx is None:
            return
        try:
            import config as _config

            snapshot_on = bool(
                getattr(_config.get_config(), "DESKTOP_FEATURE_SNAPSHOT_ENABLED", False)
            )
        except Exception:  # noqa: BLE001
            snapshot_on = False
        if not snapshot_on and not _warned_default_features:
            # Fail-open (plan §4/§7): the row is still written — with today's
            # neutral default features — until #2389 fills real TA scalars.
            logger.warning(
                "decision-capture: feature snapshot (#2389 / "
                "DESKTOP_FEATURE_SNAPSHOT_ENABLED) is OFF — capture rows carry "
                "the neutral DEFAULT features (fail-open; logged once per run)."
            )
            _warned_default_features = True

        row = build_outcome_row(ctx)

        from core.cloud_logger import get_cloud_logger

        get_cloud_logger().log_decision_outcome(row)
    except Exception as exc:  # noqa: BLE001 — observation must never raise
        logger.warning(
            "decision-capture: capture failed for %s (decision NOT affected): %s",
            getattr(ctx, "symbol", "?"),
            exc,
        )
