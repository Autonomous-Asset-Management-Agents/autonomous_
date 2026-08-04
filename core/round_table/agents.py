# core/round_table/agents.py
# Epic 2.5 — Round Table V2: spezialisierte Voting-Agents
#
# Alle Agents:
#   - Vollständig async (kein blocking I/O in vote())
#   - Arbeiten auf SymbolEvalState OHLC-Skalaren
#   - Produzieren score ∈ [0.0, 1.0] mit MiFID-Audit-Reasoning
#
# Agent-Gewichte (vom Architekten bestätigt):
#   DrawdownGuard:0.60 | SpecialistAlpha:0.55 | RegimeDetection:0.50
#   Momentum:0.45 | VIXAware:0.45 | LSTM:0.40 | RL:0.40
#   NewsSentiment:0.35
# (#1951: PatternRecognitionAgent gelöscht — dormant, keine Walk-Forward-Evidenz)
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, Tuple

import config
from core.round_table.base_agent import VoteResult, VotingAgent

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

logger = logging.getLogger(__name__)


def _specialist_alpha_weight() -> float:
    """#1346: config-gated vote weight for SpecialistAlphaAgent.

    Default 0.0 keeps the specialist DORMANT (byte-identical to today — its weight is
    clamped to 0 and it is excluded from consensus). Set SPECIALIST_ALPHA_WEIGHT
    (e.g. 0.55), once SPECIALIST_REGISTRY_ENABLED=ON and the #76 shadow gate clears,
    to give it a real weighted vote. BORA: one code path, config-switched; cloud
    (weight 0.0) unchanged. The os.environ read lives in config.py/config.oss.py
    (CODING_POLICY §2.10), not in the finance-core. Invalid values fall back to dormant.
    """
    try:
        return max(
            0.0, float(getattr(config.get_config(), "SPECIALIST_ALPHA_WEIGHT", 0.0))
        )
    except (TypeError, ValueError):
        return 0.0


def _rl_confidence_weight() -> float:
    """RTR-0 (#1947) / Epic #1958: config-gated vote weight for RLConfidenceAgent.

    Default 0.40 == the historical hardcoded weight (byte-identical to today). The RL
    vote is currently direction-blind — its score is ``0.5 + (|pred|/2)*0.5`` on a BUY
    action, so a strongly-BEARISH prediction (large |pred|) still yields a max-conviction
    BUY. Set RL_CONFIDENCE_WEIGHT=0.0 to MUTE it as an interim guard until the sign-fix +
    RTR-0 attribution land (prove-it-or-kill-it). The os.environ read lives in
    config.py/config.oss.py (CODING_POLICY §2.10), not in the finance-core; this reads it
    via get_config(). Invalid values fall back to the 0.40 default.
    """
    try:
        return max(
            0.0, float(getattr(config.get_config(), "RL_CONFIDENCE_WEIGHT", 0.40))
        )
    except (TypeError, ValueError):
        return 0.40


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


def _news_sentiment_cfg() -> Tuple[bool, int, str]:
    """RTR-1 (#1948) — (enabled, max_headlines, model) for the NLP news path.

    Default OFF ⇒ NewsSentimentAgent runs today's LLM-/abstain-path byte-identical.
    ON ⇒ the agent scores REAL point-in-time headlines (state["news_headlines"])
    with a local NLP backend (core/nlp/news_sentiment.py: FinBERT, else vendored
    VADER lexicon) instead of the headline-less LLM symbol guess. The os.environ
    reads live in config.py/config.oss.py (CODING_POLICY §2.10), not here.
    Invalid values fall back to the dormant default.
    """
    try:
        cfg = config.get_config()
        enabled = bool(getattr(cfg, "NEWS_SENTIMENT_NLP_ENABLED", False))
        max_headlines = int(getattr(cfg, "NEWS_SENTIMENT_MAX_HEADLINES", 8) or 8)
        model = str(getattr(cfg, "NEWS_SENTIMENT_MODEL", "finbert") or "finbert")
        return enabled, max_headlines, model
    except (TypeError, ValueError):
        return False, 8, "finbert"


# Modul-Level Import für Testbarkeit (patchbar)
try:
    from core.agent_registry import get_global_registry
except ImportError:  # pragma: no cover
    get_global_registry = None  # type: ignore[assignment]

# LLM-Provider-Seam (ADR-014) — der einzige sanktionierte LLM-Einstiegspunkt.
# get_llm_provider() liefert bei LLM_PROVIDER unset/"gemini" exakt das heutige
# Gemini-Singleton (byte-identisch), bei "ollama" den lokalen Desktop-Provider.
# Als Funktion importiert (nicht als Modulvariable) → in Tests patchbar, anders
# als die veraltete Modulvariable gemini_model_instance.
try:
    from core.llm.provider import get_llm_provider
except ImportError:  # pragma: no cover
    get_llm_provider = None  # type: ignore[assignment]

# Epic 3.3: SpecialistRegistry für SpecialistAlphaAgent (lazy import, optional)
try:
    from core.specialist_registry import StockSpecialistRegistry as _SpecialistRegistry

    _SPECIALIST_REGISTRY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SpecialistRegistry = None  # type: ignore[assignment,misc]
    _SPECIALIST_REGISTRY_AVAILABLE = False

# Singleton-Referenz (gesetzt von engine/strategy beim Start, falls Epic 3.3 aktiv)
_specialist_registry_instance: "Optional[_SpecialistRegistry]" = None  # type: ignore[valid-type]

# RTR-3 (#1950): Warmup-Diagnostik-Drossel — Symbole, für die bei AKTIVEM Gewicht
# der "kein Report"-WARNING bereits geloggt wurde (1×/Symbol/Prozess, verhindert
# Log-Flut über ~500 Symbole/Zyklus während des Registry-Warmups). Bounded durch
# die Universe-Größe; dormant (Gewicht 0) wird das Set nie befüllt.
_warmup_warned_symbols: set = set()


def set_specialist_registry(registry: object) -> None:
    """Injects the active StockSpecialistRegistry into the Round Table (Epic 3.3)."""
    global _specialist_registry_instance
    _specialist_registry_instance = registry  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Custom Exceptions — Fail-Fast Architecture (Anti-Watermelon)
# ---------------------------------------------------------------------------


class DependencyLostException(RuntimeError):
    """
    Raised when a critical runtime dependency (e.g. RL Registry, LLM API)
    is unavailable. The trading_loop catches this and triggers the Kill Switch.
    """


class SuspectDataException(ValueError):
    """
    Raised when OHLC data fails sanity checks (e.g. flat candle H=L=O=C).
    The trading_loop catches this and triggers the Kill Switch.
    """


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


class DrawdownGuardAgent(VotingAgent):
    """
    Bewertet das Drawdown-Risiko anhand des OHLC-Kanals.
    score = 1 - normalized_drawdown
    Hoher Drawdown (H-L)/H > 0.05 → niedrigerer Score.
    """

    default_weight: float = 0.60
    min_weight: float = 0.20
    max_weight: float = 2.00

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        ohlc = state["ohlc"]
        high = ohlc.get("high", 1.0)
        low = ohlc.get("low", 0.0)
        close = ohlc.get("close", 1.0)
        symbol = state["symbol"]

        drawdown = 0.0
        vetoed = False
        score = 0.5
        reasoning = ""
        used_fallback = True

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
                    current_time = datetime.now(timezone.utc)

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
                        if drawdown > (_severe if _cond_on else 0.07):
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

        if used_fallback:
            if high <= 0:
                drawdown = 0.0
                score = 0.5
                reasoning = (
                    "Today's price data looks invalid — "
                    "risk guard stays neutral (no veto)."
                )
            else:
                drawdown = (high - low) / high
                # #1951/#2205 three-regime (1-bar fallback): conditioner ON → veto only
                # above the SEVERE threshold; OFF → 0.05 (byte-identical).
                _cond_on, _severe = _drawdown_conditioner()
                if drawdown > (_severe if _cond_on else 0.05):
                    vetoed = True
                score = self._clamp(1.0 - (drawdown * 5.0))
                reasoning = (
                    f"Today's price swing spans {drawdown:.1%} (high {high:.2f}, low {low:.2f}) — "
                    f"{'risk limit breached, this vote blocks new buying' if vetoed else 'within limits'} "
                    f"(score {score:.3f}; 30-day history unavailable, judged on today only). "
                    f"[src: today's high/low]"
                )

        return VoteResult(
            agent_name="DrawdownGuardAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            reasoning=reasoning,
            vetoed=vetoed,
        )


