"""#3632 — one exit authority.

Intelligent Exit is the only exit rule set; its loss tiers are registry settings; the
broker stop order is armed by default (ADR-S01 precondition — the reconciler #3389 —
is live); Smart Exit is retired together with the three settings that only it read.
"""

import importlib
import inspect
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import config as _config
from core.trading_settings import REGISTRY, _cross_clamp, canonical_text

pytestmark = pytest.mark.vc4  # Stufen-Marker (#3396)

HERE = os.path.dirname(inspect.getfile(_config))


# --- settings plumbing -------------------------------------------------------


def test_loss_tiers_and_broker_switch_are_registry_settings_with_todays_defaults():
    assert REGISTRY["LOSS_CUT_PCT"].default == -4.0
    assert REGISTRY["LOSS_WATCH_PCT"].default == -2.0
    assert REGISTRY["LOSS_ESCALATION_PCT"].default == -6.0
    assert REGISTRY["BROKER_STOPS_ENABLED"].default is True
    assert (REGISTRY["LOSS_CUT_PCT"].lo, REGISTRY["LOSS_CUT_PCT"].hi) == (-7.9, -1.0)
    cfg = _config.get_config()
    assert (cfg.LOSS_CUT_PCT, cfg.LOSS_WATCH_PCT, cfg.LOSS_ESCALATION_PCT) == (
        -4.0,
        -2.0,
        -6.0,
    )
    assert cfg.BROKER_STOPS_ENABLED is True


def test_smart_exit_only_settings_are_gone():
    for k in (
        "TAKE_PROFIT_PCT",
        "TRAILING_FROM_PEAK_PCT",
        "EXIT_POLICY_TRAILING_ENABLED",
    ):
        assert k not in REGISTRY, k
    txt = open(os.path.join(HERE, "settings.py"), encoding="utf-8").read()
    for k in (
        "TAKE_PROFIT_PCT:",
        "TRAILING_FROM_PEAK_PCT:",
        "EXIT_POLICY_TRAILING_ENABLED:",
    ):
        assert k not in txt, f"{k} still declared"
    fields = open(
        os.path.join(
            HERE, "..", "src", "console", "desktop", "advancedTradingFields.ts"
        ),
        encoding="utf-8",
    ).read()
    for k in (
        "TAKE_PROFIT_PCT",
        "TRAILING_FROM_PEAK_PCT",
        "EXIT_POLICY_TRAILING_ENABLED",
    ):
        assert f'key: "{k}"' not in fields
    for k in (
        "LOSS_CUT_PCT",
        "LOSS_WATCH_PCT",
        "LOSS_ESCALATION_PCT",
        "BROKER_STOPS_ENABLED",
    ):
        assert f'key: "{k}"' in fields, k
    assert 'label: "Broker stop order"' in fields
    launcher = open(
        os.path.join(HERE, "..", "desktop", "electron", "native-engine-manager.cjs"),
        encoding="utf-8",
    ).read()
    assert "EXIT_POLICY_TRAILING_ENABLED" not in launcher


def test_clamp_keeps_the_order_watch_cut_escalation_hard_stop():
    vals = {k: spec.default for k, spec in REGISTRY.items()}
    vals.update(
        {
            "LOSS_WATCH_PCT": -5.0,  # below the cut -> pulled above it
            "LOSS_CUT_PCT": -4.0,
            "LOSS_ESCALATION_PCT": -3.0,  # above the cut -> pushed below it
            "STOP_LOSS_PCT": 9.0,  # above the hard stop -> clamped under it
        }
    )
    vals = _cross_clamp(vals, None)
    assert float(vals["LOSS_WATCH_PCT"]) > float(vals["LOSS_CUT_PCT"])
    assert float(vals["LOSS_CUT_PCT"]) > float(vals["LOSS_ESCALATION_PCT"])
    assert float(vals["LOSS_ESCALATION_PCT"]) > -8.0
    assert float(vals["STOP_LOSS_PCT"]) < 8.0


def test_out_of_range_tier_is_clamped_not_rejected():
    assert canonical_text("LOSS_CUT_PCT", -12) == "-7.9"
    assert canonical_text("LOSS_CUT_PCT", 3) == "-1.0"


# --- intelligent exit reads the settings -------------------------------------


