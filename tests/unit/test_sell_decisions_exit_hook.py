"""P1 trading-path — autonomous SELL decisions via the per-cycle exit hook.

``TradingLoopMixin._run_deconcentration_and_rotation_exits`` is a flag-gated
pre-consensus hook (right after the price-stop pass) that PRODUCES SELL decisions
through the verified-sound ``_process_signal_event`` seam via two independent levers:

* **Lever A — ROTATION** (``ROTATION_EXIT_ENABLED``): a held name that dropped out
  of the LSTM top-N past hysteresis / min-hold is FULLY exited (qty 0 → executor
  fetches the whole position).
* **Lever B — TRIM** (``DECONCENTRATION_TRIM_ENABLED``): an overweight name is
  PARTIALLY reduced by a dollar-fraction of its market value.

Both default OFF (byte-identical no-op), only ever REDUCE exposure, dedup against
the price-stop's acted-set, and dispatch ``triggered_by_stop=False`` so the WORM
audit never misclassifies them as a price stop.

Mirrors tests/unit/test_position_stop_loop.py (the sibling exit hook).
"""

import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import allure
import pytest

import core.report.lstm_panel_store as panel_mod
from core.engine.trading_loop import TradingLoopMixin

_NOW = datetime(2026, 7, 27, 15, 0, 0, tzinfo=timezone.utc)
_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
def _score(qty, market_value, current_price, days_held, avg_entry=100.0):
    return SimpleNamespace(
        qty=qty,
        market_value=market_value,
        current_price=current_price,
        days_held=days_held,
        avg_entry=avg_entry,
    )


def _make_engine(scores, recs=None):
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    pm = SimpleNamespace(
        _position_scores=dict(scores),
        refresh_positions=MagicMock(),
        record_trade=MagicMock(),
        get_rebalance_recommendations=MagicMock(return_value=list(recs or [])),
    )
    eng.active_strategy = SimpleNamespace(portfolio_manager=pm)
    eng._process_signal_event = AsyncMock()
    return eng, pm


def _cfg(
    *,
    rotation=False,
    trim=False,
    max_age=3,
    min_hold=5.0,
    hysteresis=3.0,
    min_order=50.0,
    max_exits=0,  # 0 = unlimited per cycle (existing tests stay byte-identical)
    max_session=0,  # 0 = unlimited per session (existing tests stay byte-identical)
    hard_gate=True,
):
    return SimpleNamespace(
        ROTATION_EXIT_ENABLED=rotation,
        DECONCENTRATION_TRIM_ENABLED=trim,
        ROTATION_PANEL_MAX_AGE_DAYS=max_age,
        SMART_EXIT_MIN_HOLD_DAYS=min_hold,
        SMART_EXIT_EXIT_RANK_HYSTERESIS=hysteresis,
        MIN_ORDER_VALUE_USD=min_order,
        ROTATION_MAX_EXITS_PER_CYCLE=max_exits,
        ROTATION_MAX_EXITS_PER_SESSION=max_session,
        ROTATION_MIN_HOLD_HARD_GATE=hard_gate,
    )


def _install_panel(monkeypatch, rank_by_sym, snap_date=None):
    """Fake the panel store so rank / staleness are exact (no 500-symbol fabrication)."""
    if snap_date is None:
        snap_date = _NOW.date()
    fake_store = SimpleNamespace(
        latest_snapshot_date=lambda: snap_date,
        cross_section_at=lambda now: {"_panel_present": 1.0},
    )
    monkeypatch.setattr(panel_mod, "get_store", lambda: fake_store)
    monkeypatch.setattr(
        panel_mod,
        "cross_section_standing",
        lambda xsec, sym: rank_by_sym.get(sym, (None, None, None)),
    )


def _patch_cfg(monkeypatch, cfg):
    import core.engine.trading_loop as tl

    monkeypatch.setattr(tl, "get_config", lambda: cfg)


def _rec(
    symbol,
    *,
    action="REDUCE",
    drift_pct=13.0,
    adjustment_value=-6500.0,
    market_value=23000.0,
):
    return {
        "symbol": symbol,
        "action": action,
        "current_pct": 23.0,
        "target_pct": 10.0,
        "drift_pct": drift_pct,
        "adjustment_value": adjustment_value,
        "market_value": market_value,
        "position_score": 55.0,
        "reasoning": f"{action} {symbol}",
    }


