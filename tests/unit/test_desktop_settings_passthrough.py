"""Every operator-settable trading setting must actually reach a desktop engine.

The Console records an apply on the WORM chain, then the desktop shell persists the
changed keys to ``setup.json`` and restarts the engine with them in its environment.
``setup-manager.cjs`` filters both steps through two hand-kept lists. A registry key
missing from either list is recorded as changed and is gone after that very restart —
silently (#3352 agent flags, #3371 position cap, #3349 earnings guard: 10 keys at once).

This guard derives the expectation from the REGISTRY itself, so a new setting cannot be
added without its passthrough.
"""

import inspect
import os
import re

import config as _config
from core.trading_settings import REGISTRY


def _lists():
    here = os.path.dirname(inspect.getfile(_config))
    path = os.path.join(here, "..", "desktop", "electron", "setup-manager.cjs")
    src = open(path, encoding="utf-8").read()

    def block(name, end):
        i = src.index("const " + name)
        return set(re.findall(r'"([A-Z0-9_]+)"', src[i : src.index(end, i)]))

    return block("ALLOWED_SETUP_KEYS", "]);"), block("INJECTED_ENV_KEYS", "];")


def test_every_registry_key_is_persisted_to_setup_json():
    allowed, _ = _lists()
    missing = sorted(k for k in REGISTRY if k not in allowed)
    assert (
        not missing
    ), f"dropped before setup.json (never survive a restart): {missing}"


def test_every_registry_key_is_injected_into_the_engine_env():
    _, injected = _lists()
    missing = sorted(k for k in REGISTRY if k not in injected)
    assert not missing, f"persisted but never reach the engine env: {missing}"


def test_every_registry_key_is_read_from_the_env_in_both_configs():
    """Injection is only half the way: the config must read the variable."""
    here = os.path.dirname(inspect.getfile(_config))
    for fname in ("settings.py",):
        txt = open(os.path.join(here, fname), encoding="utf-8").read()
        missing = sorted(k for k in REGISTRY if f'"{k}"' not in txt)
        assert not missing, f"{fname} never reads: {missing}"
