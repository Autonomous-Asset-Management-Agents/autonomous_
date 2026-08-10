"""LIVE-1 T1 (#1424): the pre-live SIP gate is edition-aware.

`assert_live_trading_config` enforces the MiFID II Art. 27 SIP/NBBO best-execution feed for live
trading. But Art. 27 binds investment firms executing **client** orders — the OSS desktop edition
(`DEPLOYMENT_MODE=LOCAL`) trades the operator's OWN capital, so Art. 27 is N/A and the SIP
requirement must NOT block it. The strict cloud/Enterprise (Fremdkapital) path is unchanged.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _guard():
    from core.engine import live_trading_guard

    return live_trading_guard


def test_paper_trading_is_noop():
    g = _guard()
    with patch.object(g, "PAPER_TRADING", True):
        g.assert_live_trading_config()  # must not raise


def test_oss_local_live_skips_sip_requirement():
    # OSS desktop own-account (DEPLOYMENT_MODE=LOCAL): Art. 27 N/A → live allowed without SIP.
    # GTM-1 (#1800): LOCAL live now ALSO requires a live-allowing tier. Inject a PRO
    # entitlement so this test isolates the SIP-skip behaviour it was written for; the
    # BASIC-blocks-live path is covered in test_entitlement_live_gate.py.
    from core.entitlement.tier import Entitlement, Tier

    pro = Entitlement(
        tier=Tier.PRO,
        agent_names=("DrawdownGuardAgent",),
        allow_live=True,
        backtest_months=None,
        xai_enabled=False,
        max_order_value=10000.0,
    )
    g = _guard()
    with patch.object(g, "PAPER_TRADING", False), patch.object(
        g, "ALPACA_DATA_FEED", "iex"
    ), patch.dict(os.environ, {"DEPLOYMENT_MODE": "LOCAL"}), patch(
        "core.entitlement.resolve_entitlement", return_value=pro
    ):
        g.assert_live_trading_config()  # must NOT raise


def test_cloud_live_without_sip_still_blocks():
    # Cloud/Enterprise (Fremdkapital): the SIP gate stays strict and fail-closed.
    g = _guard()
    with patch.object(g, "PAPER_TRADING", False), patch.object(
        g, "ALPACA_DATA_FEED", "iex"
    ), patch.dict(os.environ, {"DEPLOYMENT_MODE": "CLOUD_RUN"}):
        with pytest.raises(RuntimeError):
            g.assert_live_trading_config()


def test_cloud_live_with_sip_ok():
    g = _guard()
    with patch.object(g, "PAPER_TRADING", False), patch.object(
        g, "ALPACA_DATA_FEED", "sip"
    ), patch.dict(os.environ, {"DEPLOYMENT_MODE": "CLOUD_RUN"}):
        g.assert_live_trading_config()  # must not raise
