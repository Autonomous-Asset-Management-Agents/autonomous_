# tests/unit/test_lstm_collapse_guard.py
# ADR-ML-COLLAPSE-01 — a COLLAPSED model must not allocate capital.
#
# Same family as #1878, and the same doctrine: trading on a demonstrably broken input
# is not a strategy choice, it is a defect. #1878 fixed the per-symbol case (feature
# failure -> abstain, never rank). This fixes the CROSS-SECTION case.
#
# The defect (verified, not theorised):
#   `self._lstm_rank_cache = sorted(cache, key=lambda x: x[1], reverse=True)`
#   Python's sort is STABLE. If a model collapses and emits one identical prediction
#   for every name, sorted() reorders NOTHING — it returns the input order. So the
#   "Top-N" becomes literally the first N symbols in the universe list, ranked by
#   nothing at all. Then:
#   `preds_shifted = np.clip(preds - preds.min() + 0.1, 0.1, None)` -> all 0.1
#   -> equal weights. The engine allocates real capital, equally, to an arbitrary
#   handful of names, and logs "Ranked top N: [...]" as though it had ranked them.
#
# Gherkin:
#   Given every prediction in a cycle is identical (a collapsed model)
#   When update_lstm_rankings runs
#   Then no allocation weights are produced (no capital is placed this cycle)
#   And the collapse is raised to the ml_watchdog as a model-health event
#   And it is logged at ERROR
#   And a healthy, dispersed cross-section is completely unaffected

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest


def _make_strategy():
    """Minimal LSTMDynamicStrategy double (mirrors test_lstm_abstention_ranking)."""
    import numpy as np

    from core.strategies.lstm_strategy import LSTMDynamicStrategy

    strategy = LSTMDynamicStrategy.__new__(LSTMDynamicStrategy)
    strategy.torch_model = MagicMock()
    strategy.scaler_x = MagicMock()
    strategy.torch = MagicMock()
    strategy.np = np  # update_lstm_rankings does real array math
    strategy.pd = MagicMock()
    strategy.scaler_y = None
    strategy.features_list = ["close", "volume", "rsi_14"]
    strategy._initialized = True
    strategy.device = "cpu"
    strategy.client = MagicMock()
    strategy.data_provider = MagicMock()
    strategy.client.__class__ = MagicMock
    strategy._lstm_rank_cache = []
    strategy._allocation_weights = {}
    return strategy


def _now():
    return datetime(2024, 6, 1, tzinfo=timezone.utc)


def _snapshots(symbols):
    snaps = {}
    for s in symbols:
        snap = MagicMock()
        snap.latest_trade.p = 100.0
        snaps[s] = snap
    return snaps


