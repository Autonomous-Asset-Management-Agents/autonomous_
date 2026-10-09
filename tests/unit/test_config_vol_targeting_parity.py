# tests/unit/test_config_vol_targeting_parity.py
"""#1953 TRD-2 Inc 1 — BORA dual-edition parity for the 4 vol-targeting keys.

The 4 keys must exist in BOTH editions with identical env names and identical
defaults (True / 0.015 / 0.5 / 1.5) — the master flag is ON by default since the
owner-waiver 2026-08-14 (#1953 vol-targeting sizing enabled; a missing forecast is a
fail-safe no-op). Parity keeps OSS and Enterprise from drifting.
"""

import importlib.util
from pathlib import Path

from config import RuntimeConfigState

_ROOT = Path(__file__).resolve().parents[2]


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_1953", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_enterprise_defaults():
    cfg = RuntimeConfigState()
    assert cfg.VOL_TARGETING_SIZING_ENABLED is True
    assert cfg.VOL_TARGET_DAILY_VOL == 0.015
    assert cfg.VOL_SIZE_SCALER_LO == 0.5
    assert cfg.VOL_SIZE_SCALER_HI == 1.5


def test_oss_defaults_match_enterprise():
    oss = _load_oss_config()
    ent = RuntimeConfigState()
    assert oss.VOL_TARGETING_SIZING_ENABLED == ent.VOL_TARGETING_SIZING_ENABLED
    assert oss.VOL_TARGET_DAILY_VOL == ent.VOL_TARGET_DAILY_VOL
    assert oss.VOL_SIZE_SCALER_LO == ent.VOL_SIZE_SCALER_LO
    assert oss.VOL_SIZE_SCALER_HI == ent.VOL_SIZE_SCALER_HI


def test_oss_get_config_exposes_keys():
    oss = _load_oss_config()
    cfg = oss.get_config()
    assert cfg.VOL_TARGETING_SIZING_ENABLED is True
    assert cfg.VOL_TARGET_DAILY_VOL == 0.015
    assert cfg.VOL_SIZE_SCALER_LO == 0.5
    assert cfg.VOL_SIZE_SCALER_HI == 1.5


def test_enterprise_module_getattr_resolves_keys():
    """risk_manager reads via `import config; getattr(config, KEY)` — the PEP-562
    module __getattr__ must resolve all 4 keys on the Enterprise edition."""
    import config

    assert config.VOL_TARGETING_SIZING_ENABLED is True
    assert config.VOL_TARGET_DAILY_VOL == 0.015
    assert config.VOL_SIZE_SCALER_LO == 0.5
    assert config.VOL_SIZE_SCALER_HI == 1.5
