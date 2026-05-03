# core/round_table/agents.py
# Epic 2.5 — Round Table V2: 9 spezialisierte Voting-Agents
#
# Alle Agents:
#   - Vollständig async (kein blocking I/O in vote())
#   - Arbeiten auf SymbolEvalState OHLC-Skalaren
#   - Produzieren score ∈ [0.0, 1.0] mit MiFID-Audit-Reasoning
#
# Agent-Gewichte (vom Architekten bestätigt):
#   DrawdownGuard:0.60 | SpecialistAlpha:0.55 | RegimeDetection:0.50
#   Momentum:0.45 | VIXAware:0.45 | LSTM:0.40 | RL:0.40
#   NewsSentiment:0.35 | PatternRecognition:0.30
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
import math
from typing import Any, Optional, TYPE_CHECKING

from core.round_table.base_agent import VotingAgent, AsyncAIAgent, VoteResult

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

logger = logging.getLogger(__name__)

# Modul-Level Import für Testbarkeit (patchbar)
try:
    from core.agent_registry import get_global_registry
except ImportError:  # pragma: no cover
    get_global_registry = None  # type: ignore[assignment]

# Gemini client als Modul-Referenz importieren (wichtig für Testbarkeit via patch)
# "from core.gemini_client import gemini_model_instance" cacht den Namen lokal
# und ist nicht patchbar. Stattdessen: Modul halten + Attribut zur Laufzeit lesen.
try:
    import core.gemini_client as _gemini_module
except ImportError:  # pragma: no cover
    _gemini_module = None  # type: ignore[assignment]

# Epic 3.3: SpecialistRegistry für SpecialistAlphaAgent (lazy import, optional)
try:
    from core.specialist_registry import StockSpecialistRegistry as _SpecialistRegistry

    _SPECIALIST_REGISTRY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SpecialistRegistry = None  # type: ignore[assignment,misc]
    _SPECIALIST_REGISTRY_AVAILABLE = False

