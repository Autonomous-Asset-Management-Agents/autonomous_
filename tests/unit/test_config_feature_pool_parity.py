# tests/unit/test_config_feature_pool_parity.py
"""#2499 event-loop-stall Fix A — BORA dual-edition parity for the 2 feature-pool keys.

FEATURE_POOL_ENABLED / FEATURE_POOL_WORKERS must exist in BOTH editions with identical env names and
identical defaults (True / 2), mirroring the vol-targeting idiom (test_config_vol_targeting_parity.py).

PROMOTED to default-ON (0.3.6 regression fix): #2516 flipped FULL_UNIVERSE_TRADING_ENABLED default-on,
so the per-cycle ~500-symbol create_live_features now runs every cycle; left inline it holds the GIL and
starves uvicorn's /health + /portfolio-summary (8–97s), which delayed first-load paper data and timed
out the live-switch reconcile poll. Default-ON activates the #2511 ProcessPool offload built for exactly
this hot path (fail-safe: any pool problem degrades to the byte-identical inline path). Override per env
FEATURE_POOL_ENABLED=false to roll back without a rebuild.
"""

import importlib.util
from pathlib import Path

from config import RuntimeConfigState

_ROOT = Path(__file__).resolve().parents[2]


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_2499", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_enterprise_defaults():
    cfg = RuntimeConfigState()
    assert cfg.FEATURE_POOL_ENABLED is True
    assert cfg.FEATURE_POOL_WORKERS == 2


def test_oss_defaults_match_enterprise():
    oss = _load_oss_config()
    ent = RuntimeConfigState()
    assert oss.FEATURE_POOL_ENABLED == ent.FEATURE_POOL_ENABLED
    assert oss.FEATURE_POOL_WORKERS == ent.FEATURE_POOL_WORKERS


def test_oss_get_config_exposes_keys():
    oss = _load_oss_config()
    cfg = oss.get_config()
    assert cfg.FEATURE_POOL_ENABLED is True
    assert cfg.FEATURE_POOL_WORKERS == 2


def test_enterprise_module_getattr_resolves_keys():
    """featurize() reads via config.get_config(); the PEP-562 module __getattr__ must ALSO resolve
    both keys so `import config; config.FEATURE_POOL_ENABLED` stays valid on the Enterprise edition.
    """
    import config

    assert config.FEATURE_POOL_ENABLED is True
    assert config.FEATURE_POOL_WORKERS == 2
