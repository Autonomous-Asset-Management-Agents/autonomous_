"""MLR-9 (#1909): ADR-ML-GATE-03 — dual-edition config parity for the DSR gate keys.

BORA: the 3 new keys must exist in BOTH editions with identical env names and
identical safe defaults (False / 0.0 / False), mirrored on the
TFT_NET_SHARPE_FLOOR idiom (config.py:567 ↔ config.oss.py:722)."""

import importlib.util
from pathlib import Path

from config import RuntimeConfigState

_ROOT = Path(__file__).resolve().parents[2]


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_mlr9", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_enterprise_defaults():
    cfg = RuntimeConfigState()
    assert cfg.TFT_DEFLATED_SHARPE_GATE_ENABLED is False
    assert cfg.TFT_DSR_FLOOR == 0.0
    assert cfg.TFT_IC_DIAGNOSTIC_ONLY is False


def test_oss_defaults_match_enterprise():
    oss = _load_oss_config()
    ent = RuntimeConfigState()
    assert oss.TFT_DEFLATED_SHARPE_GATE_ENABLED == ent.TFT_DEFLATED_SHARPE_GATE_ENABLED
    assert oss.TFT_DSR_FLOOR == ent.TFT_DSR_FLOOR
    assert oss.TFT_IC_DIAGNOSTIC_ONLY == ent.TFT_IC_DIAGNOSTIC_ONLY


def test_oss_get_config_exposes_keys():
    oss = _load_oss_config()
    cfg = oss.get_config()
    assert cfg.TFT_DEFLATED_SHARPE_GATE_ENABLED is False
    assert cfg.TFT_DSR_FLOOR == 0.0
    assert cfg.TFT_IC_DIAGNOSTIC_ONLY is False
