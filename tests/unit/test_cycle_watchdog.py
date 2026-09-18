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


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestEvaluateStall:
    """TIME-driven stall detection (#1832) — catches a fully-dead loop that the CYCLE-driven
    empty-counter above misses (the 2026-07-02 incident: strategy loop thread died while the HTTP
    server stayed up → record_*() never called → counter frozen → nothing escalated)."""

    def _eval(self, **over):
        from core.cycle_watchdog import evaluate_stall

        kw = dict(
            now=10_000.0,
            last_cycle_ts=9_000.0,
            market_open=True,
            strategy_running=True,
            stall_after_seconds=5400,
        )
        kw.update(over)
        return evaluate_stall(**kw)

    def test_no_cycle_yet_is_not_a_stall(self):
        v = self._eval(last_cycle_ts=None)
        assert v["stalled"] is False and v["reason"] == "no_cycle_yet"
        assert v["age_seconds"] is None

    def test_fresh_cycle_is_ok(self):
        v = self._eval(now=10_000.0, last_cycle_ts=9_000.0)  # age 1000s < 5400s
        assert v["stalled"] is False and v["reason"] == "ok"
        assert v["age_seconds"] == 1000.0

    def test_stale_cycle_while_market_open_is_a_stall(self):
        # age 6000s > 5400s, market open, running -> STALL (the incident shape)
        v = self._eval(now=15_000.0, last_cycle_ts=9_000.0)
        assert v["stalled"] is True
        assert v["age_seconds"] == 6000.0
        assert "market open" in v["reason"]

    def test_market_closed_is_never_a_stall(self):
        v = self._eval(now=1_000_000.0, last_cycle_ts=9_000.0, market_open=False)
        assert v["stalled"] is False and v["reason"] == "market_closed"

    def test_strategy_stopped_is_never_a_stall(self):
        v = self._eval(now=1_000_000.0, last_cycle_ts=9_000.0, strategy_running=False)
        assert v["stalled"] is False and v["reason"] == "strategy_stopped"

    def test_exactly_at_threshold_is_not_yet_a_stall(self):
        v = self._eval(now=9_000.0 + 5400, last_cycle_ts=9_000.0)  # age == threshold
        assert v["stalled"] is False and v["reason"] == "ok"


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
class TestStallEdgeTrigger:
    """#1832 increment 2 — note_stall_verdict is EDGE-triggered, so the independent stall-monitor
    thread emits the loop_stalled signal ONCE per stall episode (not on every periodic check).
    """

    def setup_method(self):
        from core.cycle_watchdog import CycleWatchdog

        self.wd = CycleWatchdog()

    def test_emits_once_on_transition_into_stall(self):
        assert self.wd.note_stall_verdict(True) is True  # ok -> stalled: emit
        assert self.wd.note_stall_verdict(True) is False  # still stalled: no re-emit
        assert self.wd.note_stall_verdict(True) is False

    def test_no_emit_while_healthy(self):
        assert self.wd.note_stall_verdict(False) is False
        assert self.wd.note_stall_verdict(False) is False

    def test_reemits_after_recovery(self):
        assert self.wd.note_stall_verdict(True) is True  # stall -> emit
        assert self.wd.note_stall_verdict(False) is False  # recovered
        assert self.wd.note_stall_verdict(True) is True  # new episode -> emit again

    def test_reset_clears_stall_state(self):
        self.wd.note_stall_verdict(True)  # stall active
        self.wd.reset()
        assert (
            self.wd.note_stall_verdict(True) is True
        )  # fresh episode after reset -> emit
