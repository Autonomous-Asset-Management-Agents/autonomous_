import os as _os
import logging
import time
from typing import Dict, List
from datetime import datetime, timezone
import json
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
        # Review: Jährlich oder bei Kapitaländerung > 50% durch Risk Committee.
        self.max_order_value = 10_000.0

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
        self.max_daily_trades = 50
        self.cloud_logger = get_cloud_logger()



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

        try:
            # 1. Regulatory Blocklist Check
            if symbol in self.restricted_list:
                reason = f"Symbol {symbol} is on the Restricted Customer List."
                self._log_audit(order, False, reason, start_time)
                return False

            # 2. MiFID II / Data Completeness Check
            if not self._check_mifid_fields(order):
                reason = "Missing mandatory MiFID II reporting fields."
                self._log_audit(order, False, reason, start_time)
                return False

            # 3. Wash Trade Prevention (Tenant-Aware)
            if self._detect_wash_trade(symbol, side, order.get("user_id")):
                reason = "Potential Wash Trade detected (conflicting orders in short window)."
                self._log_audit(order, False, reason, start_time)
                return False

            # 4. Pre-Trade Risk Limits
            if not self._check_risk_limits(order):
                reason = "Order exceeds pre-defined risk limits (Max Order Value)."
                self._log_audit(order, False, reason, start_time)
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
            reason = f"System Error during compliance check: {str(e)}"
            compliance_logger.error(reason)
            return False

        finally:
            self._log_audit(order, decision, reason, start_time)

        return decision

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

    def check_trade(self, order: Dict) -> bool:
        """Check if a trade complies with risk rules."""
        if self.daily_trades >= self.max_daily_trades:
            logging.warning(
                f"Compliance: Max daily trades ({self.max_daily_trades}) reached."
            )
            return False

        return True

    def reset_daily_limit(self):
        """Reset the daily trades counter (called at start of each trading day)."""
        self.daily_trades = 0

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

    def _cleanup_recent_trades(self):
        """Removes trades older than the wash trade window."""
        now = time.time()
        self._recent_trades = [
            t
            for t in self._recent_trades
            if now - t["timestamp"] < self._wash_trade_window_seconds
        ]


# Module-level singleton (optional)
guardian = ComplianceGuardian()
