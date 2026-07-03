# core/engine/live_trading_guard.py
# ML-1 Pre-Live Gate — SIP data feed compliance check.
#
# MiFID II Article 27 best-execution applies only when executing real client
# orders with actual capital. Paper trading is out of scope.
#
# When PAPER_TRADING=False, the bot MUST use the SIP consolidated tape (NBBO)
# to satisfy best-execution evidence requirements. IEX is a single exchange
# and does not constitute NBBO.
#
# Call assert_live_trading_config() once at engine startup. It is a no-op
# during paper trading and a hard RuntimeError block for live trading without
# SIP.
#
# To go live:
#   1. Upgrade Alpaca account to Algo Trader Plus ($99/mo) OR configure
#      Polygon.io SIP tier (cheaper alternative — check pricing at go-live).
#   2. Set ALPACA_DATA_FEED=sip in Cloud Run env vars.
#   3. Set PAPER_TRADING=False in Cloud Run env vars.

from __future__ import annotations

import logging
import os

from config import ALPACA_DATA_FEED, PAPER_TRADING

logger = logging.getLogger(__name__)


def assert_live_trading_config() -> None:
    """Raise RuntimeError if going live without SIP data feed.

    No-op during paper trading (MiFID II Art. 27 does not apply).
    Hard block for live trading with non-NBBO feed.
    """
    if PAPER_TRADING:
        logger.debug(
            "[LiveGuard] Paper trading active — data feed check skipped (MiFID II Art. 27 N/A)."
        )
        return

    # LIVE-1 T1 (#1424): MiFID II Art. 27 best-execution binds investment firms executing CLIENT
    # orders. The OSS desktop edition (DEPLOYMENT_MODE=LOCAL) trades the operator's OWN capital on
    # their own responsibility — Art. 27 is N/A — so the SIP consolidated-tape requirement does
    # NOT apply. The strict cloud/Enterprise (Fremdkapital) path below stays byte-identical.
    if os.environ.get("DEPLOYMENT_MODE", "").upper() == "LOCAL":
        logger.info(
            "[LiveGuard] OSS own-account edition (DEPLOYMENT_MODE=LOCAL) — MiFID II Art. 27 SIP "
            "requirement N/A (own capital, not client orders); live data-feed check skipped."
        )
        return

    if ALPACA_DATA_FEED != "sip":
        raise RuntimeError(
            f"[LiveGuard] BLOCKED: PAPER_TRADING=False but ALPACA_DATA_FEED={ALPACA_DATA_FEED!r}. "
            "Live trading requires the consolidated SIP tape for MiFID II Art. 27 best-execution "
            "evidence. "
            "Fix: set ALPACA_DATA_FEED=sip in Cloud Run env vars after upgrading to "
            "Alpaca Algo Trader Plus ($99/mo) or Polygon.io SIP tier. "
            "See docs/1_architecture_and_adr/ADR-D01-institutional-data-sources.md."
        )

    logger.info(
        "[LiveGuard] Live trading config OK — ALPACA_DATA_FEED=sip, MiFID II Art. 27 compliant."
    )
