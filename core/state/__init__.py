"""#3388 (ARC-E2.5) — Betriebszustand der Engine hinter einem Port.

Der Kern kennt ``StatePort``; welche Ablage dahinterliegt, entscheidet der
Zusammenbau. Beide Adapter werden gegen dieselbe Abnahme gefahren
(``tests/unit/test_state_port_contract.py``).

Zusammengebaut wird in ``zusammenbau.state_port()`` (#3449). Aufrufer im Kern gibt es
noch keine — der Plan trennt Umleitung und Aktivierung in zwei PRs, damit ein Rueckfall
nicht beide zurueckdreht (implementation_plan.md §6, Rollback).
"""

from .port import StateLock, StateLockNotAcquired, StatePort
from .redis_adapter import RedisStateAdapter
from .sqlite_adapter import SqliteStateAdapter
from .zusammenbau import StatePortUnavailable, state_port

__all__ = [
    "StateLock",
    "StateLockNotAcquired",
    "StatePort",
    "StatePortUnavailable",
    "state_port",
    "RedisStateAdapter",
    "SqliteStateAdapter",
]
