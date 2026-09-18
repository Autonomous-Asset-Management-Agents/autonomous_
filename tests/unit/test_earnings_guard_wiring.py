# tests/unit/test_earnings_guard_wiring.py
"""#3349 — the Earnings-Guard is WIRED into the BUY path, registered, and dark.

Anti-drift + integration seam (mirrors test_immaterial_entry_guard.py Test 5,
`inspect.getsource`): the order-executor BUY branch must consult
``earnings_guard_block`` gated by ``EARNINGS_GUARD_ENABLED`` (dark ⇒ byte-identical),
the 3 settings must be registered + config-parity present, and the guard decision
the seam relies on must block/allow as designed.
"""

import inspect
from datetime import date

import config as _config
from core.engine.earnings_guard import earnings_guard_block
from core.trading_settings import REGISTRY

_KEYS = (
    "EARNINGS_GUARD_ENABLED",
    "EARNINGS_GUARD_POST_DAYS",
    "EARNINGS_GUARD_PRE_DAYS",
)


def test_seam_wired_in_buy_path():
    import core.engine.order_executor as oe

    src = inspect.getsource(oe)
    assert "earnings_guard_block(" in src, "guard not called in the executor"
    assert "EARNINGS_GUARD_ENABLED" in src, "seam not gated by the enable flag"
    # BUY-only: the call sits behind an action == "BUY" gate
    assert 'action == "BUY"' in src


def test_settings_registered_with_bounds():
    for k in _KEYS:
        assert k in REGISTRY, f"{k} missing from the settings registry"
    assert REGISTRY["EARNINGS_GUARD_POST_DAYS"].hi == 10
    assert REGISTRY["EARNINGS_GUARD_PRE_DAYS"].lo == 0


def test_config_dark_defaults():
    cfg = _config.get_config()
    assert cfg.EARNINGS_GUARD_ENABLED is False  # dark ⇒ byte-identical
    assert cfg.EARNINGS_GUARD_POST_DAYS == 2
    assert cfg.EARNINGS_GUARD_PRE_DAYS == 0


def test_config_parity_oss_and_enterprise():
    # BORA: both editions must define the three keys (source-level parity check).
    import os

    here = os.path.dirname(inspect.getfile(_config))
    for fname in ("config.py", "config.oss.py"):
        txt = open(os.path.join(here, fname), encoding="utf-8").read()
        for k in _KEYS:
            assert k in txt, f"{k} missing from {fname} (BORA parity)"


def test_seam_decision_blocks_in_window_allows_otherwise():
    # The exact decision the seam acts on, with config defaults (post_days=2).
    cfg = _config.get_config()
    post, pre = cfg.EARNINGS_GUARD_POST_DAYS, cfg.EARNINGS_GUARD_PRE_DAYS
    report = [date(2026, 7, 28)]
    # +1 day after report ⇒ blocked:earnings
    hit = earnings_guard_block(
        "CNC", date(2026, 7, 29), post, pre, lambda s: (report, False)
    )
    assert hit is not None and hit[0] == "blocked:earnings"
    # mid-quarter ⇒ no veto
    assert (
        earnings_guard_block(
            "CNC", date(2026, 8, 20), post, pre, lambda s: (report, False)
        )
        is None
    )
