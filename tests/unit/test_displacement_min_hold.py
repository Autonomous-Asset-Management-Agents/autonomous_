# tests/unit/test_displacement_min_hold.py
# #2696 — bind the DISPLACEMENT path to the configured holding policy.
#
# THE PROBLEM, in numbers. Measured on a 10-day sim replay (2026-07-22..08-04, 10-name book):
# of 24 SELLs, 13 came from displacement, 8 from intelligent_exit trailing, 3 from rotation.
# The operator had armed SMART_EXIT_MIN_HOLD_DAYS=20 — which only path 1 (rotation) reads.
# `grep -cE "SMART_EXIT|ROTATION_" core/portfolio_manager.py` → 0. So the configured holding
# period governed 3 of 24 exits (12%), which is why the churn survived the parameter change.
#
# WHY A VETO AND NOT "just read the config". portfolio_manager.py:532 only appends
# "held less than 1 day - give it time" to `args_against`, and the decision is:
#
#     if score_diff > 15:
#         should_swap = True        # ← every args_against entry is ignored here
#
# In that branch the hold argument is decorative. Swapping the literal `1` for the config value
# would have changed nothing. The period has to be checked BEFORE the debate — the way rotation
# does it (trading_loop.py:861).
#
# WHY NO RISK EXIT IS AFFECTED. `debate_position_swap` is reached only from Case 2 of
# `should_open_new_position`, so it ALWAYS requires an incoming candidate: it is opportunity-driven
# by construction and can only ever *swap*, never simply sell. Loss exits live on separate paths
# that this change does not touch — `_run_position_stop_checks` (trading_loop.py:734, runs BEFORE
# the board with triggered_by_stop=True), intelligent_exit trailing, and the DrawdownGuard.
#
# SECOND RULE, same theme (portfolio_manager.py:513):
#
#     if weakest.days_held > 5 and weakest.unrealized_pnl_pct < 2:
#         args_for.append("... held N days with minimal gain")
#
# "sitting >5 days for <2%" counts as a reason TO SELL. A growth name necessarily passes through
# that phase, so the rule punishes it precisely there — the direct opponent of "identify growth
# names and hold them mid-term". Owner decision: couple it to the holding period rather than
# delete it (the core idea — do not park capital in a dead position forever — stays valid; five
# days is simply not a basis for judging).

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _pm(min_hold_days=20.0, respects=True):
    """A PortfolioManager with only the state the swap debate reads.

    Built via __new__ on purpose: __init__ pulls config, Redis and a broker client, none of which
    this decision boundary touches.
    """
    import threading

    from core.portfolio_manager import PortfolioManager

    pm = PortfolioManager.__new__(PortfolioManager)
    pm.max_positions = 10
    pm._trade_history = {}
    pm._last_rebalance = {}
    pm._max_trades_per_day = 8
    pm._debate_history = []
    pm._history_lock = threading.RLock()
    pm._min_order_interval_sec = 0  # not under test here
    pm._rebalance_cooldown_hours = 0.5
    pm._min_hold_hours = 1.0
    pm._consecutive_sell_signals = {}
    pm._consecutive_sell_threshold = 5
    pm._sim_min_hold_days = min_hold_days  # test seam, see _config below
    return pm


@pytest.fixture(autouse=True)
def _config(monkeypatch, request):
    """Arm the two flags the veto reads, at the seam the code uses (config.get_config()).

    Parametrised per test via the `cfg` marker so each case states its own configuration.
    """
    import config

    marker = request.node.get_closest_marker("cfg")
    values = dict(marker.kwargs) if marker else {}
    cfg = SimpleNamespace(
        SMART_EXIT_MIN_HOLD_DAYS=values.get("min_hold", 20.0),
        DISPLACEMENT_RESPECTS_MIN_HOLD=values.get("respects", True),
    )
    monkeypatch.setattr(config, "get_config", lambda: cfg, raising=False)
    return cfg


def _weakest(days_held=3, pnl_pct=0.0, score=40.0, symbol="PG", age_known=True):
    from core.portfolio_manager import PositionScore

    # #2935: `age_known` macht explizit, was diese Tests immer gemeint haben — eine Position,
    # deren Haltedauer BEKANNT ist ("held 3 days"). Vorher war das implizit an `days_held > 0`
    # gekoppelt, wodurch Tag 0 (heute gekauft) ungeschuetzt blieb. Der Standard hier ist deshalb
    # True; der Unbekannt-Fall wird unten ausdruecklich mit age_known=False geprueft.
    return PositionScore(
        age_known=age_known,
        symbol=symbol,
        qty=30.0,
        avg_entry=100.0,
        current_price=100.0 * (1 + pnl_pct / 100.0),
        market_value=3000.0,
        unrealized_pnl=pnl_pct * 30.0,
        unrealized_pnl_pct=pnl_pct,
        total_score=score,
        days_held=days_held,
    )


def _opportunity(score=80.0, symbol="NVDA"):
    from core.portfolio_manager import OpportunityScore

    return OpportunityScore(symbol=symbol, current_price=500.0, total_score=score)


