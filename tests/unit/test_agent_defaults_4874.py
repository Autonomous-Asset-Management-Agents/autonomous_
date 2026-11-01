"""#4874: the round-table card's defaults are the engine's defaults — no second truth.

The card marks a row "Changed" against ``default_enabled``/``default_weight`` from the
agents view and "Reset to defaults" writes exactly those values. The engine itself runs
on the ``os.getenv`` literals in settings.py (config.get_config() → settings._config_state).
#3698 moved four weight defaults in settings.py and in the registry, but not in
``trading_settings._AGENTS`` — the card then showed "Changed" on an untouched install and
"Reset" restored the pre-#3698 weights. This pins agents view == registry == settings.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.trading_settings import _AGENTS, REGISTRY, agents_view

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

_SRC = (Path(__file__).resolve().parents[2] / "settings.py").read_text(encoding="utf-8")


def _engine_default(key: str) -> str:
    m = re.search(rf'os\.getenv\(\s*"{key}"\s*,\s*"([^"]*)"', _SRC)
    assert m, f"{key}: no os.getenv default in settings.py"
    return m.group(1)


def _shipped_cfg() -> SimpleNamespace:
    """A config carrying exactly the shipped registry defaults (no .env, no user change)."""
    return SimpleNamespace(
        **{k: s.default for k, s in REGISTRY.items()},
        IMPLIED_VOL_FORECAST_ENABLED=True,
    )


_KEYED = [(a["name"], a["enable_flag"], a["weight_key"]) for a in _AGENTS]


@pytest.mark.parametrize("name,flag,weight_key", _KEYED)
def test_registry_defaults_equal_the_engine_literal(name, flag, weight_key):
    assert str(REGISTRY[flag].default).lower() == _engine_default(flag).lower(), flag
    if weight_key:
        assert float(REGISTRY[weight_key].default) == pytest.approx(
            float(_engine_default(weight_key))
        )


def test_vix_size_influence_default_equals_engine_literal():
    assert float(REGISTRY["VIX_SIZE_INFLUENCE"].default) == pytest.approx(
        float(_engine_default("VIX_SIZE_INFLUENCE"))
    )


@pytest.mark.parametrize("name,flag,weight_key", _KEYED)
def test_agents_view_defaults_equal_the_registry(name, flag, weight_key):
    agent = {a["name"]: a for a in agents_view(_shipped_cfg())["agents"]}[name]
    assert agent["default_enabled"] is REGISTRY[agent["enable_flag"]].default
    if agent["weight_key"]:
        reg = REGISTRY[agent["weight_key"]]
        assert agent["default_weight"] == pytest.approx(float(reg.default)), name
        assert (agent["min_weight"], agent["max_weight"]) == (reg.lo, reg.hi), name


def test_untouched_install_shows_no_change():
    """With every value at its shipped default, effective == default on every row."""
    for a in agents_view(_shipped_cfg())["agents"]:
        assert a["enabled"] is a["default_enabled"], a["name"]
        if a["weight_key"]:
            assert a["weight"] == pytest.approx(a["default_weight"]), a["name"]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("LSTMSignalAgent", 0.15),
        ("SpecialistAlphaAgent", 0.15),
        ("NewsSentimentAgent", 0.20),
        ("FundamentalsAgent", 0.25),
    ],
)
def test_the_four_post_3698_weights(name, expected):
    agent = {a["name"]: a for a in agents_view(_shipped_cfg())["agents"]}[name]
    assert agent["default_weight"] == pytest.approx(expected)
