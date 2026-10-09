"""#3381 — BORA-Paritaet fuer die vier Zeitgrenzen der Entscheidung (Epic #3366).

MAX_QUOTE_AGE_SECONDS, AGENT_VOTE_TIMEOUT_SECONDS, SYMBOL_EVAL_TIMEOUT_SECONDS und
CYCLE_TIMEOUT_SECONDS muessen in BEIDEN Editionen unter demselben Env-Namen und mit
demselben Standardwert existieren. Ein Symbol, dessen Kurs zu alt ist, wird auf dem
Desktop genauso uebersprungen wie in der Enterprise-Edition; eine Enthaltung, die nur
eine Edition kennt, waere ein editionsabhaengiges Handelsverhalten.

Bauform uebernommen von tests/unit/test_config_feature_pool_parity.py.
"""

import importlib.util
from pathlib import Path

from config import RuntimeConfigState

_ROOT = Path(__file__).resolve().parents[2]

_KEYS = (
    "MAX_QUOTE_AGE_SECONDS",
    "AGENT_VOTE_TIMEOUT_SECONDS",
    "SYMBOL_EVAL_TIMEOUT_SECONDS",
    "CYCLE_TIMEOUT_SECONDS",
)


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_3381", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_enterprise_defaults():
    cfg = RuntimeConfigState()
    assert cfg.MAX_QUOTE_AGE_SECONDS == 900.0
    assert cfg.AGENT_VOTE_TIMEOUT_SECONDS == 60.0
    assert cfg.SYMBOL_EVAL_TIMEOUT_SECONDS == 120.0
    assert cfg.CYCLE_TIMEOUT_SECONDS == 1800.0


def test_oss_defaults_match_enterprise():
    oss = _load_oss_config()
    ent = RuntimeConfigState()
    for key in _KEYS:
        assert hasattr(oss, key), f"config.oss.py fehlt {key} — BORA-Paritaet verletzt"
        assert getattr(oss, key) == getattr(
            ent, key
        ), f"{key}: Desktop {getattr(oss, key)} != Enterprise {getattr(ent, key)}"


def test_beide_editionen_sind_gestaffelt():
    """Die Staffelung ist keine Eigenschaft einer Edition, sondern der Standardwerte."""
    oss = _load_oss_config()
    ent = RuntimeConfigState()
    for cfg in (oss, ent):
        assert (
            cfg.AGENT_VOTE_TIMEOUT_SECONDS
            < cfg.SYMBOL_EVAL_TIMEOUT_SECONDS
            < cfg.CYCLE_TIMEOUT_SECONDS
        )
