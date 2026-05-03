# core/strategies.py
# Epic 1.7 / PR-B — Backward-Compatibility Shim
# ⚠️  Diese Datei enthält keinen eigenen Code mehr.
#     Alle Klassen und Funktionen wurden nach core/strategies/ (Package) verschoben.
#     Bestehende Imports (from core.strategies import RLStrategy, BaseStrategy etc.)
#     bleiben durch diesen Shim vollständig kompatibel.

from core.strategies.base import BaseStrategy
from core.strategies.rl_strategy import RLStrategy
from core.strategies.lstm_strategy import LSTMDynamicStrategy

# Hilfsfunktionen und Konstanten (für Kompatibilität mit engine.py und tests)
import os
import config as _config

RL_MODEL_VERSION = os.getenv("RL_MODEL_VERSION", "rl_agent_v3_dsr")
_data_dir = getattr(
    _config, "DATA_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SEQUENCE_LENGTH = 60


def _rl_agent_file(version: str) -> str:
    return os.path.join(_data_dir, f"{version}.zip")


def _rl_stats_file(version: str) -> str:
    suffix = version.split("_")[-1]
    return os.path.join(_data_dir, f"rl_stats_{suffix}.pkl")


RL_AGENT_FILE = _rl_agent_file(RL_MODEL_VERSION)
RL_STATS_FILE = _rl_stats_file(RL_MODEL_VERSION)

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
