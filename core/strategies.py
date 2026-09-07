# core/strategies.py
# Epic 1.7 / PR-B — Backward-Compatibility Shim
# ⚠️  Diese Datei enthält keinen eigenen Code mehr.
#     Alle Klassen und Funktionen wurden nach core/strategies/ (Package) verschoben.
#     Bestehende Imports (from core.strategies import RLStrategy, BaseStrategy etc.)
#     bleiben durch diesen Shim vollständig kompatibel.

# Hilfsfunktionen und Konstanten (für Kompatibilität mit engine.py und tests)
# #1875: Single Source of Truth ist rl_strategy.py — keine Duplikat-Definitionen
# der Pfad-Resolver mehr (die alte Kopie hier riet den Legacy-Stats-Namen).
from core.strategies.base import BaseStrategy
from core.strategies.lstm_strategy import LSTMDynamicStrategy
from core.strategies.rl_strategy import (
    RL_MODEL_VERSION,
    RLStrategy,
    _rl_agent_file,
    _rl_stats_file,
)

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
    "_rl_agent_file",
    "_rl_stats_file",
    "RL_AGENT_FILE",
    "RL_STATS_FILE",
]
