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

import time
import logging
import threading

from core.notifier import send_slack_alert
from core.kill_switch import kill_switch


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

    def reset(self):
        """Manual reset — called alongside KillSwitch.reset()."""
        self.logger.info(
            "CycleWatchdog: Manual reset (was %d empty cycles).",
            self._consecutive_empty,
        )
        self._consecutive_empty = 0
        self._slack_alert_sent = False
        self._last_successful_cycle = time.time()


# Global singleton instance
cycle_watchdog = CycleWatchdog()
