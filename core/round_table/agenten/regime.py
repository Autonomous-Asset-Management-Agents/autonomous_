# core/round_table/agenten/regime.py
# #4085 (ARC-E6 G-6c): RegimeDetectionAgent samt Konditionierer-Konfiguration,
# VIX-Stellvertretern je Regime-Label und Gewicht, unveraendert aus core/round_table/agents.py umgezogen.
# agents.py importiert jeden Namen zurueck (neuladetreu ueber _frisch).
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Optional, Tuple

import config
from core.contracts.signal_candidate import AbstainReason, SignalCandidate
from core.round_table.agenten._basis import (
    _agent_enabled,
    _consensus_weight,
    _disabled_abstain,
)
from core.round_table.base_agent import VotingAgent

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

# Bewusst der Loggername von agents.py (wie _basis.py): Die Warnungen erscheinen nach dem
# Umzug unter demselben Namen wie vorher (verhaltensneutral).
logger = logging.getLogger("core.round_table.agents")


# ---------------------------------------------------------------------------
# 3. RegimeDetectionAgent (w:0.50) — Close/Open Verhältnis als Proxy
#    (#1949: flag ON → real MarketRegimeModel VIX/regime conditioner instead)
# ---------------------------------------------------------------------------


def _regime_conditioner_cfg() -> Tuple[bool, float]:
    """#1949 (RTR-2): (enabled, vix_midpoint) for the regime conditioner.

    Default ON (``config.py``: ``REGIME_CONDITIONER_ENABLED`` default ``True``):
    RegimeDetectionAgent scores the continuous market-wide VIX sigmoid around
    REGIME_VIX_MIDPOINT (25.0) from the state's "vix"/"regime" channels instead of the
    O/C ratio, removing the structural Momentum double count. OFF falls back to the
    one-bar close/open proxy. NB: this market-VIX sigmoid matches only VIXAwareRiskAgent's
    FALLBACK — VIXAware's own DEFAULT is the per-symbol implied-vol path since #3044
    (``VIXAWARE_IMPLIED_VOL_ENABLED`` default True), so the two are NO longer bit-identical
    at the shipped default. Mirrors
    _momentum_fallback_abstain/_drawdown_conditioner: the os.environ read lives in
    config.py / config.oss.py (CODING_POLICY §2.10), not in the finance-core;
    invalid values fall back to OFF.
    """
    try:
        cfg = config.get_config()
        enabled = bool(getattr(cfg, "REGIME_CONDITIONER_ENABLED", False))
        midpoint = float(getattr(cfg, "REGIME_VIX_MIDPOINT", 25.0))
        return enabled, midpoint
    except (TypeError, ValueError):
        return False, 25.0


# #1949: band-representative VIX level per MarketRegimeModel label (thresholds
# {low:15, normal:25, high:35} in core/market_regime.py). Fallback ONLY when the
# numeric VIX channel is missing/invalid — mapped through the SAME sigmoid so the
# risk-appetite scale stays consistent. "Ranging" is also the model's no-data
# default (value=None), which lands mildly risk-on by design (fail-open, plan §12.2).
_REGIME_LABEL_VIX = {
    "Low Volatility": 10.0,
    "Ranging": 20.0,
    "Trending": 30.0,
    "High Volatility": 40.0,
}

# Resolved once at import, immediately before the class that consumes it. #3084: a
# VALIDATOR TOKEN, not a directional vote — see _DRAWDOWN_GUARD_WEIGHT in
# agenten/drawdown_guard.py for the full rationale (do NOT read 0.50 as a consensus share).
_REGIME_DETECTION_WEIGHT = _consensus_weight("REGIME_DETECTION_WEIGHT", 0.50)


