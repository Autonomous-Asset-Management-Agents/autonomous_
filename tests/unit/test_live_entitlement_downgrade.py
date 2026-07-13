# tests/unit/test_live_entitlement_downgrade.py
# MLR-14 (#1918) — Graceful paper-downgrade instead of a boot-loop when the LIVE entitlement
# lapses AFTER the operator armed live.
#
# PROBLEM (fresh-eyes, #1877 / #1914 beta-grant tokens): the operator armed live (an un-revoked
# WORM enable record + open real-money positions), then the signed license token expired /
# became invalid. resolve_entitlement() fails closed to BASIC (allow_live=False), but the desktop
# shell (native-engine-manager.cjs) only reads the WORM chain — not the tier — and keeps setting
# PAPER_TRADING=false. assert_live_trading_config() then RAISED in __main__.py BEFORE uvicorn.run
# → the engine process died, /api/live/disable was unreachable, open positions went unmanaged
# (no kill-switch, no HITL, no exits), and the shell restarted into the same crash → boot-loop.
#
# FIX: when live is requested (PAPER_TRADING=False) but the entitlement forbids live
# (allow_live=False), do NOT raise. Degrade fail-closed to PAPER: force PAPER_TRADING in-process
# (so the lazily-created broker uses the paper account), log a CRITICAL alert, best-effort record
# the downgrade on the WORM chain as a system 'disable' (so the shell reads verified=false next
# boot and stops flipping PAPER_TRADING off — self-healing), and let the engine boot in paper so
# open positions stay managed and /api/live/disable stays reachable.
#
# INVARIANT preserved: a legitimate live boot (valid token → allow_live=True) is UNCHANGED — it
# still goes live. Only the entitlement-lapse case degrades. BASIC can still NEVER trade live
# (it is forced to paper — even safer than before).
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

from core.entitlement.tier import Entitlement, Tier

_BASIC = Entitlement(
    tier=Tier.BASIC,
    agent_names=("DrawdownGuardAgent",),
    allow_live=False,
    backtest_months=12,
    xai_enabled=False,
    max_order_value=1000.0,
)
_PRO = Entitlement(
    tier=Tier.PRO,
    agent_names=tuple("A" for _ in range(9)),
    allow_live=True,
    backtest_months=None,
    xai_enabled=False,
    max_order_value=10000.0,
)


def _guard():
    from core.engine import live_trading_guard

    return live_trading_guard


def test_lapsed_live_entitlement_downgrades_to_paper_not_raise():
    """(b) PAPER_TRADING=False + allow_live=False (lapsed token) + LOCAL → NO raise.

    The engine must degrade to paper: force_paper_trading() is called, a CRITICAL alert is
    logged, and the downgrade is recorded on the WORM chain as a system 'disable'. The alert is
    asserted via the guard's own logger (the app reconfigures the root logger at boot, which
    makes caplog flaky here).
    """
    g = _guard()
    fake_audit = AsyncMock()
    with patch.object(g, "logger") as mock_logger, patch.object(
        g, "PAPER_TRADING", False
    ), patch.object(g, "ALPACA_DATA_FEED", "iex"), patch.dict(
        os.environ, {"DEPLOYMENT_MODE": "LOCAL"}
    ), patch(
        "core.entitlement.resolve_entitlement", return_value=_BASIC
    ), patch(
        "config.force_paper_trading"
    ) as force_paper, patch(
        "core.hitl_gate.log_live_enablement_event", fake_audit
    ):
        g.assert_live_trading_config()  # must NOT raise

    force_paper.assert_called_once()
    fake_audit.assert_awaited_once()
    kwargs = fake_audit.await_args.kwargs
    assert kwargs["action"] == "disable"
    assert kwargs["strict"] is False
    assert kwargs["actor"].startswith("system")
    mock_logger.critical.assert_called_once()


def test_downgrade_audit_failure_never_crashes_boot(caplog):
    """A WORM-write failure during the downgrade must NOT crash the boot (best-effort audit).

    Paper is still forced — the safety action does not depend on the audit succeeding.
    """
    g = _guard()
    boom = AsyncMock(side_effect=RuntimeError("worm write failed"))
    with patch.object(g, "PAPER_TRADING", False), patch.object(
        g, "ALPACA_DATA_FEED", "iex"
    ), patch.dict(os.environ, {"DEPLOYMENT_MODE": "LOCAL"}), patch(
        "core.entitlement.resolve_entitlement", return_value=_BASIC
    ), patch(
        "config.force_paper_trading"
    ) as force_paper, patch(
        "core.hitl_gate.log_live_enablement_event", boom
    ):
        g.assert_live_trading_config()  # must NOT raise even though the audit write blew up

    force_paper.assert_called_once()


def test_valid_live_entitlement_still_boots_live_local():
    """(a) REGRESSION: valid live entitlement (PRO, allow_live=True) on LOCAL → boots live.

    No downgrade: force_paper_trading is NEVER called and the guard does not raise (LOCAL skips
    the SIP requirement after the tier gate passes).
    """
    g = _guard()
    with patch.object(g, "PAPER_TRADING", False), patch.object(
        g, "ALPACA_DATA_FEED", "iex"
    ), patch.dict(os.environ, {"DEPLOYMENT_MODE": "LOCAL"}), patch(
        "core.entitlement.resolve_entitlement", return_value=_PRO
    ), patch(
        "config.force_paper_trading"
    ) as force_paper:
        g.assert_live_trading_config()  # must NOT raise

    force_paper.assert_not_called()


