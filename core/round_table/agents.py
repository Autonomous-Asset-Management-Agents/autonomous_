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
import re
import time
from typing import TYPE_CHECKING, Optional

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
                    peak = float(closes.max())
                    current = float(close)
                    if peak > 0:
                        drawdown = (peak - current) / peak
                        used_fallback = False

                        if drawdown > 0.07:
                            vetoed = True

                        score = self._clamp(1.0 - (drawdown * 5.0))
                        reasoning = (
                            f"DrawdownGuard: VETO={vetoed} - peak-to-trough (30d) "
                            f"drawdown={drawdown:.2%} (peak={peak:.2f}, current={current:.2f}) → score={score:.3f}"
                        )
        except Exception as exc:
            logger.warning(
                "DrawdownGuardAgent Fallback auf Single-Bar wegen Fehler: %s", exc
            )

        if used_fallback:
            if high <= 0:
                drawdown = 0.0
                score = 0.5
                reasoning = "DrawdownGuard: high=0 ungültig, neutral (VETO=False)"
            else:
                drawdown = (high - low) / high
                if drawdown > 0.05:
                    vetoed = True
                score = self._clamp(1.0 - (drawdown * 5.0))
                reasoning = (
                    f"DrawdownGuard (1-Bar Fallback): VETO={vetoed} - H={high:.2f} L={low:.2f} "
                    f"drawdown={drawdown:.2%} → score={score:.3f}"
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

    default_weight: float = 0.50
    min_weight: float = 0.15
    max_weight: float = 1.50

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
                            f"Momentum: 12-1M ret={momentum_12_1:.2%} "
                            f"(p_start={p_start:.2f}, p_end={p_end:.2f}) → score={score:.3f}"
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

        if open_price <= 0:
            score = 0.5
            momentum_pct = 0.0
        else:
            momentum_pct = (close_price - open_price) / open_price
            score = self._clamp(0.5 + momentum_pct * 5.0)

        reasoning = (
            f"Momentum (1-Bar Fallback): pct={momentum_pct:.2%} → score={score:.3f}"
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
                reasoning="VIXAware: EXCLUDED — VIX-Daten nicht verfügbar",
            )

        vix_val = float(vix)
        # Continuous risk score: Normal VIX (15) -> ~0.73, Panic VIX (45) -> ~0.12
        score = self._clamp(1.0 / (1.0 + math.exp((vix_val - 25.0) / 10.0)))

        reasoning = (
            f"VIXAware: VIX={vix_val:.2f} → score={score:.3f} "
            f"(continuous risk scaling)"
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

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        symbol = state["symbol"]
        score = 0.5
        weight = self.weight
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
            if hasattr(active, "evaluate_for_symbol"):
                from datetime import datetime, timezone

                time_str = state.get("current_time", "")
                try:
                    current_time = datetime.fromisoformat(time_str)
                except (ValueError, TypeError):
                    current_time = datetime.now(timezone.utc)

                # Art. 14 EU AI Act / #1876: evaluate-only — no orders in vote phase
                signal = await active.evaluate_for_symbol(
                    symbol, state["ohlc"], {}, current_time
                )

                if signal is not None and hasattr(signal, "decision_context"):
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
                            "LSTMSignal: prediction unavailable (None) → abstention"
                        )
                    else:
                        pred = float(raw_pred)
                        action = getattr(signal, "action", "HOLD")
                        score = self._clamp(
                            0.5 + 0.5 * math.tanh(pred / _LSTM_VOTE_SCALE)
                        )
                        reasoning = (
                            f"LSTMSignal: pred={pred:+.3f} action={action} → "
                            f"continuous score={score:.3f} (0.5+0.5*tanh(pred/"
                            f"{_LSTM_VOTE_SCALE}))"
                        )
                else:
                    score = 0.5
                    weight = 0.0
                    reasoning = "LSTMSignal: strategy returned None (abstention)"
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

    default_weight: float = 0.40
    min_weight: float = 0.15
    max_weight: float = 1.50

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        symbol = state["symbol"]
        score = 0.5
        weight = self.weight
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
            if hasattr(active, "evaluate_for_symbol"):
                from datetime import datetime, timezone

                time_str = state.get("current_time", "")
                try:
                    current_time = datetime.fromisoformat(time_str)
                except (ValueError, TypeError):
                    current_time = datetime.now(timezone.utc)

                # Art. 14 EU AI Act / #1876: evaluate-only — no orders in vote phase
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
                    conv = min(1.0, abs(pred) / 2.0)  # |pred| >= 2 ⇒ full conviction
                    if action == "BUY":
                        score = self._clamp(0.5 + conv * 0.5)
                    elif action == "SELL":
                        score = self._clamp(0.5 - conv * 0.5)
                    else:
                        # HOLD: reflect the model's directional lean instead of a dead
                        # 0.5, restoring the pre-#656 property that the RL vote stays
                        # informative (a strong bullish/bearish pred still moves consensus).
                        score = self._clamp(0.5 + 0.5 * math.tanh(pred / 2.0))
                    reasoning = (
                        f"RLConfidence: action={action} pred={pred:.2f} "
                        f"conv={conv:.2f} → score={score:.3f}"
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
                    reasoning = f"RLConfidence: action={action} conf={confidence:.2f} → score={score:.3f}"
                else:
                    score = 0.5
                    weight = 0.0
                    reasoning = "RLConfidence: strategy returned None (abstention)"
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
        _gemini_available = False

        # --- Process-local cache fast-path (desktop / no-Redis fallback, see _LOCAL_SENTIMENT_CACHE) ---
        # Note: the round table evaluates DISTINCT symbols per cycle, so concurrent same-symbol votes
        # (thundering herd) don't occur in practice; the TTL bounds any duplicate LLM call to once/TTL.
        _cached_local = _LOCAL_SENTIMENT_CACHE.get(symbol)
        if _cached_local is not None and _cached_local[1] > time.monotonic():
            return VoteResult(
                agent_name="NewsSentimentAgent",
                symbol=symbol,
                score=_cached_local[0],
                weight=self.weight,
                reasoning=f"NewsSentiment: local cache → score={_cached_local[0]:.3f}",
            )

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
            if get_llm_provider is None:
                raise RuntimeError("core.llm.provider not importable")

            llm_provider = get_llm_provider()
            if llm_provider is None:
                raise RuntimeError(
                    "LLM provider not initialized (no API key / local LLM?)"
                )

            prompt = (
                f"Rate the short-term trading sentiment for stock {symbol} "
                f"based on current market context. "
                f"Respond with ONLY a single float between 0.0 (very bearish) "
                f"and 1.0 (very bullish). No explanation."
            )
            # max_output_tokens=128: 32 tokens was too aggressive causing Gemini 2.5 Flash to drop responses
            response = await llm_provider.generate_content_async(
                prompt, max_output_tokens=128
            )
            if response:
                raw = str(response).strip()
                # Robust extraction: LLM responses may include prose around the
                # float ("The sentiment score is 0.72\nBullish.") or comma-locale
                # output ("0,7"). Capture sign + decimal/comma, normalise comma
                # to dot, then range-check before counting the vote at full
                # weight. Prior `float(raw)` raised ValueError on prose so any
                # non-bare float silently fell back to neutral 0.5 with weight=0
                # (vote excluded). Out-of-range matches (e.g. "2.0", "-0.5",
                # "1.5") are treated as unparseable and excluded too — rather
                # than clamped — so a hallucinating LLM cannot inject a max-
                # bullish or max-bearish vote at full weight via an out-of-range
                # number.
                _match = re.search(r"-?\d+(?:[.,]\d+)?", raw)
                if _match:
                    try:
                        val = float(_match.group().replace(",", "."))
                    except ValueError:
                        val = None
                else:
                    val = None
                if val is not None and 0.0 <= val <= 1.0:
                    score = val
                    reasoning = f"NewsSentiment: LLM → {raw[:80]!r} → score={score:.3f}"
                    _gemini_available = True
                else:
                    score = 0.5
                    logger.warning(
                        "NewsSentimentAgent: unparseable / out-of-range LLM "
                        "response for %s: %r",
                        symbol,
                        raw[:100],
                    )
                    reasoning = (
                        "NewsSentiment: unparseable or out-of-range LLM response "
                        "→ neutral 0.5 (excluded from consensus)"
                    )

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
                    elif _gemini_available:
                        # No Redis (desktop): process-local fallback so steady-state cycles don't
                        # re-run the LLM for every symbol. Only real LLM scores are cached. Purge
                        # expired keys + cap the size on write so the cache stays bounded (no
                        # unbounded growth in a long-running engine).
                        _now = time.monotonic()
                        for _expired in [
                            _k
                            for _k, (_, _exp) in _LOCAL_SENTIMENT_CACHE.items()
                            if _exp <= _now
                        ]:
                            _LOCAL_SENTIMENT_CACHE.pop(_expired, None)
                        if (
                            len(_LOCAL_SENTIMENT_CACHE)
                            >= _LOCAL_SENTIMENT_CACHE_MAXSIZE
                        ):
                            _soonest = min(
                                _LOCAL_SENTIMENT_CACHE,
                                key=lambda _k: _LOCAL_SENTIMENT_CACHE[_k][1],
                            )
                            _LOCAL_SENTIMENT_CACHE.pop(_soonest, None)
                        _LOCAL_SENTIMENT_CACHE[symbol] = (score, _now + _CACHE_TTL)
                except Exception as _set_exc:
                    logger.debug("NewsSentimentAgent: Redis set failed: %s", _set_exc)
            else:
                score = 0.5
                reasoning = "NewsSentiment: Gemini leer → kein Signal"
        except Exception as exc:
            logger.warning(
                "NewsSentimentAgent: Gemini nicht erreichbar oder inaktiv: %s", exc
            )
            score = 0.5
            reasoning = f"NewsSentiment: EXCLUDED — Gemini nicht verfügbar ({type(exc).__name__})"

        if not _gemini_available:
            logger.warning(
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

    default_weight: float = 0.0
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
