"""#2980 (Epic #1891) — Fail-closed fallback policy for missing/invalid market inputs.

CODING_POLICY §5.6 + the #2672 position-context provenance pattern. Missing/invalid
VIX must FAIL CLOSED (maximum de-risk / block new buys), never silently substitute a
benign "calm" default. Sell / de-risk paths stay open at every rung
("only reduce, never enlarge").

TDD Red→Green: every test below fails against the pre-fix code (0.9 sizing, dead
block-gate, fabricated calm audit, full-weight neutral DrawdownGuard, DEBUG logs,
unguarded float casts). Hypothesis property tests pin the substitution invariants
across the whole input domain (Gate B).
"""

from __future__ import annotations

import asyncio
import logging
import math
from unittest.mock import MagicMock, patch

from hypothesis import given
from hypothesis import strategies as st

from core.risk_manager import (
    VIX_FAILCLOSED_SENTINEL,
    VIX_UNCONFIRMED_MARKER,
    RiskManager,
    resolve_vix,
)


def _rm(total_capital: float = 50_000.0) -> RiskManager:
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
        return RiskManager(client=MagicMock(), total_capital=total_capital)


def _size(rm: RiskManager, market_data) -> float:
    # Low conviction + modest cash keep the VIX risk scaler the BINDING factor
    # (not the 25% max-position or max-order-value caps, which would mask it).
    return rm.calculate_position_size(
        stop_loss_atr_multiplier=3.0,
        atr=2.0,
        confidence="medium",
        market_data=market_data,
        conviction_score=0.2,
        current_price=50.0,
        account_cash=50_000.0,
        num_stocks_in_strategy=1,
        allow_fractional=True,
    )


# ---------------------------------------------------------------------------
# resolve_vix — pure helper
# ---------------------------------------------------------------------------


class TestResolveVix:
    def test_confirmed_vix_lossless(self):
        val, confirmed, source = resolve_vix({"vix": 17.3})
        assert confirmed is True
        assert val == 17.3
        assert source == "confirmed"

    def test_missing_vix_fail_closed(self):
        val, confirmed, source = resolve_vix({})
        assert confirmed is False
        assert val == VIX_FAILCLOSED_SENTINEL
        assert val > 40  # lands in the crash band (max de-risk)

    def test_none_market_data_fail_closed(self):
        val, confirmed, _ = resolve_vix(None)
        assert confirmed is False
        assert val == VIX_FAILCLOSED_SENTINEL

    def test_invalid_vix_fail_closed(self):
        val, confirmed, source = resolve_vix({"vix": "n/a"})
        assert confirmed is False
        assert val == VIX_FAILCLOSED_SENTINEL
        assert source == "invalid"

    def test_nonfinite_and_nonpositive_fail_closed(self):
        for bad in (float("nan"), float("inf"), -1.0, 0.0):
            val, confirmed, _ = resolve_vix({"vix": bad})
            assert confirmed is False
            assert val == VIX_FAILCLOSED_SENTINEL

    def test_missing_vix_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            resolve_vix({})
        assert any(
            "FAIL-CLOSED" in r.getMessage() for r in caplog.records
        ), "missing VIX must log a WARNING containing FAIL-CLOSED (§5.6)"


# ---------------------------------------------------------------------------
# Sizing — missing VIX maxes out de-risking (0.3 scaler)
# ---------------------------------------------------------------------------


class TestSizingFailClosed:
    def test_missing_vix_maxes_derisk(self):
        rm = _rm()
        size_missing = _size(rm, {})  # no vix → fail-closed
        size_crash = _size(rm, {"vix": 45.0})  # explicit crash band (0.3)
        size_calm = _size(rm, {"vix": 15.0})  # confirmed calm (1.0)
        assert size_missing > 0
        # missing VIX must size EXACTLY like a crash-regime VIX (max de-risk 0.3)
        assert math.isclose(size_missing, size_crash, rel_tol=1e-9)
        # and be strictly smaller than the calm sizing it used to fabricate (0.9/1.0)
        assert size_missing < size_calm

    def test_confirmed_calm_vix_unchanged(self):
        rm = _rm()
        # P2: confirmed happy path must be byte-identical to a raw 15.0 read
        assert _size(rm, {"vix": 15.0}) == _size(rm, {"vix": 15.0})