# --------------------------------------------------------------------------- #
# Lever A — ROTATION
# --------------------------------------------------------------------------- #
@allure.feature("VC-3 Trading & Execution")
@allure.story("Autonomous SELL — ROTATION lever")
class TestRotationLever:
    @pytest.mark.anyio
    async def test_rotation_fires_full_exit(self, monkeypatch):
        """Scenario 1: dropped out of top-N + min-hold satisfied → one FULL SELL."""
        eng, pm = _make_engine({"AAPL": _score(10.0, 5000.0, 500.0, days_held=10)})
        _patch_cfg(monkeypatch, _cfg(rotation=True))
        _install_panel(monkeypatch, {"AAPL": (5.0, 400, 503)})

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )

        assert exited == {"AAPL"}
        eng._process_signal_event.assert_awaited_once()
        ev = eng._process_signal_event.call_args[0][0]
        assert ev.symbol == "AAPL"
        assert ev.action == "SELL"
        assert ev.suggested_quantity == 0.0  # full exit → executor fetches whole pos
        assert ev.decision_context.triggered_by_stop is False
        assert ev.decision_context.in_position is True
        assert "rotation" in ev.decision_context.portfolio_reason
        assert "400/503" in ev.decision_context.portfolio_reason
        assert "top-10" in ev.decision_context.portfolio_reason
        pm.record_trade.assert_called_once_with("AAPL", "sell")

    @pytest.mark.anyio
    async def test_rank_collapse_early_exit_before_min_hold(self, monkeypatch):
        """#2711 (P0): Min-hold is a HARD gate by default; rank collapse inside min-hold is blocked.
        With legacy flag hard_gate=False, rank collapse early exit is allowed.
        """
        eng, pm = _make_engine({"AAPL": _score(10.0, 5000.0, 500.0, days_held=1)})
        _patch_cfg(monkeypatch, _cfg(rotation=True, hard_gate=True))
        # rank 400 > 10*3=30 → hard gate blocks exit because days_held(1) < min_hold(5)
        _install_panel(monkeypatch, {"AAPL": (5.0, 400, 503)})

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == set()

        # Legacy behavior via hard_gate=False: rank collapse early exit allowed
        _patch_cfg(monkeypatch, _cfg(rotation=True, hard_gate=False))
        exited_legacy = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited_legacy == {"AAPL"}
        eng._process_signal_event.assert_awaited_once()

    @pytest.mark.anyio
    async def test_min_hold_arm_fires_within_hysteresis_band(self, monkeypatch):
        """OR-arm: rank in the hysteresis band but past min-hold → SELL."""
        eng, pm = _make_engine({"AAPL": _score(10.0, 5000.0, 500.0, days_held=10)})
        _patch_cfg(monkeypatch, _cfg(rotation=True))
        # rank 15: >10 (dropped) but <=30 (not collapsed); days_held 10 >= 5 → min-hold arm
        _install_panel(monkeypatch, {"AAPL": (50.0, 15, 503)})

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == {"AAPL"}

    @pytest.mark.anyio
    async def test_rank_none_is_holdsafe(self, monkeypatch):
        """Scenario 2a: symbol absent from panel (rank None) → HOLD (never a false sell)."""
        eng, pm = _make_engine({"AAPL": _score(10.0, 5000.0, 500.0, days_held=10)})
        _patch_cfg(monkeypatch, _cfg(rotation=True))
        _install_panel(monkeypatch, {})  # AAPL absent → (None, None, None)

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == set()
        eng._process_signal_event.assert_not_awaited()
        pm.record_trade.assert_not_called()

    @pytest.mark.anyio
    async def test_stale_panel_skips_lever(self, monkeypatch):
        """Scenario 2b/9: a panel older than ROTATION_PANEL_MAX_AGE_DAYS skips the lever.

        Uses the REAL store so latest_snapshot_date() returns a genuine ``date`` — the
        (now.date() - date).days path must not raise a date−datetime TypeError.
        """
        store = panel_mod.get_store()
        store.reset()
        old = _NOW - timedelta(days=10)
        store.record_cycle(old, [("AAPL", 0.1)])  # snapshot date is 10 days stale
        try:
            eng, pm = _make_engine({"AAPL": _score(10.0, 5000.0, 500.0, days_held=10)})
            _patch_cfg(monkeypatch, _cfg(rotation=True, max_age=3))

            exited = await eng._run_deconcentration_and_rotation_exits(
                already_acted=set(), now=_NOW
            )
            assert exited == set()
            eng._process_signal_event.assert_not_awaited()
            pm.refresh_positions.assert_not_called()  # stale → zero position work
        finally:
            store.reset()

    @pytest.mark.anyio
    async def test_fresh_panel_at_max_age_boundary_is_not_stale(self, monkeypatch):
        """Boundary: age == max_age is allowed (not stale) — the lever still evaluates."""
        store = panel_mod.get_store()
        store.reset()
        boundary = _NOW - timedelta(days=3)
        store.record_cycle(boundary, [("AAPL", 0.1)])
        try:
            eng, pm = _make_engine({"AAPL": _score(10.0, 5000.0, 500.0, days_held=10)})
            _patch_cfg(monkeypatch, _cfg(rotation=True, max_age=3))
            # AAPL is the only name → rank 1/1, in top-N → HOLD, but the lever RAN
            exited = await eng._run_deconcentration_and_rotation_exits(
                already_acted=set(), now=_NOW
            )
            assert exited == set()
            pm.refresh_positions.assert_called_once()  # lever evaluated (not skipped)
        finally:
            store.reset()

    @pytest.mark.anyio
    async def test_gate_holds(self, monkeypatch):
        """Scenario 3: in-top-N, and (within-hysteresis AND within min-hold) → no SELL."""
        eng, pm = _make_engine(
            {
                "IN": _score(10.0, 5000.0, 500.0, days_held=20),  # rank 5 → in top-N
                "BAND": _score(10.0, 5000.0, 500.0, days_held=1),  # rank 15, days<min
            }
        )
        _patch_cfg(monkeypatch, _cfg(rotation=True))
        _install_panel(
            monkeypatch,
            {"IN": (95.0, 5, 503), "BAND": (50.0, 15, 503)},
        )
        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == set()
        eng._process_signal_event.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Lever B — TRIM