# ---------------------------------------------------------------------------
# The veto — the whole point of the change
# ---------------------------------------------------------------------------


@pytest.mark.cfg(min_hold=20.0)
def test_min_hold_vetoes_the_swap_even_for_a_strong_candidate():
    """score_diff 40 lands in the `> 15` branch, which ignored every counter-argument.
    That branch is exactly why a soft argument was never enough."""
    pm = _pm()
    should_swap, reason = pm.debate_position_swap(
        _opportunity(score=80.0), _weakest(days_held=3, score=40.0)
    )
    assert should_swap is False, f"swapped inside the holding period: {reason}"


@pytest.mark.cfg(min_hold=20.0)
def test_veto_reason_names_the_policy_and_the_remaining_days():
    """An operator reading the log must see WHY, and for how much longer — otherwise the block
    is indistinguishable from a bug."""
    pm = _pm()
    _, reason = pm.debate_position_swap(
        _opportunity(score=80.0), _weakest(days_held=3, score=40.0)
    )
    low = reason.lower()
    assert "hold" in low or "haltefrist" in low
    assert "3" in reason and "20" in reason


@pytest.mark.cfg(min_hold=20.0)
def test_swap_is_allowed_once_the_holding_period_has_passed():
    pm = _pm()
    should_swap, reason = pm.debate_position_swap(
        _opportunity(score=70.0), _weakest(days_held=25, score=40.0)
    )
    assert should_swap is True, reason


@pytest.mark.cfg(min_hold=20.0)
def test_boundary_day_is_inclusive_so_the_period_is_honoured_in_full():
    """days_held == min_hold means the period is exactly served — the position is free."""
    pm = _pm()
    assert (
        pm.debate_position_swap(
            _opportunity(score=80.0), _weakest(days_held=20, score=40.0)
        )[0]
        is True
    )
    assert (
        pm.debate_position_swap(
            _opportunity(score=80.0), _weakest(days_held=19, score=40.0)
        )[0]
        is False
    )


@pytest.mark.cfg(min_hold=20.0)
def test_veto_holds_across_all_three_swap_branches():
    """The decision has three independent doors (>15, >10 with arg-majority, >8 with a loser).
    A veto that only closes one of them is not a veto."""
    pm = _pm()
    cases = [
        (
            "strong upgrade",
            _opportunity(score=80.0),
            _weakest(days_held=2, pnl_pct=0.0),
        ),
        (
            "moderate upgrade",
            _opportunity(score=52.0),
            _weakest(days_held=2, pnl_pct=0.0),
        ),
        ("cut loser", _opportunity(score=50.0), _weakest(days_held=2, pnl_pct=-6.0)),
    ]
    for label, opp, weak in cases:
        should_swap, reason = pm.debate_position_swap(opp, weak)
        assert should_swap is False, f"{label} broke through the veto: {reason}"


# ---------------------------------------------------------------------------
# Fail-open: an unknown holding age must not imprison a position
# ---------------------------------------------------------------------------


@pytest.mark.cfg(min_hold=20.0)
def test_unknown_holding_age_does_not_block():
    """Ohne Handelshistorie ist das Alter UNBEKANNT (z. B. eine aus dem Broker
    wiederhergestellte Position ohne rekonstruierte Einstiegszeit). "Unbekannt" als "brandneu" zu
    behandeln wuerde sie ohne jeden Beleg fuer die ganze Frist sperren — das Veto muss fail-OPEN
    bleiben, passend zur bestehenden Haltung `no trade history -> can sell` in can_sell_position.

    #2935: Unbekanntheit wird jetzt EXPLIZIT ueber age_known=False ausgedrueckt statt implizit
    ueber days_held == 0. Der frueher damit vermischte Fall "heute gekauft, Alter bekannt" ist
    seither geschuetzt (siehe tests/unit/test_displacement_carousel.py)."""
    pm = _pm()
    weak = _weakest(days_held=0, score=40.0, age_known=False)
    should_swap, reason = pm.debate_position_swap(_opportunity(score=80.0), weak)
    assert should_swap is True, reason


@pytest.mark.cfg(min_hold=0.0)
def test_min_hold_zero_disables_the_veto():
    pm = _pm()
    assert (
        pm.debate_position_swap(_opportunity(score=80.0), _weakest(days_held=0))[0]
        is True
    )


@pytest.mark.cfg(min_hold=20.0)
def test_no_positions_still_short_circuits_to_yes():
    """Unchanged behaviour: nothing to displace, nothing to protect."""
    pm = _pm()
    assert pm.debate_position_swap(_opportunity(), None)[0] is True


# ---------------------------------------------------------------------------
# Backwards compatibility — the flag is the rollback
# ---------------------------------------------------------------------------


@pytest.mark.cfg(min_hold=20.0, respects=False)
def test_flag_off_restores_todays_behaviour():
    """Flag off → the 1-day argument and the 5-day heuristic behave exactly as before, so the
    rollback is a config change rather than a revert."""
    pm = _pm()
    should_swap, _ = pm.debate_position_swap(
        _opportunity(score=80.0), _weakest(days_held=3, score=40.0)
    )
    assert should_swap is True, "flag off must not veto"


