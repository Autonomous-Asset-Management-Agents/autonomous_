# core/cycle_watchdog.py
# MiFID II Art. 17 — Trading Cycle Liveness Watchdog
#
# Detects silent trading loop failures by tracking consecutive cycles
# where the Round Table produced ZERO completed evaluations.
#
# IMPORTANT: A cycle where all signals are HOLD or all are VETOED by the
# ComplianceGatekeeper is HEALTHY — the system is working as designed.
# Only cycles where the graph dispatch itself failed (timeout, crash,
# zero completions) count as "empty".
#
# Escalations:
#   >= 3 empty cycles: Slack Alert (Warning)
#   >= 5 empty cycles: Kill Switch Trip (Safe Halt)
#
# Pattern: Follows MLWatchdog (core/ml_watchdog.py)

import logging
import threading
import time

from core.kill_switch import kill_switch
from core.notifier import send_slack_alert


class CycleWatchdog:
    """
    Trading Cycle Liveness Watchdog (MiFID II Art. 17).

    Detects silent trading loop failures by tracking consecutive cycles
    where the Round Table produced ZERO completed evaluations.

    A cycle where all signals are HOLD or all are VETOED by the
    ComplianceGatekeeper is HEALTHY — the system is working as designed.
    Only cycles where the graph dispatch itself failed (timeout, crash,
    zero completions) count as "empty".

    Escalations:
      >= 3 empty cycles: Slack Alert (Warning)
      >= 5 empty cycles: Kill Switch Trip (Safe Halt)
    """

    def __init__(self, alert_threshold: int = 3, kill_threshold: int = 5):
        self.logger = logging.getLogger("cycle_watchdog")
        self.alert_threshold = alert_threshold
        self.kill_threshold = kill_threshold
        self._consecutive_empty = 0
        self._slack_alert_sent = False
        self._last_successful_cycle: float = time.time()
        self._stall_active = False  # #1832 — edge-trigger state for note_stall_verdict
        self._lock = threading.Lock()

    def record_successful_cycle(self):
        """Round Table completed at least 1 symbol evaluation (any signal OK)."""
        if self._consecutive_empty > 0:
            self.logger.info(
                "CycleWatchdog: Recovered after %d empty cycles.",
                self._consecutive_empty,
            )
        self._consecutive_empty = 0
        self._slack_alert_sent = False
        self._last_successful_cycle = time.time()

    def record_empty_cycle(self, symbol_count: int = 0):
        """Round Table completed ZERO evaluations (timeout/crash/deadlock)."""
        self._consecutive_empty += 1
        self.logger.warning(
            "MIFID_AUDIT CYCLE_EMPTY: %d/%d consecutive empty cycles "
            "(symbols_attempted=%d, alert=%d, kill=%d)",
            self._consecutive_empty,
            self.kill_threshold,
            symbol_count,
            self.alert_threshold,
            self.kill_threshold,
        )

        if (
            self._consecutive_empty >= self.alert_threshold
            and not self._slack_alert_sent
        ):
            elapsed_min = (time.time() - self._last_successful_cycle) / 60.0
            _msg = (
                f"⚠️ *Cycle Watchdog*: {self._consecutive_empty} consecutive trading "
                f"cycles produced ZERO Round Table completions ({elapsed_min:.1f} min "
                f"since last successful cycle). Kill Switch at {self.kill_threshold}."
            )
            # Fire-and-forget: send_slack_alert uses synchronous requests.post
            # which would block the async event loop for up to 10s.
            threading.Thread(target=send_slack_alert, args=(_msg,), daemon=True).start()
            self._slack_alert_sent = True

        if self._consecutive_empty == self.kill_threshold:
            elapsed_min = (time.time() - self._last_successful_cycle) / 60.0
            self.logger.critical(
                "MIFID_AUDIT SYSTEM_HALT: CycleWatchdog tripping Kill Switch "
                "after %d empty cycles (%.1f min).",
                self._consecutive_empty,
                elapsed_min,
            )
            kill_switch.trip(
                reason=(
                    f"CycleWatchdog: {self._consecutive_empty} consecutive cycles "
                    f"without Round Table completions ({elapsed_min:.1f} min). "
                    f"System non-functional."
                ),
            )

    def note_stall_verdict(self, stalled: bool) -> bool:
        """Edge-trigger for the TIME-driven stall (fed by the independent stall-monitor thread,
        #1832). Returns True ONLY on the ok -> stalled transition, so the loop_stalled signal fires
        ONCE per episode rather than on every periodic check. Logs recovery on stalled -> ok.
        """
        with self._lock:
            prev = getattr(self, "_stall_active", False)
            self._stall_active = stalled
            if stalled and not prev:
                return True
            if prev and not stalled:
                self.logger.info("CycleWatchdog: loop-stall cleared (cycles resumed).")
            return False

    def status(self) -> dict:
        """Read-only liveness snapshot (ADR-OBS-01 / PR B) — NEVER mutates state.

        Exposed via /engine-diagnostics ``watchdogs.cycle``: how many consecutive
        empty cycles, the alert/kill thresholds, and how long since the last
        successful Round-Table cycle. Null-safe: a missing private attr degrades
        to a benign default rather than raising."""
        last = getattr(self, "_last_successful_cycle", None)
        age = (time.time() - last) if last is not None else None
        return {
            "consecutive_empty": getattr(self, "_consecutive_empty", 0),
            "alert_threshold": getattr(self, "alert_threshold", None),
            "kill_threshold": getattr(self, "kill_threshold", None),
            "seconds_since_last_successful_cycle": (
                round(age, 1) if age is not None else None
            ),
        }

    def reset(self):
        """Manual reset — called alongside KillSwitch.reset()."""
        self.logger.info(
            "CycleWatchdog: Manual reset (was %d empty cycles).",
            self._consecutive_empty,
        )
        self._consecutive_empty = 0
        self._slack_alert_sent = False
        self._last_successful_cycle = time.time()
        with self._lock:
            self._stall_active = False