# --------------------------------------------------------------------------- #
@allure.feature("VC-3 Trading & Execution")
@allure.story("Autonomous SELL — TRIM lever")
class TestTrimLever:
    @pytest.mark.anyio
    async def test_trim_fires_partial(self, monkeypatch):
        """Scenario 4: REDUCE rec → partial SELL, qty ≈ held * min(1, |adj|/market_value)."""
        held_qty = 100.0
        eng, pm = _make_engine(
            {"HUM": _score(held_qty, 23000.0, 230.0, days_held=8)},
            recs=[
                _rec(
                    "HUM",
                    drift_pct=13.0,
                    adjustment_value=-6500.0,
                    market_value=23000.0,
                )
            ],
        )
        _patch_cfg(monkeypatch, _cfg(trim=True))

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == {"HUM"}
        eng._process_signal_event.assert_awaited_once()
        ev = eng._process_signal_event.call_args[0][0]
        assert ev.symbol == "HUM"
        assert ev.action == "SELL"
        expected = held_qty * min(1.0, abs(-6500.0) / 23000.0)
        assert ev.suggested_quantity == pytest.approx(expected)
        assert 0.0 < ev.suggested_quantity < held_qty  # partial, never oversell
        assert ev.decision_context.triggered_by_stop is False
        assert "deconcentration_trim" in ev.decision_context.portfolio_reason
        assert "+13.0%" in ev.decision_context.portfolio_reason
        pm.record_trade.assert_called_once_with("HUM", "sell")

    @pytest.mark.anyio
    async def test_trim_market_value_zero_skipped(self, monkeypatch):
        """Scenario 5a: market_value <= 0 → skipped (no ZeroDivision, no price division)."""
        eng, pm = _make_engine(
            {"HUM": _score(100.0, 0.0, 0.0, days_held=8)},
            recs=[_rec("HUM", market_value=0.0)],
        )
        _patch_cfg(monkeypatch, _cfg(trim=True))
        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == set()
        eng._process_signal_event.assert_not_awaited()

    @pytest.mark.anyio
    async def test_trim_clamps_to_held(self, monkeypatch):
        """Scenario 5b: a huge |adjustment| clamps to held_qty — never oversells."""
        held_qty = 100.0
        eng, pm = _make_engine(
            {"HUM": _score(held_qty, 23000.0, 230.0, days_held=8)},
            recs=[_rec("HUM", adjustment_value=-9_999_999.0, market_value=23000.0)],
        )
        _patch_cfg(monkeypatch, _cfg(trim=True))
        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == {"HUM"}
        ev = eng._process_signal_event.call_args[0][0]
        assert ev.suggested_quantity == pytest.approx(held_qty)  # clamped, not more

    @pytest.mark.anyio
    async def test_trim_dust_skipped(self, monkeypatch):
        """Scenario 5c: trim_qty * price below MIN_ORDER_VALUE_USD → dust-skip."""
        # adj -1 on mv 23000, qty 100 → trim_qty ≈ 0.00435 shares; *230 ≈ $1 << $50
        eng, pm = _make_engine(
            {"HUM": _score(100.0, 23000.0, 230.0, days_held=8)},
            recs=[_rec("HUM", adjustment_value=-1.0, market_value=23000.0)],
        )
        _patch_cfg(monkeypatch, _cfg(trim=True, min_order=50.0))
        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == set()
        eng._process_signal_event.assert_not_awaited()

    @pytest.mark.anyio
    async def test_trim_no_recs_no_sell(self, monkeypatch):
        """Scenario 5d: cooldown/threshold suppresses recs at the source → no trim."""
        eng, pm = _make_engine(
            {"HUM": _score(100.0, 23000.0, 230.0, days_held=8)}, recs=[]
        )
        _patch_cfg(monkeypatch, _cfg(trim=True))
        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == set()
        eng._process_signal_event.assert_not_awaited()

    @pytest.mark.anyio
    async def test_increase_rec_ignored(self, monkeypatch):
        """Only REDUCE reduces exposure — an INCREASE rec never emits a SELL."""
        eng, pm = _make_engine(
            {"HUM": _score(100.0, 5000.0, 50.0, days_held=8)},
            recs=[
                _rec(
                    "HUM",
                    action="INCREASE",
                    drift_pct=-13.0,
                    adjustment_value=6500.0,
                    market_value=5000.0,
                )
            ],
        )
        _patch_cfg(monkeypatch, _cfg(trim=True))
        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == set()
        eng._process_signal_event.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Cross-cutting — dedup, flags-off, audit
