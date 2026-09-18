# test_notifier.py
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import allure

# Adjust path to import from core
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import config
from core.notifier import send_slack_alert


@allure.feature("VC-6 Reporting & Client Servicing")
@allure.story("Reporting & Auditing")
class TestNotifier(unittest.TestCase):
    def setUp(self):
        # Backup config values
        self.orig_webhook = getattr(config, "SLACK_WEBHOOK_URL", None)
        self.orig_enabled = getattr(config, "ENABLE_SLACK_ALERTS", False)

        config.SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/test/webhook"
        config.ENABLE_SLACK_ALERTS = True

    def tearDown(self):
        # Restore config values
        config.SLACK_WEBHOOK_URL = self.orig_webhook
        config.ENABLE_SLACK_ALERTS = self.orig_enabled

    @patch("requests.post")
    def test_send_slack_alert_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        send_slack_alert("Test Success Message", level="success")

        # Verify requests.post was called
        self.assertTrue(mock_post.called)

        # Verify the call arguments
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], config.SLACK_WEBHOOK_URL)

        payload = json.loads(kwargs["data"])
        self.assertIn("🚀", payload["text"])
        self.assertIn("SUCCESS", payload["text"])
        self.assertIn("Test Success Message", payload["text"])

    @patch("requests.post")
    def test_send_slack_alert_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        send_slack_alert("Test Error Message", level="error")

        self.assertTrue(mock_post.called)
        payload = json.loads(mock_post.call_args[1]["data"])
        self.assertIn("❌", payload["text"])
        self.assertIn("ERROR", payload["text"])

    def test_send_slack_alert_disabled(self):
        config.ENABLE_SLACK_ALERTS = False
        with patch("requests.post") as mock_post:
            send_slack_alert("Test Message", level="info")
            self.assertFalse(mock_post.called)


if __name__ == "__main__":
    unittest.main()