class RegimeDetectionAgent(VotingAgent):
    """
    Detektiert Marktregime anhand des Close/Open-Verhältnisses.
    Bullisch (>1.02): score > 0.6 | Bärisch (<0.98): score < 0.4 | Neutral: ~0.5

    #1949 (REGIME_CONDITIONER_ENABLED, default OFF): flag ON replaces the one-bar
    O/C proxy with the REAL market regime (MarketRegimeModel VIX / regime label via
    the state seam) — a market-wide CONDITIONER score, independent of the symbol's
    bar. Missing vix+regime → neutral abstain (score 0.5, weight 0.0, WARNING §5.6).
    """

    default_weight: float = (
        _REGIME_DETECTION_WEIGHT  # #2815: config-gated, unset == historical literal
    )
    min_weight: float = 0.15
    max_weight: float = 1.50

    def _conditioner_vote(
        self, state: "SymbolEvalState", midpoint: float
    ) -> SignalCandidate:
        """#1949 flag-ON path: score = clamp(1/(1+exp((vix - midpoint)/10))).

        Same sigmoid form as VIXAwareRiskAgent (one risk-appetite scale): low VIX →
        high score (risk-on), crisis VIX → low score (risk-off). Source priority:
        numeric state["vix"] → state["regime"] label (band-representative VIX) →
        neutral ABSTAIN (fail-open: no data, no conditioning).
        """
        symbol = state["symbol"]

        vix: Optional[float] = None
        try:
            raw = state.get("vix")
            if raw is not None:
                vix = float(raw)
        except (TypeError, ValueError):
            vix = None

        source = None
        if vix is not None and vix > 0:
            source = f"[src: VIX {vix:.1f}]"
        else:
            label = state.get("regime")
            rep = _REGIME_LABEL_VIX.get(str(label).strip()) if label else None
            if rep is not None:
                vix = rep
                source = f"[src: regime label '{label}' ≈ VIX {rep:.0f}]"

        if source is None:
            # §5.6: fallback substitution at WARNING, never DEBUG. Fail-open:
            # weight 0.0 → excluded from consensus → NO conditioning this cycle.
            logger.warning(
                "RegimeDetectionAgent: neither vix nor regime in state for %s — "
                "neutral ABSTAIN (weight 0, no conditioning). Is the monitor loop "
                "publishing current_market_data?",
                symbol,
            )
            return SignalCandidate(
                agent_name="RegimeDetectionAgent",
                symbol=symbol,
                score=None,
                weight=0.0,
                abstain_reason=AbstainReason.NO_DATA,
                reasoning="EXCLUDED — no VIX/regime data available right now",
            )

        score = self._clamp(1.0 / (1.0 + math.exp((vix - midpoint) / 10.0)))
        reasoning = (
            f"Market-wide regime CONDITIONER — this vote only scales how much "
            f"weight the directional voices get, it is not a call on this stock "
            f"(risk appetite {score:.3f}, midpoint {midpoint:.0f}). {source}"
        )
        return SignalCandidate(
            agent_name="RegimeDetectionAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            abstain_reason=AbstainReason.NO_DATA if score is None else None,
            reasoning=reasoning,
        )

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        # #3154 Rev. 3: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("REGIME_DETECTION_AGENT_ENABLED"):
            return _disabled_abstain("RegimeDetectionAgent", state["symbol"])

        # #1949: flag ON (default) -> real regime conditioner; OFF -> the exact
        # one-bar O/C proxy below (byte-identical dark ship).
        _enabled, _midpoint = _regime_conditioner_cfg()
        if _enabled:
            return self._conditioner_vote(state, _midpoint)

        ohlc = state.get("ohlc")
        if not ohlc or "open" not in ohlc or "close" not in ohlc:
            return SignalCandidate(
                agent_name="RegimeDetectionAgent",
                symbol=state["symbol"],
                score=None,
                weight=0.0,
                abstain_reason=AbstainReason.NO_DATA,
                reasoning=(
                    "RegimeDetectionAgent: missing ohlc open/close data → abstention"
                ),
            )
        open_price = ohlc["open"]
        close_price = ohlc["close"]
        symbol = state["symbol"]

        if open_price <= 0:
            score = 0.5
            regime = "unknown"
        else:
            ratio = close_price / open_price  # >1 bullisch, <1 bärisch
            # Sigmoid-artiger Score: ratio=1.05 → ~0.75, ratio=0.95 → ~0.25
            score = self._clamp(0.5 + (ratio - 1.0) * 5.0)
            if ratio > 1.02:
                regime = "bullish"
            elif ratio < 0.98:
                regime = "bearish"
            else:
                regime = "neutral"

        reasoning = (
            f"Market regime today: {regime} — this vote only adjusts how much "
            f"weight the other voices get, it is not a call on this stock "
            f"(open {open_price:.2f} → close {close_price:.2f}, score {score:.3f}). "
            f"[src: today's open/close]"
        )
        return SignalCandidate(
            agent_name="RegimeDetectionAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            abstain_reason=AbstainReason.NO_DATA if score is None else None,
            reasoning=reasoning,
        )
