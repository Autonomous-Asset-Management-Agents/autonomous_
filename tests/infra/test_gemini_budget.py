# tests/unit/test_gemini_budget.py
# Hard daily Gemini call limit — free-tier guard.
#
# Gherkin:
#   Given: daily limit is 950 calls
#   When:  950th call is made
#   Then:  allowed=True
#
#   Given: 951st call
#   Then:  allowed=False, no Gemini API call made
#
#   Given: limit hit today
#   When:  day rolls over (midnight UTC)
#   Then:  counter resets → calls allowed again
#
#   Given: multiple threads calling simultaneously
#   Then:  total calls never exceed the limit (thread-safe)

from __future__ import annotations

import threading
from datetime import date, timedelta
from unittest.mock import patch

import pytest


class TestGeminiBudget:
    def test_allows_calls_under_limit(self):
        from core.gemini_budget import GeminiBudget

        budget = GeminiBudget(daily_limit=5)
        for _ in range(5):
            assert budget.check_and_increment() is True

    def test_blocks_calls_over_limit(self):
        from core.gemini_budget import GeminiBudget

        budget = GeminiBudget(daily_limit=3)
        for _ in range(3):
            budget.check_and_increment()
        assert budget.check_and_increment() is False

    def test_resets_on_new_day(self):
        from core.gemini_budget import GeminiBudget

        budget = GeminiBudget(daily_limit=2)
        budget.check_and_increment()
        budget.check_and_increment()
        assert budget.check_and_increment() is False

        # Simulate day rollover
        tomorrow = date.today() + timedelta(days=1)
        with patch("core.gemini_budget.date") as mock_date:
            mock_date.today.return_value = tomorrow
            assert budget.check_and_increment() is True

    def test_remaining_count(self):
        from core.gemini_budget import GeminiBudget

        budget = GeminiBudget(daily_limit=10)
        budget.check_and_increment()
        budget.check_and_increment()
        assert budget.remaining() == 8

    def test_remaining_zero_when_exhausted(self):
        from core.gemini_budget import GeminiBudget

        budget = GeminiBudget(daily_limit=2)
        budget.check_and_increment()
        budget.check_and_increment()
        assert budget.remaining() == 0

    def test_thread_safe_never_exceeds_limit(self):
        from core.gemini_budget import GeminiBudget

        limit = 50
        budget = GeminiBudget(daily_limit=limit)
        allowed_count = []
        lock = threading.Lock()

        def worker():
            result = budget.check_and_increment()
            with lock:
                allowed_count.append(result)

        threads = [threading.Thread(target=worker) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(allowed_count) == limit

    def test_is_exhausted_property(self):
        from core.gemini_budget import GeminiBudget

        budget = GeminiBudget(daily_limit=1)
        assert not budget.is_exhausted
        budget.check_and_increment()
        assert budget.is_exhausted

    def test_default_limit_from_config(self):
        """Default limit reads from GEMINI_DAILY_CALL_LIMIT config."""
        from core.gemini_budget import GeminiBudget

        with patch("core.gemini_budget.GEMINI_DAILY_CALL_LIMIT", 42):
            budget = GeminiBudget()
            assert budget.daily_limit == 42