async def _run(strategy, preds: dict):
    async def fake_prediction(symbol, current_time, market_data):
        return preds[symbol], None

    strategy._get_torch_prediction = AsyncMock(side_effect=fake_prediction)
    symbols = list(preds)
    await strategy.update_lstm_rankings(
        symbols, _snapshots(symbols), {"vix": 20.0}, _now()
    )


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestCollapsedModelDoesNotAllocate:
    @pytest.mark.anyio
    async def test_identical_predictions_produce_no_allocation(self):
        """THE acceptance test. Without the guard the engine equal-weights the first
        N symbols of the list and calls it a ranking."""
        strategy = _make_strategy()
        # one identical prediction for every name — the collapse itself
        collapsed = dict.fromkeys(["ZTS", "AAPL", "MSFT", "NVDA", "XOM"], 0.5)

        await _run(strategy, collapsed)

        assert strategy._allocation_weights == {}, (
            "a collapsed model must place NO capital — the ordering it produces is "
            "the input order, not a ranking"
        )

    @pytest.mark.anyio
    async def test_collapse_is_reported_without_escalating(self):
        """⚠ This test used to assert the OPPOSITE — `rec.called` — and shipped the
        defect it was meant to guard. Reaching the ml_watchdog sounded like diligence;
        it is the crash-escalation channel, and it ends in kill_switch.trip().

        The intent was right: a collapse must not be a silent skip, because silence is
        exactly what made the original defect expensive (the log said "Ranked top N"
        while nothing was ranked). The expectation was wrong: loud != escalated. It is
        reported at ERROR and the bot keeps running — see TestCollapseMustNotKillTheBot.
        """
        strategy = _make_strategy()

        with patch("core.ml_watchdog.ml_watchdog.record_error") as rec:
            await _run(strategy, dict.fromkeys(["A", "B", "C"], 0.5))

        assert not rec.called

    @pytest.mark.anyio
    async def test_collapse_is_logged_at_error(self, caplog):
        """CODING_POLICY §5.6: never DEBUG for a degraded path. This one is worse
        than a fallback — it is a broken model."""
        strategy = _make_strategy()

        with caplog.at_level(logging.ERROR):
            await _run(strategy, dict.fromkeys(["A", "B", "C"], 0.5))

        assert any(r.levelno >= logging.ERROR for r in caplog.records)

    @pytest.mark.anyio
    async def test_near_collapse_is_caught_too(self):
        """Bit-identical is the obvious case; numerically-constant is the realistic
        one. Predictions separated by ~1e-12 carry no ordering either — the sort is
        then driven by float noise."""
        strategy = _make_strategy()
        preds = {f"S{i}": 0.5 + i * 1e-12 for i in range(6)}

        await _run(strategy, preds)

        assert strategy._allocation_weights == {}


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestHealthyModelIsUntouched:
    """The guard's whole risk is a FALSE POSITIVE: refusing to trade a working model
    costs real return. These pin that it cannot happen for any realistic dispersion."""

    @pytest.mark.anyio
    async def test_dispersed_predictions_allocate_normally(self):
        strategy = _make_strategy()
        preds = {"AAA": 0.9, "BBB": 0.4, "CCC": -0.2}

        await _run(strategy, preds)

        assert set(strategy._allocation_weights) <= {"AAA", "BBB", "CCC"}
        assert strategy._allocation_weights, "a healthy model must still allocate"
        assert [s for s, _ in strategy._lstm_rank_cache][0] == "AAA"

    @pytest.mark.anyio
    async def test_tight_but_real_dispersion_still_allocates(self):
        """Sanity on the threshold: the OOS prediction sigma is 1.79 (see
        _LSTM_VOTE_SCALE). Even a cross-section 1000x tighter than that is orders of
        magnitude above the collapse floor and must trade."""
        strategy = _make_strategy()
        preds = {f"S{i}": i * 0.002 for i in range(5)}

        await _run(strategy, preds)

        assert strategy._allocation_weights, "tight-but-real dispersion is not collapse"

    @pytest.mark.anyio
    async def test_single_symbol_cycle_is_not_treated_as_collapse(self):
        """n=1 has no dispersion by definition, but it is a THIN cycle (the others
        abstained per #1878), not a broken model. Pre-existing behaviour stands."""
        strategy = _make_strategy()

        await _run(strategy, {"AAA": 0.7})

        assert strategy._allocation_weights == {"AAA": 1.0}


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestCollapseMustNotKillTheBot:
    """⚠ MY OWN BUG, one merge later (#2148 -> main).

    The guard called `ml_watchdog.record_error(...)`. I never read what that does:
    ml_watchdog.py:99-108 — after `kill_threshold_sec` of CONTINUOUS errors it calls
    `kill_switch.trip(...)`. And the guard fires on EVERY cycle a collapse persists,
    so escalation is not a risk, it is a certainty.

    Intended: "do not allocate this cycle, and say so loudly."
    Built:    "after X seconds, halt the entire bot until a human resets it."

    That is a category error on top of a severity error. `record_error` means the
    INFERENCE CRASHED. A collapsed model did not crash — it answered, and the answer
    carries no information. Routing "useless output" through the crash channel borrows
    an escalation nobody chose.

    And the kill switch is GLOBAL: it stops exits, stop-losses and every other healthy
    path too. Refusing to allocate on a dead model must never cost you the ability to
    SELL what you already hold.
    """

    @pytest.mark.anyio
    async def test_a_collapse_does_not_trip_the_kill_switch(self):
        strategy = _make_strategy()

        with patch("core.ml_watchdog.ml_watchdog.record_error") as rec:
            await _run(strategy, dict.fromkeys(["A", "B", "C"], 0.5))

        assert not rec.called, (
            "the collapse guard escalated through the ML-crash channel — that path "
            "ends in kill_switch.trip() and halts the whole bot, including exits. "
            "A model with nothing to say must not cost you the ability to sell."
        )

    @pytest.mark.anyio
    async def test_a_collapse_is_still_loud(self, caplog):
        """Not escalating must not become not reporting. Silence is what made the
        original defect expensive."""
        strategy = _make_strategy()

        with caplog.at_level(logging.ERROR):
            await _run(strategy, dict.fromkeys(["A", "B", "C"], 0.5))

        assert any(r.levelno >= logging.ERROR for r in caplog.records)

    @pytest.mark.anyio
    async def test_a_collapse_still_places_no_capital(self):
        """The property that actually protects money is unchanged."""
        strategy = _make_strategy()
        await _run(strategy, dict.fromkeys(["ZTS", "AAPL", "MSFT"], 0.5))
        assert strategy._allocation_weights == {}