# --------------------------------------------------------------------------- #
@allure.feature("VC-3 Trading & Execution")
@allure.story("Autonomous SELL — guardrails")
class TestGuardrails:
    @pytest.mark.anyio
    async def test_dedup_with_price_stop(self, monkeypatch):
        """Scenario 6: a symbol stopped this cycle is neither rotated nor trimmed."""
        eng, pm = _make_engine(
            {"AAPL": _score(10.0, 5000.0, 500.0, days_held=10)},
            recs=[_rec("AAPL", market_value=5000.0)],
        )
        _patch_cfg(monkeypatch, _cfg(rotation=True, trim=True))
        _install_panel(monkeypatch, {"AAPL": (5.0, 400, 503)})

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted={"AAPL"}, now=_NOW
        )
        assert exited == set()
        eng._process_signal_event.assert_not_awaited()
        pm.record_trade.assert_not_called()

    @pytest.mark.anyio
    async def test_flags_off_is_noop(self, monkeypatch):
        """Scenario 7: both flags OFF → byte-identical no-op (no panel/recommender/SELL)."""
        eng, pm = _make_engine(
            {"AAPL": _score(10.0, 5000.0, 500.0, days_held=10)},
            recs=[_rec("AAPL")],
        )
        _patch_cfg(monkeypatch, _cfg(rotation=False, trim=False))
        store_spy = MagicMock()
        monkeypatch.setattr(panel_mod, "get_store", store_spy)

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == set()
        eng._process_signal_event.assert_not_awaited()
        store_spy.assert_not_called()
        pm.refresh_positions.assert_not_called()
        pm.get_rebalance_recommendations.assert_not_called()
        pm.record_trade.assert_not_called()

    @pytest.mark.anyio
    async def test_audit_attribution_both_levers(self, monkeypatch):
        """Scenario 8: rotation & trim SELLs set triggered_by_stop=False + a reason.

        Rotation runs first (AAPL fully exits); trim then acts on HUM (still held).
        """
        eng, pm = _make_engine(
            {
                "AAPL": _score(10.0, 5000.0, 500.0, days_held=10),
                "HUM": _score(100.0, 23000.0, 230.0, days_held=8),
            },
            recs=[
                _rec(
                    "HUM",
                    drift_pct=13.0,
                    adjustment_value=-6500.0,
                    market_value=23000.0,
                )
            ],
        )
        _patch_cfg(monkeypatch, _cfg(rotation=True, trim=True))
        # AAPL dropped (rank 400); HUM stays in top-N so it does NOT rotate (only trims)
        _install_panel(monkeypatch, {"AAPL": (5.0, 400, 503), "HUM": (95.0, 5, 503)})

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == {"AAPL", "HUM"}
        assert eng._process_signal_event.await_count == 2
        by_symbol = {
            c.args[0].symbol: c.args[0]
            for c in eng._process_signal_event.await_args_list
        }
        rot = by_symbol["AAPL"]
        trim = by_symbol["HUM"]
        assert rot.decision_context.triggered_by_stop is False
        assert trim.decision_context.triggered_by_stop is False
        assert rot.decision_context.portfolio_reason.startswith("rotation")
        assert trim.decision_context.portfolio_reason.startswith("deconcentration_trim")
        # rotation = full exit, trim = partial
        assert rot.suggested_quantity == 0.0
        assert 0.0 < trim.suggested_quantity < 100.0

    @pytest.mark.anyio
    async def test_no_pm_is_failsafe(self, monkeypatch):
        """No active-strategy PM → empty set, no crash (fail-safe)."""
        eng = TradingLoopMixin.__new__(TradingLoopMixin)
        eng.active_strategy = None
        eng._process_signal_event = AsyncMock()
        _patch_cfg(monkeypatch, _cfg(rotation=True, trim=True))
        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == set()
        eng._process_signal_event.assert_not_awaited()


