# core/strategies/__init__.py
# Epic 1.7 / PR-B — Backward-Compatibility Re-Export
# Alle bestehenden Imports von `from core.strategies import X` bleiben kompatibel.

import os

from core.strategies.base import BaseStrategy
from core.strategies.lstm_strategy import LSTMDynamicStrategy
from core.strategies.rl_strategy import RLStrategy

# ---------------------------------------------------------------------------
# Hilfsfunktionen + Konstanten — werden von engine.py importiert
# ---------------------------------------------------------------------------

RL_MODEL_VERSION = os.getenv("RL_MODEL_VERSION", "rl_agent_v3_dsr")
SEQUENCE_LENGTH = 60

try:
    import config as _config

    _data_dir = getattr(
        _config,
        "DATA_DIR",
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )
except Exception:
    _data_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def _rl_agent_file(version: str) -> str:
    return os.path.join(_data_dir, f"{version}.zip")


def _rl_stats_file(version: str) -> str:
    suffix = version.split("_")[-1]
    return os.path.join(_data_dir, f"rl_stats_{suffix}.pkl")


RL_AGENT_FILE = _rl_agent_file(RL_MODEL_VERSION)
RL_STATS_FILE = _rl_stats_file(RL_MODEL_VERSION)

# Strategie-Klassen-Dictionary für Engine
STRATEGY_CLASSES = {
    "RLAgent": RLStrategy,
    "LSTMDynamic": LSTMDynamicStrategy,
}

__all__ = [
    "BaseStrategy",
    "RLStrategy",
    "LSTMDynamicStrategy",
    "STRATEGY_CLASSES",
    "RL_MODEL_VERSION",
    "SEQUENCE_LENGTH",
    "_rl_agent_file",
    "_rl_stats_file",
    "RL_AGENT_FILE",
    "RL_STATS_FILE",
]