# Singleton-Referenz (gesetzt von engine/strategy beim Start, falls Epic 3.3 aktiv)
_specialist_registry_instance: "Optional[_SpecialistRegistry]" = None  # type: ignore[valid-type]


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
        symbol = state["symbol"]

        if high <= 0:
            score = 0.5
            reasoning = "DrawdownGuard: high=0 ungültig, neutral"
        else:
            drawdown = (high - low) / high  # 0.0 = kein Drawdown, 1.0 = Total-Crash
            # Normalisierung: drawdown > 10% = Score < 0.5
            score = self._clamp(1.0 - (drawdown * 5.0))
            reasoning = (
                f"DrawdownGuard: H={high:.2f} L={low:.2f} "
                f"drawdown={drawdown:.2%} → score={score:.3f}"
            )

        return VoteResult(
            agent_name="DrawdownGuardAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# 2. SpecialistAlphaAgent (w:0.55) — Stock Specialist System (Epic 3.3)
# ---------------------------------------------------------------------------


class SpecialistAlphaAgent(VotingAgent):
    """
    Liest Sentiment-Score aus dem StockSpecialistRegistry (Epic 3.3).
    Konvertiert sentiment_score (0-100) in einen normalisierten Score (0.0-1.0).

    Fallback: 0.5 (neutral) wenn Registry nicht verfügbar oder kein Report gecacht.
    Empfehlung: "buy" → ≥0.6 | "sell" → ≤0.4 | "hold" → ~0.5
    """

    default_weight: float = 0.0
    min_weight: float = 0.0
    max_weight: float = 0.0

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
                        f"SpecialistAlpha: {symbol} sentiment={report.sentiment_score:.0f}/100 "
                        f"rec={rec}{escalation} → score={score:.3f}"
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
        logger.debug(
            "SpecialistAlphaAgent: kein Report für %s — Vote aus Konsens AUSGESCHLOSSEN (w=0)",
            symbol,
        )
        return VoteResult(
            agent_name="SpecialistAlphaAgent",
            symbol=symbol,
            score=0.5,
            weight=0.0,  # ← EXCLUDED: Pydantic gt=0.0 schlägt fehl → aus active_votes entfernt
            reasoning="SpecialistAlpha: EXCLUDED — kein Report gecacht (Warmup läuft oder Registry nicht verbunden)",
        )


# ---------------------------------------------------------------------------
# 3. RegimeDetectionAgent (w:0.50) — Close/Open Verhältnis als Proxy
# ---------------------------------------------------------------------------


class RegimeDetectionAgent(VotingAgent):
    """
    Detektiert Marktregime anhand des Close/Open-Verhältnisses.
    Bullisch (>1.02): score > 0.6 | Bärisch (<0.98): score < 0.4 | Neutral: ~0.5
    """

    default_weight: float = 25.0
    min_weight: float = 15.0
    max_weight: float = 35.0

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
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
                regime = "bullisch"
            elif ratio < 0.98:
                regime = "bärisch"
            else:
                regime = "neutral"

        reasoning = (
            f"RegimeDetection: O={open_price:.2f} C={close_price:.2f} "
            f"regime={regime} → score={score:.3f}"
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

        if open_price <= 0:
            score = 0.5
            momentum_pct = 0.0
        else:
            momentum_pct = (close_price - open_price) / open_price
            # +10% → score ~1.0, -10% → score ~0.0
            score = self._clamp(0.5 + momentum_pct * 5.0)

        reasoning = f"Momentum: pct={momentum_pct:.2%} → score={score:.3f}"
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
        volume = state["ohlc"].get("volume", _NORMAL_VOLUME_REF)
        symbol = state["symbol"]

        volume_ratio = volume / max(_NORMAL_VOLUME_REF, 1.0)
        # Inverse: hohes Volumen = höheres Risiko = niedrigerer Score
        # volume_ratio=1 → score=0.7 | volume_ratio=10 → score~0.2 | volume_ratio=50 → score<0.1
        score = self._clamp(0.7 / (1.0 + math.log1p(max(volume_ratio - 1, 0))))
        regime_label = "Risk-Off" if volume_ratio > _VIX_VOLUME_THRESHOLD else "Normal"

        reasoning = (
            f"VIXAware: vol={volume:.0f} ratio={volume_ratio:.1f}× "
            f"regime={regime_label} → score={score:.3f}"
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


class LSTMSignalAgent(VotingAgent):
    """
    Delegiert an die aktive Strategie in der AgentRegistry.
    Fallback: 0.5 (neutral) wenn keine Registry / kein LSTM verfügbar.
    """

    default_weight: float = 25.0
    min_weight: float = 15.0
    max_weight: float = 40.0

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        symbol = state["symbol"]
        score = 0.5
        reasoning = "LSTMSignal: kein Registry-Eintrag → neutral 0.5"

        try:
            registry_fn = get_global_registry
            registry = registry_fn() if callable(registry_fn) else None
            if registry is None:
                raise DependencyLostException(
                    "LSTMSignalAgent: get_global_registry() returned None — "
                    "AgentRegistry not initialized. Kill Switch required."
                )
            active = registry.get_active()
            if active is None:
                raise DependencyLostException(
                    f"LSTMSignalAgent: registry.get_active() returned None for {symbol} — "
                    "No active strategy registered. Kill Switch required."
                )
            if hasattr(active, "run_for_symbol"):
                from datetime import datetime, timezone

                time_str = state.get("current_time", "")
                try:
                    current_time = datetime.fromisoformat(time_str)
                except (ValueError, TypeError):
                    current_time = datetime.now(timezone.utc)

                # Nur Signal-Richtung verwenden — nicht blockend
                signal = await active.run_for_symbol(
                    symbol, state["ohlc"], {}, current_time
                )

                if signal is not None and hasattr(signal, "action"):
                    action = getattr(signal, "action", "HOLD")
                    score = {"BUY": 0.75, "HOLD": 0.5, "SELL": 0.25}.get(
                        str(action).upper(), 0.5
                    )
                    reasoning = f"LSTMSignal: action={action} → score={score}"
        except DependencyLostException as exc:
            from core.ml_watchdog import ml_watchdog

            ml_watchdog.record_error("LSTMSignalAgent", exc)
            return VoteResult(
                agent_name="LSTMSignalAgent",
                symbol=symbol,
                score=0.5,
                weight=0.0,
                reasoning=f"LSTMSignal: DependencyLostException → neutral 0.5 ({exc!s:.50})",
            )
        except Exception as exc:
            from core.ml_watchdog import ml_watchdog

            logger.warning(
                "LSTMSignalAgent: Fehler bei Registry-Lookup/Inference: %s", exc
            )
            ml_watchdog.record_error("LSTMSignalAgent", exc)
            score = 0.5
            reasoning = f"LSTMSignal: Exception → neutral 0.5 ({exc!s:.50})"
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
            weight=self.weight,
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

    default_weight: float = 25.0
    min_weight: float = 15.0
    max_weight: float = 40.0

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        symbol = state["symbol"]
        score = 0.5
        reasoning = "RLConfidence: kein Registry-Eintrag → neutral 0.5"

        try:
            registry_fn = get_global_registry
            registry = registry_fn() if callable(registry_fn) else None
            if registry is None:
                raise DependencyLostException(
                    "RLConfidenceAgent: get_global_registry() returned None — "
                    "AgentRegistry not initialized. Kill Switch required."
                )
            active = registry.get_active()
            if active is None:
                raise DependencyLostException(
                    f"RLConfidenceAgent: registry.get_active() returned None for {symbol} — "
                    "No active strategy registered. Kill Switch required."
                )
            if hasattr(active, "run_for_symbol"):
                from datetime import datetime, timezone

                time_str = state.get("current_time", "")
                try:
                    current_time = datetime.fromisoformat(time_str)
                except (ValueError, TypeError):
                    current_time = datetime.now(timezone.utc)

                signal = await active.run_for_symbol(
                    symbol, state["ohlc"], {}, current_time
                )

                # SignalEvent handling (V2 strategy returns SignalEvent)
                if signal is not None and hasattr(signal, "decision_context"):
                    # Extract confidence from SignalEvent's decision_context
                    confidence = float(
                        getattr(signal.decision_context, "conviction_score", 0.5)
                    )
                    # LSTMSignal/RLSignal action isn't directly on conviction, but we can look at the raw prediction
                    # Actually, if the action is BUY, conviction is > 0, otherwise it might be 0.
                    # Let's read the raw rl_stabilized_action if available, else just use the final action
                    action = getattr(signal, "action", "HOLD")
                    if str(action).upper() == "BUY":
                        score = self._clamp(0.5 + confidence * 0.5)
                    elif str(action).upper() == "SELL":
                        score = self._clamp(0.5 - confidence * 0.5)
                    else:
                        score = 0.5
                    reasoning = f"RLConfidence: action={action} conf={confidence:.2f} → score={score:.3f}"

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
                    reasoning = f"RLConfidence: action={action} conf={confidence:.2f} → score={score:.3f}"
        except DependencyLostException as exc:
            from core.ml_watchdog import ml_watchdog

            ml_watchdog.record_error("RLConfidenceAgent", exc)
            return VoteResult(
                agent_name="RLConfidenceAgent",
                symbol=symbol,
                score=0.5,
                weight=0.0,
                reasoning=f"RLConfidence: DependencyLostException → neutral 0.5 ({exc!s:.50})",
            )
        except Exception as exc:
            from core.ml_watchdog import ml_watchdog

            logger.warning(
                "RLConfidenceAgent: Fehler bei Registry-Lookup/Inference: %s", exc
            )
            ml_watchdog.record_error("RLConfidenceAgent", exc)
            score = 0.5
            reasoning = f"RLConfidence: Exception → neutral 0.5 ({exc!s:.50})"
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
            weight=self.weight,
            reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# 8. NewsSentimentAgent (w:0.35) — Gemini-Flash (Fallback: 0.5)
# ---------------------------------------------------------------------------


class NewsSentimentAgent(VotingAgent):
    """
    Nutzt Gemini-Flash für News-Sentiment-Analyse (nicht-preis-basiert).
    Bekämpft Echo-Chamber-Risiko durch externe Informationsquelle.

    Fallback: 0.5 wenn Gemini nicht erreichbar (kein API-Key in CI).
    Non-blocking: generate_content_async wird innerhalb des vote()-Calls awaited.
    """

    default_weight: float = 25.0
    min_weight: float = 10.0
    max_weight: float = 35.0

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        symbol = state["symbol"]
        _gemini_available = False

        # --- Redis cache check (async, 5 min TTL) ---
        # Prevents duplicate Gemini calls for the same symbol within a Round Table cycle.
        _CACHE_KEY = f"gemini:sentiment:{symbol}"
        _CACHE_TTL = 300  # 5 minutes
        try:
            from core.redis_client import RedisClient

            _r = await RedisClient.get_redis()
            if _r is not None:
                _cached = await _r.get(_CACHE_KEY)
                if _cached is not None:
                    _cached_score = self._clamp(float(_cached))
                    logger.debug(
                        "NewsSentimentAgent: cache HIT for %s → %.3f",
                        symbol,
                        _cached_score,
                    )
                    return VoteResult(
                        agent_name="NewsSentimentAgent",
                        symbol=symbol,
                        score=_cached_score,
                        weight=self.weight,
                        reasoning=f"NewsSentiment: Redis cache → score={_cached_score:.3f}",
                    )
        except Exception as _cache_exc:
            logger.debug("NewsSentimentAgent: Redis cache check failed: %s", _cache_exc)

        try:
            if _gemini_module is None:
                raise RuntimeError("core.gemini_client not importable")

            gemini_instance = _gemini_module.get_gemini_instance()
            if gemini_instance is None:
                raise RuntimeError("Gemini model not initialized (no API key?)")

            prompt = (
                f"Rate the short-term trading sentiment for stock {symbol} "
                f"based on current market context. "
                f"Respond with ONLY a single float between 0.0 (very bearish) "
                f"and 1.0 (very bullish). No explanation."
            )
            # max_output_tokens=128: 32 tokens was too aggressive causing Gemini 2.5 Flash to drop responses
            response = await gemini_instance.generate_content_async(
                prompt, max_output_tokens=128
            )
            if response:
                raw = str(response).strip()
                score = self._clamp(float(raw))
                reasoning = f"NewsSentiment: Gemini → {raw} → score={score:.3f}"
                _gemini_available = True

                # --- Populate cache ---
                try:
                    from core.redis_client import RedisClient

                    _r = await RedisClient.get_redis()
                    if _r is not None:
                        await _r.set(_CACHE_KEY, str(score), ex=_CACHE_TTL)
                        logger.debug(
                            "NewsSentimentAgent: cached %s → %.3f (%ds TTL)",
                            symbol,
                            score,
                            _CACHE_TTL,
                        )
                except Exception as _set_exc:
                    logger.debug("NewsSentimentAgent: Redis set failed: %s", _set_exc)
            else:
                score = 0.5
                reasoning = "NewsSentiment: Gemini leer → kein Signal"
        except Exception as exc:
            logger.debug("NewsSentimentAgent: Gemini nicht erreichbar: %s", exc)
            score = 0.5
            reasoning = f"NewsSentiment: EXCLUDED — Gemini nicht verfügbar ({type(exc).__name__})"

        if not _gemini_available:
            # Kein echtes Gemini-Signal: weight=0.0 → aus Konsens ausgeschlossen.
            # 0.5 mit w:0.35 würde Konsens ohne jede Info zu 0.5 ziehen!
            logger.debug(
                "NewsSentimentAgent: kein Gemini-Signal für %s — Vote AUSGESCHLOSSEN (w=0)",
                symbol,
            )
            return VoteResult(
                agent_name="NewsSentimentAgent",
                symbol=symbol,
                score=0.5,
                weight=0.0,  # ← EXCLUDED: Pydantic gt=0.0 → aus active_votes entfernt
                reasoning=reasoning,
            )

        return VoteResult(
            agent_name="NewsSentimentAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# 9. PatternRecognitionAgent (w:0.30) — Candlestick-Muster aus OHLC
# ---------------------------------------------------------------------------


class PatternRecognitionAgent(VotingAgent):
    """
    Einfache Candlestick-Mustererkennung auf OHLC-Skalaren.
    Bullish Engulfing, Hammer, Doji aus reinen Skalaren (kein TA-Lib).
    """

    default_weight: float = 0.30
    min_weight: float = 0.00
    max_weight: float = 1.00

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        ohlc = state["ohlc"]
        open_price = ohlc.get("open", 1.0)
        high = ohlc.get("high", 1.0)
        low = ohlc.get("low", 1.0)
        close = ohlc.get("close", 1.0)
        symbol = state["symbol"]

        body = abs(close - open_price)
        upper_shadow = high - max(close, open_price)
        lower_shadow = min(close, open_price) - low
        total_range = high - low if high != low else 1.0

        pattern = "neutral"
        score = 0.5

        if total_range > 0:
            body_ratio = body / total_range
            # Doji: sehr kleiner Body
            if body_ratio < 0.1:
                pattern = "doji"
                score = 0.5
            # Bullish Hammer: langer unterer Schatten, kleiner Body oben
            elif lower_shadow > 2 * body and close >= open_price:
                pattern = "bullish_hammer"
                score = 0.7
            # Bearish Shooting Star: langer oberer Schatten, kleiner Body unten
            elif upper_shadow > 2 * body and close < open_price:
                pattern = "shooting_star"
                score = 0.3
            # Starke bullische Kerze: Körper > 60% der Range, close > open
            elif body_ratio > 0.6 and close > open_price:
                pattern = "bullish_marubozu"
                score = 0.75
            # Starke bärische Kerze: Körper > 60%, close < open
            elif body_ratio > 0.6 and close < open_price:
                pattern = "bearish_marubozu"
                score = 0.25

        score = self._clamp(score)
        reasoning = (
            f"Pattern: {pattern} | body_ratio={body / total_range:.2%} "
            f"→ score={score:.3f}"
        )

        return VoteResult(
            agent_name="PatternRecognitionAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# Convenience: alle 9 Agents als geordnete Liste
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
    PatternRecognitionAgent(),
]