# --------------------------------------------------------------------------- #
# ROTATION per-cycle cap — bound the whole-book flush (2026-07-28 live incident)
# --------------------------------------------------------------------------- #
@allure.feature("VC-3 Trading & Execution")
@allure.story("Autonomous SELL — ROTATION per-cycle cap")
class TestRotationCap:
    def _five_droppers(self):
        syms = [f"S{i}" for i in range(5)]
        scores = {s: _score(10.0, 5000.0, 500.0, days_held=10) for s in syms}
        panel = {
            s: (5.0, 400, 503) for s in syms
        }  # all dropped out of top-N + collapsed
        return syms, scores, panel

    @pytest.mark.anyio
    async def test_cap_bounds_rotation_exits(self, monkeypatch, caplog):
        """5 names all out of top-N, cap=2 → exactly 2 full exits this cycle (3 deferred)."""
        _syms, scores, panel = self._five_droppers()
        eng, pm = _make_engine(scores)
        _patch_cfg(monkeypatch, _cfg(rotation=True, max_exits=2))
        _install_panel(monkeypatch, panel)
        with caplog.at_level("WARNING"):
            exited = await eng._run_deconcentration_and_rotation_exits(
                already_acted=set(), now=_NOW
            )
        assert len(exited) == 2
        assert eng._process_signal_event.await_count == 2
        assert any("cap" in r.getMessage().lower() for r in caplog.records)

    @pytest.mark.anyio
    async def test_cap_zero_floors_to_one(self, monkeypatch):
        """cap=0 FLOORS to 1 (owner directive 2026-08-03): zeroing the per-cycle cap is NOT
        'unlimited' (that was a footgun that re-opened the single-cycle flush). 5 names all
        eligible → only ONE exits this cycle. Rotation is disabled via ROTATION_EXIT_ENABLED,
        never by zeroing the cap."""
        _syms, scores, panel = self._five_droppers()
        eng, pm = _make_engine(scores)
        _patch_cfg(monkeypatch, _cfg(rotation=True, max_exits=0))
        _install_panel(monkeypatch, panel)
        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert len(exited) == 1
        assert eng._process_signal_event.await_count == 1


# --------------------------------------------------------------------------- #
# ROTATION per-SESSION ceiling — the per-cycle cap alone does NOT stop a
# whole-book flush over a session (rotation exits are exempt from the daily-trades
# cap and the loop runs ~390 cycles/session). The day-scoped ceiling is what bounds
# it. (2026-08-03 owner directive; adversarial-review finding.)
# --------------------------------------------------------------------------- #
@allure.feature("VC-3 Trading & Execution")
@allure.story("Autonomous SELL — ROTATION per-session ceiling")
class TestRotationSessionCap:
    def _five_droppers(self):
        syms = [f"S{i}" for i in range(5)]
        scores = {s: _score(10.0, 5000.0, 500.0, days_held=10) for s in syms}
        panel = {s: (5.0, 400, 503) for s in syms}  # all dropped out + collapsed
        return syms, scores, panel

    @pytest.mark.anyio
    async def test_shipped_caps_bound_whole_book_over_session(
        self, monkeypatch, caplog
    ):
        """SHIPPED CONFIG (per-cycle=1, per-session=3): 5 held names ALL fall out of the
        top-N and every cycle is eligible — yet across 5 cycles in ONE day only 3 exit and
        2 names SURVIVE. The per-cycle cap alone would drain all 5 over 5 cycles; the session
        ceiling is what prevents the whole-book flush."""
        _syms, scores, panel = self._five_droppers()
        eng, pm = _make_engine(scores)
        _patch_cfg(monkeypatch, _cfg(rotation=True, max_exits=1, max_session=3))
        _install_panel(monkeypatch, panel)
        per_cycle = []
        with caplog.at_level("WARNING"):
            for _ in range(5):
                ex = await eng._run_deconcentration_and_rotation_exits(
                    already_acted=set(), now=_NOW
                )
                per_cycle.append(len(ex))
                for s in ex:  # a sold name leaves the book (realistic drain)
                    pm._position_scores.pop(s, None)
        assert per_cycle == [1, 1, 1, 0, 0]  # 3 total, then session-capped
        assert eng._process_signal_event.await_count == 3
        assert len(pm._position_scores) == 2  # 2 of 5 survive — NO whole-book flush
        assert any("per-session cap" in r.getMessage() for r in caplog.records)

    @pytest.mark.anyio
    async def test_session_ceiling_resets_next_trading_day(self, monkeypatch):
        """The day-scoped counter resets on date rollover: once the day's 3 are spent the
        4th cycle is capped, but the NEXT day rotation resumes."""
        _syms, scores, panel = self._five_droppers()
        eng, pm = _make_engine(scores)
        _patch_cfg(monkeypatch, _cfg(rotation=True, max_exits=1, max_session=3))
        _install_panel(monkeypatch, panel)
        for _ in range(3):  # spend the day's ceiling
            ex = await eng._run_deconcentration_and_rotation_exits(
                already_acted=set(), now=_NOW
            )
            for s in ex:
                pm._position_scores.pop(s, None)
        capped = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert len(capped) == 0  # same day → session-capped
        next_day = _NOW + timedelta(days=1)
        resumed = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=next_day
        )
        assert len(resumed) == 1  # counter reset → one of the 2 remaining exits


