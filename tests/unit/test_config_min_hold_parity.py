"""C6 (#2372): MIN_HOLD_HOURS must be edition-parity — the OSS/desktop portfolio
anti-churn hold gate must match the Enterprise value (config.py:171 == 1.0), not the
old accidentally-drifted 0.5. Also guards that the dead MIN_HOLD_HOURS_PORTFOLIO key
is gone (no reader; absent in Enterprise)."""

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_c6", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_min_hold_hours_matches_enterprise_one_hour():
    cfg = _load_oss_config()
    assert (
        cfg.MIN_HOLD_HOURS == 1.0
    ), f"OSS MIN_HOLD_HOURS must be 1.0 (Enterprise parity), got {cfg.MIN_HOLD_HOURS!r}"


def test_dead_portfolio_hold_constant_removed():
    cfg = _load_oss_config()
    assert not hasattr(
        cfg, "MIN_HOLD_HOURS_PORTFOLIO"
    ), "dead MIN_HOLD_HOURS_PORTFOLIO must be removed"
