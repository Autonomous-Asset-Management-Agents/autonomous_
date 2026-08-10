"""BORA parity: ``POSITION_EXIT_HWM_TRAILING_ENABLED`` (the per-cycle trailing-stop high-water-mark
feed) must be identical across config.py (Enterprise) and config.oss.py (desktop) — same env var, same
default — so the desktop and cloud exit behaviour can never drift (Paper == Live).
Mirrors test_config_min_hold_parity.py.
"""

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_exit_hwm", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_position_exit_hwm_flag_edition_parity():
    import config as ent

    oss = _load_oss_config()
    ent_val = ent.get_config().POSITION_EXIT_HWM_TRAILING_ENABLED
    assert oss.POSITION_EXIT_HWM_TRAILING_ENABLED == ent_val, (
        "POSITION_EXIT_HWM_TRAILING_ENABLED must match across editions: "
        f"oss={oss.POSITION_EXIT_HWM_TRAILING_ENABLED!r} vs enterprise={ent_val!r}"
    )


def test_position_exit_hwm_default_on_both_editions():
    import os

    if os.getenv("POSITION_EXIT_HWM_TRAILING_ENABLED") is None:
        import config as ent

        oss = _load_oss_config()
        assert oss.POSITION_EXIT_HWM_TRAILING_ENABLED is True
        assert ent.get_config().POSITION_EXIT_HWM_TRAILING_ENABLED is True