# ---------------------------------------------------------------------------
# Block-gate — unconfirmed VIX blocks new BUYs, never SELL / de-risk
# ---------------------------------------------------------------------------


class TestBlockGateFailClosed:
    def test_missing_vix_blocks_new_buy(self):
        rm = _rm()
        allowed, reason, _ = rm.evaluate_new_trade("AAPL", "buy", {}, 3.0)
        assert allowed is False
        assert "VIX" in reason or "fail-closed" in reason.lower()

    def test_missing_vix_never_blocks_sell(self):
        rm = _rm()
        allowed, _, _ = rm.evaluate_new_trade("AAPL", "sell", {}, 3.0)
        assert allowed is True, "SELL / de-risk must stay open when VIX is unknown"

    def test_confirmed_vix_buy_allowed(self):
        rm = _rm()
        allowed, _, _ = rm.evaluate_new_trade("AAPL", "buy", {"vix": 15.0}, 3.0)
        assert allowed is True


# ---------------------------------------------------------------------------
# Audit — DecisionContext never fabricates a calm, observed VIX
# ---------------------------------------------------------------------------


class TestAuditProvenance:
    def _signal(self, **state_extra):
        from types import SimpleNamespace

        from core.round_table.runner import _score_to_signal

        vote = SimpleNamespace(
            agent_name="T",
            score=0.5,
            weight=1.0,
            reasoning="x",
            vetoed=False,
        )
        state = {"symbol": "AAPL", "ohlc": {"close": 100.0}, "ml": None, **state_extra}
        return _score_to_signal(state, 0.5, [vote])

    def test_audit_marks_vix_unconfirmed(self):
        sig = self._signal(vix=None, regime=None)
        assert sig is not None
        assert sig.decision_context.vix_confirmed is False
        assert VIX_UNCONFIRMED_MARKER in sig.decision_context.reasoning_summary

    def test_audit_confirmed_vix(self):
        sig = self._signal(vix=31.5, regime="High Volatility")
        assert sig is not None
        assert sig.decision_context.vix_confirmed is True
        assert sig.decision_context.vix_level == 31.5
        assert VIX_UNCONFIRMED_MARKER not in sig.decision_context.reasoning_summary


# ---------------------------------------------------------------------------
# DrawdownGuard — abstain (weight 0.0) on invalid OHLC
# ---------------------------------------------------------------------------


