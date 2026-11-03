"""#3632 - the holding rules that survived the Smart Exit retirement.

Rank-drop rebalance (#1952 min-hold + hysteresis, #2711 hard gate), hold-age fail-open
and the honest position HWM (RTR-5/B2). Behaviour pinned byte-identically to the tests
that used to live in test_smart_exit_min_hold.py / test_rotation_min_hold_hard_gate.py /
test_smart_exit_trailing_policy.py. Price exits are NOT decided here any more - see
test_exit_authority_3632.py for the intelligent exit.
"""

from datetime import datetime, timedelta

import pytest

from core.hold_policy import derive_position_hwm, rank_drop_exit, resolve_hold_hours

pytestmark = pytest.mark.vc2  # Stufen-Marker (#3396)

DAY = 24.0


def _rank(**over):
    base = {
        "in_top_n": False,
        "lstm_rank": 12,
        "hours_held": 1.0,
        "top_n_size": 10,
        "min_hold_days": 5.0,
        "exit_rank_hysteresis": 3.0,
        "min_hold_hard_gate": True,
    }
    base.update(over)
    return rank_drop_exit(**base)


# ------------------------------------------------------------------ (a) in-window HOLD
def test_a_dropped_within_min_hold_no_collapse_holds():
    assert not _rank(hours_held=1 * DAY, lstm_rank=12).sell


def test_a2_dropped_just_under_min_hold_holds():
    assert not _rank(hours_held=4.99 * DAY, lstm_rank=15).sell


# ------------------------------------------------------------- (b) SELL after min-hold
def test_b_dropped_after_min_hold_sells():
    d = _rank(hours_held=5 * DAY, lstm_rank=12)
    assert d.sell and "rebalanc" in d.reason.lower()


def test_b2_min_hold_boundary_exact_sells():
    assert _rank(hours_held=5 * DAY, lstm_rank=11).sell


# --------------------------------------------------- (c) rank collapse vs. hard gate
def test_c_rank_collapse_within_min_hold_holds_hard_gate():
    """#2711: inside min-hold a rank collapse alone may NOT exit (HAS forensics)."""
    assert not _rank(hours_held=1 * DAY, lstm_rank=35).sell  # 35 > 10*3


def test_c_legacy_early_collapse_sell_via_flag_off():
    d = _rank(hours_held=1 * DAY, lstm_rank=35, min_hold_hard_gate=False)
    assert d.sell and "rank-collapse" in d.reason


def test_c2_rank_at_threshold_does_not_early_exit():
    assert not _rank(hours_held=1 * DAY, lstm_rank=30, min_hold_hard_gate=False).sell


# ------------------------------------------------------------ (e) rule does not apply
def test_e_in_top_n_holds():
    assert not _rank(in_top_n=True, lstm_rank=3, hours_held=10 * DAY).sell


def test_e2_none_rank_never_sells():
    """Abstained / unranked symbol (#1878): no forced rank exit, ever."""
    assert not _rank(lstm_rank=None, hours_held=10 * DAY).sell


def test_e3_rollback_min_hold_zero_restores_immediate_sell():
    d = _rank(hours_held=0.0, lstm_rank=12, min_hold_days=0.0)
    assert d.sell and "rebalanc" in d.reason.lower()


def test_f_deterministic():
    a = _rank(hours_held=1 * DAY, lstm_rank=35)
    b = _rank(hours_held=1 * DAY, lstm_rank=35)
    assert (a.sell, a.reason) == (b.sell, b.reason)


def test_f2_shipped_defaults_are_active():
    """Without explicit parameters the runtime settings gate a fresh dropped name."""
    import config

    assert float(config.get_config().SMART_EXIT_MIN_HOLD_DAYS) >= 1.0
    assert not rank_drop_exit(in_top_n=False, lstm_rank=12, hours_held=0.0).sell


# --------------------------------------------------------- (g) hold age fail-open
_NOW = datetime(2026, 7, 11, 12, 0, 0)


def test_g_unknown_entry_fails_open_past_the_window():
    h = resolve_hold_hours(None, _NOW, min_hold_days=5.0)
    assert h > 5.0 * DAY
    assert _rank(hours_held=h, lstm_rank=12).sell


def test_g2_known_entry_returns_real_age_and_holds_in_window():
    h = resolve_hold_hours(_NOW - timedelta(days=2), _NOW, min_hold_days=5.0)
    assert abs(h - 2 * DAY) < 1e-6
    assert not _rank(hours_held=h, lstm_rank=12).sell


def test_g3_none_entry_is_none_safe_with_runtime_min_hold():
    import config

    h = resolve_hold_hours(None, _NOW)
    assert h > float(config.get_config().SMART_EXIT_MIN_HOLD_DAYS) * DAY


# ------------------------------------------------------------- (h) honest position HWM
@pytest.mark.parametrize(
    "args, expected",
    [
        (
            {
                "entry_price": 100.0,
                "bar_highs": [101.0, 140.0, 120.0],
                "current_price": 105.0,
            },
            140.0,
        ),
        (
            {
                "entry_price": 100.0,
                "session_hwm": 120.0,
                "bar_highs": [110.0, 130.5],
                "current_price": 125.0,
            },
            130.5,
        ),
        ({"entry_price": 100.0, "session_hwm": 112.0}, 112.0),
        ({"entry_price": 100.0}, 100.0),
        ({"entry_price": 100.0, "bar_highs": [None, "x", 115.0]}, 115.0),
        ({"entry_price": 0.0}, 0.0),
    ],
)
def test_h_derive_hwm(args, expected):
    assert derive_position_hwm(**args) == expected
