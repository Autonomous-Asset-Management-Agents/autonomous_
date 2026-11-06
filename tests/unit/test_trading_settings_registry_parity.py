# tests/unit/test_trading_settings_registry_parity.py
"""Binding pre-merge guard (#3284 follow-up): the operator Trading-Settings REGISTRY
default MUST equal the config SOURCE default.

A drift here means the console shows/operates a default that the code does not actually
use — exactly the class of error that must not survive a merge. This test runs in the
normal suite, so any future divergence fails CI before merge.

Env-IMMUNE by construction: it compares the config **source** default (the `os.getenv`
fallback literal, parsed by the FEATURE_FLAGS generator) — NEVER `getattr(config, key)`,
which resolves environment variables / `.env` and would raise false positives (a local
`ROTATION_MAX_EXITS_PER_SESSION=1` once did exactly that during the #3284 audit).
"""

import importlib.util
from pathlib import Path

import pytest

from core.trading_settings import REGISTRY

# Load the generator (single source of truth for "what is the source default").
_GEN_PATH = Path(__file__).resolve().parents[3] / "scripts" / "gen_feature_flags.py"
_spec = importlib.util.spec_from_file_location("gen_feature_flags", _GEN_PATH)
assert _spec and _spec.loader
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def _source_defaults() -> dict:
    """name -> normalized source-default string, for flags with a LITERAL default."""
    out = {}
    for f in gen.collect_flags():
        raw = f.default_ent if f.default_ent != "__ABSENT__" else f.default_oss
        if raw in (None, "__ABSENT__"):
            continue  # secret (no default) or computed expression — not statically comparable
        out[f.name] = gen._norm_default(raw)
    return out


def _same(registry_default, source_str: str) -> bool:
    if isinstance(registry_default, bool):
        return source_str.strip().lower() == ("true" if registry_default else "false")
    try:
        return abs(float(registry_default) - float(source_str)) < 1e-9
    except (TypeError, ValueError):
        return str(registry_default).strip().lower() == source_str.strip().lower()


_SRC = _source_defaults()


def test_registry_is_nonempty():
    assert len(REGISTRY) >= 40  # sanity: the curated operator set is present


def test_guard_has_teeth():
    # The comparator MUST flag a real drift (else this whole guard is a dead control).
    assert _same(2, "2") and _same(0.05, "0.05") and _same(True, "True")
    assert not _same(2, "1")  # the exact false case the #3284 audit worried about
    assert not _same(0.25, "0.30") and not _same("b", "off")


@pytest.mark.parametrize("key", sorted(REGISTRY))
def test_registry_default_matches_config_source(key):
    setting = REGISTRY[key]
    assert key in _SRC, (
        f"{key} is in the Trading-Settings registry but has no literal config source "
        f"default (computed/absent) — cannot document a default the code does not pin. "
        f"Give it a literal os.getenv default in config.py/config.oss.py."
    )
    assert _same(setting.default, _SRC[key]), (
        f"DEFAULT DRIFT for {key}: registry default={setting.default!r} but config "
        f"source default={_SRC[key]!r}. The operator console would document/operate the "
        f"wrong value. Align the registry default in core/trading_settings.py with config."
    )
