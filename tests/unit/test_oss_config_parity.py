# tests/unit/test_oss_config_parity.py
# Dual-edition drift guard — prevents the class of bug that broke #1159.
#
# The OSS image swaps config.py -> config.oss.py (scripts/oss_make_snapshot.sh). So in
# the OSS build, `config` IS the flat stub config.oss.py. Any flag that engine code reads
# via `get_config().<ATTR>` on the OSS boot/runtime path MUST therefore exist on
# config.oss.py's get_config() — otherwise the OSS backend ImportErrors/AttributeErrors at
# boot and `test-oss-stack` fails (container "unhealthy", no traceback). config.py and
# config.oss.py are hand-maintained parallels that silently drift; this test fails loudly
# the moment a get_config() flag is added to config.py but not mirrored into config.oss.py.
#
# Runs in the normal (config.py) environment — it loads config.oss.py BY PATH and inspects
# its get_config() surface, then scans core/ for the flags engine code actually reads.

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
_OSS_CONFIG = _AI_BOT / "config.oss.py"
_CORE = _AI_BOT / "core"

# Flags the engine reads via get_config() on the OSS boot/runtime path. Kept explicit so
# the guard is deterministic even if the auto-scan below ever misses an access pattern.
_REQUIRED_GET_CONFIG_FLAGS = {
    "GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED",
    "GATEKEEPER_REQUIRE_CONTEXT",
    "SHADOW_TFT_VOTE_ENABLED",
    "SHADOW_TFT_VOTE_CHAIN_PATH",
    "ML_PREDICTION_ENABLED",
    "ML_SENTIMENT_BLEND_ENABLED",
    "TFT_MODELS_ROOT",
    "TFT_SERVING_FIX",
    "TFT_QUALITY_GATE_HONEST_IC",
}


def _load_oss_config():
    spec = importlib.util.spec_from_file_location("config_oss_under_test", _OSS_CONFIG)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scan_get_config_attrs() -> set[str]:
    """Every UPPER_CASE attribute read off get_config() in core/.

    Covers both `get_config().FLAG` (direct chain) and the `cfg = get_config(); cfg.FLAG`
    pattern (only vars provably assigned from get_config() in the same file)."""
    direct = re.compile(r"get_config\(\)\.([A-Z][A-Z0-9_]+)")
    assign = re.compile(r"(\w+)\s*(?::[^=\n]+)?=\s*get_config\(\)")
    attrs: set[str] = set()
    for py in _CORE.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        if "get_config(" not in text:
            continue
        attrs.update(direct.findall(text))
        for var in set(assign.findall(text)):
            attrs.update(re.findall(rf"\b{re.escape(var)}\.([A-Z][A-Z0-9_]+)", text))
    return attrs


def test_oss_config_exposes_get_config():
    module = _load_oss_config()
    assert hasattr(module, "get_config"), (
        "config.oss.py MUST expose get_config() — the OSS boot path "
        "(trading_loop.py -> 'from config import get_config') imports it."
    )
    assert module.get_config() is not None


def test_oss_get_config_has_required_flags():
    cfg = _load_oss_config().get_config()
    missing = sorted(f for f in _REQUIRED_GET_CONFIG_FLAGS if not hasattr(cfg, f))
    assert (
        not missing
    ), f"config.oss.py get_config() is missing required flags: {missing}"


def test_oss_get_config_mirrors_all_engine_reads():
    """Auto-detected: every flag core/ reads via get_config() must exist on the OSS stub."""
    cfg = _load_oss_config().get_config()
    read = _scan_get_config_attrs()
    assert read, "scan found no get_config() reads — regex likely broke"
    missing = sorted(a for a in read if not hasattr(cfg, a))
    assert not missing, (
        "config.oss.py get_config() drifted from config.py — these flags are read by "
        f"engine code via get_config() but missing from the OSS stub: {missing}. "
        "Add them to config.oss.py's RuntimeConfigState (default OFF)."
    )


def test_oss_gatekeeper_flags_default_state():
    """Verify default states of the ComplianceGatekeeper flags."""
    cfg = _load_oss_config().get_config()
    assert cfg.GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED is True
    assert cfg.GATEKEEPER_REQUIRE_CONTEXT is False