# --------------------------------------------------------------------------- #
# Additive rec field — market_value must be emitted by the recommender
# --------------------------------------------------------------------------- #
@allure.feature("VC-3 Trading & Execution")
@allure.story("Rebalance recommender — additive market_value")
class TestRecommenderMarketValue:
    def test_rebalance_rec_exposes_market_value(self):
        """The REDUCE rec must additively carry ``market_value`` (Lever B trims a $-fraction)."""
        from core.portfolio_manager import PortfolioManager

        # Overweight single position: 100 sh @ $230 = $23k of a $50k book (target 10%).
        pos = {
            "symbol": "HUM",
            "qty": 100.0,
            "avg_entry_price": 200.0,
            "current_price": 230.0,
            "market_value": 23000.0,
            "unrealized_pl": 3000.0,
            "unrealized_plpc": 0.15,
        }
        client = SimpleNamespace(get_all_positions=lambda: [pos])
        pm = PortfolioManager(client=client, total_capital=50000.0, max_positions=10)

        recs = pm.get_rebalance_recommendations()

        reduce_recs = [r for r in recs if r["action"] == "REDUCE"]
        assert reduce_recs, "expected a REDUCE rec for the overweight name"
        rec = reduce_recs[0]
        assert "market_value" in rec
        assert rec["market_value"] == pytest.approx(23000.0)


# --------------------------------------------------------------------------- #
# BORA config parity — both editions, both new flags + the panel-age tunable
# --------------------------------------------------------------------------- #
def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_sell_decisions", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@allure.feature("VC-3 Trading & Execution")
@allure.story("BORA config parity — sell-decisions flags")
class TestConfigParity:
    @pytest.mark.parametrize(
        "name",
        [
            "ROTATION_EXIT_ENABLED",
            "DECONCENTRATION_TRIM_ENABLED",
            "ROTATION_PANEL_MAX_AGE_DAYS",
            "ROTATION_MAX_EXITS_PER_CYCLE",
            "ROTATION_MAX_EXITS_PER_SESSION",
        ],
    )
    def test_edition_parity(self, name):
        import config as ent

        oss = _load_oss_config()
        ent_val = getattr(ent.get_config(), name)
        oss_val = getattr(oss, name)
        assert (
            oss_val == ent_val
        ), f"{name} must match across editions: oss={oss_val!r} vs enterprise={ent_val!r}"

    def test_flag_defaults(self):
        """Owner directive 2026-08-03: the ROTATION lever ships ON by default (staged,
        single-exit-per-cycle rotation — see test_rotation_max_exits_default). The
        de-concentration TRIM lever stays OFF (an independent rollback lever; TRIM was the
        'flatten every name to 10%' half of the 2026-07-28 whole-book-flush incident).
        """
        import config as ent

        oss = _load_oss_config()
        if os.getenv("ROTATION_EXIT_ENABLED") is None:
            assert oss.ROTATION_EXIT_ENABLED is True
            assert ent.get_config().ROTATION_EXIT_ENABLED is True
        if os.getenv("DECONCENTRATION_TRIM_ENABLED") is None:
            assert oss.DECONCENTRATION_TRIM_ENABLED is False
            assert ent.get_config().DECONCENTRATION_TRIM_ENABLED is False

    def test_panel_max_age_default(self):
        import config as ent

        oss = _load_oss_config()
        if os.getenv("ROTATION_PANEL_MAX_AGE_DAYS") is None:
            assert oss.ROTATION_PANEL_MAX_AGE_DAYS == 3
            assert ent.get_config().ROTATION_PANEL_MAX_AGE_DAYS == 3

    def test_rotation_max_exits_default(self):
        """The per-cycle rotation cap ships at 1 (owner directive 2026-08-03): at most ONE
        full rotation exit per cycle — the most conservative staged drain, so a ranking shift
        can never approach a whole-book flush (it bleeds out one name per cycle)."""
        import config as ent

        oss = _load_oss_config()
        if os.getenv("ROTATION_MAX_EXITS_PER_CYCLE") is None:
            assert oss.ROTATION_MAX_EXITS_PER_CYCLE == 1
            assert ent.get_config().ROTATION_MAX_EXITS_PER_CYCLE == 1

    def test_rotation_max_exits_per_session_default(self):
        """The per-SESSION rotation ceiling ships at 3 (owner directive 2026-08-03): the
        per-cycle cap alone does not bound a whole-book flush over a session (rotation exits
        are exempt from the daily-trades cap); this day-scoped ceiling is what bounds it.
        """
        import config as ent

        oss = _load_oss_config()
        if os.getenv("ROTATION_MAX_EXITS_PER_SESSION") is None:
            assert oss.ROTATION_MAX_EXITS_PER_SESSION == 2
            assert ent.get_config().ROTATION_MAX_EXITS_PER_SESSION == 2


