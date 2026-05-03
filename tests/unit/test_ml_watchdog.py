import unittest
from unittest.mock import patch

from core.ml_watchdog import MLWatchdog


class TestMLWatchdog(unittest.TestCase):
    def setUp(self):
        # Create a fresh watchdog instance for each test to avoid state bleeding
        self.watchdog = MLWatchdog(alert_threshold_sec=60, kill_threshold_sec=300)

    @patch("core.ml_watchdog.kill_switch")
    @patch("core.ml_watchdog.send_slack_alert")
    @patch("core.ml_watchdog.time.time")
    def test_record_error_slack_alert(
        self, mock_time, mock_slack_alert, mock_kill_switch
    ):
        mock_kill_switch.is_halted.return_value = False

        # 1. First error at t=100
        mock_time.return_value = 100.0
        self.watchdog.record_error("LSTMAgent", Exception("test"))
        self.assertEqual(self.watchdog.first_error_time, 100.0)
        mock_slack_alert.assert_not_called()

        # 2. Second error at t=130 (30s elapsed -> no alert yet)
        mock_time.return_value = 130.0
        self.watchdog.record_error("LSTMAgent", Exception("test"))
        mock_slack_alert.assert_not_called()

        # 3. Third error at t=161 (61s elapsed -> Alert!)
        mock_time.return_value = 161.0
        self.watchdog.record_error("LSTMAgent", Exception("test"))
        self.assertTrue(self.watchdog.slack_alert_sent)
        mock_slack_alert.assert_called_once()
        mock_kill_switch.trip.assert_not_called()

    @patch("core.ml_watchdog.kill_switch")
    @patch("core.ml_watchdog.send_slack_alert")
    @patch("core.ml_watchdog.time.time")
    def test_record_error_kill_switch_trip(
        self, mock_time, mock_slack_alert, mock_kill_switch
    ):
        mock_kill_switch.is_halted.return_value = False

        # First error at t=100
        mock_time.return_value = 100.0
        self.watchdog.record_error("RLAgent", Exception("test2"))

        # Error at t=401 (301s elapsed -> Kill Switch Trip!)
        mock_time.return_value = 401.0
        self.watchdog.record_error("RLAgent", Exception("test2"))

        mock_slack_alert.assert_called_once()  # Should send the slack alert too
        mock_kill_switch.trip.assert_called_once()

    @patch("core.ml_watchdog.kill_switch")
    @patch("core.ml_watchdog.send_slack_alert")
    def test_record_success_resets_state(self, mock_slack_alert, mock_kill_switch):
        # Manually force partial escalated state
        self.watchdog.first_error_time = 100.0
        self.watchdog.slack_alert_sent = True

        self.watchdog.record_success("LSTMAgent")

        self.assertIsNone(self.watchdog.first_error_time)
        self.assertFalse(self.watchdog.slack_alert_sent)
        mock_slack_alert.assert_called_once()  # Should have sent the 'recovered' slack alert

    @patch("core.ml_watchdog.kill_switch")
    @patch("core.ml_watchdog.time.time")
    def test_no_escalation_if_system_already_halted(self, mock_time, mock_kill_switch):
        mock_kill_switch.is_halted.return_value = True
        mock_time.return_value = 100.0

        self.watchdog.record_error("RLAgent", Exception("system dead"))

        # Should not start the timer or escalate if Kill Switch is already active
        self.assertIsNone(self.watchdog.first_error_time)


if __name__ == "__main__":
    unittest.main()
