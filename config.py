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
# #3741 (Owner-Entscheid 28.09.2026): eingeschaltet. Die Leseschicht verglich bisher
# nichts — `build_metrics_report` hatte keinen Aufrufer, also fiel jeder Abruf auf die
# Neuberechnung zurueck und die Lueckenkarte (#3405) blieb leer. Mit dem Schreib-
# Durchgriff aus #3741 entsteht der Bericht beim ersten Lesen; weicht er von der
# Neuberechnung ab, meldet der Parallelbetrieb das als Warnung (api_routes.py).
CH6_READ_METRICS_DB_3391 = True
CH6_READ_METRICS_DB_3394 = True
CH6_READ_METRICS_DB_3542 = True
CH6_READ_METRICS_DB_3634 = True
CH6_READ_METRICS_DB_3649 = True
CH6_READ_METRICS_DB_3799 = True
