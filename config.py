# config.oss.py
from typing import Any

from dotenv import load_dotenv

# SEC-5: Load secrets from OS keychain BEFORE dotenv.
from core.keychain import load_secrets_from_keychain

load_dotenv(".env.oss")
load_secrets_from_keychain()

import os

# 2. Delegate to the single source of truth (settings.py)
import settings  # noqa: E402

if "SPECIALIST_NEWS_V2" not in os.environ:
    settings._config_state.SPECIALIST_NEWS_V2 = True


# Export all settings dynamically via PEP-562
def __getattr__(name: str) -> Any:
    if name == "_config_state":
        return settings._config_state
    try:
        return getattr(settings._config_state, name)
    except AttributeError:
        # Old aliases evaluated first
        if name == "BASE_URL":
            return getattr(settings._config_state, "ALPACA_BASE_URL", None)

        if hasattr(settings, name):
            return getattr(settings, name)
        raise AttributeError(f"module 'config.oss' has no attribute '{name}'")


# #3406 ARC-E5.5 Toggles
CH6_READ_METRICS_DB_3391 = False
CH6_READ_METRICS_DB_3394 = False
CH6_READ_METRICS_DB_3542 = False
CH6_READ_METRICS_DB_3634 = False
CH6_READ_METRICS_DB_3649 = False
CH6_READ_METRICS_DB_3799 = False
