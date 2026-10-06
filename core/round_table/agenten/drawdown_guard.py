# core/round_table/agenten/drawdown_guard.py
# #4085 (ARC-E6 G-6c): DrawdownGuardAgent samt Konditionierer, Veto-Gate,
# Fensterlaenge und Gewicht, unveraendert aus core/round_table/agents.py umgezogen.
# agents.py importiert jeden Namen zurueck (neuladetreu ueber _frisch).
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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


def _drawdown_conditioner():
    """#1951 / plan #2205 — (enabled, severe_veto_threshold) for the DrawdownGuard.

    When enabled, the guard stops hard-vetoing a routine pullback (>0.07 / >0.05) and
    only hard-blocks a genuinely SEVERE drawdown (> severe_threshold); the moderate band
    is handled downstream as a conviction dampener (runner._score_to_signal). Default OFF
    ⇒ the old 0.07/0.05 hard veto (byte-identical). The os.environ read lives in config.py /
    config.oss.py (CODING_POLICY §2.10); invalid values fall back to disabled.
    """
    try:
        cfg = config.get_config()
        enabled = bool(getattr(cfg, "DRAWDOWN_GUARD_CONDITIONER_ENABLED", False))
        severe = float(getattr(cfg, "DRAWDOWN_SEVERE_VETO_THRESHOLD", 0.25))
        return enabled, severe
    except (TypeError, ValueError):
        return False, 0.25


def _drawdown_guard_veto_enabled() -> bool:
    """Master gate for the DrawdownGuard HARD-VETO of new BUYs (both the 30d-window
    and the 1-bar-fallback paths). Default True ⇒ shipped behaviour (byte-identical);
    False ⇒ the guard still votes its soft score but never sets ``vetoed``. Measured
    (16y S&P, correctly-timed, Alpaca cost, docs/rtr/drawdown-guard-removal/): the
    per-name ">7% below 30d high" veto neither reduces portfolio max-drawdown (the
    always-on VIX sizer already halves it) nor lifts Sharpe, and it costs return by
    vetoing the mean-reverting pullback names that then out-perform. The os.environ
    read lives in config.py / config.oss.py (CODING_POLICY §2.10)."""
    try:
        return bool(getattr(config.get_config(), "DRAWDOWN_GUARD_VETO_ENABLED", True))
    except (TypeError, ValueError):
        return True


# ---------------------------------------------------------------------------
# 1. DrawdownGuardAgent (w:0.60) — Max Drawdown aus OHLC
# ---------------------------------------------------------------------------

# ADR-R14: DrawdownGuard lookback window = last 30 TRADING days (bars).
# Basis: #2584 (P0) field audit — data_provider.get_data(days=30) treats `days`
# as a MINIMUM depth, not a slice: the fetch stores days+200 calendar days and
# the disk cache serves ANY deeper frame ("Deeper history than requested is
# fine", data_provider.py:430). `closes.max()` over that uncut frame was a
# multi-year-peak check (SNDK "30-day high 2335.46, now 1250" = -46.5% phantom
# drawdown; TYL 613.11/321.99; INTU 807.10/304.75; CRWD -76.8%) → a veto wall
# that blocked effectively every recovered BUY (MiFID II Art. 25: the audit
# reasoning must describe the check actually executed).
# Window definition: the LAST 30 bars of the provider frame — daily bars ==
# trading days, index ascending, already filtered to index <= current_time by
# get_data. A shorter frame (minimal-depth cache) uses all its bars: a strict
# SUBSET of the last 30 trading days, never more (>=5-bar floor below).
# The provider request deliberately STAYS get_data(sym, t, 30) — #2389 shares
# that exact cache key with the compute_features node (graph.py:
# _FEATURE_WINDOW_DAYS) — and get_data's min-depth semantics stay unchanged
# (~20 call sites rely on them, e.g. torch_model days+200, sim/data_client
# 100_000). Annual review.
DRAWDOWN_WINDOW_TRADING_DAYS = 30


# Resolved once at import, immediately before the classes that consume them (mirrors
# _SPECIALIST_ALPHA_WEIGHT / _RL_CONFIDENCE_WEIGHT, since #4084 in agenten/).
# #3084: these two weights are VALIDATOR TOKENS, not directional votes — both
# agents are excluded from the consensus mean by name (consensus.py
# NON_DIRECTIONAL_AGENTS); DrawdownGuard acts via VETO, RegimeDetection via
# weight-coupling. The value only keeps their VoteResult validator-compatible
# (weight gt=0, so the agent-veto path sees the vote). Audit records therefore
# stamp them with weight 0.0 + role (runner._serialize_votes) — do NOT read
# 0.60/0.50 as a share of the consensus (#1993 made exactly that mistake).
_DRAWDOWN_GUARD_WEIGHT = _consensus_weight("DRAWDOWN_GUARD_WEIGHT", 0.60)


