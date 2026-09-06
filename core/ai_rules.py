# ai_rules.py
# --- MODIFIED: Added validation for 'proactive_signal' rules.

import json
import logging
import os
from typing import Dict, List

from core.redis_client import RedisClient


class AILearnedRules:
    """
    Singleton class to manage loading and saving AI-generated rules
    from a JSON file.
    """

    _instance = None
    rules: List[Dict]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.rules = []
            cls._instance.load_rules()
        return cls._instance

    def load_rules(self):
        """Loads rules from Redis."""
        try:
            r = RedisClient.get_sync_redis()
            data_str = r.get("ai_learned_rules")
            if data_str:
                self.rules = json.loads(data_str)
                # TODO(PR-D): Complex f-string, review manually:                 logging.info(f"Loaded {len(self.rules)} AI rules from Redis.")
                logging.info(f"Loaded {len(self.rules)} AI rules from Redis.")
            else:
                logging.info("No AI learned rules found in Redis.")
                self.rules = []
        except Exception as e:
            logging.error("Failed to load AI rules from Redis: %s", e)
            self.rules = []

    def save_rules(self, new_rules: List[Dict]):
        """Saves a new list of rules to the JSON file, performing validation."""
        valid_rules = []
        for r in new_rules:
            trigger = r.get("trigger")
            action = r.get("action")
            reason = r.get("reason")
            value = r.get("value")

            is_valid = False

            if not all([isinstance(trigger, dict), action, reason]):
                logging.warning(
                    f"Rule validation failed: Missing trigger/action/reason in {r}"
                )
                continue

            if action in ["block_trade"]:
                is_valid = True
            elif action in ["reduce_size", "tighten_sl", "increase_size", "widen_sl"]:
                try:
                    float(value)
                    is_valid = True
                except (ValueError, TypeError):
                    logging.warning(
                        f"Rule validation failed for '{action}': Invalid/missing 'value' in {r}"
                    )
            # --- NEW: Proactive Signal Validation ---
            elif action == "proactive_signal":
                required_proactive_fields = [
                    "headline_keywords",
                    "sentiment_gt",
                    "signal_ticker",
                ]
                if all(field in trigger for field in required_proactive_fields):
                    try:
                        float(trigger["sentiment_gt"])
                        is_valid = True
                    except (ValueError, TypeError):
                        logging.warning(
                            f"Rule validation failed for 'proactive_signal': Invalid 'sentiment_gt' in {r}"
                        )
                else:
                    logging.warning(
                        f"Rule validation failed for 'proactive_signal': Missing required fields {required_proactive_fields} in {r}"
                    )
            # --- END NEW ---
            else:
                logging.warning(
                    f"Rule validation failed: Unknown action '{action}' in {r}"
                )

            if is_valid:
                valid_rules.append(r)

        self.rules = valid_rules
        try:
            r = RedisClient.get_sync_redis()
            r.set("ai_learned_rules", json.dumps(self.rules))
            # TODO(PR-D): Complex f-string, review manually:             logging.info(f"Saved {len(self.rules)} AI rules to Redis.")
            logging.info(f"Saved {len(self.rules)} AI rules to Redis.")
        except Exception as e:
            logging.error("Failed to save AI rules to Redis: %s", e)

    def get_rules(self) -> List[Dict]:
        """Returns the currently loaded rules."""
        return self.rules
