# tests/unit/test_rotation_min_hold_hard_gate.py
"""#2711 (Epic #1957, P0) — min-hold is a HARD gate; rank-collapse cannot override it.

Order forensics 04.-07.08 (HAS bought 16:01, rotation-sold 16:02 at a loss; HPE/PCAR
overnight roundtrips): the old `min_hold_ok OR rank_collapsed` let normal rank volatility
exit 60-second-old positions, voiding ANY min-hold value (#2678's 20d included). Option A
(Archon-approved): rank hysteresis evaluates only AFTER min-hold expires. Risk exits
(stop-loss/TP/trailing) stay un-gated. ROTATION_MIN_HOLD_HARD_GATE=False restores legacy.
"""

from core.smart_exit import should_sell_smart

DAY = 24.0


def _kwargs(**over):
    base = {
        "symbol": "HAS",
        "entry_price": 100.0,
        "current_price": 100.0,
        "high_water_mark": 100.0,
        "hours_held": 1.0,  # 60 minutes old — deep inside the 5d min-hold window
        "in_top_n": False,
        "lstm_rank": 100,  # far beyond top_n_size * hysteresis (10*3=30) => "collapapsed"
        "top_n_size": 10,
        "min_hold_days": 5.0,
        "exit_rank_hysteresis": 3.0,
    }
    base.update(over)
    return base


def test_hard_gate_blocks_rank_collapse_inside_min_hold():
    # THE bug: rank collapse alone exited a 1-hour-old position. Hard gate => HOLD.
    dec = should_sell_smart(**_kwargs())
    assert dec.action != "SELL"


def test_after_min_hold_rank_drop_still_sells():
    dec = should_sell_smart(**_kwargs(hours_held=6 * DAY, lstm_rank=15))
    assert dec.action == "SELL"


def test_legacy_behavior_restorable_via_flag_off():
    dec = should_sell_smart(**_kwargs(min_hold_hard_gate=False))
    assert dec.action == "SELL"  # old rank-collapse early exit


def test_stop_loss_stays_ungated_inside_min_hold():
    # Risk exits must NEVER wait behind the gate.
    dec = should_sell_smart(**_kwargs(current_price=80.0))  # -20% => stop-loss
    assert dec.action == "SELL"
    assert "stop" in dec.reason.lower() or "loss" in dec.reason.lower()
