"""BORA parity (#2558): ``USE_LIMIT_EXITS`` — the exit-selective marketable-limit flag — must be
identical across the OSS/desktop config (config.oss.py) and the Enterprise config (config.py): same
env var, same default, so the two editions can never drift and Paper == Live stays byte-identical.
Mirrors test_config_cash_aware_slots_parity.py / test_config_min_hold_parity.py.
"""

import importlib.util
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_use_limit_exits", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_use_limit_exits_edition_parity():
    """OSS value == Enterprise value (both read the same env var with the same default)."""
    import config as ent

    oss = _load_oss_config()
    ent_val = ent.get_config().USE_LIMIT_EXITS
    assert oss.USE_LIMIT_EXITS == ent_val, (
        "USE_LIMIT_EXITS must match across editions: "
        f"oss={oss.USE_LIMIT_EXITS!r} vs enterprise={ent_val!r}"
    )


def test_use_limit_exits_default_off_both_editions():
    """Default is OFF in both editions (new flag ships dark; opt-in only)."""
    import config as ent

    oss = _load_oss_config()
    if os.getenv("USE_LIMIT_EXITS") is None:
        assert oss.USE_LIMIT_EXITS is False
        assert ent.get_config().USE_LIMIT_EXITS is False
