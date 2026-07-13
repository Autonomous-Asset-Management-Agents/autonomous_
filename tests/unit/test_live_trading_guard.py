# tests/unit/test_live_trading_guard.py
# Pre-live gate: enforce SIP data feed when going live with real capital.
#
# Gherkin:
#   Given: PAPER_TRADING=False AND ALPACA_DATA_FEED != "sip"
#   When:  assert_live_trading_config() is called at engine startup
#   Then:  RuntimeError is raised — hard block, bot cannot start
#
#   Given: PAPER_TRADING=False AND ALPACA_DATA_FEED = "sip"
#   When:  assert_live_trading_config() is called
#   Then:  No exception — bot starts normally
#
#   Given: PAPER_TRADING=True (paper trading)
#   When:  assert_live_trading_config() is called
#   Then:  No exception regardless of feed — MiFID II Art.27 ≠ paper trading

from __future__ import annotations

from unittest.mock import patch

import allure
import pytest


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestLiveTradingGuard:
    def test_blocks_live_trading_with_iex_feed(self):
        """Going live with IEX feed must raise — not NBBO, violates MiFID II Art. 27."""
        from core.engine.live_trading_guard import assert_live_trading_config

        with (
            patch("core.engine.live_trading_guard.PAPER_TRADING", False),
            patch("core.engine.live_trading_guard.ALPACA_DATA_FEED", "iex"),
        ):
            with pytest.raises(RuntimeError, match="SIP"):
                assert_live_trading_config()

    def test_blocks_live_trading_with_unknown_feed(self):
        """Any non-SIP feed blocks live startup."""
        from core.engine.live_trading_guard import assert_live_trading_config

        with (
            patch("core.engine.live_trading_guard.PAPER_TRADING", False),
            patch("core.engine.live_trading_guard.ALPACA_DATA_FEED", "iex_plus"),
        ):
            with pytest.raises(RuntimeError):
                assert_live_trading_config()

    def test_allows_live_trading_with_sip_feed(self):
        """SIP feed + live trading = compliant. No exception."""
        from core.engine.live_trading_guard import assert_live_trading_config

        with (
            patch("core.engine.live_trading_guard.PAPER_TRADING", False),
            patch("core.engine.live_trading_guard.ALPACA_DATA_FEED", "sip"),
        ):
            assert_live_trading_config()  # must not raise

    def test_paper_trading_allows_iex(self):
        """Paper trading: IEX is fine — MiFID II Art. 27 does not apply."""
        from core.engine.live_trading_guard import assert_live_trading_config

        with (
            patch("core.engine.live_trading_guard.PAPER_TRADING", True),
            patch("core.engine.live_trading_guard.ALPACA_DATA_FEED", "iex"),
        ):
            assert_live_trading_config()  # must not raise

    def test_paper_trading_allows_sip(self):
        """Paper trading with SIP is also fine (upgraded account on paper)."""
        from core.engine.live_trading_guard import assert_live_trading_config

        with (
            patch("core.engine.live_trading_guard.PAPER_TRADING", True),
            patch("core.engine.live_trading_guard.ALPACA_DATA_FEED", "sip"),
        ):
            assert_live_trading_config()  # must not raise

    def test_error_message_names_required_action(self):
        """Error message must tell the operator exactly what to fix."""
        from core.engine.live_trading_guard import assert_live_trading_config

        with (
            patch("core.engine.live_trading_guard.PAPER_TRADING", False),
            patch("core.engine.live_trading_guard.ALPACA_DATA_FEED", "iex"),
        ):
            with pytest.raises(RuntimeError, match="ALPACA_DATA_FEED=sip"):
                assert_live_trading_config()
