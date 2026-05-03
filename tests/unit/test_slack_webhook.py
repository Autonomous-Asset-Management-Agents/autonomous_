import logging
import os
from unittest.mock import patch
import pytest

from core.cloud_logger import SlackWebhookHandler, setup_logging


def test_slack_webhook_handler_critical(requests_mock):
    webhook_url = "https://hooks.slack.com/services/dummy-test-webhook/does-not-exist"
    requests_mock.post(webhook_url, json={"status": "ok"})

    # Create handler directly
    handler = SlackWebhookHandler(webhook_url=webhook_url)
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("test_slack_logger")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Send a critical message
    logger.critical("Database connection lost")

    assert requests_mock.called
    assert requests_mock.call_count == 1
    assert "Database connection lost" in requests_mock.last_request.json()["content"]


def test_slack_webhook_handler_ignores_lower_levels(requests_mock):
    webhook_url = "https://hooks.slack.com/services/dummy-test-webhook/does-not-exist"
    requests_mock.post(webhook_url, json={"status": "ok"})

    handler = SlackWebhookHandler(webhook_url=webhook_url)
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("test_slack_logger_levels")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Send lower level messages
    logger.info("Just an info")
    logger.error("An error occurred")

    assert not requests_mock.called


@patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"})
def test_setup_logging_attaches_slack_handler():
    setup_logging()

    root_logger = logging.getLogger()
    slack_handlers = [
        h for h in root_logger.handlers if isinstance(h, SlackWebhookHandler)
    ]

    assert len(slack_handlers) == 1
    assert slack_handlers[0].webhook_url == "https://hooks.slack.com/test"


def test_slack_webhook_handler_fails_silently(requests_mock):
    webhook_url = "https://hooks.slack.com/fail"
    # Mock to specifically raise a timeout/connection error
    requests_mock.post(webhook_url, exc=Exception("Connection Error"))

    handler = SlackWebhookHandler(webhook_url=webhook_url)
    logger = logging.getLogger("test_slack_logger_silence")
    logger.addHandler(handler)

    # This should not raise an exception
    logger.critical("This will fail silently")