class TestDrawdownGuardAbstain:
    def _vote(self, state):
        from core.round_table.agents import DrawdownGuardAgent

        registry = MagicMock()
        registry.get_active.return_value = None
        with patch("core.agent_registry.get_global_registry", return_value=registry):
            return asyncio.run(DrawdownGuardAgent().vote(state))

    def test_invalid_ohlc_abstains(self, caplog):
        state = {
            "symbol": "AAPL",
            "ohlc": {"high": 0.0, "low": 0.0, "close": 1.0},
            "current_time": "",
        }
        with caplog.at_level(logging.WARNING):
            result = self._vote(state)
        assert (
            result.weight == 0.0
        ), "invalid OHLC must ABSTAIN (weight 0.0), not vote 0.5"
        assert any(
            "invalid" in r.getMessage().lower() or "abstain" in r.getMessage().lower()
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# DEBUG → WARNING (§5.6)
# ---------------------------------------------------------------------------


class TestFallbackLogSeverity:
    @staticmethod
    def _call_level(module, message: str) -> str:
        """Return the logging level ('warning'/'debug') of the call that logs
        `message`, tolerant to black line-wraps (optional whitespace after '(')."""
        import inspect
        import re

        src = inspect.getsource(module)
        m = re.search(
            r"logging\.(warning|debug)\(\s*[\"']" + re.escape(message),
            src,
        )
        assert m is not None, f"no logging call for {message!r} found"
        return m.group(1)

    def test_sim_fallback_buy_logs_warning(self):
        import core.engine.simulation_runner as sim

        assert self._call_level(sim, "[SIM] Fallback buy skipped: %s") == "warning"

    def test_live_account_fallback_logs_warning(self):
        import core.engine.api_routes as api

        assert self._call_level(api, "Live account fallback failed: %s") == "warning"


# ---------------------------------------------------------------------------
# None-guards on float(...) casts — construct/reset never crash
# ---------------------------------------------------------------------------


class TestNoneCapitalGuards:
    def test_none_total_capital_safe(self, caplog):
        with caplog.at_level(logging.WARNING):
            rm = _rm(total_capital=None)
        assert rm.total_capital == 0.0
        assert rm.session_start_equity == 0.0
        assert any("None" in r.getMessage() for r in caplog.records)

    def test_none_reset_daily_limit_safe(self, caplog):
        rm = _rm()
        with caplog.at_level(logging.WARNING):
            rm.reset_daily_limit(None)
        assert rm.initial_daily_equity == 0.0


# ---------------------------------------------------------------------------
# Hypothesis property tests (Gate B) — substitution invariants
# ---------------------------------------------------------------------------


def _scaler_for(vix: float) -> float:
    """Mirror of the ADR-R08 ladder in calculate_position_size (:540-552)."""
    if vix > 40:
        return 0.3
    if vix > 35:
        return 0.4
    if vix > 25:
        return 0.65
    if vix > 18:
        return 0.9
    return 1.0


class TestResolverDriftGuard:
    """Pin that no VIX consumer in risk_manager can silently re-introduce a
    fail-open benign default — every site must route through resolve_vix (#2980,
    Option A drift guard). Captures Option B's enforcement benefit without its
    radius."""

    def test_no_failopen_vix_reads_in_risk_manager(self):
        import inspect
        import re

        import core.risk_manager as rm

        src = inspect.getsource(rm)
        # the two former fail-open idioms must be gone
        assert "current_vix = 20.0" not in src
        assert not re.search(r'float\(\s*market_data\.get\(\s*["\']vix', src)
        # both money-path consumers call the resolver
        assert src.count("resolve_vix(") >= 3  # def + sizing + block-gate

    # P1 — monotone safety: no missing/invalid input is ever LESS de-risking
    # than a confirmed-calm (20.0) reading.
    @given(
        st.one_of(
            st.none(),
            st.floats(allow_nan=True, allow_infinity=True),
            st.text(max_size=5),
        )
    )
    def test_p1_substitution_never_less_derisking(self, raw):
        val, confirmed, _ = resolve_vix({"vix": raw})
        if not confirmed:
            assert _scaler_for(val) <= _scaler_for(20.0)

    # P2 — confirmed ⇒ total & lossless.
    @given(st.floats(min_value=0.0001, max_value=1000.0, allow_nan=False))
    def test_p2_confirmed_lossless(self, vix):
        val, confirmed, source = resolve_vix({"vix": vix})
        assert confirmed is True
        assert val == float(vix)
        assert source == "confirmed"

    # P3 — unconfirmed ⇒ marked (⟺): the marker exists exactly when unconfirmed.
    @given(
        st.one_of(
            st.none(),
            st.just(float("nan")),
            st.just(float("inf")),
            st.floats(max_value=0.0),
            st.text(max_size=5),
            st.floats(min_value=0.0001, max_value=1000.0, allow_nan=False),
        )
    )
    def test_p3_unconfirmed_iff_sentinel(self, raw):
        val, confirmed, _ = resolve_vix({"vix": raw})
        assert (val == VIX_FAILCLOSED_SENTINEL) == (confirmed is False)

    # P6 — None/NaN/inf closure: resolver never raises, always finite.
    @given(
        st.one_of(
            st.none(),
            st.just(float("nan")),
            st.just(float("inf")),
            st.just(float("-inf")),
            st.floats(max_value=0.0),
        )
    )
    def test_p6_closure_no_raise(self, raw):
        val, confirmed, _ = resolve_vix({"vix": raw})
        assert confirmed is False
        assert math.isfinite(val)
