# tests/unit/test_config_top_k_eval_parity.py
"""BORA dual-edition parity for ROUND_TABLE_TOP_K_EVAL (round-table-v2 rank-cache funnel).

The flag must exist in BOTH editions with the same env name and the same default. The round-table-v2
constellation is now ACTIVATED: ROUND_TABLE_TOP_K_EVAL defaults to 20 (funnel evaluates the top-20
ranked names) and ROUND_TABLE_RANK_REFRESH_MINUTES defaults to 120 (rank cache refreshes every 2h) in
BOTH editions. ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS stays 2 (unchanged). 0 remains reachable via env
(ROUND_TABLE_TOP_K_EVAL=0 → full universe) for anyone who wants to disable the funnel.
"""
import importlib.util
from pathlib import Path

from config import RuntimeConfigState

_ROOT = Path(__file__).resolve().parents[2]


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_topk", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_KEYS = {
    "ROUND_TABLE_TOP_K_EVAL": 20,
    "ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS": 2,
    "ROUND_TABLE_RANK_REFRESH_MINUTES": 120,
}


def test_enterprise_defaults():
    cfg = RuntimeConfigState()
    for key, default in _KEYS.items():
        assert getattr(cfg, key) == default


def test_oss_defaults_match_enterprise():
    oss, ent = _load_oss_config(), RuntimeConfigState()
    for key in _KEYS:
        assert getattr(oss, key) == getattr(ent, key)


def test_enterprise_module_getattr_resolves_keys():
    import config

    for key, default in _KEYS.items():
        assert getattr(config, key) == default
