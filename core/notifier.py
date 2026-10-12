# notifier.py
import json
import logging

import requests

import config

logger = logging.getLogger(__name__)


def send_slack_alert(message: str, level: str = "info"):
    """
    Sends an alert message to Slack via an Incoming Webhook.

    Args:
        message: The text of the alert.
        level: Severity level ('info', 'warning', 'error', 'success').
    """
    if not getattr(config, "ENABLE_SLACK_ALERTS", False):
        return

    webhook_url = getattr(config, "SLACK_WEBHOOK_URL", None)
    if not webhook_url:
        logger.debug("SLACK_WEBHOOK_URL not set. Skipping notification.")
        return

    # Map levels to emojis and colors
    level_map = {
        "info": {"emoji": "ℹ️", "color": "#3AA3E3"},
        "warning": {"emoji": "⚠️", "color": "#EBB424"},
        "error": {"emoji": "❌", "color": "#FF0000"},
        "success": {"emoji": "🚀", "color": "#2EB886"},
    }

    meta = level_map.get(level, level_map["info"])
    formatted_msg = f"{meta['emoji']} *Bot Engine Alert* ({level.upper()}):\n{message}"

    payload = {
        "text": formatted_msg,
        "attachments": [
            {
                "color": meta["color"],
                "fields": [
                    {"title": "Severity", "value": level.upper(), "short": True}
                ],
                "footer": "AAA-Bot Engine",
                "ts": config.time.time() if hasattr(config, "time") else None,
            }
        ],
    }

    try:
        response = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if response.status_code != 200:
            logger.warning(
                f"Slack webhook returned error {response.status_code}: {response.text}"
            )
    except Exception as e:
        logger.error("Failed to send Slack alert: %s", e)