# Global singleton instance
cycle_watchdog = CycleWatchdog()


def evaluate_stall(
    now: float,
    last_cycle_ts,
    market_open: bool,
    strategy_running: bool,
    stall_after_seconds: float,
) -> dict:
    """Pure, TIME-DRIVEN loop-liveness verdict — is the trading loop silently STALLED? (#1832)

    CycleWatchdog above is CYCLE-DRIVEN: the trading loop reports empty/successful cycles into it.
    If the loop THREAD dies entirely (the 2026-07-02 incident — the strategy loop died while the
    HTTP server stayed up), ``record_*`` is never called, ``_consecutive_empty`` freezes and nothing
    escalates. This verdict is derived purely from the AGE of the last completed cycle, so a fully
    dead loop is caught. It is observational (no side effects): callers decide what to expose/emit.

    A stall is flagged ONLY when the loop is expected to be producing cycles — ``strategy_running``
    AND ``market_open`` AND a cycle has run before AND its age exceeds ``stall_after_seconds``.
    no_cycle_yet / strategy_stopped / market_closed are NOT stalls (no false positives).
    """
    if last_cycle_ts is None:
        return {"stalled": False, "age_seconds": None, "reason": "no_cycle_yet"}
    age = round(now - last_cycle_ts, 1)
    if not strategy_running:
        return {"stalled": False, "age_seconds": age, "reason": "strategy_stopped"}
    if not market_open:
        return {"stalled": False, "age_seconds": age, "reason": "market_closed"}
    if age > stall_after_seconds:
        return {
            "stalled": True,
            "age_seconds": age,
            "reason": (
                f"no completed trading cycle for {age}s "
                f"(> {stall_after_seconds}s) while market open"
            ),
        }
    return {"stalled": False, "age_seconds": age, "reason": "ok"}
