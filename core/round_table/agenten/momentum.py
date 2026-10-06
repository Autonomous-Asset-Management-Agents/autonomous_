# core/round_table/agenten/momentum.py
# #4085 (ARC-E6 G-6c): MomentumAgent samt Glaettungs- und Enthaltungs-Gate,
# Skala und Gewicht, unveraendert aus core/round_table/agents.py umgezogen.
# agents.py importiert jeden Namen zurueck (neuladetreu ueber _frisch).
# Die Warn-Drossel _MOMENTUM_ABSTAIN_WARNED liest der Agent zur Laufzeit ueber ``_ag``
# (Zugriffsregel, agenten/).
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

import config
from core.contracts.signal_candidate import AbstainReason, SignalCandidate
from core.round_table import agents as _ag
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
# 4. MomentumAgent (w:0.45) — Price Momentum (Close-Open)/Open
# ---------------------------------------------------------------------------


# TRD-8 T6 (#3004): the scale that maps 12-1M momentum onto the [0,1] vote. Pulled out
# of the formula UNCHANGED in value — naming it makes the number reviewable on its own
# and lets stage 3 revisit it without touching the mapping again. It is NOT the defect:
# the defect is the hard clamp around it (see _momentum_score_smooth below).
_MOMENTUM_SCORE_SCALE = 0.60


def _momentum_score_smooth() -> bool:
    """#3004 (TRD-8 T6): config-gated monotone squashing for the MomentumAgent score.

    ``clamp(0.5 + momentum_12_1 / 0.60)`` reaches the ceiling as soon as 12-1M momentum
    passes +30 %. Measured over 26,744 logged votes, **13,033 (48.7 %) carry exactly
    1.00** — reconstructed raw values there have a median of +51.8 % and a maximum of
    +2,879 %. A value identical across half the universe stops being a vote inside a
    weighted mean (``consensus.py:295-306``): it becomes a constant +0.1285 lift in
    front of the fixed 0.65 buy threshold (``runner.py:903-915``) and carries 17.0 % of
    logged buys across on its own.

    ON  → ``0.5 + 0.5*tanh(m / _MOMENTUM_SCORE_SCALE)``: strictly monotone (no ties),
    same [0,1] range, same 0.5 neutral point, and the absolute reading the consumption
    path relies on is preserved — unlike a cross-section z-score, which centres on 0.5
    by construction and collapses when one outlier inflates the day's dispersion
    (measured 17.08.: one +3,468 % name drove every score to ~0.5).

    OFF (default) ⇒ byte-identical to today. Mirrors ``_momentum_fallback_abstain``:
    the os.environ read lives in config.py / config.oss.py (CODING_POLICY §2.10), not
    in the finance-core; a missing or invalid value falls back to OFF.
    """
    try:
        cfg = config.get_config()
        return bool(getattr(cfg, "MOMENTUM_SCORE_SMOOTH_ENABLED", False))
    except (TypeError, ValueError):
        return False


def _momentum_fallback_abstain() -> bool:
    """#1968 (RT-BUG-2): config-gated ABSTAIN for the MomentumAgent one-bar fallback.

    The one-bar fallback (close-open)/open is direction-identical to RegimeDetection's
    close/open ratio — with <40 daily closes (12-1M path unavailable) the Momentum vote
    is a silent ECHO of the regime signal that also conditions its weight via the
    bearish-regime halving in consensus.py (self-referential coupling). ON → the
    fallback abstains (score 0.5, weight 0.0 — excluded from consensus, the
    VIXAware/SpecialistAlpha abstention pattern) instead of echoing. Default ON ⇒
    byte-identical to today. Mirrors _specialist_alpha_weight/_drawdown_conditioner:
    the os.environ read lives in config.py / config.oss.py (CODING_POLICY §2.10),
    not in the finance-core; invalid values fall back to False (echo retained).
    """
    try:
        return bool(
            getattr(config.get_config(), "MOMENTUM_FALLBACK_ABSTAIN_ENABLED", False)
        )
    except (TypeError, ValueError):
        return False


# Die Warn-Drossel _MOMENTUM_ABSTAIN_WARNED bleibt in agents.py (geteilter Zustand,
# Tests leeren agents._MOMENTUM_ABSTAIN_WARNED) und wird zur Laufzeit ueber _ag gelesen.

# Resolved once at import, immediately before the class that consumes it.
_MOMENTUM_AGENT_WEIGHT = _consensus_weight("MOMENTUM_AGENT_WEIGHT", 0.45)


