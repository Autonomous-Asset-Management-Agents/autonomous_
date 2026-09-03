import json
import logging
import os as _os
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List

from .audit_paths import migrate_legacy_audit_log, resolve_audit_log_path
from .cloud_logger import get_cloud_logger

# Float tolerance for the held-quantity comparison in the #2065 exit-exemption
# (guards against fractional-share rounding when quantity ≈ held_qty).
_QTY_EPS = 1e-6

# Intraday buy-pacing: retention window of the dedicated BUY-timestamp buffer.
# 2 h covers the 30 m cooldown + the 60 m rate window with margin. This buffer is
# SEPARATE from the 60 s wash-trade buffer (_recent_trades) — reading that one for
# 30 m/60 m pacing would silently expire the cooldown after 60 s (Archon CRITICAL-1).
_RECENT_BUYS_WINDOW_SEC = 7200.0

# Setup specific logger for compliance to ensure auditability (Local Fallback)
compliance_logger = logging.getLogger("ComplianceGuardian")
compliance_logger.setLevel(logging.INFO)
# In production, this would go to a WORM (Write Once Read Many) storage

# ADR-C08: Durable audit-sink location — compliance_audit.log resolves into
# USER_DATA_DIR (AAA_USER_DATA_DIR) instead of the process CWD.
# Basis: MiFID II Art. 16(6)/(7) record-keeping — the audit trail must survive an
# app upgrade. The CWD is the app BUNDLE under a desktop install, which the
# bootstrap replaces wholesale (#2586: log was wiped on upgrade; audit_chain.jsonl
# under USER_DATA_DIR survived). Cloud/dev (env unset) stays CWD-relative,
# byte-identical (BORA). A bundle-relative leftover is migrated ONCE at import.
_audit_log_path = resolve_audit_log_path("compliance_audit.log")
migrate_legacy_audit_log("compliance_audit.log", _audit_log_path)
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
        # ADR-C01: Max Order Value = 10.000 EUR (ENTRY-/Sizing-Limit)
        # Basis: ESMA Position Limit Guidelines (MiFID II Art. 57) + internes Risikopolicy v1.0
        # Begründung: Obergrenze für Positionseröffnungen/-vergrößerungen schützt vor
        # Fehleingaben (Fat-Finger) und ungewollter Marktbewegung; unter dem ESMA-
        # Schwellenwert für erweiterte Meldepflichten.
        # #2065: Risikoreduzierende EXITS (side==sell, quantity<=held_qty) sind ausgenommen —
        # ein Exit erhöht kein Positionslimit; ihn zu blocken würde das Risiko *erhöhen*
        # (Position könnte nicht geschlossen werden). Siehe _check_risk_limits.
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
        # Intraday buy-pacing: dedicated BUY-timestamp buffer (session-scoped, 2 h window),
        # SEPARATE from the 60 s wash-trade _recent_trades buffer. Feeds the gatekeeper's
        # OpeningNoise / BuyPacing metrics via build_portfolio_context. Cleared at the NY
        # daily reset. (Archon CRITICAL-1: never read _recent_trades for 30 m/60 m pacing.)
        self._recent_buys: List[float] = []

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

        # HFT Throttle (ADR-C07)
        self.hft_max_orders_per_sec_symbol = (
            get_config().COMPLIANCE_HFT_MAX_ORDERS_PER_SEC_SYMBOL
        )
        self.hft_max_orders_per_sec_aggregate = (
            get_config().COMPLIANCE_HFT_MAX_ORDERS_PER_SEC_AGGREGATE
        )
        self._hft_recent_orders: List[Dict] = []
        # #1835 thread-safety: guards the Gate-5 read-modify-write on
        # ``_hft_recent_orders`` (housekeep->read->append), non-atomic under
        # concurrent callers (lost update, proven in test_compliance_hft.py).
        # NEVER held across an external call (audit/cloud_logger) — that is the
        # #1835 deadlock-cross.
        self._hft_lock = threading.Lock()
        # #1849 follow-up thread-safety: guards the SAME lost-update RMW class for
        # the OTHER two compliance-gate buffers —
        #   * Gate 3 ``_recent_trades`` (approve-path append + cleanup-reassign, and
        #     the ``_detect_wash_trade`` read), and
        #   * Gate 4 ``daily_trades`` (the ``+= 1`` increment + the cap read).
        # DISTINCT from ``_hft_lock`` and NEVER nested with it: the ``_hft_lock``
        # critical section in ``check_order`` fully releases before the approve-path
        # ``_recent_trades`` mutation, and ``check_trade`` / ``_detect_wash_trade``
        # never touch ``_hft_lock`` — so the two locks are never held together (no
        # deadlock). Like ``_hft_lock`` it is NEVER held across an external call
        # (audit / cloud_logger / kill-switch) — the #1835 deadlock-cross.
        self._state_lock = threading.Lock()

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

            # 1b. Universal Spot US-Equity-only Guard (#1803 / GTM-1)
            # Fail-closed defense-in-depth: reject any instrument that is NOT a spot
            # US equity/ETF (CFDs, options, crypto, futures, forex). UNIVERSAL — applies
            # to every tier identically (NOT entitlement-gated, #1800). The trading
            # universe is already US-equity-only (data_provider fetches only
            # AssetClass.US_EQUITY), but a non-equity instrument that ever reaches this
            # path MUST be rejected — the class is confirmed POSITIVELY, never assumed.
            if not self._is_spot_us_equity(order):
                reason = f"Symbol {symbol} is not a spot US equity/ETF (rejected)."
                reason_code = "non_spot_us_equity"
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

            # 5. HFT Throttle (ADR-C07)
            # Basis: GTM-1 #1802 - Verbot algorithmischer Sub-Sekunden-Cancel/Replace-Schleifen.
            now = time.time()
            user_id = order.get("user_id")

            trade_record = {
                "symbol": symbol,
                "side": side,
                "timestamp": now,
                "user_id": user_id,
            }

            # One atomic critical section: housekeep->read->decide->append share
            # the lock so a concurrent housekeep can't drop this append (no lost
            # update, no TOCTOU). The audit stays OUTSIDE in ``finally`` — no
            # external call under the lock (#1835 deadlock-cross avoidance).
            with self._hft_lock:
                # Housekeeping HFT buffer (orders older than 1.0s are removed)
                self._hft_recent_orders = [
                    o for o in self._hft_recent_orders if now - o["timestamp"] < 1.0
                ]

                # Extract user's orders within the last second
                user_hft_orders = [
                    o for o in self._hft_recent_orders if o.get("user_id") == user_id
                ]

                if len(user_hft_orders) >= self.hft_max_orders_per_sec_aggregate:
                    reason = f"HFT Throttle: aggregate cap {self.hft_max_orders_per_sec_aggregate}/s exceeded."
                    reason_code = "hft_throttle"
                    return False

                symbol_hft_orders = [
                    o for o in user_hft_orders if o.get("symbol") == symbol
                ]
                if len(symbol_hft_orders) >= self.hft_max_orders_per_sec_symbol:
                    reason = f"HFT Throttle: per-symbol cap {self.hft_max_orders_per_sec_symbol}/s for {symbol}."
                    reason_code = "hft_throttle"
                    return False

                # Approve: append inside the SAME lock so no concurrent housekeep
                # reassignment can drop this record (fixes the #1835 lost update).
                self._hft_recent_orders.append(trade_record)

            # If all pass:
            decision = True
            reason = "All compliance checks passed."

            # ``_recent_trades`` (Gate 3 wash-trade) is a SEPARATE buffer from the
            # HFT one; its append + cleanup-reassign is the SAME lost-update RMW
            # the #1849 HFT lock fixed. Funnelled through ``_record_recent_trade``
            # under ``_state_lock``. The ``with self._hft_lock`` block above has
            # ALREADY released here (its scope ended at the append), so the two
            # locks are NEVER held simultaneously — no nesting, no deadlock.
            self._record_recent_trade(trade_record)

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
            # #2069 (PURE OBSERVATION): the compliance CHOKE-POINT — every reject emits
            # ONE scrubbed telemetry span so the block reason reaches the diagnose-export
            # (was: SQLite/file-log only, invisible in the export). ``reason_code`` is a
            # fixed machine string (never symbol/order content); the symbol is a public
            # ticker, additionally scrubbed by the exporter. DOUBLE-guarded (call site +
            # inside emit_block_span, which is also flag-gated + fail-safe) so it can
            # neither raise into the decision nor perturb the audit write below.
            if not decision and reason_code:
                try:
                    from core.round_table.execution_outcomes import emit_block_span

                    emit_block_span(order.get("symbol"), f"compliance:{reason_code}")
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

    # ADR-C05: US-equity asset class marker (Alpaca literal)
    # Alpaca's only US-equity asset-class marker (alpaca.trading.enums.AssetClass.
    # US_EQUITY == "us_equity"). Kept as a literal so the guard needs no alpaca import.
    _US_EQUITY_ASSET_CLASS = "us_equity"

    # ADR-C06: Allowed US-equity ticker separators
    # Allowed separators inside a US-equity ticker (class shares / preferred, e.g.
    # BRK.B, BF-B). Same shape used for defense-in-depth at api_routes.force_cycle.
    _EQUITY_SYMBOL_SEPARATORS = frozenset({".", "-"})

    def _is_spot_us_equity(self, order: Dict) -> bool:
        """#1803 (GTM-1): positively confirm the order is a spot US equity/ETF.

        Fail-closed: returns True ONLY when the instrument can be *positively confirmed*
        as a spot US equity; on any doubt it returns False (reject). No network/broker
        call — the check is synchronous and deterministic so it can never fail open on an
        API error.

        Two independent signals, combined fail-closed:
          1. Explicit field (future-proof): if the order carries an ``asset_class`` /
             ``asset_type`` field it MUST equal ``us_equity`` (case-insensitive). Any
             other value (crypto, us_option, ...) → reject.
          2. Symbol shape (always applied): the canonical US-equity ticker shape —
             starts with a letter, then only ``A–Z`` / ``.`` / ``-``, length 1–10, and
             NO digits. This deterministically rejects crypto/forex pairs (contain
             ``/``), OCC option symbols (long, embed digits) and futures/CFD codes.
        """
        # Signal 1 — explicit asset-class field, if the order carries one.
        for key in ("asset_class", "asset_type"):
            declared = order.get(key)
            if declared is not None:
                if str(declared).strip().lower() != self._US_EQUITY_ASSET_CLASS:
                    return False

        # Signal 2 — canonical US-equity ticker shape (always required).
        symbol = order.get("symbol")
        if not isinstance(symbol, str):
            return False
        symbol = symbol.strip().upper()
        if not (1 <= len(symbol) <= 10):
            return False
        if not symbol[0].isalpha():
            return False
        for char in symbol:
            if not (char.isalpha() or char in self._EQUITY_SYMBOL_SEPARATORS):
                # Any digit (options/futures) or other separator ('/' in crypto/forex
                # pairs) means this is not a plain US-equity ticker → reject.
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

        # Snapshot the buffer under ``_state_lock`` so the scan runs over a stable
        # list — a concurrent approve-path ``_record_recent_trade`` (append +
        # cleanup-reassign) can't tear the iteration or hand us a half-built list.
        # Only the cheap copy is under the lock; the comparison work runs after
        # release, and there is NO external call here — no deadlock-cross.
        with self._state_lock:
            recent_snapshot = list(self._recent_trades)

        for trade in recent_snapshot:
            if (
                trade["symbol"] == symbol
                and trade["side"] == opposite_side
                and trade.get("user_id") == user_id
            ):
                if now - trade["timestamp"] < self._wash_trade_window_seconds:
                    return True
        return False

    def _check_risk_limits(self, order: Dict) -> bool:
        """Checks the per-order value cap (ADR-C01).

        The cap is an ENTRY / position-sizing limit. A risk-reducing EXIT — a SELL of
        (part of) an already-held position (``quantity <= held_qty``) — is exempt (#2065):
        the exit is bounded by the held position, and blocking it would prevent closing
        a position, i.e. INCREASE risk. ``held_qty`` is supplied by the executor from the
        broker's real position (``get_open_position``); a SELL without a held position, or
        one exceeding the held quantity (would flip to short), stays blocked.
        """
        try:
            value = float(order["quantity"]) * float(order["price"])
            if value <= self.max_order_value:
                return True
            # #2065 exit-exemption: only a SELL that reduces an existing position.
            if order.get("side") == "sell":
                held_qty = float(order.get("held_qty", 0.0) or 0.0)
                if held_qty > 0.0 and float(order["quantity"]) <= held_qty + _QTY_EPS:
                    return True
            return False
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
        # ADR-C04-EXIT: the daily-trades cap is SIDE-AGNOSTIC — record_trade() takes no
        # side — so once the budget is spent, no SELL lands either, INCLUDING a
        # stop-loss, until reset_daily_limit() at the NY rollover. There is no
        # broker-side stop to fall back on: order_executor submits only Limit/Market
        # orders, so every exit is bot-issued and must pass this gate. A bot that cannot
        # sell cannot cut a loss — that is a safety property, not a performance one.
        #
        # The codebase already ratified this principle one layer up, in gatekeeper.py:
        #   "#2031: nur BUY — eine risikoreduzierende SELL darf NIE durch das (weiche,
        #    selbst gesetzte) Tageslimit blockiert werden."
        # This restores that invariant here. The cap keeps binding on everything that
        # ADDS risk; only risk-REDUCING exits are exempt, using the SAME predicate the
        # #2065 exemption already uses above — deliberately not a second definition of
        # "exit".
        #
        # Ordering: the exemption is evaluated BEFORE the warning, because that string
        # feeds an operator alert reading "Trading halted." Emitting it for a trade we
        # then allow would make the audit trail contradict what happened.
        from config import get_config  # lazy — mirrors __init__'s import pattern

        if (
            source != "human_approved"
            and not (
                getattr(get_config(), "COMPLIANCE_ALLOW_RISK_REDUCING_EXITS", False)
                and self._is_risk_reducing_exit(order)
            )
            and self.daily_trades >= self.max_daily_trades
        ):
            logging.warning(
                "Compliance: Max daily trades (%s) reached.", self.max_daily_trades
            )
            return False

        return True

    def _is_risk_reducing_exit(self, order: Dict) -> bool:
        """True only for a SELL bounded by an already-held long position.

        Byte-identical test to the ADR-C01 / #2065 exemption in ``_check_risk_limits``
        above: a SELL of at most the held quantity cannot increase exposure. Selling more
        would flip to short — that ADDS risk, so the cap must still bind.

        Fail-CLOSED. A malformed or incomplete order returns False and stays capped. That
        direction matters: a missing ``held_qty`` must never read as "exempt", or a caller
        that forgets the field silently un-caps the daily limit for every SELL.
        """
        try:
            if order.get("side") != "sell":
                return False
            # #2713: labeled rotation/trim churn is NOT risk-reducing — it is counted and
            # wash-checked like any trade. Unknown/absent kind keeps the legacy exemption
            # (fail-SAFE: an unlabeled stop path must never get capped by accident).
            if order.get("exit_kind") in ("rotation", "trim"):
                return False
            held_qty = float(order.get("held_qty", 0.0) or 0.0)
            return held_qty > 0.0 and float(order["quantity"]) <= held_qty + _QTY_EPS
        except (ValueError, KeyError, TypeError):
            return False

    def record_trade(self, order=None):
        """Count one executed autonomous trade against the daily cap (ADR-C04).

        The single atomic increment site for ``daily_trades``. The bare
        ``daily_trades += 1`` RMW the executor/strategy callers used is non-atomic
        (load-add-store) → concurrent callers can lose an increment, letting the
        daily cap be silently exceeded (a compliance failure). Held under
        ``_state_lock`` so every increment is conserved. No external call inside
        the lock — no deadlock-cross.

        ADR-C04-EXIT (counter symmetry): a risk-reducing exit that ``check_trade``
        already exempts from the BLOCK (:478-489) must ALSO not CONSUME a buy slot —
        otherwise a rotation of held names cannibalises the day's buy budget and
        freezes re-entry (live 2026-07-28: 8 forced sells spent 8 of 10 slots →
        blocked:daily_limit). When ``order`` identifies a risk-reducing exit AND the
        same flag the block honours is on, skip the increment. Fail-closed: no order,
        the flag OFF, or a risk-ADDING order (buy / short-flip) → increments, exactly
        as before — so the cap still bounds every trade that adds risk.
        """
        from config import get_config  # lazy — mirrors check_trade's import pattern

        if (
            order is not None
            and getattr(get_config(), "COMPLIANCE_ALLOW_RISK_REDUCING_EXITS", False)
            and self._is_risk_reducing_exit(order)
        ):
            return  # exempt exit — neither blocked nor charged against the buy budget
        with self._state_lock:
            self.daily_trades += 1

    def refund_trade(self):
        """Release one reserved daily slot (Reserve-and-Reclaim, #2118).

        ``record_trade`` reserves a slot BEFORE the broker submit so the check-and-
        reserve stays atomic w.r.t. the asyncio loop (concurrent tenants can never
        exceed the cap — see ``order_executor`` ``asyncio.gather``). If the broker then
        **deterministically** rejects the order (APIError 4xx = guaranteed no fill), the
        executor calls this to give the reserved slot back, so a rejected order does not
        permanently burn budget. NEVER called for ambiguous failures (timeout/5xx) where
        a fill is possible — those keep the reservation (fail-closed).

        Same ``_state_lock`` as ``record_trade`` (conserved under concurrency); guarded so
        the counter can never underflow below 0.
        """
        with self._state_lock:
            if self.daily_trades > 0:
                self.daily_trades -= 1

    def reset_daily_limit(self):
        """Reset the daily trades counter (called at start of each NY trading day)."""
        self.daily_trades = 0
        self._daily_limit_alert_sent = False
        # Intraday buy-pacing is session-scoped: the day rolls over → the pacing history
        # starts fresh (the opening-noise / cooldown / rate windows are intra-day).
        self._recent_buys = []

    def buys_last_hour(self, now: float) -> int:
        """Number of autonomous BUYs recorded within the last hour of ``now`` (epoch s).
        Reads the dedicated buy buffer (NOT the 60 s wash-trade buffer). Fed to the
        gatekeeper's BuyPacingGuard via build_portfolio_context."""
        with self._state_lock:
            buys = list(self._recent_buys)
        return sum(1 for t in buys if now - t <= 3600.0)

    def minutes_since_last_buy(self, now: float):
        """Minutes since the most recent recorded BUY, or ``None`` if none in the buffer
        (fail-open: the gatekeeper's cooldown guard then does not fire)."""
        with self._state_lock:
            buys = list(self._recent_buys)
        if not buys:
            return None
        return (now - max(buys)) / 60.0

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

    def _record_recent_trade(self, trade_record: Dict):
        """Approve-path Gate-3 buffer write: append the record then housekeep the
        wash-trade window, as ONE atomic critical section under ``_state_lock``.

        The append and the ``_cleanup_recent_trades`` reassignment (``= [...]``)
        are a non-atomic read-modify-write: without the lock a concurrent cleanup
        rebuilds the list around a just-completed ``.append`` and drops it (lost
        update — the #1849 bug class). Both run under the SAME lock so no record is
        lost. Only in-memory work + a ``time.time()`` clock read run here — NO
        external call (audit/cloud_logger/kill-switch), so there is no
        deadlock-cross, and ``_state_lock`` is never nested with ``_hft_lock``
        (that critical section already released in ``check_order``).
        """
        with self._state_lock:
            self._recent_trades.append(trade_record)
            self._cleanup_recent_trades()
            # Intraday buy-pacing: mirror BUY timestamps into the dedicated buffer (NOT
            # subject to the 60 s wash-trade purge), pruned to the 2 h retention window.
            if str(trade_record.get("side", "")).lower() == "buy":
                ts = float(trade_record.get("timestamp", 0.0) or 0.0)
                self._recent_buys.append(ts)
                cutoff = ts - _RECENT_BUYS_WINDOW_SEC
                self._recent_buys = [t for t in self._recent_buys if t >= cutoff]

    def _cleanup_recent_trades(self):
        """Removes trades older than the wash trade window.

        Assumes ``_state_lock`` is already held by the caller
        (``_record_recent_trade``) — it mutates ``_recent_trades`` in place.
        """
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
