import time
import logging
from typing import Optional

from core.notifier import send_slack_alert
from core.kill_switch import kill_switch


class MLWatchdog:
    """
    ML Escalation Watchdog.
    Tracks continuous prediction failures of core ML models (LSTM/RL).

    Escalations:
      > 60s: Slack Alert (Warning)
      > 300s: Kill Switch Trip (Safe Halt)
    """

    def __init__(self, alert_threshold_sec: int = 60, kill_threshold_sec: int = 300):
        self.logger = logging.getLogger("ml_watchdog")
        self.alert_threshold_sec = alert_threshold_sec
        self.kill_threshold_sec = kill_threshold_sec

        self.first_error_time: Optional[float] = None
        self.slack_alert_sent: bool = False

    def record_success(self, agent_name: str = "MLAgent"):
        """
        Marks an inference operation as successful.
        Resets any ongoing escalation trackers.
        """
        if self.first_error_time is not None:
            self.logger.info(
                f"MLWatchdog: {agent_name} recovered. Resetting error trackers."
            )
            self.first_error_time = None

        if self.slack_alert_sent:
            # If we sent a panic alert, send an all-clear
            send_slack_alert(
                f"✅ *ML Subsystem Recovered*\n{agent_name} successfully reported a prediction after an outage."
            )
            self.slack_alert_sent = False

    def record_error(self, agent_name: str, exc: Exception):
        """
        Marks an inference failure. Escalates if time thresholds are crossed.
        """
        if kill_switch.is_halted():
            return  # No need to process escalating timeouts if the system is already dead

        now = time.time()

        if self.first_error_time is None:
            self.first_error_time = now
            self.logger.warning(
                "MLWatchdog: %s reported first failure. Escalation timer started. Error: %s",
                agent_name,
                exc,
            )
            return

        elapsed = now - self.first_error_time

        # Escalation 1: Slack Alert
        if elapsed >= self.alert_threshold_sec and not self.slack_alert_sent:
            self.logger.warning(
                "MLWatchdog: Escalation %ds reached for %s. Dispatching warning.",
                self.alert_threshold_sec,
                agent_name,
            )
            send_slack_alert(
                f"🚨 *ML Subsystem Warning*\n"
                f"Core model (`{agent_name}`) has been failing continuously for > {self.alert_threshold_sec}s.\n"
                f"Trading for current symbols is temporarily blocked locally.\n"
                f"Latest Exception: {exc}"
            )
            self.slack_alert_sent = True

        # Escalation 2: Kill Switch Trip
        if elapsed >= self.kill_threshold_sec:
            self.logger.critical(
                "MLWatchdog: Critical Escalation %ds reached. TRIPPING KILL SWITCH.",
                self.kill_threshold_sec,
            )
            kill_switch.trip(
                reason=f"Business Critical ML Event: {agent_name} crashed continuously for > {self.kill_threshold_sec}s ({exc})",
                user_id=None,
            )


# Global singleton instance
ml_watchdog = MLWatchdog()