# --------------------------------------------------------------------------- #
# #3180 Consensus Retention Gate — the OPINION exit levers (rotation / trim /
# book-overflow) must SUPPRESS a sell when the live round-table blend-consensus
# still rates the held name highly. Default OFF (threshold 0.0) ⇒ byte-identical.
# Fail-open on a missing live consensus. RISK exits are gated elsewhere (tier).
# --------------------------------------------------------------------------- #
@allure.feature("VC-3 Trading & Execution")
@allure.story("Autonomous SELL — #3180 consensus retention gate")
class TestConsensusRetentionGate:
    @pytest.mark.anyio
    async def test_rotation_suppressed_when_consensus_high(self, monkeypatch):
        """A dropped-rank name that the live round table STILL rates highly is retained."""
        eng, pm = _make_engine({"AAPL": _score(10.0, 5000.0, 500.0, days_held=10)})
        pm.get_live_consensus = lambda s: 0.80  # high live conviction → retain
        _patch_cfg(monkeypatch, _cfg(rotation=True))
        _install_panel(monkeypatch, {"AAPL": (5.0, 400, 503)})  # dropped → would rotate
        monkeypatch.setattr("core.consensus_retention._threshold", lambda: 0.50)

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == set()  # SUPPRESSED by the gate
        eng._process_signal_event.assert_not_awaited()
        pm.record_trade.assert_not_called()

    @pytest.mark.anyio
    async def test_rotation_fires_when_consensus_low(self, monkeypatch):
        """The round table AGREES the name is weak (consensus <= threshold) → rotation fires."""
        eng, pm = _make_engine({"AAPL": _score(10.0, 5000.0, 500.0, days_held=10)})
        pm.get_live_consensus = lambda s: 0.30
        _patch_cfg(monkeypatch, _cfg(rotation=True))
        _install_panel(monkeypatch, {"AAPL": (5.0, 400, 503)})
        monkeypatch.setattr("core.consensus_retention._threshold", lambda: 0.50)

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == {"AAPL"}
        eng._process_signal_event.assert_awaited_once()

    @pytest.mark.anyio
    async def test_gate_off_default_is_byte_identical(self, monkeypatch):
        """Threshold 0.0 (shipped default) ⇒ even max consensus does NOT suppress — the
        rotation lever behaves bit-for-bit as pre-#3180."""
        eng, pm = _make_engine({"AAPL": _score(10.0, 5000.0, 500.0, days_held=10)})
        pm.get_live_consensus = lambda s: 0.99  # would retain if the gate were on
        _patch_cfg(monkeypatch, _cfg(rotation=True))
        _install_panel(monkeypatch, {"AAPL": (5.0, 400, 503)})
        monkeypatch.setattr("core.consensus_retention._threshold", lambda: 0.0)

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == {"AAPL"}  # gate OFF → fires exactly as before

    @pytest.mark.anyio
    async def test_missing_consensus_fails_open(self, monkeypatch):
        """No live consensus for the name (None) ⇒ fail-open: the exit still fires."""
        eng, pm = _make_engine({"AAPL": _score(10.0, 5000.0, 500.0, days_held=10)})
        pm.get_live_consensus = lambda s: None
        _patch_cfg(monkeypatch, _cfg(rotation=True))
        _install_panel(monkeypatch, {"AAPL": (5.0, 400, 503)})
        monkeypatch.setattr("core.consensus_retention._threshold", lambda: 0.50)

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == {"AAPL"}  # fail-open

    @pytest.mark.anyio
    async def test_trim_suppressed_when_consensus_high(self, monkeypatch):
        """The TRIM lever is gated too (owner directive: rotation AND trim must be
        consensus-aware like take-profit)."""
        held_qty = 100.0
        eng, pm = _make_engine(
            {"HUM": _score(held_qty, 23000.0, 230.0, days_held=8)},
            recs=[_rec("HUM", adjustment_value=-6500.0, market_value=23000.0)],
        )
        pm.get_live_consensus = lambda s: 0.80
        _patch_cfg(monkeypatch, _cfg(trim=True))
        monkeypatch.setattr("core.consensus_retention._threshold", lambda: 0.50)

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == set()  # trim SUPPRESSED
        eng._process_signal_event.assert_not_awaited()
        pm.record_trade.assert_not_called()

    @pytest.mark.anyio
    async def test_trim_fires_when_consensus_low(self, monkeypatch):
        """Consensus agrees (low) ⇒ trim proceeds unchanged."""
        held_qty = 100.0
        eng, pm = _make_engine(
            {"HUM": _score(held_qty, 23000.0, 230.0, days_held=8)},
            recs=[_rec("HUM", adjustment_value=-6500.0, market_value=23000.0)],
        )
        pm.get_live_consensus = lambda s: 0.30
        _patch_cfg(monkeypatch, _cfg(trim=True))
        monkeypatch.setattr("core.consensus_retention._threshold", lambda: 0.50)

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == {"HUM"}
        eng._process_signal_event.assert_awaited_once()

    @pytest.mark.anyio
    async def test_book_overflow_unwind_suppressed_when_consensus_high(
        self, monkeypatch
    ):
        """The #2886 book-overflow unwind (an OPINION rank-based exit) is gated too."""
        # 3 held names, cap 2 → one name is overflow. All rank-collapsed so rotation would
        # also fire; keep min-hold so only the overflow path is exercised via the cap.
        syms = ["AAA", "BBB", "CCC"]
        scores = {s: _score(10.0, 5000.0, 500.0, days_held=10) for s in syms}
        eng, pm = _make_engine(scores)
        pm.max_positions = 2
        pm.get_live_consensus = lambda s: 0.90  # all retained
        # In top-N (rank 1) so the rotation lever holds; overflow unwind keys on book size.
        _install_panel(monkeypatch, {s: (95.0, 1, 3) for s in syms})
        cfg = _cfg(rotation=True, max_exits=0, max_session=0)
        cfg.BOOK_CAP_ENFORCEMENT_ENABLED = True  # enable the #2886 overflow lever
        _patch_cfg(monkeypatch, cfg)
        monkeypatch.setattr("core.consensus_retention._threshold", lambda: 0.50)

        exited = await eng._run_deconcentration_and_rotation_exits(
            already_acted=set(), now=_NOW
        )
        assert exited == set()  # overflow unwind suppressed for the retained name
        eng._process_signal_event.assert_not_awaited()


