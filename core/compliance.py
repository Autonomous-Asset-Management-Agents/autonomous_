import json
import logging
import os as _os
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List

from .cloud_logger import get_cloud_logger

# Setup specific logger for compliance to ensure auditability (Local Fallback)
compliance_logger = logging.getLogger("ComplianceGuardian")
compliance_logger.setLevel(logging.INFO)
# In production, this would go to a WORM (Write Once Read Many) storage

_audit_log_path = "compliance_audit.log"
if _os.path.isdir(_audit_log_path):
    # Docker artifact left a directory at this path — fall back to stdout
    compliance_logger.addHandler(logging.StreamHandler())
else:
    try:
        file_handler = logging.FileHandler(_audit_log_path)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(formatter)
        compliance_logger.addHandler(file_handler)
    except Exception:
        compliance_logger.addHandler(logging.StreamHandler())


# --- ADR-OBS-01 / PR A.2: compliance-decision instrumentation (PURE OBSERVATION) ---
# Fail-safe module-level counters at the check_order GO / NO-GO return points. Reject
# reasons are bounded MACHINE reason codes (never symbol/order content). ``_bump_*``
# helpers swallow every error so a counter failure can NEVER change a GO/NO-GO verdict —
# the decision logic in ``check_order`` stays byte-identical.
_COMPLIANCE_COUNTERS: Dict[str, int] = {"go_count": 0, "nogo_count": 0}
_REJECT_REASONS: "Counter[str]" = Counter()
_MAX_REJECT_REASON_KEYS = 32  # bound the machine-code cardinality


def _bump_compliance(decision: bool, reason_code: str = "") -> None:
    """Fail-safe GO/NO-GO counter mutation — swallows EVERY error."""
    try:
        if decision:
            _COMPLIANCE_COUNTERS["go_count"] += 1
        else:
            _COMPLIANCE_COUNTERS["nogo_count"] += 1
            if reason_code:
                # Bound cardinality: only count a NEW code while under the cap
                # (existing codes always increment). reason_code is a fixed machine
                # string set at each reject site — never symbol/order content.
                if reason_code in _REJECT_REASONS or (
                    len(_REJECT_REASONS) < _MAX_REJECT_REASON_KEYS
                ):
                    _REJECT_REASONS[reason_code] += 1
    except Exception:  # noqa: BLE001 — a broken counter must never block a decision
        pass


def get_compliance_counters() -> Dict:
    """Read-only snapshot of the GO/NO-GO counters + bounded reject-reason map."""
    return {
        "go_count": _COMPLIANCE_COUNTERS.get("go_count", 0),
        "nogo_count": _COMPLIANCE_COUNTERS.get("nogo_count", 0),
        "reject_reasons": dict(_REJECT_REASONS),
    }


def reset_compliance_counters() -> None:
    """Test/daily-reset helper — zeroes the compliance counters."""
    _COMPLIANCE_COUNTERS.update({"go_count": 0, "nogo_count": 0})
    _REJECT_REASONS.clear()


