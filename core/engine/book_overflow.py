"""#2886 — book_overflow unwind selection (pure, unit-tested).

When the broker-verified book holds MORE positions than the cap (the 14.08.
account-switch incident left 12/10), the trading loop winds the excess down
through the EXISTING rotation-exit machinery — this module only SELECTS the
candidates. Deliberately patient and fail-safe:

* only positions past the min-hold commitment are eligible (no forced sale
  before the holding promise expires — the 2026-07-28 flush lesson),
* weakest LSTM cross-section rank first (same ordering signal the rotation
  lever trades on),
* a position without a rank is NEVER selected (fail-safe HOLD, mirrors the
  rotation lever's ``rank is None`` guard),
* at most ``budget`` names (the caller passes its remaining session budget),
  and never more than the actual excess over the cap.

Protective stop-losses are entirely out of scope here — they run uncapped.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional


def select_overflow_unwind(
    position_scores: Dict[str, object],
    rank_of: Callable[[str], Optional[int]],
    max_positions: int,
    min_hold_days: float,
    budget: int,
) -> List[str]:
    """Return the symbols to exit this cycle, weakest rank first.

    ``position_scores``: the PM's symbol → PositionScore map (needs ``days_held``).
    ``rank_of``: symbol → current cross-section rank (None = unknown ⇒ skip).
    ``budget``: how many exits the caller may still fire (session/cycle caps).
    """
    excess = len(position_scores) - max_positions
    if excess <= 0 or budget <= 0:
        return []

    eligible = []
    for sym, score in position_scores.items():
        days_held = float(getattr(score, "days_held", 0) or 0)
        if days_held < min_hold_days:
            continue  # min-hold is binding — patience over speed
        rank = rank_of(sym)
        if rank is None:
            continue  # fail-safe HOLD — never exit on a missing rank
        eligible.append((rank, sym))

    # Weakest = numerically highest rank first.
    eligible.sort(key=lambda t: t[0], reverse=True)
    n = min(excess, budget, len(eligible))
    return [sym for _rank, sym in eligible[:n]]