class DrawdownGuardAgent(VotingAgent):
    """
    Bewertet das Drawdown-Risiko anhand des OHLC-Kanals.
    score = 1 - normalized_drawdown
    Hoher Drawdown (H-L)/H > 0.05 → niedrigerer Score.
    """

    default_weight: float = (
        _DRAWDOWN_GUARD_WEIGHT  # #2815: config-gated, unset == historical literal
    )
    min_weight: float = 0.20
    max_weight: float = 2.00

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        # #3154 Rev. 3: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("DRAWDOWN_GUARD_AGENT_ENABLED"):
            return _disabled_abstain("DrawdownGuardAgent", state["symbol"])

        ohlc = state["ohlc"]
        high = ohlc.get("high")
        low = ohlc.get("low")
        close = ohlc.get("close")
        symbol = state["symbol"]

        drawdown = 0.0
        vetoed = False
        score = None
        reasoning = ""
        used_fallback = True
        # #2980: the vote weight is normally the agent's configured weight; on
        # invalid OHLC it drops to 0.0 (ABSTAIN — the vote is excluded from the
        # consensus) instead of a confident full-weight neutral 0.5.
        vote_weight = self.weight

        try:
            import asyncio
            from datetime import datetime, timezone

            from core.agent_registry import get_global_registry

            registry = get_global_registry()
            active = registry.get_active() if registry else None
            data_provider = getattr(active, "data_provider", None) if active else None

            if data_provider is not None:
                time_str = state.get("current_time", "")
                try:
                    current_time = datetime.fromisoformat(time_str)
                except Exception:
                    from core.composition.root import CompositionRoot

                    current_time = CompositionRoot.get_instance().clock_port.now()

                df = await asyncio.to_thread(
                    data_provider.get_data, symbol, current_time, 30
                )

                closes = None
                if df is not None and not df.empty:
                    if "Close" in df.columns:
                        closes = df["Close"].dropna()
                    elif "close" in df.columns:
                        closes = df["close"].dropna()

                if closes is not None and len(closes) >= 5:
                    # #2584 / ADR-R14: the frame is DEEPER than 30 days (get_data
                    # min-depth semantics) — slice to the last 30 TRADING bars,
                    # else .max() is a multi-year-peak check. sort_index() pins
                    # "last" even if a provider ever returns unordered bars.
                    window = closes.sort_index().tail(DRAWDOWN_WINDOW_TRADING_DAYS)
                    window_bars = len(window)
                    peak = float(window.max())
                    current = float(close)
                    if peak > 0:
                        drawdown = (peak - current) / peak
                        used_fallback = False

                        # #1951/#2205 three-regime: conditioner ON → hard-veto only above
                        # the SEVERE threshold (moderate band dampens downstream); OFF → 0.07.
                        _cond_on, _severe = _drawdown_conditioner()
                        if _drawdown_guard_veto_enabled() and drawdown > (
                            _severe if _cond_on else 0.07
                        ):
                            vetoed = True

                        score = self._clamp(1.0 - (drawdown * 5.0))
                        reasoning = (
                            f"Price is {drawdown:.1%} below its {window_bars}-trading-day high — "
                            f"{'risk limit breached, this vote blocks new buying' if vetoed else 'within limits'} "
                            f"(peak {peak:.2f}, now {current:.2f}, score {score:.3f}). "
                            f"[src: {window_bars}-trading-day price history]"
                        )
        except Exception as exc:
            logger.warning(
                "DrawdownGuardAgent Fallback auf Single-Bar wegen Fehler: %s", exc
            )

        abstain_reason = None

        if used_fallback:
            if high is None or low is None or high <= 0:
                # #2980 (§5.6): a broken price feed (high <= 0) must REMOVE the
                # guard's vote from the consensus, not inject a confident
                # full-weight neutral 0.5 that dilutes it toward BUY. Abstain
                # (weight 0.0) — a single malformed bar is a data-quality event,
                # not evidence of a real drawdown, so we degrade gracefully while
                # the other agents still vote (abstain, not veto — see ADR-R09).
                drawdown = 0.0
                score = None  # SignalCandidate explicitly needs None for score if abstaining
                vote_weight = 0.0
                abstain_reason = AbstainReason.NO_DATA
                high_str = f"{high:.4f}" if high is not None else "None"
                logger.warning(
                    "DrawdownGuardAgent: invalid OHLC for %s (high=%s <= 0) — "
                    "ABSTAIN (weight 0.0), vote excluded from consensus (§5.6).",
                    symbol,
                    high_str,
                )
                reasoning = (
                    "Today's price data looks invalid — "
                    "risk guard ABSTAINS (vote excluded, not a neutral 0.5)."
                )
            else:
                drawdown = (high - low) / high
                # #1951/#2205 three-regime (1-bar fallback): conditioner ON → veto only
                # above the SEVERE threshold; OFF → 0.05 (byte-identical).
                _cond_on, _severe = _drawdown_conditioner()
                if _drawdown_guard_veto_enabled() and drawdown > (
                    _severe if _cond_on else 0.05
                ):
                    vetoed = True
                score = self._clamp(1.0 - (drawdown * 5.0))
                reasoning = (
                    f"Today's price swing spans {drawdown:.1%} (high {high:.2f}, low {low:.2f}) — "
                    f"{'risk limit breached, this vote blocks new buying' if vetoed else 'within limits'} "
                    f"(score {score:.3f}; 30-day history unavailable, judged on today only). "
                    f"[src: today's high/low]"
                )

        return SignalCandidate(
            agent_name="DrawdownGuardAgent",
            symbol=symbol,
            score=score,
            weight=vote_weight,
            reasoning=reasoning,
            abstain_reason=abstain_reason,
            vetoed=vetoed,
        )
