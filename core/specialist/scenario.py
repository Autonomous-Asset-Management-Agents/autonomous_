# core/specialist/scenario.py
# Rich-synthesis card (Direction A) — honest, non-directional scenario band.
"""Deterministic volatility scenario band for the synthesis card.

The old prototype card showed directional scenario percentages (Bear/Base/Bull
returns) that came from the per-symbol TFT quantile forecast. That forecast is
GATE-DEAD — 0/488 symbols survive FDR (the empirical anchor), so ``ML_PREDICTION``
stays OFF and the ``ml_*_return_pct`` fields are ``None``. Rather than fabricate a
directional edge that does not exist, this module derives an HONEST *volatility
band* from the per-symbol HAR-RV forward-vol model (``core/ml/vol_model.py``),
which IS a real, walk-forward-validated per-symbol signal.

The band is a spread, NOT a direction: base is 0.0% (no drift claim), low/high are
±σ over the 20-trading-day horizon. The card labels it as "range, not a forecast"
and states plainly that no directional edge survived FDR. Pure and LLM-free; a
missing forecast (too little history) → ``None`` → the card omits the row.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

from core.ml.vol_model import _HORIZON, _extract_close, forecast_forward_vol


def vol_scenario(
    bars: pd.DataFrame, horizon: int = _HORIZON
) -> Optional[Dict[str, Any]]:
    """Build the non-directional volatility band, or ``None``.

    ``forecast_forward_vol`` returns a daily realized-vol stdev (or ``None`` when
    there is too little history — <180 bars). The horizon sigma is
    ``daily_vol * sqrt(horizon) * 100`` (percent). Returns a dict with a zero
    base (no directional claim), symmetric ±sigma band, the sigma, and the
    scenario basis label; ``None`` when no forecast is available.
    """
    daily_vol = forecast_forward_vol(bars, horizon=horizon)
    if daily_vol is None or daily_vol <= 0:
        return None
    sigma_pct = float(daily_vol) * math.sqrt(horizon) * 100.0
    out: Dict[str, Any] = {
        "vol_band_base_pct": 0.0,
        "vol_band_low_pct": -sigma_pct,
        "vol_band_high_pct": sigma_pct,
        "vol_band_sigma_pct": sigma_pct,
        "scenario_basis": "realized_vol_har",
    }
    # OWN-MODEL price range + deterministic price stats (no LLM anywhere):
    # the HAR-RV sigma projected onto the last close gives a concrete expected
    # 20-day PRICE range; momentum/52w-distance are hard facts from the same
    # bars. All all-or-nothing optional — a malformed close series just omits
    # the keys and the card/prompt skip the rows (honest empty, never guessed).
    try:
        close = _extract_close(bars)
        if close is not None and len(close) > 0 and float(close[-1]) > 0:
            last = float(close[-1])
            out["last_close"] = last
            out["price_band_low"] = last * (1.0 - sigma_pct / 100.0)
            out["price_band_high"] = last * (1.0 + sigma_pct / 100.0)
            if len(close) > 21 and float(close[-21]) > 0:
                chg = (last / float(close[-21]) - 1.0) * 100.0
                # Split-guard: a >2.5x jump across the window is a corporate
                # action (split) in an unadjusted series, not a real move —
                # omit rather than display a -94% artifact (NFLX live find).
                if abs(chg) < 150.0:
                    out["chg_20d_pct"] = chg
            window = close[-252:] if len(close) >= 252 else close
            hi = float(max(window))
            if hi > 0 and hi / last < 2.5:
                out["dist_52w_high_pct"] = (last / hi - 1.0) * 100.0
    except Exception as exc:  # noqa: BLE001 — stats are optional, band stays valid
        logger.warning("Failed to derive price stats in vol_scenario: %s", exc)
    return out