# ---------------------------------------------------------------------------
# 2. SpecialistAlphaAgent (w:0.55) — Stock Specialist System (Epic 3.3)
# ---------------------------------------------------------------------------

# #1346: resolve the config-gated weight ONCE at import — not twice in the class body
# (avoids a monkeypatch race between default_weight and max_weight reading different env).
_SPECIALIST_ALPHA_WEIGHT = _specialist_alpha_weight()

# RTR-0 (#1947) / Epic #1958: resolve the RL vote weight ONCE at import (same reason as
# above). Default 0.40 = byte-identical; desktop arms 0.0 to mute the direction-blind
# RL vote as an interim guard. When muted, min/max clamp to 0 so it is fully excluded.
_RL_CONFIDENCE_WEIGHT = _rl_confidence_weight()


class SpecialistAlphaAgent(VotingAgent):
    """
    Liest Sentiment-Score aus dem StockSpecialistRegistry (Epic 3.3).
    Konvertiert sentiment_score (0-100) in einen normalisierten Score (0.0-1.0).

    Fallback: 0.5 (neutral) wenn Registry nicht verfügbar oder kein Report gecacht.
    Empfehlung: "buy" → ≥0.6 | "sell" → ≤0.4 | "hold" → ~0.5
    """

    # #1346: config-gated. Default 0.0 keeps the specialist DORMANT (excluded from
    # consensus, byte-identical to today). SPECIALIST_ALPHA_WEIGHT (e.g. 0.55) restores
    # a real weighted vote — resolves the stale "w:0.55" header comment above.
    default_weight: float = _SPECIALIST_ALPHA_WEIGHT
    min_weight: float = 0.0
    max_weight: float = 2.0 if _SPECIALIST_ALPHA_WEIGHT > 0.0 else 0.0

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        symbol = state["symbol"]

        if _specialist_registry_instance is not None:
            try:
                report = _specialist_registry_instance.get_report(symbol)
                if report is not None:
                    # Map 0-100 sentiment_score to 0.0-1.0
                    raw_score = report.sentiment_score / 100.0
                    # Recommendation nudge: buy/sell shift score ±0.05
                    rec = getattr(report, "recommendation", "hold")
                    if rec == "buy":
                        raw_score = min(1.0, raw_score + 0.05)
                    elif rec == "sell":
                        raw_score = max(0.0, raw_score - 0.05)
                    score = self._clamp(raw_score)
                    escalation = (
                        " [ESCALATED]" if getattr(report, "escalate", False) else ""
                    )
                    reasoning = (
                        f"Our stock specialist rates {symbol} at "
                        f"{report.sentiment_score:.0f}/100 with a {rec} "
                        f"recommendation{escalation} (score {score:.3f})."
                    )
                    return VoteResult(
                        agent_name="SpecialistAlphaAgent",
                        symbol=symbol,
                        score=score,
                        weight=self.weight,
                        reasoning=reasoning,
                    )
            except Exception as exc:
                logger.debug(
                    "SpecialistAlphaAgent: Registry error for %s: %s", symbol, exc
                )

        # Kein Report gecacht (Registry nicht bereit oder Warmup läuft):
        # weight=0.0 → Pydantic Field(gt=0.0) schließt diesen Vote aus dem Konsens aus.
        # Besser ausgeschlossen als 0.5 mit vollem Gewicht (w:0.55) den Konsens zu zerren!
        # RTR-3 (#1950): Bei AKTIVEM Gewicht ist ein dauerhaft fehlender Report ein
        # Betriebsproblem — der gearmte Voter zahlt kein Signal ein → WARNING (§5.6,
        # nie DEBUG), gedrosselt auf 1×/Symbol/Prozess. Dormant bleibt der heutige
        # DEBUG-Pfad byte-identisch; `max_weight > 0` short-circuits davor, damit der
        # dormante Pfad keinen zusätzlichen Redis-Read (weight-Property) bekommt.
        live_weight = self.weight if self.max_weight > 0.0 else 0.0
        if live_weight > 0.0 and symbol not in _warmup_warned_symbols:
            _warmup_warned_symbols.add(symbol)
            logger.warning(
                "SpecialistAlphaAgent: Gewicht aktiv (%.2f), aber KEIN Report für %s — "
                "Vote aus Konsens AUSGESCHLOSSEN (w=0; Registry im Warmup oder nicht "
                "verbunden/injiziert).",
                live_weight,
                symbol,
            )
        else:
            logger.debug(
                "SpecialistAlphaAgent: kein Report für %s — Vote aus Konsens AUSGESCHLOSSEN (w=0)",
                symbol,
            )
        return VoteResult(
            agent_name="SpecialistAlphaAgent",
            symbol=symbol,
            score=0.5,
            weight=0.0,  # ← EXCLUDED: Pydantic gt=0.0 schlägt fehl → aus active_votes entfernt
            reasoning="EXCLUDED — research report not ready yet (warming up)",
        )


# ---------------------------------------------------------------------------
# 3. RegimeDetectionAgent (w:0.50) — Close/Open Verhältnis als Proxy
#    (#1949: flag ON → real MarketRegimeModel VIX/regime conditioner instead)
# ---------------------------------------------------------------------------


def _regime_conditioner_cfg() -> Tuple[bool, float]:
    """#1949 (RTR-2): (enabled, vix_midpoint) for the regime conditioner.

    Default OFF keeps the one-bar close/open proxy (byte-identical to today). ON →
    RegimeDetectionAgent scores the continuous VIX sigmoid around REGIME_VIX_MIDPOINT
    (the same 25.0 midpoint/shape VIXAwareRiskAgent uses — one consistent
    risk-appetite scale) from the state's "vix"/"regime" channels instead of the
    O/C ratio, removing the structural Momentum double count. Mirrors
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


class RegimeDetectionAgent(VotingAgent):
    """
    Detektiert Marktregime anhand des Close/Open-Verhältnisses.
    Bullisch (>1.02): score > 0.6 | Bärisch (<0.98): score < 0.4 | Neutral: ~0.5

    #1949 (REGIME_CONDITIONER_ENABLED, default OFF): flag ON replaces the one-bar
    O/C proxy with the REAL market regime (MarketRegimeModel VIX / regime label via
    the state seam) — a market-wide CONDITIONER score, independent of the symbol's
    bar. Missing vix+regime → neutral abstain (score 0.5, weight 0.0, WARNING §5.6).
    """

    default_weight: float = 0.50
    min_weight: float = 0.15
    max_weight: float = 1.50

    def _conditioner_vote(
        self, state: "SymbolEvalState", midpoint: float
    ) -> VoteResult:
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
            return VoteResult(
                agent_name="RegimeDetectionAgent",
                symbol=symbol,
                score=0.5,
                weight=0.0,
                reasoning="EXCLUDED — no VIX/regime data available right now",
            )

        score = self._clamp(1.0 / (1.0 + math.exp((vix - midpoint) / 10.0)))
        reasoning = (
            f"Market-wide regime CONDITIONER — this vote only scales how much "
            f"weight the directional voices get, it is not a call on this stock "
            f"(risk appetite {score:.3f}, midpoint {midpoint:.0f}). {source}"
        )
        return VoteResult(
            agent_name="RegimeDetectionAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            reasoning=reasoning,
        )

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        # #1949: flag ON → real regime conditioner; OFF (default) → the exact
        # one-bar O/C proxy below (byte-identical dark ship).
        _enabled, _midpoint = _regime_conditioner_cfg()
        if _enabled:
            return self._conditioner_vote(state, _midpoint)

        ohlc = state["ohlc"]
        open_price = ohlc.get("open", 1.0)
        close_price = ohlc.get("close", 1.0)
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
        return VoteResult(
            agent_name="RegimeDetectionAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# 4. MomentumAgent (w:0.45) — Price Momentum (Close-Open)/Open
# ---------------------------------------------------------------------------


def _momentum_fallback_abstain() -> bool:
    """#1968 (RT-BUG-2): config-gated ABSTAIN for the MomentumAgent one-bar fallback.

    The one-bar fallback (close-open)/open is direction-identical to RegimeDetection's
    close/open ratio — with <40 daily closes (12-1M path unavailable) the Momentum vote
    is a silent ECHO of the regime signal that also conditions its weight via the
    bearish-regime halving in consensus.py (self-referential coupling). ON → the
    fallback abstains (score 0.5, weight 0.0 — excluded from consensus, the
    VIXAware/SpecialistAlpha abstention pattern) instead of echoing. Default OFF ⇒
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


