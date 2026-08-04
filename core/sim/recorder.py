"""Sim execution-outcome recorder (#2548) — captures EVERY order outcome so the runner's
``sim_result.json`` shows blocked orders + reasons, not only the fills that reached the broker.

The engine funnels all outcomes through ``order_executor._rec_outcome(symbol, code, reason)``
("executed", "blocked:risk", "blocked:daily_limit", "blocked:market_closed", "error", …). A
single guarded hook there feeds this recorder. Inactive (no-op) outside a sim run, so it is
byte-identical in production — the runner calls ``reset()`` to arm it.
"""

from typing import Dict, List


class SimRecorder:
    def __init__(self):
        self.active = False
        self.events: List[Dict] = []

    def reset(self) -> None:
        """Arm the recorder for a fresh run (clears prior events)."""
        self.active = True
        self.events = []

    def record(self, symbol: str, code: str, reason: str = "") -> None:
        if not self.active:
            return
        self.events.append(
            {"symbol": symbol, "outcome": str(code), "reason": str(reason or "")}
        )


_recorder = SimRecorder()


def get_recorder() -> SimRecorder:
    return _recorder
