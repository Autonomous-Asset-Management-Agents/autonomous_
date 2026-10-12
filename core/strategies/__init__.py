# core/strategies/__init__.py
# Epic 1.7 / PR-B — Backward-Compatibility Re-Export
# Alle bestehenden Imports von `from core.strategies import X` bleiben kompatibel.

from core.strategies.base import BaseStrategy
from core.strategies.lstm_strategy import LSTMDynamicStrategy

# ---------------------------------------------------------------------------
# Hilfsfunktionen + Konstanten — werden von engine.py importiert.
# #1875: Single Source of Truth ist rl_strategy.py — die frühere Duplikat-
# Definition von _rl_stats_file hier riet den Legacy-Namen rl_stats_{suffix}.pkl
# und verfehlte das models-v1.0-Bundle (rl_agent_v3_dsr_stats.pkl).
# ---------------------------------------------------------------------------
from core.strategies.rl_strategy import (
    RL_MODEL_VERSION,
    RLStrategy,
    _rl_agent_file,
    _rl_stats_file,
)

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
    "_rl_agent_file",
    "_rl_stats_file",
    "RL_AGENT_FILE",
    "RL_STATS_FILE",
]
