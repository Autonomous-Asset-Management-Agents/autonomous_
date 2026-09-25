"""#3361 — the regime throttle is WIRED into both BUY sizing paths, dark by default,

BUY-only, fail-open — and its settings actually reach a desktop engine.



`inspect.getsource` for the seams (the executor is too heavy to drive end-to-end in a

unit test; same idiom as test_immaterial_entry_guard.py Test 5), plus behavioural tests

of the shared helper the seams call.

"""

import inspect
import os
import re
from types import SimpleNamespace
from unittest.mock import patch

import config as _config
import core.engine.order_executor as oe
from core.trading_settings import REGISTRY

_KEYS = (
    "REGIME_THROTTLE_ENABLED",
    "REGIME_RISKOFF_PERCENTILE",
    "REGIME_THROTTLE_SIZE_FACTOR",
)


def _cfg(enabled, pct=75, factor=0.5):

    return SimpleNamespace(
        REGIME_THROTTLE_ENABLED=enabled,
        REGIME_RISKOFF_PERCENTILE=pct,
        REGIME_THROTTLE_SIZE_FACTOR=factor,
        SIM_MODE=False,
    )


def _reading(factor):

    return {"factor": factor, "score": 85.0, "threshold": 67.0, "asof": "2026-09-10"}


def test_both_money_paths_call_the_shared_helper():

    src = inspect.getsource(oe)

    # one definition + the tenant path + the desktop/[Global] path

    assert src.count("_regime_throttled_size(") >= 3

    assert "size = _regime_throttled_size(symbol, action, size, context)" in src

    assert re.search(
        r"max_allowed_qty = _regime_throttled_size\(\s*symbol, action, max_allowed_qty, context",
        src,
    )


def test_dark_default_leaves_size_untouched_without_reading_the_signal():

    ctx = SimpleNamespace()

    with patch("config.get_config", return_value=_cfg(False)), patch(
        "core.engine.regime_signal.regime_reading"
    ) as reading:

        assert oe._regime_throttled_size("ABNB", "BUY", 10.0, ctx) == 10.0

        reading.assert_not_called()

    assert not hasattr(ctx, "regime_throttle_factor")


def test_enabled_and_risk_off_shrinks_a_buy_and_mirrors_the_factor():

    ctx = SimpleNamespace()

    with patch("config.get_config", return_value=_cfg(True)), patch(
        "core.engine.regime_signal.regime_reading", return_value=_reading(0.5)
    ):

        assert oe._regime_throttled_size("ABNB", "BUY", 10.0, ctx) == 5.0

    assert ctx.regime_throttle_factor == 0.5


def test_enabled_but_calm_is_a_no_op():

    with patch("config.get_config", return_value=_cfg(True)), patch(
        "core.engine.regime_signal.regime_reading", return_value=_reading(1.0)
    ):

        assert oe._regime_throttled_size("ABNB", "BUY", 10.0, SimpleNamespace()) == 10.0


def test_sell_and_zero_size_are_never_touched():

    with patch("config.get_config", return_value=_cfg(True)), patch(
        "core.engine.regime_signal.regime_reading", return_value=_reading(0.5)
    ) as reading:

        assert (
            oe._regime_throttled_size("ABNB", "SELL", 10.0, SimpleNamespace()) == 10.0
        )

        assert oe._regime_throttled_size("ABNB", "BUY", 0.0, SimpleNamespace()) == 0.0

        reading.assert_not_called()


def test_any_error_fails_open():

    with patch("config.get_config", return_value=_cfg(True)), patch(
        "core.engine.regime_signal.regime_reading", side_effect=RuntimeError("boom")
    ):

        assert oe._regime_throttled_size("ABNB", "BUY", 10.0, SimpleNamespace()) == 10.0


def test_settings_registered_dark_with_bounds_and_config_parity():

    for k in _KEYS:

        assert k in REGISTRY, f"{k} missing from the settings registry"

    assert REGISTRY["REGIME_THROTTLE_ENABLED"].default is False

    assert (
        REGISTRY["REGIME_RISKOFF_PERCENTILE"].lo,
        REGISTRY["REGIME_RISKOFF_PERCENTILE"].hi,
    ) == (50, 95)

    assert REGISTRY["REGIME_THROTTLE_SIZE_FACTOR"].hi == 1.0  # never an up-size

    cfg = _config.get_config()

    assert cfg.REGIME_THROTTLE_ENABLED is False

    here = os.path.dirname(inspect.getfile(_config))

    for fname in ("settings.py",):
        txt = open(os.path.join(here, fname), encoding="utf-8").read()
        for k in _KEYS:
            assert k in txt, f"{k} missing from {fname} (BORA parity)"


def test_desktop_persists_and_injects_the_settings():
    """The #3371 class of bug: a Console setting that is saved but never reaches the

    engine. setup.json only keeps ALLOWED_SETUP_KEYS and only INJECTED_ENV_KEYS reach

    the engine env — both lists must carry the three keys."""

    here = os.path.dirname(inspect.getfile(_config))

    path = os.path.join(here, "..", "desktop", "electron", "setup-manager.cjs")

    src = open(path, encoding="utf-8").read()

    def block(name, end):

        i = src.index("const " + name)

        return src[i : src.index(end, i)]

    allowed = block("ALLOWED_SETUP_KEYS", "]);")

    injected = block("INJECTED_ENV_KEYS", "];")

    for k in _KEYS:

        assert f'"{k}"' in allowed, f"{k} would be dropped from setup.json"

        assert f'"{k}"' in injected, f"{k} would never reach the engine env"


def test_loop_refresh_is_armed_only_and_never_in_sim():

    import core.engine.trading_loop as tl

    src = inspect.getsource(tl)

    hook = src[src.index("#3361: regime-beyond-VIX signal") :]

    hook = hook[: hook.index("GAP9")]

    assert '"REGIME_THROTTLE_ENABLED", False' in hook

    assert '"SIM_MODE", False' in hook

    assert "ensure_fresh_state" in hook and "asyncio.to_thread" in hook
