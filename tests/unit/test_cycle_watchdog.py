"""Unit tests for CycleWatchdog (AC-6, AC-7, AC-9, AC-10)."""

from unittest.mock import patch

import allure


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestCycleWatchdog:
    """Tests for core.cycle_watchdog.CycleWatchdog."""

    def setup_method(self):
        """Fresh CycleWatchdog instance per test (no singleton state leakage)."""
        from core.cycle_watchdog import CycleWatchdog

        self.wd = CycleWatchdog(alert_threshold=3, kill_threshold=5)

    # --- AC-6: Slack alert after exactly 3 empty cycles ---

    @patch("core.cycle_watchdog.threading.Thread")
    @patch("core.cycle_watchdog.kill_switch")
    def test_ac6_no_alert_before_threshold(self, mock_ks, mock_thread):
        """No Slack alert before alert_threshold is reached."""
        mock_ks.is_halted.return_value = False
        self.wd.record_empty_cycle(14)
        self.wd.record_empty_cycle(14)
        mock_thread.assert_not_called()

    @patch("core.cycle_watchdog.threading.Thread")
    @patch("core.cycle_watchdog.kill_switch")
    def test_ac6_slack_alert_at_threshold(self, mock_ks, mock_thread):
        """AC-6: Slack alert fires at exactly alert_threshold (3)."""
        mock_ks.is_halted.return_value = False
        for _ in range(3):
            self.wd.record_empty_cycle(14)
        mock_thread.assert_called_once()
        # Verify the Thread was started and the message content
        _call_kwargs = mock_thread.call_args[1]
        assert "3 consecutive" in _call_kwargs["args"][0]
        mock_thread.return_value.start.assert_called_once()

    @patch("core.cycle_watchdog.threading.Thread")
    @patch("core.cycle_watchdog.kill_switch")
    def test_ac6_slack_alert_only_once(self, mock_ks, mock_thread):
        """Slack alert fires only once, not on every subsequent empty cycle."""
        mock_ks.is_halted.return_value = False
        for _ in range(4):
            self.wd.record_empty_cycle(14)
        # Only 1 Thread created despite 4 empty cycles (alert fires at 3, not at 4)
        mock_thread.assert_called_once()

    # --- AC-7: Kill Switch after exactly 5 empty cycles ---

    @patch("core.cycle_watchdog.threading.Thread")
    @patch("core.cycle_watchdog.kill_switch")
    def test_ac7_no_kill_before_threshold(self, mock_ks, mock_thread):
        """No Kill Switch trip before kill_threshold."""
        mock_ks.is_halted.return_value = False
        for _ in range(4):
            self.wd.record_empty_cycle(14)
        mock_ks.trip.assert_not_called()

    @patch("core.cycle_watchdog.threading.Thread")
    @patch("core.cycle_watchdog.kill_switch")
    def test_ac7_kill_switch_at_threshold(self, mock_ks, mock_thread):
        """AC-7: Kill Switch trips at exactly kill_threshold (5)."""
        mock_ks.is_halted.return_value = False
        for _ in range(5):
            self.wd.record_empty_cycle(14)
        mock_ks.trip.assert_called_once()
        assert "5 consecutive" in mock_ks.trip.call_args[1]["reason"]

    @patch("core.cycle_watchdog.threading.Thread")
    @patch("core.cycle_watchdog.kill_switch")
    def test_ac7_no_retrip_after_threshold(self, mock_ks, mock_thread):
        """N3: Kill Switch trips exactly ONCE — not on every cycle past threshold."""
        mock_ks.is_halted.return_value = False
        for _ in range(7):
            self.wd.record_empty_cycle(14)
        # trip() called at exactly 5, not at 6 or 7
        mock_ks.trip.assert_called_once()

    # --- AC-9: HOLD/VETO = healthy (successful cycle resets counter) ---

    @patch("core.cycle_watchdog.threading.Thread")
    @patch("core.cycle_watchdog.kill_switch")
    def test_ac9_successful_cycle_resets_counter(self, mock_ks, mock_thread):
        """AC-9: record_successful_cycle() resets the empty counter."""
        mock_ks.is_halted.return_value = False
        self.wd.record_empty_cycle(14)
        self.wd.record_empty_cycle(14)
        assert self.wd._consecutive_empty == 2
        self.wd.record_successful_cycle()
        assert self.wd._consecutive_empty == 0

    @patch("core.cycle_watchdog.threading.Thread")
    @patch("core.cycle_watchdog.kill_switch")
    def test_ac9_interleaved_success_prevents_alert(self, mock_ks, mock_thread):
        """A successful cycle between empty ones prevents alert."""
        mock_ks.is_halted.return_value = False
        self.wd.record_empty_cycle(14)
        self.wd.record_empty_cycle(14)
        self.wd.record_successful_cycle()  # Reset
        self.wd.record_empty_cycle(14)
        self.wd.record_empty_cycle(14)
        # Only 2 consecutive empty — should not alert (threshold=3)
        mock_thread.assert_not_called()
        assert self.wd._consecutive_empty == 2

    # --- AC-10: reset() prevents re-trip after restart ---

    @patch("core.cycle_watchdog.threading.Thread")
    @patch("core.cycle_watchdog.kill_switch")
    def test_ac10_reset_clears_state(self, mock_ks, mock_thread):
        """AC-10: reset() clears the counter and Slack alert flag."""
        mock_ks.is_halted.return_value = False
        for _ in range(4):
            self.wd.record_empty_cycle(14)
        self.wd.reset()
        assert self.wd._consecutive_empty == 0
        assert self.wd._slack_alert_sent is False

    @patch("core.cycle_watchdog.threading.Thread")
    @patch("core.cycle_watchdog.kill_switch")
    def test_ac10_reset_prevents_immediate_retrip(self, mock_ks, mock_thread):
        """AC-10: After reset, a single empty cycle does NOT trip Kill Switch."""
        mock_ks.is_halted.return_value = False
        for _ in range(4):
            self.wd.record_empty_cycle(14)
        self.wd.reset()
        self.wd.record_empty_cycle(14)
        mock_ks.trip.assert_not_called()  # Only 1 empty after reset

    # --- K2: send_slack_alert is non-blocking ---

    @patch("core.cycle_watchdog.threading.Thread")
    @patch("core.cycle_watchdog.kill_switch")
    def test_k2_slack_uses_thread(self, mock_ks, mock_thread):
        """K2: send_slack_alert is called via threading.Thread (non-blocking)."""
        mock_ks.is_halted.return_value = False
        for _ in range(3):
            self.wd.record_empty_cycle(14)
        # Verify Thread was created with daemon=True
        mock_thread.assert_called_once()
        assert mock_thread.call_args[1]["daemon"] is True
        mock_thread.return_value.start.assert_called_once()