# #1968 audit follow-up: on history-poor desktops the fallback fires for most of the
# ~500-symbol universe EVERY cycle — an unthrottled per-abstain WARNING floods the log.
# Once-per-symbol-per-session throttle: the FIRST abstain per symbol logs WARNING
# (CODING_POLICY §5.6 — the fallback substitution itself is announced at WARNING),
# repeats for the same symbol drop to DEBUG. Session-scoped module state by design.
_MOMENTUM_ABSTAIN_WARNED: set = set()


class MomentumAgent(VotingAgent):
    """
    Einfacher Preis-Momentum-Indikator.
    Positives Momentum (>2%) → score > 0.5
    """

    default_weight: float = 0.45
    min_weight: float = 0.00
    max_weight: float = 1.50

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        ohlc = state["ohlc"]
        open_price = ohlc.get("open", 1.0)
        close_price = ohlc.get("close", 1.0)
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
                    current_time = datetime.now(timezone.utc)

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
                        score = self._clamp(0.5 + momentum_12_1 / 0.60)
                        reasoning = (
                            f"Momentum over the past year (excluding the most "
                            f"recent month): {momentum_12_1:+.1%} "
                            f"(price {p_start:.2f} → {p_end:.2f}, score {score:.3f}). "
                            f"[src: 12-month price history]"
                        )
                        return VoteResult(
                            agent_name="MomentumAgent",
                            symbol=symbol,
                            score=score,
                            weight=self.weight,
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
            # _MOMENTUM_ABSTAIN_WARNED above) — repeats for the same symbol → DEBUG.
            if symbol in _MOMENTUM_ABSTAIN_WARNED:
                _log = logger.debug
            else:
                _MOMENTUM_ABSTAIN_WARNED.add(symbol)
                _log = logger.warning
            _log(
                "MomentumAgent: <40 Tages-Closes für symbol=%s → 12-1M-Pfad nicht "
                "verfügbar; ABSTAIN (weight=0, aus Konsens ausgeschlossen) statt "
                "Regime-Echo (RT-BUG-2 #1968).",
                symbol,
            )
            return VoteResult(
                agent_name="MomentumAgent",
                symbol=symbol,
                score=0.5,
                weight=0.0,  # ← EXCLUDED: Pydantic gt=0.0 filtert den Vote aus active_votes
                reasoning=(
                    "Momentum: EXCLUDED — insufficient history for 12-1M momentum "
                    "(one-bar fallback would duplicate RegimeDetection)"
                ),
            )

        if open_price <= 0:
            score = 0.5
            momentum_pct = 0.0
        else:
            momentum_pct = (close_price - open_price) / open_price
            score = self._clamp(0.5 + momentum_pct * 5.0)

        reasoning = (
            f"Today's move: {momentum_pct:+.1%} (score {score:.3f}). Full price "
            f"history was unavailable, so this is a one-day move only — weak "
            f"evidence on its own. "
            f"[src: today's price bar]"
        )
        return VoteResult(
            agent_name="MomentumAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# 5. VIXAwareRiskAgent (w:0.45) — Volume-Inverse als VIX-Proxy
# ---------------------------------------------------------------------------

# Referenz-Volumen für Normalisierung (typisches S&P 500 Tagesvolumen)
_NORMAL_VOLUME_REF = 1_000_000.0
_VIX_VOLUME_THRESHOLD = 10.0  # 10× normales Volumen = Stress-Signal


class VIXAwareRiskAgent(VotingAgent):
    """
    Nutzt hohes Handelsvolumen als Proxy für VIX-Stress.
    Volume >> Normal → Risk-Off → score < 0.3 (Strong Avoid).

    Gherkin (Architect):
      Given: Volume > 10× normal
      When:  VIXAwareRiskAgent.vote()
      Then:  score < 0.3
    """

    default_weight: float = 0.45
    min_weight: float = 0.10
    max_weight: float = 1.50

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        symbol = state["symbol"]
        vix = state.get("vix")
        if vix is None:
            vix = state.get("ohlc", {}).get("vix")

        if vix is None or float(vix) <= 0:
            logger.warning(
                "VIXAwareRiskAgent inaktiv: VIX-Wert fehlt (oder <= 0). "
                "Agent abstiniert vom Konsens."
            )
            return VoteResult(
                agent_name="VIXAwareRiskAgent",
                symbol=symbol,
                score=0.5,
                weight=0.0,
                reasoning="EXCLUDED — VIX volatility data not available right now",
            )

        vix_val = float(vix)
        # Continuous risk score: Normal VIX (15) -> ~0.73, Panic VIX (45) -> ~0.12
        score = self._clamp(1.0 / (1.0 + math.exp((vix_val - 25.0) / 10.0)))

        reasoning = (
            f"Market-wide fear gauge (VIX) at {vix_val:.1f} — lower means calmer "
            f"markets (score {score:.3f}). This reads the whole market's risk mood, "
            f"identical for every symbol today — not this stock. "
            f"[src: VIX quote]"
        )
        return VoteResult(
            agent_name="VIXAwareRiskAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# 6. LSTMSignalAgent (w:0.40) — Delegiert an aktive Registry-Strategie
# ---------------------------------------------------------------------------


# ADR-RT01 (#1969): LSTM vote scale for the continuous tanh score mapping.
# score = clamp(0.5 + 0.5 * tanh(pred / _LSTM_VOTE_SCALE)) — a MONOTONE, bounded
# transform of the raw LSTM prediction, replacing the old 3-bucket
# {BUY:0.75, HOLD:0.5, SELL:0.25} discretisation that destroyed the signal
# (raw pred cross-sectional IC = +0.067 t=20.4, collapsed to +0.011 with a
#  NEGATIVE consensus contribution — attribution harness #1947).
# Basis: 2-sigma of the empirical prediction distribution on the OOS holdout
# (2024-01..2026-05, N=285,445): sigma = 1.79, so 2*sigma ≈ 3.6. Calibrating to
# 2-sigma keeps the bulk of predictions in the responsive (non-saturated) region
# of tanh — a +/-2σ prediction maps to score ≈ 0.88 / 0.12. The Spearman IC is
# scale-invariant; the scale only sets the vote's spread inside the weighted
# consensus mean. Net-of-cost proof (#1947 harness, 10bps one-way): the fix
# lifts the LSTM leave-one-out delta-Sharpe from -0.013 to +0.78. Annual review.
_LSTM_VOTE_SCALE: float = 3.6

# ADR-RT-LSTM-02: Minimum panel breadth before a CROSS-SECTIONAL standing may be
# voted on (flag-gated path only — see LSTM_VOTE_USES_CROSS_SECTIONAL_RANK).
# Basis: the cross-sectional signal was validated across the FULL universe (~488-500
# names). A percentile computed against a handful of symbols is not that signal — at
# n=1 it is definitionally 0.0 (you beat nobody), and it stays degenerate while the
# panel is thin. 30 keeps percentile resolution at ~3.3pp, coarse but honest; below
# it the module abstains rather than dress a thin panel as a universe standing.
# Reviewed with the walk-forward that gates the flag itself. Annual review.
_MIN_PANEL_SYMBOLS: int = 30


# INC (ML-gate outage): sentinel distinguishing "the runner shared no signal" (key
# ABSENT → each voice resolves + evaluates its own active, byte-identical to before)
# from "the shared eval genuinely produced None" (key PRESENT, value None → both voices
# abstain CONSISTENTLY). A bare ``None`` cannot carry that distinction; a unique object
# can. Written by runner._maybe_share_active_signal, read by both ML voices below.
_SHARED_UNSET = object()


class LSTMSignalAgent(VotingAgent):
    """
    Delegiert an die aktive Strategie in der AgentRegistry.

    Der Vote-Score ist eine MONOTONE KONTINUIERLICHE Funktion der LSTM-Prediction
    (`signal.decision_context.lstm_prediction`, das V2-Äquivalent des rohen `pred`),
    nicht mehr eine 3-Bucket-Diskretisierung (#1969). Fehlt die Prediction (None),
    abstiniert der Agent (weight=0.0) statt ein 0.5-Fake mit vollem Gewicht zu voten.
    """

    default_weight: float = 0.40
    min_weight: float = 0.15
    max_weight: float = 1.50

    def _vote_on_rank(
        self, symbol: str, pred: float, action: str
    ) -> Tuple[float, float, str]:
        """Vote on the stock's standing in the universe, not its isolated prediction.

        ⚠ Only reachable behind LSTM_VOTE_USES_CROSS_SECTIONAL_RANK (default OFF).

        Rationale (ADR-RT-LSTM-01): our own walk-forward refutes the raw per-symbol
        prediction — 0 of 488 symbols survive FDR correction, i.e. the isolated
        `pred` this agent votes on today is statistically indistinguishable from
        noise once you account for having tested 488 of them. What DID survive is
        the CROSS-SECTIONAL RANK (IC 0.067, t 20.4): the model is far better at
        ordering stocks against each other than at calling any one of them. So the
        honest question this module can answer is "how does this stock stand against
        the other ~500 today?", NOT "will this stock go up?".

        The standing comes from the same panel + the same `cross_section_standing()`
        the auditable report renders, so the board votes on exactly the number the
        report shows the user. Percentile maps directly to the [0,1] score — a stock
        in the top 10% scores 0.9. `pred` is kept only to explain the vote; it no
        longer drives it.

        Absent from the panel -> abstain (weight 0). The panel is only fed while the
        LSTM ranking cycle runs; without it this module has no validated basis to
        vote on, and a quiet 0.5 at full weight would drag every consensus toward
        neutral while looking like a real opinion.
        """
        from core.report.lstm_panel_store import cross_section_standing, get_store
        from core.sim.clock import engine_now

        # #2630: read the panel AS OF the engine's current day — the sim clock's date under
        # SIM_MODE, else byte-identical to datetime.now(). Was datetime.now(): the sim queried the
        # wall-clock date (no panel → n=0 → LSTM abstains → strict-ML-gate hard-blocks every symbol).
        as_of = engine_now(timezone.utc).date()
        xsec = get_store().cross_section_at(as_of)

        # ADR-RT-LSTM-02: a standing is only meaningful against a BROAD, DISPERSED
        # panel. Two degenerate panels the live engine can genuinely produce would
        # otherwise turn into confident bearishness:
        #   * a thin panel (n=1): the symbol beats nobody -> 0th percentile -> score
        #     0.0, a maximally BEARISH vote drawn from no information at all;
        #   * a collapsed model (every score identical): NO symbol beats any other,
        #     so EVERY symbol lands at the 0th percentile at once -> the board reads
        #     the entire universe as "sell" off a model failure.
        # Both are absence of signal, and absence of signal is an abstention.
        n_panel = len(xsec)
        if n_panel < _MIN_PANEL_SYMBOLS or len(set(xsec.values())) < 2:
            logger.warning(
                "LSTMSignalAgent: panel @ %s unusable for a standing "
                "(n=%d, distinct scores=%d) → abstention",
                as_of.isoformat(),
                n_panel,
                len(set(xsec.values())),
            )
            return (
                0.5,
                0.0,
                f"EXCLUDED — today's AI model ranking does not have enough "
                f"usable data to place this stock ({n_panel} stocks, "
                f"{len(set(xsec.values()))} distinct scores as of "
                f"{as_of.isoformat()})",
            )

        pct, rank, n = cross_section_standing(xsec, symbol)
        if pct is None:
            logger.warning(
                "LSTMSignalAgent: %s absent from the LSTM panel @ %s → abstention "
                "(cross-sectional mode needs the ranking cycle to have run)",
                symbol,
                as_of.isoformat(),
            )
            return (
                0.5,
                0.0,
                f"EXCLUDED — {symbol} is not in today's AI model ranking "
                f"({as_of.isoformat()}), so there is no validated basis to vote on",
            )
        score = self._clamp(pct / 100.0)
        return (
            score,
            self.weight,
            f"AI price model puts this stock at rank {rank} of {n} in today's "
            f"universe ({pct:.1f}th percentile, score {score:.3f}). The vote rests "
            f"on this cross-sectional standing — how the stock ranks against the "
            f"others, which is what the model is validated for — not on its raw "
            f"single-stock prediction ({pred:+.3f}, {action}), which failed "
            f"per-stock validation (0/488 passed). [src: AI model daily ranking @ "
            f"{as_of.isoformat()}]",
        )

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        symbol = state["symbol"]
        score = 0.5
        weight = self.weight
        reasoning = "AI price model not connected — staying neutral (0.5)."

        try:
            registry_fn = get_global_registry
            registry = registry_fn() if callable(registry_fn) else None
            if registry is None:
                raise DependencyLostException(
                    "LSTMSignalAgent: get_global_registry() returned None — "
                    "AgentRegistry not initialized. Kill Switch required."
                )
            # RTR / distinct-ML-sources (dark, default OFF): resolve the LSTM voice's
            # OWN registered model (the standby "LSTMDynamic" ranker, monitor_loop.py:77)
            # instead of the shared get_active() — which is the RL trader under the
            # default ACTIVE_STRATEGY="RLAgent", the LSTM/RL collapse documented in
            # core/agent_registry.py::get(). OFF -> get_active(), byte-identical to today.
            if getattr(config.get_config(), "ROUND_TABLE_DISTINCT_ML_SOURCES", False):
                active = registry.get("LSTMDynamic")
                if active is None:
                    # Producer not registered yet -> abstain (weight 0), NEVER a
                    # 0.5-fake at full weight that pollutes the consensus mean.
                    return VoteResult(
                        agent_name="LSTMSignalAgent",
                        symbol=symbol,
                        score=0.5,
                        weight=0.0,
                        reasoning=(
                            "AI price model (LSTMDynamic) not registered yet — "
                            "abstention (vote not counted)"
                        ),
                    )
            else:
                active = registry.get_active()
                if active is None:
                    raise DependencyLostException(
                        f"LSTMSignalAgent: registry.get_active() returned None for {symbol} — "
                        "No active strategy registered. Kill Switch required."
                    )
            if hasattr(active, "evaluate_for_symbol"):
                from datetime import datetime, timezone

                time_str = state.get("current_time", "")
                try:
                    current_time = datetime.fromisoformat(time_str)
                except (ValueError, TypeError):
                    current_time = datetime.now(timezone.utc)

                # Art. 14 EU AI Act / #1876: evaluate-only — no orders in vote phase.
                # INC (ML-gate outage): prefer the signal the runner evaluated ONCE and
                # shared, so both ML voices read the SAME result. Their own concurrent
                # per-voice eval returned inconsistent None/valid → the LSTM voice
                # abstained → the strict-ML gate blocked every decision. Fall back to our
                # own eval only when the runner did not share (distinct-ML-sources
                # routing, or an unresolvable active — the key stays absent there).
                _shared = state.get("_shared_active_signal", _SHARED_UNSET)  # type: ignore[call-overload]  # noqa: E501
                if _shared is not _SHARED_UNSET:
                    signal = _shared
                elif config.get_config().LSTM_VOTE_USES_CROSS_SECTIONAL_RANK:
                    # INC (ML-gate outage) robustness: in rank mode the vote is driven by
                    # the PANEL standing; this per-symbol evaluate is best-effort
                    # ENRICHMENT ONLY (it colours the reasoning string, never the
                    # score/weight — see the rank branch below). The live standby
                    # "LSTMDynamic" ranker can RAISE for a symbol not in its per-symbol
                    # cache (a "no bars"/KeyError inside the strategy), not just return
                    # None. Letting that raise propagate to the outer except would abort
                    # the whole vote into a weight-0 abstain — the LSTM voice would still
                    # abstain on every symbol and the strict-ML gate would still block
                    # every decision, i.e. the exact ML-gate outage this decoupling exists
                    # to remove. So swallow it (WARNING, §5.6) and vote on the panel
                    # regardless. The flag-OFF branch below is UNCHANGED — a raise there
                    # still propagates to the outer except, byte-identical to today.
                    try:
                        signal = await active.evaluate_for_symbol(
                            symbol, state["ohlc"], {}, current_time
                        )
                    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
                        logger.warning(
                            "LSTMSignalAgent: best-effort evaluate raised in rank mode "
                            "for %s (%s) — voting on the cross-sectional panel standing "
                            "regardless.",
                            symbol,
                            exc,
                        )
                        signal = None
                else:
                    signal = await active.evaluate_for_symbol(
                        symbol, state["ohlc"], {}, current_time
                    )

                # INC (ML-gate outage): the cross-sectional RANK is a PANEL property
                # (core.report.lstm_panel_store), NOT a per-symbol evaluate signal. Under
                # distinct-ML-sources the resolved "LSTMDynamic" ranker returns None per
                # symbol (the symbol is not in its per-symbol cache), which — before this
                # decoupling — fell into the "no signal" abstain BELOW, UPSTREAM of the
                # rank branch → the LSTM voice abstained → the strict-ML gate blocked
                # EVERY decision. When the rank flag is ON we must vote on the panel
                # standing REGARDLESS of the per-symbol signal; the signal only enriches
                # the reasoning string. All abstain guards (thin panel / symbol absent →
                # weight 0) live inside _vote_on_rank and are preserved. The flag-OFF
                # (per-symbol) path below is byte-identical to before.
                if config.get_config().LSTM_VOTE_USES_CROSS_SECTIONAL_RANK:
                    # Best-effort: if a signal with a prediction is present, pass it as
                    # `pred`/`action` for a richer reasoning string; otherwise a neutral
                    # (0.0, "HOLD") — the score/weight come from the panel, not from these.
                    pred = 0.0
                    action = "HOLD"
                    if signal is not None and hasattr(signal, "decision_context"):
                        raw_pred = getattr(
                            signal.decision_context, "lstm_prediction", None
                        )
                        if raw_pred is not None:
                            pred = float(raw_pred)
                            action = getattr(signal, "action", "HOLD")
                    score, weight, reasoning = self._vote_on_rank(symbol, pred, action)
                elif signal is not None and hasattr(signal, "decision_context"):
                    # #1969: continuous, monotone score from the raw LSTM prediction
                    # (the same ALWAYS-set field RLConfidenceAgent reads), NOT a
                    # 3-bucket {BUY:0.75, HOLD:0.5, SELL:0.25} discretisation.
                    ctx = signal.decision_context
                    raw_pred = getattr(ctx, "lstm_prediction", None)
                    if raw_pred is None:
                        # Prediction genuinely missing → abstain (weight 0), never a
                        # 0.5-fake with full weight that pollutes the consensus mean.
                        score = 0.5
                        weight = 0.0
                        reasoning = (
                            "AI price model has no prediction right now — "
                            "abstention (vote not counted)"
                        )
                    else:
                        pred = float(raw_pred)
                        action = getattr(signal, "action", "HOLD")
                        score = self._clamp(
                            0.5 + 0.5 * math.tanh(pred / _LSTM_VOTE_SCALE)
                        )
                        reasoning = (
                            f"AI price model (LSTM) signals {action} "
                            f"(prediction {pred:+.3f}, score {score:.3f}). "
                            f"[src: LSTM model output]"
                        )
                else:
                    score = 0.5
                    weight = 0.0
                    reasoning = (
                        "AI price model returned no signal this cycle — "
                        "abstention (vote not counted)"
                    )
        except DependencyLostException as exc:
            from core.ml_watchdog import ml_watchdog

            ml_watchdog.record_error("LSTMSignalAgent", exc)
            return VoteResult(
                agent_name="LSTMSignalAgent",
                symbol=symbol,
                score=0.5,
                weight=0.0,
                reasoning=(
                    f"AI price model connection lost — staying neutral, "
                    f"vote not counted ({exc!s:.50})"
                ),
            )
        except Exception as exc:
            from core.ml_watchdog import ml_watchdog

            logger.warning(
                "LSTMSignalAgent: Fehler bei Registry-Lookup/Inference: %s", exc
            )
            ml_watchdog.record_error("LSTMSignalAgent", exc)
            score = 0.5
            reasoning = (
                f"AI price model hit a temporary error — staying neutral, "
                f"vote not counted ({exc!s:.50})"
            )
            # Set weight=0.0 to exclude this vote from the consensus average
            return VoteResult(
                agent_name="LSTMSignalAgent",
                symbol=symbol,
                score=score,
                weight=0.0,
                reasoning=reasoning,
            )

        from core.ml_watchdog import ml_watchdog

        ml_watchdog.record_success("LSTMSignalAgent")

        return VoteResult(
            agent_name="LSTMSignalAgent",
            symbol=symbol,
            score=score,
            weight=weight,
            reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# 7. RLConfidenceAgent (w:0.40) — RL-Confidence aus aktiver Strategie
# ---------------------------------------------------------------------------


class RLConfidenceAgent(VotingAgent):
    """
    Liest RL-Konfidenz aus der aktiven Registry-Strategie.
    Fallback: 0.5 wenn nicht verfügbar.
    """

    # RTR-0 (#1947): config-gated. Default 0.40 = byte-identical (min 0.15 / max 1.50 as
    # before). RL_CONFIDENCE_WEIGHT=0.0 mutes the direction-blind RL vote — min/max clamp
    # to 0 so it is fully excluded from consensus (mirrors the SpecialistAlpha pattern).
    default_weight: float = _RL_CONFIDENCE_WEIGHT
    min_weight: float = 0.15 if _RL_CONFIDENCE_WEIGHT > 0.0 else 0.0
    max_weight: float = 1.50 if _RL_CONFIDENCE_WEIGHT > 0.0 else 0.0

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        symbol = state["symbol"]
        score = 0.5
        weight = self.weight
        reasoning = (
            "Reinforcement-learning agent not connected — staying neutral (0.5)."
        )

        try:
            registry_fn = get_global_registry
            registry = registry_fn() if callable(registry_fn) else None
            if registry is None:
                raise DependencyLostException(
                    "RLConfidenceAgent: get_global_registry() returned None — "
                    "AgentRegistry not initialized. Kill Switch required."
                )
            # RTR / distinct-ML-sources (dark, default OFF): resolve the RL voice's OWN
            # registered trader BY NAME (ACTIVE_STRATEGY, default "RLAgent" — the key the
            # active trader registers under, rl_strategy.py:174 / monitor_loop.py:280) so
            # it is a DISTINCT source from the LSTM voice's get("LSTMDynamic"). OFF ->
            # get_active(), byte-identical to today.
            if getattr(config.get_config(), "ROUND_TABLE_DISTINCT_ML_SOURCES", False):
                _rl_name = getattr(config.get_config(), "ACTIVE_STRATEGY", "RLAgent")
                active = registry.get(_rl_name)
                if active is None:
                    # RL trader not registered under that name -> abstain (weight 0),
                    # never collapse back onto whatever get_active() happens to be.
                    return VoteResult(
                        agent_name="RLConfidenceAgent",
                        symbol=symbol,
                        score=0.5,
                        weight=0.0,
                        reasoning=(
                            "Reinforcement-learning agent source not registered — "
                            "abstention (vote not counted)"
                        ),
                    )
            else:
                active = registry.get_active()
                if active is None:
                    raise DependencyLostException(
                        f"RLConfidenceAgent: registry.get_active() returned None for {symbol} — "
                        "No active strategy registered. Kill Switch required."
                    )
            if hasattr(active, "evaluate_for_symbol"):
                from datetime import datetime, timezone

                time_str = state.get("current_time", "")
                try:
                    current_time = datetime.fromisoformat(time_str)
                except (ValueError, TypeError):
                    current_time = datetime.now(timezone.utc)

                # Art. 14 EU AI Act / #1876: evaluate-only — no orders in vote phase.
                # INC (ML-gate outage): prefer the signal the runner evaluated ONCE and
                # shared, so both ML voices read the SAME result. Their own concurrent
                # per-voice eval returned inconsistent None/valid → the LSTM voice
                # abstained → the strict-ML gate blocked every decision. Fall back to our
                # own eval only when the runner did not share (distinct-ML-sources
                # routing, or an unresolvable active — the key stays absent there).
                _shared = state.get("_shared_active_signal", _SHARED_UNSET)  # type: ignore[call-overload]  # noqa: E501
                if _shared is not _SHARED_UNSET:
                    signal = _shared
                else:
                    signal = await active.evaluate_for_symbol(
                        symbol, state["ohlc"], {}, current_time
                    )

                # SignalEvent handling (V2 strategy returns SignalEvent)
                if signal is not None and hasattr(signal, "decision_context"):
                    ctx = signal.decision_context
                    action = str(getattr(signal, "action", "HOLD")).upper()
                    # Regression fix — reverses #656 (f5cc27da, "stop endless
                    # strategy-switch"). That commit switched this read to
                    # `conviction_score`, which rl_execution only sets on BUY (else 0.0),
                    # so every HOLD/SELL collapsed to a dead neutral 0.5 — the RL agent
                    # effectively stopped voting. `lstm_prediction` (= the strategy's
                    # `pred`) is the ALWAYS-set directional signal and the V2 equivalent
                    # of the pre-#656 `signal.confidence`.
                    pred = float(getattr(ctx, "lstm_prediction", 0.0))
                    # #2480: sign-coherent score across all actions (reverses direction-blindness).
                    # Uses continuous pred: pred > 0 => score > 0.5, pred < 0 => score < 0.5.
                    score = self._clamp(0.5 + 0.5 * math.tanh(pred / 2.0))
                    reasoning = (
                        f"Reinforcement-learning agent leans {action} "
                        f"(prediction {pred:+.2f}, score {score:.3f})."
                    )

                # Legacy handling (just in case)
                elif signal is not None and hasattr(signal, "confidence"):
                    confidence = float(getattr(signal, "confidence", 0.5))
                    action = getattr(signal, "action", "HOLD")
                    if str(action).upper() == "BUY":
                        score = self._clamp(0.5 + confidence * 0.5)
                    elif str(action).upper() == "SELL":
                        score = self._clamp(0.5 - confidence * 0.5)
                    else:
                        score = 0.5
                    reasoning = (
                        f"Reinforcement-learning agent leans {action} "
                        f"(confidence {confidence:.2f}, score {score:.3f}). "
                        f"[src: RL model]"
                    )
                else:
                    score = 0.5
                    weight = 0.0
                    reasoning = (
                        "Reinforcement-learning agent returned no signal this "
                        "cycle — abstention (vote not counted)"
                    )
        except DependencyLostException as exc:
            from core.ml_watchdog import ml_watchdog

            ml_watchdog.record_error("RLConfidenceAgent", exc)
            return VoteResult(
                agent_name="RLConfidenceAgent",
                symbol=symbol,
                score=0.5,
                weight=0.0,
                reasoning=(
                    f"Reinforcement-learning agent connection lost — staying "
                    f"neutral, vote not counted ({exc!s:.50})"
                ),
            )
        except Exception as exc:
            from core.ml_watchdog import ml_watchdog

            logger.warning(
                "RLConfidenceAgent: Fehler bei Registry-Lookup/Inference: %s", exc
            )
            ml_watchdog.record_error("RLConfidenceAgent", exc)
            score = 0.5
            reasoning = (
                f"Reinforcement-learning agent hit a temporary error — staying "
                f"neutral, vote not counted ({exc!s:.50})"
            )
            # Set weight=0.0 to exclude this vote from the consensus average
            return VoteResult(
                agent_name="RLConfidenceAgent",
                symbol=symbol,
                score=score,
                weight=0.0,
                reasoning=reasoning,
            )

        from core.ml_watchdog import ml_watchdog

        ml_watchdog.record_success("RLConfidenceAgent")

        return VoteResult(
            agent_name="RLConfidenceAgent",
            symbol=symbol,
            score=score,
            weight=weight,
            reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# 8. NewsSentimentAgent (w:0.35) — Gemini-Flash (Fallback: 0.5)
# ---------------------------------------------------------------------------


# Process-local sentiment cache (symbol -> (score, expiry_monotonic)). Fallback for the Redis cache
# inside NewsSentimentAgent.vote() when Redis is absent (desktop / no-Redis): without it the round
# table re-runs the LLM for every symbol every cycle (the calls serialize on a local CPU model →
# ~14s/cycle). BOUNDED: expired entries are purged on write and the dict is capped at MAXSIZE (the
# live universe is small — ≤ S&P 500), so it cannot grow without limit. An autouse fixture clears it.
_LOCAL_SENTIMENT_CACHE: "dict[str, tuple[float, float]]" = {}
_LOCAL_SENTIMENT_CACHE_MAXSIZE = 512


class NewsSentimentAgent(VotingAgent):
    """
    Nutzt Gemini-Flash für News-Sentiment-Analyse (nicht-preis-basiert).
    Bekämpft Echo-Chamber-Risiko durch externe Informationsquelle.

    Fallback: 0.5 wenn Gemini nicht erreichbar (kein API-Key in CI).
    Non-blocking: generate_content_async wird innerhalb des vote()-Calls awaited.
    """

    default_weight: float = 0.35
    min_weight: float = 0.10
    max_weight: float = 1.50

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        symbol = state["symbol"]

        # --- RTR-1 (#1948): real NLP headline sentiment behind
        # NEWS_SENTIMENT_NLP_ENABLED (default OFF). OFF ⇒ the LLM-/abstain-path
        # below runs byte-identical to today. ON ⇒ score point-in-time headlines
        # (state["news_headlines"], free Google-News-RSS producer) with a local
        # NLP backend instead of the headline-less LLM symbol guess. ---
        _nlp_enabled, _nlp_max, _nlp_model = _news_sentiment_cfg()
        if _nlp_enabled:
            return await self._vote_nlp(state, symbol, _nlp_max, _nlp_model)

        return VoteResult(
            agent_name="NewsSentimentAgent",
            symbol=symbol,
            score=0.5,
            weight=0.0,
            reasoning=(
                "NewsSentimentAgent is dormant (NEWS_SENTIMENT_NLP_ENABLED=False). "
                "The legacy ticker-only LLM prior was dropped."
            ),
        )

    async def _vote_nlp(
        self,
        state: "SymbolEvalState",
        symbol: str,
        max_headlines: int,
        model: str,
    ) -> VoteResult:
        """RTR-1 (#1948): score REAL point-in-time headlines with a local NLP model.

        Three outcomes (plan §5.3, all fail-transparent — WARNING never DEBUG §5.6):
          * headlines + backend → NLP score at full weight; reasoning names the top
            headlines + source + label (MiFID audit moat / Senate log).
          * no headlines → weight 0.0 (excluded from consensus, RT-BUG-5 #1971
            posture) + WARNING.
          * no NLP backend/signal → weight 0.0 + WARNING (never a silent guess).
        The scorer runs in a thread (FinBERT forward is blocking CPU work — the
        AsyncAIAgent asyncio.to_thread rule).
        """
        headlines = list(state.get("news_headlines") or [])[:max_headlines]
        if not headlines:
            logger.warning(
                "NewsSentimentAgent[NLP]: no point-in-time headlines for %s — "
                "Vote AUSGESCHLOSSEN (w=0)",
                symbol,
            )
            return VoteResult(
                agent_name="NewsSentimentAgent",
                symbol=symbol,
                score=0.5,
                weight=0.0,  # ← EXCLUDED: Pydantic gt=0.0 → aus active_votes entfernt
                reasoning=(
                    "NewsSentiment(NLP): no point-in-time headlines → "
                    "vote EXCLUDED (w=0)"
                ),
            )

        # Content-hash cache key (plan §5.3/R9): the NLP score depends on the
        # HEADLINE SET, so the LLM path's symbol-only key would serve a stale
        # score after the news flow changes. Same bounded local cache + 5-min
        # TTL posture as the LLM path (_LOCAL_SENTIMENT_CACHE / _CACHE_TTL).
        import hashlib

        _titles = "\n".join(
            str(h.get("title", "")) if isinstance(h, dict) else str(h)
            for h in headlines
        )
        _key = (
            f"{symbol}|nlp|{hashlib.sha256(_titles.encode('utf-8')).hexdigest()[:16]}"
        )
        _cached = _LOCAL_SENTIMENT_CACHE.get(_key)
        if _cached is not None and _cached[1] > time.monotonic():
            return VoteResult(
                agent_name="NewsSentimentAgent",
                symbol=symbol,
                score=_cached[0],
                weight=self.weight,
                reasoning=f"NewsSentiment(NLP): local cache → score={_cached[0]:.3f}",
            )

        import asyncio

        from core.nlp import news_sentiment as _news_nlp

        try:
            result = await asyncio.to_thread(
                _news_nlp.score_headlines, headlines, model
            )
        except Exception as exc:  # noqa: BLE001 — a scorer crash must not kill the vote
            logger.warning(
                "NewsSentimentAgent[NLP]: scorer failed for %s (%s: %.120s) — "
                "Vote AUSGESCHLOSSEN (w=0)",
                symbol,
                type(exc).__name__,
                exc,
            )
            result = None
        if result is None:
            logger.warning(
                "NewsSentimentAgent[NLP]: no local NLP backend/signal for %s — "
                "Vote AUSGESCHLOSSEN (w=0)",
                symbol,
            )
            return VoteResult(
                agent_name="NewsSentimentAgent",
                symbol=symbol,
                score=0.5,
                weight=0.0,
                reasoning=(
                    "NewsSentiment(NLP): no local NLP backend/signal → "
                    "vote EXCLUDED (w=0)"
                ),
            )

        score = self._clamp(float(result.score))
        # Audit moat (plan §5.3): name the strongest headlines + source + label.
        _parts = " | ".join(
            f"'{d['title'][:60]}' ({d['source']},{d['label']})"
            for d in result.details[:3]
        )
        reasoning = f"NewsSentiment({result.backend}): {score:.3f} | {_parts}"

        # Bounded cache write (purge expired + cap — the LLM path's idiom).
        _now = time.monotonic()
        for _expired in [
            _k for _k, (_, _exp) in _LOCAL_SENTIMENT_CACHE.items() if _exp <= _now
        ]:
            _LOCAL_SENTIMENT_CACHE.pop(_expired, None)
        if len(_LOCAL_SENTIMENT_CACHE) >= _LOCAL_SENTIMENT_CACHE_MAXSIZE:
            _soonest = min(
                _LOCAL_SENTIMENT_CACHE,
                key=lambda _k: _LOCAL_SENTIMENT_CACHE[_k][1],
            )
            _LOCAL_SENTIMENT_CACHE.pop(_soonest, None)
        _LOCAL_SENTIMENT_CACHE[_key] = (score, _now + 300)  # 300s = LLM _CACHE_TTL

        return VoteResult(
            agent_name="NewsSentimentAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# (#1951: PatternRecognitionAgent gelöscht — er war dormant (default_weight=0.0),
# stimmte ohne Konsens-Wirkung mit und hatte keine Walk-Forward-Evidenz.)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Fundamentaldaten-Stimmen (Phase B) — die Lücke im Gremium.
#
# Der Round Table urteilte bisher ausschliesslich auf Kurs-/ML-/Stimmungs-Signalen:
# KEIN Modul sah je eine Bilanz. Die Daten liegen laengst vor — der auditierbare
# Report (core/report) baut auf genau diesem PIT-Fundamentals-Cache auf — sie
# erreichten das Gremium nur nie. Diese beiden Module schliessen die Luecke und
# lesen dieselbe Quelle wie der Report, damit Report und Entscheidung nie
# auseinanderlaufen koennen.
#
# DORMANT: default_weight = 0.0 -> sie stimmen mit und begruenden sichtbar
# (RoundTableView/Audit-Trail), bewegen den Konsens aber nicht, bis ihr Gewicht
# nach einem Walk-forward-Beweis angehoben wird (validate-before-activate,
# ConsensusEngine zaehlt nur weight > 0). Kein neues Flag noetig.
# ---------------------------------------------------------------------------
def _read_pit_fundamentals(
    symbol: str, close: Optional[float] = None
) -> Optional[dict]:
    """Read the SAME point-in-time fundamentals the auditable report is built on.

    ``close`` is the symbol's current price. It is NOT optional in practice: the feed
    derives ``pe`` (and ``ps``) from price ÷ per-share earnings, so without it every
    price-based ratio comes back None and any agent reading them abstains forever.
    Verified against the real reader — `CachedFundamentalsReader()` with no
    close_lookup returns pe=None; supplying {symbol: 180.0} returns pe=61.22.

    Blocking (file-backed cache) -> callers must use ``asyncio.to_thread``.
    Returns None on a cold cache or any read error: an honest gap, never a guess.
    """
    try:
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        from core.report.financials_feed import CachedFundamentalsReader

        lookup = {symbol: float(close)} if close and float(close) > 0 else None
        fund = CachedFundamentalsReader(close_lookup=lookup).get_fundamentals(
            symbol, _dt.now(_tz.utc).date()
        )
        return dict(fund) if fund else None
    except Exception as exc:  # noqa: BLE001 — a data gap must never break the vote
        logger.warning(
            "Fundamentals read failed for %s (%s) — agent abstains (weight 0).",
            symbol,
            exc,
        )
        return None


def _pct(fraction: Optional[float]) -> Optional[float]:
    """Feed fractions -> percent.

    ⚠ The feed emits ratios as FRACTIONS (`net_margin` = 0.5585, not 55.85), while
    every threshold below is stated in percent because that is how the numbers are
    read and reasoned about. Converting at the boundary keeps the ADR thresholds
    legible and stops a 100x unit error from silently inverting a verdict.
    """
    return None if fraction is None else float(fraction) * 100.0


def _debt_to_equity(fund: dict) -> Optional[float]:
    """Derive D/E — the feed does not emit it, but it emits both sides.

    Verified against the real reader: `total_liabilities` and `total_equity` are
    present (NVDA: 32.274e9 / 79.327e9 -> 0.41). Reading a `debt_to_equity` key
    instead would read None forever, because no such key exists.
    """
    liabilities = fund.get("total_liabilities")
    equity = fund.get("total_equity")
    if liabilities is None or not equity:
        return None
    try:
        return float(liabilities) / float(equity)
    except (TypeError, ZeroDivisionError):
        return None


class FundamentalsAgent(VotingAgent):
    """Beurteilt das GESCHAEFT: waechst der Umsatz, verdient die Firma Geld?

    Liest den PIT-Fundamentals-Cache (dieselbe Quelle wie der Report) und
    begruendet mit den Zahlen, auf denen das Urteil beruht.

    Gherkin (Architect):
      Given: Umsatz +65.5% YoY und Nettomarge 55.6%
      When:  FundamentalsAgent.vote()
      Then:  score > 0.5 und die Begruendung nennt beide Zahlen

      Given: kein Fundamentals-Eintrag im Cache
      When:  FundamentalsAgent.vote()
      Then:  weight == 0.0 (Abstinenz statt Rateschaetzung)
    """

    # ADR-RT-FUND-01: Schwellen sind redaktionelle Startkalibrierung, KEINE
    #   Modell-Ausgabe — sie gehoeren in den Walk-forward, nicht ins Bauchgefuehl.
    #   +15% YoY = waechst spuerbar; <=0% = schrumpft; Nettomarge >=20% = profitabel.
    GROWTH_STRONG_PCT: float = 15.0
    GROWTH_SHRINKING_PCT: float = 0.0
    MARGIN_STRONG_PCT: float = 20.0

    default_weight: float = 0.0  # DORMANT — validate before activate
    min_weight: float = 0.0
    max_weight: float = 1.50

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        import asyncio

        symbol = state["symbol"]
        close = (state.get("ohlc") or {}).get("close")
        fund = await asyncio.to_thread(_read_pit_fundamentals, symbol, close)
        if not fund:
            logger.debug(
                "FundamentalsAgent: no PIT fundamentals for %s → abstention.", symbol
            )
            return VoteResult(
                agent_name="FundamentalsAgent",
                symbol=symbol,
                score=0.5,
                weight=0.0,
                reasoning="EXCLUDED — no verified fundamentals data yet",
            )

        # ⚠ The feed emits FRACTIONS (`revenue_yoy`, `net_margin`), not percent, and
        # there is no `*_pct` key. Reading the percent names returns None for every
        # symbol forever — verified against the real reader.
        growth = _pct(fund.get("revenue_yoy"))
        margin = _pct(fund.get("net_margin"))
        if growth is None and margin is None:
            return VoteResult(
                agent_name="FundamentalsAgent",
                symbol=symbol,
                score=0.5,
                weight=0.0,
                reasoning="EXCLUDED — no revenue or margin data available yet",
            )

        score, parts = 0.5, []
        if growth is not None:
            g = float(growth)
            if g >= self.GROWTH_STRONG_PCT:
                score += 0.2
                parts.append(f"revenue +{g:.1f}% year-over-year (growing)")
            elif g <= self.GROWTH_SHRINKING_PCT:
                score -= 0.2
                parts.append(f"revenue {g:.1f}% year-over-year (shrinking)")
            else:
                parts.append(f"revenue +{g:.1f}% year-over-year (modest)")
        if margin is not None:
            m = float(margin)
            if m >= self.MARGIN_STRONG_PCT:
                score += 0.2
                parts.append(f"net margin {m:.1f}% (profitable)")
            elif m <= 0:
                score -= 0.2
                parts.append(f"net margin {m:.1f}% (loss-making)")
            else:
                parts.append(f"net margin {m:.1f}% (thin)")

        return VoteResult(
            agent_name="FundamentalsAgent",
            symbol=symbol,
            score=self._clamp(score),
            weight=self.weight,
            reasoning="Business health: "
            + "; ".join(parts)
            + ". [src: audited annual filings]",
        )


class ValuationAgent(VotingAgent):
    """Beurteilt den PREIS: ist das Multiple relativ zum Gewinnwachstum gestreckt?

    Trailing PEG (KGV / GEWINNwachstum) + Verschuldungsgrad aus derselben PIT-Quelle
    wie der Report. Kein Forward-KGV — dafuer fehlen lokale Schaetzungen, und eine
    geratene Schaetzung waere genau die Fabrikation, die wir verbieten.

    ⚠ PEG ist auf EARNINGS growth definiert, nicht auf Umsatzwachstum. Eine fruehere
    Fassung teilte das KGV durch `revenue_yoy` und nannte das Ergebnis trotzdem
    "PEG" — ein Etikett, das dem Leser eine andere Kennzahl vorspiegelt als die
    gerechnete. Hier wird `eps_yoy` gelesen; fehlt es, wird abstiniert, statt die
    naechstbeste Zahl unter dem bekannten Namen zu servieren.

    Gherkin (Architect):
      Given: KGV 40.5 auf +65.5% GEWINNwachstum (PEG 0.62) und Verb/EK 0.31
      When:  ValuationAgent.vote()
      Then:  score > 0.5 und die Begruendung nennt PEG und Verschuldungsgrad

      Given: Gewinnwachstum <= 0 (PEG nicht bildbar)
      When:  ValuationAgent.vote()
      Then:  weight == 0.0 (Abstinenz statt Division durch ~0)
    """

    # ADR-RT-VAL-01: redaktionelle Startkalibrierung, KEINE Modell-Ausgabe.
    #   PEG < 1 = Wachstum nicht eingepreist; PEG > 3 = gestreckt;
    #   Verb/EK < 1 = schuldenarme Bilanz.
    PEG_CHEAP: float = 1.0
    PEG_STRETCHED: float = 3.0
    DEBT_HEALTHY: float = 1.0

    default_weight: float = 0.0  # DORMANT — validate before activate
    min_weight: float = 0.0
    max_weight: float = 1.50

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        import asyncio

        symbol = state["symbol"]
        # The price is what makes `pe` exist at all — the feed derives it from
        # price ÷ per-share earnings, so without it every price ratio is None.
        close = (state.get("ohlc") or {}).get("close")
        fund = await asyncio.to_thread(_read_pit_fundamentals, symbol, close)
        pe = (fund or {}).get("pe")
        # PEG is defined on EARNINGS growth: `eps_yoy`, a FRACTION in the feed.
        growth = _pct((fund or {}).get("eps_yoy"))
        as_of_txt = ""
        if fund:
            as_of = fund.get("filing_date") or fund.get("as_of")
            if as_of:
                as_of_txt = f" (as of {as_of})"

        if not fund or pe is None or growth is None or float(growth) <= 0:
            logger.debug(
                "ValuationAgent: no usable P/E or earnings growth for %s → abstention.",
                symbol,
            )
            return VoteResult(
                agent_name="ValuationAgent",
                symbol=symbol,
                score=0.5,
                weight=0.0,
                reasoning=(
                    f"EXCLUDED — no reliable price-to-earnings or "
                    f"earnings-growth data yet{as_of_txt}"
                ),
            )

        peg = float(pe) / float(growth)
        debt = _debt_to_equity(fund)
        debt_txt = "n/a" if debt is None else f"{float(debt):.2f}"
        if peg < self.PEG_CHEAP and (debt is None or float(debt) < self.DEBT_HEALTHY):
            score = 0.7
            verdict = "the market has not yet priced in its growth"
        elif peg > self.PEG_STRETCHED:
            score = 0.3
            verdict = "the price looks stretched relative to growth"
        else:
            score = 0.5
            verdict = "neither cheap nor stretched"

        return VoteResult(
            agent_name="ValuationAgent",
            symbol=symbol,
            score=self._clamp(score),
            weight=self.weight,
            reasoning=(
                f"Valuation: {verdict} — PEG {peg:.2f} "
                f"(price-to-earnings {float(pe):.1f} vs +{float(growth):.1f}% "
                f"earnings growth), debt-to-equity {debt_txt}{as_of_txt}. "
                f"[src: audited annual filings]"
            ),
        )


# ---------------------------------------------------------------------------
# Convenience: alle Agents als geordnete Liste
# ---------------------------------------------------------------------------

ALL_AGENTS: list[VotingAgent] = [
    DrawdownGuardAgent(),
    SpecialistAlphaAgent(),
    RegimeDetectionAgent(),
    MomentumAgent(),
    VIXAwareRiskAgent(),
    LSTMSignalAgent(),
    RLConfidenceAgent(),
    NewsSentimentAgent(),
    # Phase B — the fundamentals voices the gremium never had. Dormant at
    # weight 0: they speak and justify, they do not (yet) move the consensus.
    FundamentalsAgent(),
    ValuationAgent(),
]