def test_valid_live_entitlement_still_boots_live_cloud():
    """(a) REGRESSION: cloud live with SIP → boots live, never downgrades (allow_live=True)."""
    g = _guard()
    with patch.object(g, "PAPER_TRADING", False), patch.object(
        g, "ALPACA_DATA_FEED", "sip"
    ), patch.dict(os.environ, {"DEPLOYMENT_MODE": "CLOUD_RUN"}), patch(
        "config.force_paper_trading"
    ) as force_paper:
        g.assert_live_trading_config()  # must NOT raise (real resolve → PROFESSIONAL)

    force_paper.assert_not_called()


def test_pure_paper_boot_unchanged():
    """(c) REGRESSION: a pure paper boot is untouched — no downgrade, no audit, no raise."""
    g = _guard()
    with patch.object(g, "PAPER_TRADING", True), patch.dict(
        os.environ, {"DEPLOYMENT_MODE": "LOCAL"}
    ), patch("core.entitlement.resolve_entitlement", return_value=_BASIC), patch(
        "config.force_paper_trading"
    ) as force_paper:
        g.assert_live_trading_config()  # must NOT raise

    force_paper.assert_not_called()


def test_force_paper_trading_selects_paper_account():
    """config.force_paper_trading() flips PAPER_TRADING on and re-selects the PAPER account.

    A model_dump()-based update would carry the already-swapped LIVE key forward; force_paper_
    trading() re-reads the environment so the paper key / paper base-URL win. Restores the global
    singleton afterwards to keep the test session clean.
    """
    import config

    original_state = config.get_config()
    original_env = {
        k: os.environ.get(k)
        for k in (
            "PAPER_TRADING",
            "DEPLOYMENT_MODE",
            "ALPACA_API_KEY",
            "ALPACA_LIVE_API_KEY",
            "ALPACA_BASE_URL",
        )
    }
    try:
        os.environ.update(
            {
                "DEPLOYMENT_MODE": "LOCAL",
                "PAPER_TRADING": "false",
                "ALPACA_API_KEY": "PAPER-KEY-123",
                "ALPACA_LIVE_API_KEY": "LIVE-KEY-999",
            }
        )
        # Enter a "live" config state (paper key swapped to the live key).
        config._config_state = config.RuntimeConfigState()
        live_key = config.get_config().ALPACA_API_KEY
        assert live_key is not None and live_key.get_secret_value() == "LIVE-KEY-999"

        config.force_paper_trading(reason="unit-test lapse")

        cfg = config.get_config()
        assert cfg.PAPER_TRADING is True
        assert cfg.ALPACA_API_KEY.get_secret_value() == "PAPER-KEY-123"
        assert cfg.ALPACA_BASE_URL == "https://paper-api.alpaca.markets"
        # The engine's primary broker (_init_trading_clients) derives is_paper from config.BASE_URL
        # → __getattr__ → ALPACA_BASE_URL. It MUST resolve to the paper endpoint post-downgrade.
        assert config.BASE_URL == "https://paper-api.alpaca.markets"
    finally:
        config._config_state = original_state
        for k, v in original_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_config_oss_force_paper_trading_flips_globals():
    """Dual-edition parity: the flat OSS config (renamed to config.py in the shipped desktop —
    oss_make_snapshot.sh:273 / make-release.ps1:155) MUST also expose force_paper_trading, else
    the guard's config.force_paper_trading() call would AttributeError at the exact boot point we
    are trying to protect. Load config.oss.py in isolation and verify it flips to paper.
    """
    import importlib.util

    import config as config_full

    oss_path = os.path.join(os.path.dirname(config_full.__file__), "config.oss.py")
    spec = importlib.util.spec_from_file_location("config_oss_dg", oss_path)
    config_oss = importlib.util.module_from_spec(spec)
    # Load in the safe paper default (PAPER_TRADING unset → True) so the module-level Art-14
    # boot gate does not raise at import, then simulate a live state and downgrade.
    with patch.dict(
        os.environ,
        {"DEPLOYMENT_MODE": "LOCAL", "PAPER_TRADING": "true"},
    ):
        spec.loader.exec_module(config_oss)
    assert hasattr(
        config_oss, "force_paper_trading"
    ), "config.oss.py must expose force_paper_trading (desktop crash-site)"

    # Simulate the lapsed-token live state the guard would hit at boot.
    config_oss.PAPER_TRADING = False
    config_oss.ALPACA_API_KEY = "LIVE-KEY"
    config_oss.ALPACA_BASE_URL = "https://api.alpaca.markets"
    # LEGACY global that the engine's primary broker actually reads for is_paper
    # (api_routes.py::_init_trading_clients: `is_paper = "paper" in config.BASE_URL`). If the
    # downgrade misses this, the desktop broker stays on the LIVE endpoint (#1918 defeated).
    config_oss.BASE_URL = "https://api.alpaca.markets"

    _prev_env = {k: os.environ.get(k) for k in ("PAPER_TRADING", "ALPACA_BASE_URL")}
    try:
        config_oss.force_paper_trading(reason="unit-test lapse")

        assert config_oss.PAPER_TRADING is True
        assert config_oss.get_config().PAPER_TRADING is True
        assert (
            config_oss.ALPACA_API_KEY == config_oss.API_KEY
        )  # paper slot, never the live key
        assert config_oss.ALPACA_BASE_URL == "https://paper-api.alpaca.markets"
        assert config_oss.BASE_URL == "https://paper-api.alpaca.markets"
        assert os.environ["ALPACA_BASE_URL"] == "https://paper-api.alpaca.markets"
    finally:
        # force_paper_trading writes ALPACA_BASE_URL/PAPER_TRADING into the real process env.
        for k, v in _prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
