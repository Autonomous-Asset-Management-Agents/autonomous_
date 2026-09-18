"""#1955 TRD-4 — dual-edition config parity for the Meta-Label filter keys.

BORA: the 3 new keys must exist in BOTH editions with identical env names and
identical safe defaults (False / 0.55 / "data/meta_label_model.pkl"), mirrored
on the DSR-gate parity idiom (test_config_dsr_parity.py)."""

import importlib.util
from pathlib import Path

from config import RuntimeConfigState

_ROOT = Path(__file__).resolve().parents[2]


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_meta_label", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_enterprise_defaults():
    cfg = RuntimeConfigState()
    assert cfg.META_LABEL_FILTER_ENABLED is False
    assert cfg.META_LABEL_MIN_PROBA == 0.55
    assert cfg.META_LABEL_MODEL_PATH == "data/meta_label_model.pkl"


def test_oss_defaults_match_enterprise():
    oss = _load_oss_config()
    ent = RuntimeConfigState()
    assert oss.META_LABEL_FILTER_ENABLED == ent.META_LABEL_FILTER_ENABLED
    assert oss.META_LABEL_MIN_PROBA == ent.META_LABEL_MIN_PROBA
    assert oss.META_LABEL_MODEL_PATH == ent.META_LABEL_MODEL_PATH


def test_oss_get_config_exposes_keys():
    oss = _load_oss_config()
    cfg = oss.get_config()
    assert cfg.META_LABEL_FILTER_ENABLED is False
    assert cfg.META_LABEL_MIN_PROBA == 0.55
    assert cfg.META_LABEL_MODEL_PATH == "data/meta_label_model.pkl"
