# core/gemini_budget.py
# Hard daily Gemini API call limit — free-tier guard.
#
# Gemini 2.5 Flash free tier: 1M tokens/day.
# 500 S&P symbols × 2 cycles/day × ~950 tokens/call ≈ 950K tokens/day.
# Default limit: 950 calls/day → ~900K tokens → safely under 1M free limit.
#
# On limit hit: check_and_increment() returns False → caller returns neutral
# signal instead of calling Gemini. No crash, no unexpected cost.
# Resets automatically at midnight UTC.
#
# Override via GEMINI_DAILY_CALL_LIMIT env var (e.g. =1200 if on paid tier).

from __future__ import annotations

import logging
import threading
from datetime import date

from config import GEMINI_DAILY_CALL_LIMIT

logger = logging.getLogger(__name__)


class GeminiBudget:
    """Thread-safe daily Gemini API call budget.

    Usage:
        budget = GeminiBudget()          # uses GEMINI_DAILY_CALL_LIMIT from config
        if budget.check_and_increment():
            result = call_gemini(...)
        else:
            result = neutral_fallback()
    """

    def __init__(self, daily_limit: int | None = None) -> None:
        self.daily_limit: int = (
            daily_limit if daily_limit is not None else GEMINI_DAILY_CALL_LIMIT
        )
        self._lock = threading.Lock()
        self._count: int = 0
        self._date: date = date.today()

    def check_and_increment(self) -> bool:
        """Return True and increment counter if budget allows. False if exhausted."""
        with self._lock:
            self._reset_if_new_day()
            if self._count >= self.daily_limit:
                logger.warning(
                    "[GeminiBudget] Daily limit reached (%d/%d). "
                    "Returning neutral signal — no Gemini call made. "
                    "Resets at midnight UTC. Override: GEMINI_DAILY_CALL_LIMIT env var.",
                    self._count,
                    self.daily_limit,
                )
                return False
            self._count += 1
            return True

    def remaining(self) -> int:
        """Calls remaining today."""
        with self._lock:
            self._reset_if_new_day()
            return max(0, self.daily_limit - self._count)

    @property
    def is_exhausted(self) -> bool:
        with self._lock:
            self._reset_if_new_day()
            return self._count >= self.daily_limit

    def _reset_if_new_day(self) -> None:
        """Must be called under self._lock."""
        today = date.today()
        if today != self._date:
            logger.info(
                "[GeminiBudget] New day — resetting counter (was %d/%d).",
                self._count,
                self.daily_limit,
            )
            self._count = 0
            self._date = today


# Module-level singleton — shared across all specialist agents in the process.
_budget: GeminiBudget | None = None
_budget_lock = threading.Lock()


def get_budget() -> GeminiBudget:
    """Return the process-wide GeminiBudget singleton."""
    global _budget
    if _budget is None:
        with _budget_lock:
            if _budget is None:
                _budget = GeminiBudget()
                logger.info(
                    "[GeminiBudget] Initialized — daily limit: %d calls (~%dK tokens).",
                    _budget.daily_limit,
                    _budget.daily_limit * 950 // 1000,
                )
    return _budget
