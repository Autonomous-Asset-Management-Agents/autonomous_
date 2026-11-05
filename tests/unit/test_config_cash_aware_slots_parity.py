"""BORA parity (O3): ``CASH_AWARE_SLOTS_ENABLED`` — the cash-aware sizing-divisor flag — must be
identical across the OSS/desktop config (config.oss.py) and the Enterprise config (config.py): same
env var, same default, so the two editions can never drift and Paper == Live stays byte-identical.
Mirrors test_config_min_hold_parity.py / test_config_vol_targeting_parity.py.
"""

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_o3", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cash_aware_slots_enabled_edition_parity():
    """OSS value == Enterprise value (both read the same env var with the same default)."""
    import config as ent

    oss = _load_oss_config()
    ent_val = ent.get_config().CASH_AWARE_SLOTS_ENABLED
    assert oss.CASH_AWARE_SLOTS_ENABLED == ent_val, (
        "CASH_AWARE_SLOTS_ENABLED must match across editions: "
        f"oss={oss.CASH_AWARE_SLOTS_ENABLED!r} vs enterprise={ent_val!r}"
    )


def test_cash_aware_slots_default_on_both_editions():
    """Default is ON in both editions (operator directive; flag OFF is the exact-rollback lever)."""
    import config as ent

    oss = _load_oss_config()
    # In a clean env (no CASH_AWARE_SLOTS_ENABLED override) the shipped default is True.
    import os

    if os.getenv("CASH_AWARE_SLOTS_ENABLED") is None:
        assert oss.CASH_AWARE_SLOTS_ENABLED is True
        assert ent.get_config().CASH_AWARE_SLOTS_ENABLED is True