@allure.feature("VC-3 Trading & Execution")
@allure.story("POLICY-01 — no silent exception swallow (CLAUDE.md §5.6)")
class TestRecordTradeFailureIsLogged:
    @pytest.mark.anyio
    async def test_rotation_record_trade_failure_is_logged_not_swallowed(
        self, monkeypatch, caplog
    ):
        """Antigravity P1 / POLICY-01: a ``record_trade`` failure must be LOGGED
        (CLAUDE.md §5.6), never silently swallowed. The SELL is already dispatched, so
        the exit still stands — only the anti-churn cooldown bookkeeping may be missed.
        """
        import logging as _logging

        eng, pm = _make_engine(
            {"AAPL": _score(qty=10, market_value=2000, current_price=200, days_held=10)}
        )
        pm.record_trade.side_effect = RuntimeError("cooldown store down")
        _patch_cfg(monkeypatch, _cfg(rotation=True))
        _install_panel(monkeypatch, {"AAPL": (5.0, 400, 503)})  # dropped from top-N

        with caplog.at_level(_logging.WARNING):
            exited = await eng._run_deconcentration_and_rotation_exits(
                already_acted=set(), now=_NOW
            )

        assert exited == {"AAPL"}  # exit stands despite the bookkeeping failure
        eng._process_signal_event.assert_awaited_once()  # the SELL was dispatched
        assert any(
            "failed to record trade" in r.getMessage().lower() for r in caplog.records
        ), "record_trade failure must be logged at WARNING, not swallowed"