@pytest.mark.cfg(min_hold=20.0, respects=False)
def test_flag_off_keeps_the_hardcoded_one_day_argument():
    pm = _pm()
    pm.debate_position_swap(_opportunity(score=52.0), _weakest(days_held=0, score=40.0))
    args = pm._debate_history[-1]["arguments_against_swap"]
    assert any("less than 1 day" in a for a in args)


# ---------------------------------------------------------------------------
# The 5-day heuristic, coupled to the same period
# ---------------------------------------------------------------------------


@pytest.mark.cfg(min_hold=20.0)
def test_minimal_gain_is_not_an_argument_before_the_period_ends():
    """7 days at +1% used to read as "held with minimal gain -> sell". With min_hold=20 that
    judgement is premature by construction."""
    pm = _pm()
    pm.debate_position_swap(
        _opportunity(score=52.0), _weakest(days_held=7, pnl_pct=1.0, score=40.0)
    )
    args = pm._debate_history[-1]["arguments_for_swap"]
    assert not any("minimal gain" in a for a in args), args


@pytest.mark.cfg(min_hold=20.0)
def test_minimal_gain_becomes_an_argument_after_the_period():
    """The rule keeps its legitimate core — capital must not sit in a dead position forever."""
    pm = _pm()
    pm.debate_position_swap(
        _opportunity(score=52.0), _weakest(days_held=25, pnl_pct=1.0, score=40.0)
    )
    args = pm._debate_history[-1]["arguments_for_swap"]
    assert any("minimal gain" in a for a in args), args


@pytest.mark.cfg(min_hold=20.0)
def test_a_profitable_long_hold_is_not_argued_against():
    """>=2% gain was never the target of the rule and must stay untouched."""
    pm = _pm()
    pm.debate_position_swap(
        _opportunity(score=52.0), _weakest(days_held=25, pnl_pct=5.0, score=40.0)
    )
    args = pm._debate_history[-1]["arguments_for_swap"]
    assert not any("minimal gain" in a for a in args), args


@pytest.mark.cfg(min_hold=5.0, respects=False)
def test_flag_off_keeps_the_five_day_threshold():
    """Rollback fidelity: with the flag off the literal 5 stays in force, independent of config."""
    pm = _pm()
    pm.debate_position_swap(
        _opportunity(score=52.0), _weakest(days_held=7, pnl_pct=1.0, score=40.0)
    )
    args = pm._debate_history[-1]["arguments_for_swap"]
    assert any("minimal gain" in a for a in args), args


# ---------------------------------------------------------------------------
# Robustness + wiring
# ---------------------------------------------------------------------------


@pytest.mark.cfg(min_hold=20.0)
def test_unreadable_config_fails_open(monkeypatch):
    """A config that cannot be read must never freeze the book. Fail-OPEN here is the safe
    direction: the veto only ever *blocks* trading, and an unreadable config is not evidence
    that a position should be protected."""
    import config

    def _explode():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(config, "get_config", _explode, raising=False)
    pm = _pm()
    should_swap, _ = pm.debate_position_swap(
        _opportunity(score=80.0), _weakest(days_held=1)
    )
    assert should_swap is True


@pytest.mark.cfg(min_hold=20.0)
def test_veto_is_recorded_in_the_debate_history():
    """The debate log is the audit trail for why a swap did not happen."""
    pm = _pm()
    pm.debate_position_swap(_opportunity(score=80.0), _weakest(days_held=3))
    entry = pm._debate_history[-1]
    assert entry["decision"] == "HOLD"
    assert "hold" in entry["reasoning"].lower()


def test_hold_period_is_resolved_at_call_time():
    """Module constants freeze at import, which is how operator-set env values have gone inert
    before in this codebase. The period must be read per decision."""
    import inspect

    from core.portfolio_manager import PortfolioManager

    src = inspect.getsource(PortfolioManager.debate_position_swap)
    assert "get_config()" in src or "_displacement_min_hold_days" in src


def test_config_exposes_the_flag_in_both_editions():
    """BORA parity — a flag missing from the OSS config diverges the editions."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for name in ("config.py", "config.oss.py"):
        assert "DISPLACEMENT_RESPECTS_MIN_HOLD" in (root / name).read_text(
            encoding="utf-8"
        ), name


def test_flag_defaults_to_true():
    """The owner armed the holding period; the fix is on by default, and the flag is the rollback."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "config.py").read_text(
        encoding="utf-8"
    )
    # The assignment spans two physical lines (black), so match across the newline.
    m = re.search(
        r"DISPLACEMENT_RESPECTS_MIN_HOLD.*?getenv\(\s*[\"']DISPLACEMENT_RESPECTS_MIN_HOLD[\"']\s*,\s*[\"'](\w+)[\"']",
        src,
        re.S,
    )
    assert m, "flag not found in config.py"
    assert m.group(1).lower() == "true", f"default is {m.group(1)!r}, expected True"