class MomentumAgent(VotingAgent):
    """Relative Stärke über zwölf Monate — die einzige Langfrist-Stimme des Gremiums.

    Primärpfad: 12-1-Monats-Momentum ``(closes[-20] / closes[-252]) - 1``. Der jüngste
    Monat bleibt ausgespart, weil dort die kurzfristige Umkehr sitzt (Marktstandard,
    vgl. MSCI Momentum §2.2). Der Beitrag muss **abgestuft** sein: der Konsens ist ein
    gewichteter Mittelwert gegen eine feste Schwelle, verwertet also die Höhe des
    Scores — ein für viele Titel identischer Wert wirkt dort als konstanter Aufschlag
    statt als Votum (#3004, siehe ``_momentum_score_smooth``).

    Fallback bei <40 Tages-Closes: Enthaltung (Default) oder — Flag AUS — ein
    Ein-Bar-Score, der richtungsgleich mit RegimeDetection ist (#1968).
    """

    default_weight: float = (
        _MOMENTUM_AGENT_WEIGHT  # #2815: config-gated, unset == historical literal
    )
    min_weight: float = 0.00
    max_weight: float = 1.50

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        # #3154: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("MOMENTUM_AGENT_ENABLED"):
            return _disabled_abstain("MomentumAgent", state["symbol"])

        ohlc = state.get("ohlc") or {}
        open_price = ohlc.get("open")
        close_price = ohlc.get("close")
        symbol = state["symbol"]

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

                # Fetch 365 calendar days of daily data
                df = await asyncio.to_thread(
                    data_provider.get_data, symbol, current_time, 365
                )

                closes = None
                if df is not None and not df.empty:
                    if "Close" in df.columns:
                        closes = df["Close"].dropna()
                    elif "close" in df.columns:
                        closes = df["close"].dropna()

                if closes is not None and len(closes) >= 40:
                    p_end = float(closes.iloc[-20])  # 1 month ago (20 trading days)
                    idx_start = -252 if len(closes) >= 252 else 0
                    p_start = float(closes.iloc[idx_start])

                    if p_start > 0:
                        momentum_12_1 = (p_end - p_start) / p_start
                        if _momentum_score_smooth():
                            # #3004: monotone, tie-free. tanh keeps the result inside
                            # (0,1) for every finite input, so _clamp is a formality
                            # here — kept so the contract holds for both branches.
                            score = self._clamp(
                                0.5
                                + 0.5 * math.tanh(momentum_12_1 / _MOMENTUM_SCORE_SCALE)
                            )
                        else:
                            score = self._clamp(
                                0.5 + momentum_12_1 / _MOMENTUM_SCORE_SCALE
                            )
                        reasoning = (
                            f"Momentum over the past year (excluding the most "
                            f"recent month): {momentum_12_1:+.1%} "
                            f"(price {p_start:.2f} → {p_end:.2f}, score {score:.3f}). "
                            f"[src: 12-month price history]"
                        )
                        return SignalCandidate(
                            agent_name="MomentumAgent",
                            symbol=symbol,
                            score=score,
                            weight=self.weight,
                            abstain_reason=(
                                AbstainReason.NO_DATA if score is None else None
                            ),
                            reasoning=reasoning,
                        )
        except Exception as exc:
            logger.warning(
                "MomentumAgent Fallback auf 1-Bar-Spread wegen Fehler: %s", exc
            )

        # #1968 (RT-BUG-2): every path that reaches this point lost the 12-1M primary
        # signal (<40 daily closes, no data_provider, or a fetch error). The one-bar
        # fallback below duplicates RegimeDetection's direction — flag ON substitutes
        # an honest ABSTAIN instead of that regime echo.
        if _momentum_fallback_abstain():
            # Audit follow-up: once-per-symbol-per-session WARNING throttle (see
            # _MOMENTUM_ABSTAIN_WARNED in agents.py) — repeats for the same symbol → DEBUG.
            if symbol in _ag._MOMENTUM_ABSTAIN_WARNED:
                _log = logger.debug
            else:
                _ag._MOMENTUM_ABSTAIN_WARNED.add(symbol)
                _log = logger.warning
            _log(
                "MomentumAgent: <40 Tages-Closes für symbol=%s → 12-1M-Pfad nicht "
                "verfügbar; ABSTAIN (weight=0, aus Konsens ausgeschlossen) statt "
                "Regime-Echo (RT-BUG-2 #1968).",
                symbol,
            )
            return SignalCandidate(
                agent_name="MomentumAgent",
                symbol=symbol,
                score=None,
                weight=0.0,
                abstain_reason=AbstainReason.NO_DATA,  # ← EXCLUDED: Pydantic gt=0.0 filtert den Vote aus active_votes
                reasoning=(
                    "Momentum: EXCLUDED — insufficient history for 12-1M momentum "
                    "(one-bar fallback would duplicate RegimeDetection)"
                ),
            )

        if open_price is None or open_price <= 0:
            score = None
            momentum_pct = 0.0
        else:
            close = close_price if close_price is not None else open_price
            momentum_pct = (close - open_price) / open_price
            score = self._clamp(0.5 + momentum_pct * 5.0)

        reasoning = (
            f"Today's move: {momentum_pct:+.1%} (score {score:.3f}). Full price "
            f"history was unavailable, so this is a one-day move only — weak "
            f"evidence on its own. "
            f"[src: today's price bar]"
        )
        return SignalCandidate(
            agent_name="MomentumAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            abstain_reason=AbstainReason.NO_DATA if score is None else None,
            reasoning=reasoning,
        )
