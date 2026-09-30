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
        self.daily_equity: List[Dict] = []

    def reset(self) -> None:
        """Arm the recorder for a fresh run (clears prior events)."""
        self.active = True
        self.events = []
        self.daily_equity = []

    def record(self, symbol: str, code: str, reason: str = "") -> None:
        if not self.active:
            return
        self.events.append(
            {"symbol": symbol, "outcome": str(code), "reason": str(reason or "")}
        )

    def record_daily_equity(self, day, equity, cash, n_positions) -> None:
        """One closing point per replayed trading day — the multi-day equity curve (#2693).

        Re-recording the same date OVERWRITES it rather than appending: the closing day is
        recorded twice by design (once by the rollover, once by the final mark in ``run_sim``),
        and a duplicated date would corrupt every day-over-day return derived from the curve.
        """
        if not self.active:
            return
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)
        point = {
            "date": key,
            "equity": float(equity),
            "cash": float(cash),
            "n_positions": int(n_positions),
        }
        for i, existing in enumerate(self.daily_equity):
            if existing["date"] == key:
                self.daily_equity[i] = point
                return
        self.daily_equity.append(point)


_recorder = SimRecorder()


def get_recorder() -> SimRecorder:
    return _recorder