class ComplianceGuardian:
    """
    The 'Iron Dome' for the trading system.
    Enforces hard-coded regulatory and risk rules that cannot be overridden by AI agents.
    """

    def __init__(self):
        # ADR-C01: Max Order Value = 10.000 EUR
        # Basis: ESMA Position Limit Guidelines (MiFID II Art. 57) + internes Risikopolicy v1.0
        # Begründung: Obergrenze für Einzelorders schützt vor Fehleingaben (Fat-Finger) und
        # ungewollter Marktbewegung; unter dem ESMA-Schwellenwert für erweiterte Meldepflichten.
        from config import get_config

        self.max_order_value = get_config().COMPLIANCE_MAX_ORDER_VALUE

        # ADR-C02: Restricted Instrument List
        # Basis: MAR Art. 5 (Market Abuse Regulation) — Pflicht zur Blocklist-Pflege
        # Wird zur Laufzeit aus Cloud-Config/DB erweiterbar (hier: Fallback-Defaults)
        self.restricted_list = [
            "SCAM_TOKEN",
            "EVIL_CORP",
        ]
        self._recent_trades: List[Dict] = []

        # ADR-C03: Wash-Trade-Window = 60 Sekunden
        # Basis: MiFID II / MAR Art. 12 — Verbot von Wash Trades
        # Begründung: 60s deckt Bot-typische Reaktionszeiten ab (API-Latenz + Strategy-Cycle).
        # < 30s würde legitime Korrekturtrades fälschlich blocken; > 120s erhöht False-Negative-Rate.
        # ESMA Guideline 2021/1974 nennt keine exakte Sekunden-Schwelle — 60s ist branchenüblich.
        self._wash_trade_window_seconds = 60

        self.daily_trades = 0

        # ADR-C04: Max Daily Trades = 50
        # Basis: Internes Risikopolicy v1.0 (kein regulatorischer Zwang, aber Guardrail)
        # Begründung: Verhindert Runaway-Loops bei Strategy-Bugs; bei > 50 Trades/Tag
        # wahrscheinlich pathologisches Verhalten statt echter Alpha-Generierung.
        # Bei Scale-Up der Strategie-Anzahl reviewen.
        # ADR-C04 / ADR-SEC-06 (#1584): config-driven (default 10), runtime-overridable via
        # the single SystemConfig policy source (reload_policy), clamped to the immutable
        # ceiling of 50. base.py no longer overrides this — one source of truth.
        self.max_daily_trades = get_config().COMPLIANCE_MAX_DAILY_TRADES
        self._daily_limit_alert_sent = False
        self.cloud_logger = get_cloud_logger()

    def reload_policy(self, config_value=None):
        """ADR-SEC-06 (#1596): re-read the effective Iron Dome policy and apply it in place.

        Called at boot and after an admin change (ADR §5a) so a policy update takes effect
        without a restart. Every value is clamped to the immutable hard-floor; a missing or
        invalid source fails closed to the strict default.
        """
        from core.governance.iron_dome_policy import load_policy

        policy = load_policy(config_value)
        self.max_daily_trades = policy.max_daily_trades
        self.max_order_value = policy.max_order_value
        self._wash_trade_window_seconds = policy.wash_trade_window_seconds

    def check_order(self, order: Dict) -> bool:
        """
        Main entry point. Validates an order against all rules.
        Returns True if approved, False if rejected.

        Order dict expected structure:
        {
            "symbol": "AAPL",
            "side": "buy" | "sell",
            "quantity": 10,
            "price": 150.0,
            "strategy_id": "momentum_bot_1",
            "timestamp": <timestamp>
        }
        """

        symbol = order.get("symbol")
        side = order.get("side")

        start_time = time.time()
        decision = False
        reason = ""
        # PR A.2: machine reason code for the fail-safe reject counter (NOT the human
        # ``reason`` above, which embeds the symbol). Set alongside each reject; the
        # single fail-safe bump happens in ``finally`` so every path is counted once.
        reason_code = ""

        try:
            # 1. Regulatory Blocklist Check
            if symbol in self.restricted_list:
                reason = f"Symbol {symbol} is on the Restricted Customer List."
                reason_code = "restricted_symbol"
                return False

            # 2. MiFID II / Data Completeness Check
            if not self._check_mifid_fields(order):
                reason = "Missing mandatory MiFID II reporting fields."
                reason_code = "missing_mifid_fields"
                return False

            # 3. Wash Trade Prevention (Tenant-Aware)
            if self._detect_wash_trade(symbol, side, order.get("user_id")):
                reason = "Potential Wash Trade detected (conflicting orders in short window)."
                reason_code = "wash_trade"
                return False

            # 4. Pre-Trade Risk Limits
            if not self._check_risk_limits(order):
                reason = "Order exceeds pre-defined risk limits (Max Order Value)."
                reason_code = "max_order_value"
                return False

            # If all pass:
            decision = True
            reason = "All compliance checks passed."
            self._recent_trades.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "timestamp": time.time(),
                    "user_id": order.get("user_id"),
                }
            )
            # Housekeeping: Trades außerhalb des Wash-Trade-Windows aus dem In-Memory-Buffer
            # entfernen (ADR-C03). Verhindert unbegrenztes Speicherwachstum bei Long-Running-Prozessen.
            self._cleanup_recent_trades()

            return True

        except Exception as e:
            # A failure anywhere above means the order is NOT approved — reset the decision so
            # the finally-audit records the true outcome (False), never a phantom approval
            # (e.g. if a post-checks step threw after decision was tentatively True). #1237.
            decision = False
            reason = f"System Error during compliance check: {str(e)}"
            reason_code = "system_error"
            compliance_logger.error(reason, exc_info=True)
            return False

        finally:
            # PR A.2 (PURE OBSERVATION): single fail-safe GO/NO-GO count for EVERY path.
            # Placed BEFORE _log_audit and DOUBLE-guarded (call site + inside _bump) so it
            # can neither raise into the decision (already returned) nor perturb the audit
            # write below — defense-in-depth: even a wholly-broken _bump can't escape here.
            try:
                _bump_compliance(decision, reason_code)
            except (
                Exception
            ):  # noqa: BLE001 — observation must never alter the decision
                pass
            # SINGLE audit point for EVERY path (approve / reject / exception) — exactly one
            # entry per order. The early-return reject paths above intentionally no longer log
            # explicitly; doing so plus this finally double-logged every rejection into the
            # tamper-evident compliance trail (#1237). decision/reason are set before each
            # return, so this records the true outcome once.
            self._log_audit(order, decision, reason, start_time)

    def _check_mifid_fields(self, order: Dict) -> bool:
        """Ensures all data required for transaction reporting is present."""
        required = ["symbol", "side", "quantity", "price", "strategy_id", "timestamp"]
        for field in required:
            if field not in order or order[field] is None:
                return False
        return True

    def _detect_wash_trade(
        self, symbol: str, current_side: str, user_id: str = None
    ) -> bool:
        """
        Checks if we executed an opposite trade for the same symbol recently.
        Simple implementation of Wash Trade prevention, scoped per tenant/user_id.
        """
        now = time.time()
        opposite_side = "sell" if current_side == "buy" else "buy"

        for trade in self._recent_trades:
            if (
                trade["symbol"] == symbol
                and trade["side"] == opposite_side
                and trade.get("user_id") == user_id
            ):
                if now - trade["timestamp"] < self._wash_trade_window_seconds:
                    return True
        return False

    def _check_risk_limits(self, order: Dict) -> bool:
        """Checks monetary limits."""
        try:
            value = float(order["quantity"]) * float(order["price"])
            if value > self.max_order_value:
                return False
            return True
        except (ValueError, KeyError):
            return False

    def check_trade(self, order: Dict, source: str = "ai") -> bool:
        """Check if a trade complies with risk rules.

        ``source`` is the order's origin. ``"ai"`` (default) is an autonomous order, subject
        to the daily-trades cap (ADR-C04). ``"human_approved"`` is an operator-approved HITL
        order (EU AI Act Art. 14): a human has authorised this specific capital decision, so
        it bypasses the *autonomous* daily-trades cap — but every other Iron-Dome check
        (``check_order`` / ``max_order_value`` / restricted list / wash-trade) is a separate
        method and still applies. Dormant until the HITL drain path (PR-0a-ii-5) passes
        ``source="human_approved"``; the four existing callers use the default.
        """
        if source != "human_approved" and self.daily_trades >= self.max_daily_trades:
            logging.warning(
                "Compliance: Max daily trades (%s) reached.", self.max_daily_trades
            )
            return False

        return True

    def reset_daily_limit(self):
        """Reset the daily trades counter (called at start of each NY trading day)."""
        self.daily_trades = 0
        self._daily_limit_alert_sent = False

    def _log_audit(self, order: Dict, approved: bool, reason: str, start_time: float):
        """
        Writes immutable audit log to Cloud (Postgres) and Local Backup.
        """
        latency = (time.time() - start_time) * 1000

        # 1. Cloud Log (Primary)
        self.cloud_logger.log_compliance_event(
            order=order,
            approved=approved,
            reason=reason,
            check_latency_ms=latency,
            is_simulation=order.get("is_simulation", False),
        )

        # 2. Local File Backup
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "order": order,
            "approved": approved,
            "reason": reason,
            "check_latency_ms": round(latency, 2),
        }
        compliance_logger.info(json.dumps(entry))

    def log_execution_outcome(self, order: Dict, submitted: bool, reason: str = ""):
        """Honesty fix: the 'approved' entry written by check_order is a PRE-TRADE compliance
        check, NOT proof of execution. Buying-power / market / PDT gates between approval and the
        broker call can still drop an approved order so it never reaches Alpaca. This records the
        post-approval EXECUTION outcome into the SAME tamper-evident trail, so 'approved'
        reconciles with 'submitted' — an approved-but-dropped order is now visible instead of
        being misread as executed."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "execution",
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "quantity": order.get("quantity"),
            "submitted": submitted,
            "reason": reason,
        }
        compliance_logger.info(json.dumps(entry))

    def _cleanup_recent_trades(self):
        """Removes trades older than the wash trade window."""
        now = time.time()
        self._recent_trades = [
            t
            for t in self._recent_trades
            if now - t["timestamp"] < self._wash_trade_window_seconds
        ]


# BUG-AI-102 (#1240): the module-level ComplianceGuardian() singleton was DEAD code —
# the engine builds a fresh ComplianceGuardian() per boot (core/engine/base.py) and
# nothing imported this global, so its un-reset daily_trades/_recent_trades could never
# leak into a decision. Removed to drop the import-time constructor side effect.