def _cfg(**over):
    base = {
        "LOSS_WATCH_PCT": -2.0,
        "LOSS_CUT_PCT": -4.0,
        "LOSS_ESCALATION_PCT": -6.0,
        "HARD_STOP_LOSS_PCT": -8.0,
        "EXIT_TRAIL_PROFILE": "midterm",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _pressure(pnl, hours, **over):
    import core.intelligent_exit as ie

    with patch("config.get_config", return_value=_cfg(**over)):
        return ie._calculate_loss_pressure(pnl, hours)


def test_defaults_are_byte_identical_to_the_old_constants():
    assert _pressure(-4.5, 30) == pytest.approx(min(100.0, 70 * 1.4))
    assert _pressure(-4.5, 5) == pytest.approx(70 * 1.2)  # 84 < 90: no cut before 24 h
    assert _pressure(-6.5, 1) == pytest.approx(90.0)
    assert _pressure(-8.5, 0) == 100.0
    assert _pressure(-2.5, 0) == pytest.approx(40.0)


def test_a_changed_loss_cut_moves_the_sell_point():
    # cut at -3: a -3.5 % loss held 30 h now reaches 70 x 1.4 = 98 -> sell
    assert _pressure(-3.5, 30, LOSS_CUT_PCT=-3.0) >= 90
    # unchanged default: -3.5 % is only the watch band -> 40 x 1.4 = 56 -> hold
    assert _pressure(-3.5, 30) < 90


def test_analyze_exit_names_the_applied_cut():
    import core.intelligent_exit as ie

    ctx = ie.PositionContext(
        symbol="X",
        entry_price=100.0,
        current_price=96.4,
        high_water_mark=100.0,
        hours_held=30.0,
    )
    with patch("config.get_config", return_value=_cfg(LOSS_CUT_PCT=-3.0)):
        a = ie.analyze_exit(ctx)
    assert a.should_sell and a.tier == "risk" and "LOSS CUT" in a.reason
    assert "-3.0" in a.reason  # the applied setting, not a hard-coded tier


# --- one exit authority ------------------------------------------------------


def test_smart_exit_module_is_gone_and_nothing_imports_it():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("core.smart_exit")
    root = os.path.join(HERE, "core")
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.endswith(".py"):
                src = open(os.path.join(dirpath, f), encoding="utf-8").read()
                assert "core.smart_exit" not in src, os.path.join(dirpath, f)


def test_rl_execution_has_no_second_rule_set():
    import core.strategies.rl_execution as rl

    src = inspect.getsource(rl)
    assert "should_sell_smart" not in src
    hook = inspect.getsource(rl.RLExecutionMixin._check_exit)
    assert "analyze_exit(" in hook
    assert "exc_info=True" in hook  # the error is logged with its stack trace


def test_rl_execution_error_path_holds_and_logs(caplog):
    import logging

    from core.strategies.rl_execution import RLExecutionMixin

    fake = SimpleNamespace(
        _entry_time={},
        portfolio_manager=None,
        high_water_marks={},
        client=None,
        _signal_history={},
        log_thought=lambda *_: None,
    )
    from datetime import datetime

    with patch(
        "core.strategies.rl_execution.analyze_exit", side_effect=RuntimeError("boom")
    ), caplog.at_level(logging.WARNING):
        out = RLExecutionMixin._check_exit(
            fake,
            symbol="X",
            in_position=True,
            qty=1.0,
            avg=100.0,
            curr=90.0,
            current_time=datetime(2026, 9, 24, 14, 0),
            features=None,
        )
    assert out == {"triggered": False, "signal": "HOLD", "exit_error": "boom"}
    assert any(r.exc_info for r in caplog.records)


def test_lstm_strategy_uses_the_intelligent_exit():
    import core.strategies.lstm_strategy as ls

    src = inspect.getsource(ls)
    assert "should_sell_smart" not in src
    assert "analyze_exit(" in src
    assert "from core.hold_policy import" in src


def test_hold_helpers_kept_their_behaviour():
    from datetime import datetime, timedelta

    from core.hold_policy import derive_position_hwm, resolve_hold_hours

    now = datetime(2026, 9, 24, 14, 0)
    assert resolve_hold_hours(now - timedelta(hours=48), now) == pytest.approx(48.0)
    assert resolve_hold_hours(None, now, min_hold_days=5.0) == pytest.approx(144.0)
    assert derive_position_hwm(100.0, 105.0, [103.0, "bad", None], 101.0) == 105.0
    assert derive_position_hwm(0.0) == 0.0


# --- broker stop -------------------------------------------------------------


def test_broker_stop_gate_reads_the_setting_directly():
    import core.engine.trading_loop as tl

    src = inspect.getsource(tl.TradingLoopMixin._maintain_broker_stops)
    assert "BROKER_STOPS_ENABLED" in src
    assert (
        'getattr(cfg, "BROKER_STOPS_ENABLED"' not in src
    )  # ratchet: no silent default


def test_desktop_passthrough_carries_the_new_keys():
    cjs = open(
        os.path.join(HERE, "..", "desktop", "electron", "setup-manager.cjs"),
        encoding="utf-8",
    ).read()
    for k in (
        "LOSS_CUT_PCT",
        "LOSS_WATCH_PCT",
        "LOSS_ESCALATION_PCT",
        "BROKER_STOPS_ENABLED",
    ):
        assert cjs.count(f'"{k}"') == 2, k
    for k in (
        "TAKE_PROFIT_PCT",
        "TRAILING_FROM_PEAK_PCT",
        "EXIT_POLICY_TRAILING_ENABLED",
    ):
        assert f'"{k}"' not in cjs, k
