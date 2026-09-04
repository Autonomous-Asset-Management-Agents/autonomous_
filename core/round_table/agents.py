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
    """#1346 / PR #2839: config-gated vote weight for SpecialistAlphaAgent.

    Default 0.40 keeps the specialist ACTIVE. The agent leverages local LLMs (Ollama)
    to enable deep specialist research without incurring expensive Cloud API costs.
    The os.environ read lives in config.py/config.oss.py (CODING_POLICY §2.10),
    not in the finance-core. Invalid values fall back to dormant.
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


def _consensus_weight(name: str, default: float) -> float:
    """#2815 (TRD-8 T1): config-gated vote weight, clamped to [0, 1].

    Same shape and reasons as ``_specialist_alpha_weight`` above: the read goes through
    ``get_config()`` (CODING_POLICY §2.10 — no raw os.environ in the finance-core) and is
    resolved ONCE at import (the #1346 monkeypatch-race argument). The clamp is the safety
    half: a sweep typo like "7" must yield 1.0, never a 7x mega-vote; garbage falls back
    to the historical default (config._env_float already guards the parse). Unset env ⇒
    byte-identical to the pre-#2815 hardcoded literal — pinned by
    tests/unit/test_consensus_weight_seam.py.
    """
    try:
        return max(0.0, min(1.0, float(getattr(config.get_config(), name, default))))
    except (TypeError, ValueError):
        return default


# Resolved once at import, immediately before the classes that consume them (mirrors
# _SPECIALIST_ALPHA_WEIGHT / _RL_CONFIDENCE_WEIGHT below).
# #3084: these two weights are VALIDATOR TOKENS, not directional votes — both
# agents are excluded from the consensus mean by name (consensus.py
# NON_DIRECTIONAL_AGENTS); DrawdownGuard acts via VETO, RegimeDetection via
# weight-coupling. The value only keeps their VoteResult validator-compatible
# (weight gt=0, so the agent-veto path sees the vote). Audit records therefore
# stamp them with weight 0.0 + role (runner._serialize_votes) — do NOT read
# 0.60/0.50 as a share of the consensus (#1993 made exactly that mistake).
_DRAWDOWN_GUARD_WEIGHT = _consensus_weight("DRAWDOWN_GUARD_WEIGHT", 0.60)
_REGIME_DETECTION_WEIGHT = _consensus_weight("REGIME_DETECTION_WEIGHT", 0.50)
_MOMENTUM_AGENT_WEIGHT = _consensus_weight("MOMENTUM_AGENT_WEIGHT", 0.45)
_VIX_RISK_WEIGHT = _consensus_weight("VIX_RISK_WEIGHT", 0.45)
_LSTM_SIGNAL_WEIGHT = _consensus_weight("LSTM_SIGNAL_WEIGHT", 0.40)

# #3145(3)/#3146: consensus weights for the FundamentalsAgent / ValuationAgent. ACTIVATED
# at 0.35 as the shipped default (owner decision 2026-09-03). The config-missing fallback
# mirrors the config default so no path silently reverts to the old dormant 0.0.
_FUNDAMENTALS_AGENT_WEIGHT = _consensus_weight("FUNDAMENTALS_AGENT_WEIGHT", 0.35)
_VALUATION_AGENT_WEIGHT = _consensus_weight("VALUATION_AGENT_WEIGHT", 0.35)

# #3154 (UXC-1 S1): the two remaining directional voters get the same seam —
# NewsSentiment was hardcoded 0.35 (Review B3), UpsideSkew had the config key
# but never read it (Review B4). Unset env ⇒ byte-identical defaults.
_NEWS_SENTIMENT_WEIGHT = _consensus_weight("NEWS_SENTIMENT_WEIGHT", 0.35)
_UPSIDE_SKEW_WEIGHT = _consensus_weight("UPSIDE_SKEW_WEIGHT", 0.30)


def _agent_enabled(flag_name: str) -> bool:
    """#3154 (UXC-1 S1): parametrisierter Master-Gate-Helper für die per-Agent
    Enable-Flags der direktionalen Voter (Option B des Rev.-2-Dual-Designs —
    Hausmuster ``_upside_skew_enabled``, ein Helper statt fünf Kopien).

    Drei Zustände:
    - Flag ``true``/unset (getattr-Default) → Agent stimmt wie heute (byte-
      identischer Normalfall, kein Log).
    - Flag ``false`` → Aufrufer liefert einen Abstain-Record ("EXCLUDED — …").
    - ``get_config()`` wirft → **FAIL-CLOSED**: False (Abstain) + WARNING.

    Die Fail-Closed-Regel ist eine BEWUSSTE Abweichung vom Fail-open-Hausmuster
    (``_upside_skew_enabled``: Lesefehler ⇒ Default) — dort ist der Default
    ``false``/dark, hier wäre "Default zurückgeben" bei Default-``true``-Flags
    fail-open (Archon-Audit #3154: ein per Konfiguration deaktivierter Agent
    darf bei kaputtem Config-Read nicht still weiterstimmen). Abstain statt
    Exception, weil eine Exception im Runner nur als maskierter Modellausfall
    landet (runner.py return_exceptions-Pfad + MLWatchdog-Eskalation) — gleiche
    Konsens-Wirkung, schlechtere Observability. WARNING, nie DEBUG (§5.6).
    """
    try:
        return bool(getattr(config.get_config(), flag_name, True))
    except Exception:  # noqa: BLE001 — fail CLOSED: unreadable flag ⇒ abstain
        logger.warning(
            "Enable-Flag %s unlesbar — FAIL-CLOSED: Agent enthält sich diese "
            "Runde (Abstain statt Stimme).",
            flag_name,
            exc_info=True,
        )
        return False


def _disabled_abstain(agent_name: str, symbol: str) -> VoteResult:
    """#3154: Abstain-Record eines per Konfiguration deaktivierten Voters.
    weight 0.0 DIREKT auf dem VoteResult (nie via self.weight — base_agent
    würde auf min_weight hochklemmen = echte Stimme, Review B1)."""
    return VoteResult(
        agent_name=agent_name,
        symbol=symbol,
        score=0.5,
        weight=0.0,
        reasoning=f"EXCLUDED — {agent_name} deactivated by configuration",
    )


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

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        # #3154 Rev. 3: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("DRAWDOWN_GUARD_AGENT_ENABLED"):
            return _disabled_abstain("DrawdownGuardAgent", state["symbol"])

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

        if used_fallback:
            if high <= 0:
                # #2980 (§5.6): a broken price feed (high <= 0) must REMOVE the
                # guard's vote from the consensus, not inject a confident
                # full-weight neutral 0.5 that dilutes it toward BUY. Abstain
                # (weight 0.0) — a single malformed bar is a data-quality event,
                # not evidence of a real drawdown, so we degrade gracefully while
                # the other agents still vote (abstain, not veto — see ADR-R09).
                drawdown = 0.0
                score = 0.5
                vote_weight = 0.0
                logger.warning(
                    "DrawdownGuardAgent: invalid OHLC for %s (high=%.4f <= 0) — "
                    "ABSTAIN (weight 0.0), vote excluded from consensus (§5.6).",
                    symbol,
                    high,
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

        return VoteResult(
            agent_name="DrawdownGuardAgent",
            symbol=symbol,
            score=score,
            weight=vote_weight,
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

        # #3154: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("SPECIALIST_ALPHA_AGENT_ENABLED"):
            return _disabled_abstain("SpecialistAlphaAgent", symbol)

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
        # #3154 Rev. 3: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("REGIME_DETECTION_AGENT_ENABLED"):
            return _disabled_abstain("RegimeDetectionAgent", state["symbol"])

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

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        # #3154: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("MOMENTUM_AGENT_ENABLED"):
            return _disabled_abstain("MomentumAgent", state["symbol"])

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
# 5. VIXAwareRiskAgent (w:0.45) — die erwartete Schwankung des Titels
# ---------------------------------------------------------------------------
#
# #3038 (TRD-10) removed the last remnants of the original volume-as-stress-proxy
# idea (`_NORMAL_VOLUME_REF`, `_VIX_VOLUME_THRESHOLD` — dead constants, and a
# heading that had claimed "Volume-Inverse" long after the input became the VIX).
# Measured, the old idea does not carry: per-symbol volume against excess return is
# r = +0.040 (10 d) and +0.002 (20 d).


def _vixaware_implied_vol() -> bool:
    """#3038 (TRD-10): config-gated per-symbol input for VIXAwareRiskAgent.

    Measured over 29,395 live decisions the agent's score spans 0.754..0.773, is
    identical for every symbol on 17 of 23 days, and is **bit-identical to
    RegimeDetectionAgent in 26,755 of 26,755 cases** — same sigmoid (`:891` vs
    `:577`), same market-wide input. The consensus compares the symbols of one day
    against each other, so a quantity equal for all of them separates nobody; it
    only shifts the level, and blocks 0.3 % of decisions while carrying 22 % weight.

    ON substitutes the symbol's own ATM-30d implied volatility, ranked against the
    PREVIOUS day's cross-section. Evidence: 71,559 symbol-days, IV against maximum
    drawdown r = -0.330, negative in ten of ten quarters, with an accelerating
    decile profile (research/iv_study.py). The criterion is DRAWDOWN, not return —
    the return correlation flips sign between periods, which is why an earlier,
    return-based version of this finding was discarded (VISION_AND_GOALS §3).

    **Seit #3044 ist der Flag default AN** (``config.py``: ``VIXAWARE_IMPLIED_VOL_ENABLED``
    default ``"true"``) — der per-Symbol-IV-Pfad ist damit das Live-Verhalten; der marktweite
    VIX-Zweig (und dessen bit-identische Deckung mit RegimeDetection) ist nur noch der
    Fallback, falls der Flag explizit aus ist.

    Read through ``config.get_config()`` on every call: the single config seam of
    the finance core (CODING_POLICY §2.10), and the only form a sweep can flip
    between two runs of the same process.
    """
    try:
        return bool(getattr(config.get_config(), "VIXAWARE_IMPLIED_VOL_ENABLED", False))
    except Exception:  # pragma: no cover - config must never break the vote path
        return False


def _iv_percentile(value: float, reference: list) -> Optional[float]:
    """Rank ``value`` inside ``reference`` — strictly monotone, no ties, in (0,1).

    Why not a plain step-function percentile: with a fixed reference, two symbols
    falling between the same two reference points would receive the SAME rank. That
    is precisely the defect #3004 fixed for momentum (48.7 % of votes carried an
    identical 1.00), just moved to a different agent. AC-3 pins it.

    Inside the reference range: linear interpolation over mid-rank plotting
    positions ``(below + 0.5*equal) / n``. Mid-rank rather than ``i/(n-1)`` so the
    extremes map to ``0.5/n`` and ``1 - 0.5/n`` instead of hard 0.0 and 1.0 — the
    saturation the sim criterion caps at 2 %.

    Outside it: an algebraic tail rather than a clamp. A volatility jump is exactly
    the case where several symbols sit above everything yesterday knew. If they all
    snapped to the boundary, the voice would separate least in stress — when it
    matters most. The tail is continuous at the boundary, strictly monotone, and
    stays inside (0,1) for every finite input.

    Algebraic and not exponential for a concrete reason: ``exp(-t)`` underflows to
    0.0 in float64 around t = 745, and the first version of this function did snap
    to exactly 1.0 for an IV of 1.20 against a reference of 0.10..0.60 — the very
    tie AC-3 forbids, reintroduced by rounding. ``1/(1+t)`` decays slowly enough to
    stay representable out to t ≈ 1e16.

    Returns None when the reference cannot support a rank (fewer than two distinct
    values) — the caller abstains rather than guessing.
    """
    werte = sorted(
        float(v) for v in reference if v is not None and math.isfinite(float(v))
    )
    n = len(werte)
    if n < 2:
        return None

    # Mid-rank plotting positions over the DISTINCT values. Building them over
    # distinct values keeps the interpolation strictly increasing even when the
    # reference carries repeated quotes.
    einzeln: list = []
    stellen: list = []
    i = 0
    while i < n:
        j = i
        while j < n and werte[j] == werte[i]:
            j += 1
        einzeln.append(werte[i])
        stellen.append((i + 0.5 * (j - i)) / n)
        i = j
    if len(einzeln) < 2:
        return None

    lo, hi = einzeln[0], einzeln[-1]
    if lo <= value <= hi:
        for k in range(1, len(einzeln)):
            if value <= einzeln[k]:
                x0, x1 = einzeln[k - 1], einzeln[k]
                p0, p1 = stellen[k - 1], stellen[k]
                if x1 == x0:
                    return p1
                return p0 + (p1 - p0) * (value - x0) / (x1 - x0)
        return stellen[-1]

    # Tail scale: the average spacing of the reference. Small enough that a genuine
    # outlier is recognisably outside, large enough that the tail does not collapse
    # to the boundary within one step.
    spanne = max((hi - lo) / max(len(einzeln) - 1, 1), 1e-9)
    if value < lo:
        return stellen[0] / (1.0 + (lo - value) / spanne)
    return 1.0 - (1.0 - stellen[-1]) / (1.0 + (value - hi) / spanne)


class VIXAwareRiskAgent(VotingAgent):
    """Bringt das Risiko ein, das die richtungsschätzenden Stimmen nicht sehen.

    Momentum, LSTM und NewsSentiment schätzen die *Richtung*; diese Stimme steuert die
    *Schwankung* bei — als Gegengewicht zur Vorrangregel „Kapitalerhalt vor
    kurzfristigem und risikobehaftetem Gewinn" (VISION_AND_GOALS §3).

    ⚠️ **ROLLE (seit #3094/#3115, Default):** Bei ``IMPLIED_VOL_FORECAST_ENABLED`` (default
    ``True``) ist diese Stimme über ``consensus_exclusions()`` **aus dem direktionalen
    Konsens-Mittel AUSGESCHLOSSEN** (per Name, nicht über das Gewicht) — sie ist ein
    **SIZING-Input** (per-Titel-IV → ``forecast_vol`` im Vol-Targeting-Sizer #1953), **kein
    Richtungs-Votum**. Sie zählt nur dann wieder im Richtungs-Mittel, wenn der Flag aus ist.

    Zwei Eingänge für den (Sizing-)Score, per Flag ``VIXAWARE_IMPLIED_VOL_ENABLED`` (#3038/#3044):

    * **AN (Default, seit #3044 — ``VIXAWARE_IMPLIED_VOL_ENABLED`` default True):** die
      implizite Volatilität des TITELS, als Rang gegen die Verteilung des Vortages:
      ``score = 1 − Perzentil``. Ruhig heißt hoher Score. **Per-Symbol, cross-sektional
      trennend** — NICHT marktweit.
    * **AUS (Fallback, nur wenn Flag off):** marktweiter VIX durch eine Sigmoid um 25 —
      für alle Titel eines Tages derselbe Wert. **Nur in diesem Fallback** deckt es sich
      mit RegimeDetectionAgent; die historische „26.755/26.755 bit-identisch"-Messung
      galt diesem alten Default, NICHT dem heutigen IV-Pfad.

    Fehlt die Datengrundlage, wird **enthalten** statt geraten — Gewicht 0.0 direkt
    im VoteResult, nicht über ``self.weight`` (``min_weight = 0.10`` würde sonst
    greifen, siehe ``vote``).
    """

    default_weight: float = (
        _VIX_RISK_WEIGHT  # #2815: config-gated, unset == historical literal
    )
    min_weight: float = 0.10
    max_weight: float = 1.50

    def _abstain(self, symbol: str, grund: str) -> VoteResult:
        """Enthaltung mit Gewicht 0.0 **direkt im VoteResult**.

        Nicht über ``self.weight``: ``base_agent.py:104`` zieht jedes Gewicht auf
        ``[min_weight, max_weight]`` hoch, und ``min_weight`` ist hier 0.10. Eine
        Stimme ohne Datengrundlage bekäme damit rund 6 % Gewicht im Konsens — eine
        geratene Stimme, die im Log wie eine echte aussieht. Die Klemme ist eine
        Sicherung gegen Redis-Manipulation und kann nicht unterscheiden, ob die Null
        vom Angreifer oder vom Agenten kommt; deshalb der Weg am Gewicht vorbei.

        Der Grund steht im Reasoning, weil im Log sonst nicht unterscheidbar ist, ob
        die Optionskette oder die Referenzverteilung gefehlt hat (AC-8).
        """
        logger.warning("VIXAwareRiskAgent enthält sich für %s: %s", symbol, grund)
        return VoteResult(
            agent_name="VIXAwareRiskAgent",
            symbol=symbol,
            score=0.5,
            weight=0.0,
            reasoning=f"EXCLUDED — {grund}",
        )

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        symbol = state["symbol"]

        # #3154: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("VIX_RISK_AGENT_ENABLED"):
            return _disabled_abstain("VIXAwareRiskAgent", symbol)

        if _vixaware_implied_vol():
            return self._vote_implied_vol(state, symbol)

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

    def _vote_implied_vol(self, state: "SymbolEvalState", symbol: str) -> VoteResult:
        """#3038: Rang der erwarteten Schwankung gegen die Verteilung des Vortages.

        Ausschließlich aus abgeschlossenen Zyklen (AC-7): gelesen wird nur
        ``implied_vol_reference``, das der Erzeuger aus dem Vortag füllt. Werte des
        laufenden Zyklus rührt der Agent nicht an — sonst bewertete er einen Titel
        gegen eine Verteilung, die ihn selbst schon enthält, und der Sim zeigte
        einen Vorteil, den es live nicht gibt.
        """
        iv = state.get("implied_vol")
        if iv is None:
            return self._abstain(symbol, "no implied volatility for this symbol today")
        iv = float(iv)
        if not math.isfinite(iv) or iv <= 0:
            return self._abstain(symbol, f"implied volatility not usable ({iv!r})")

        referenz = state.get("implied_vol_reference")
        if not referenz or len(referenz) < 2:
            return self._abstain(
                symbol, "no reference distribution from a completed cycle yet"
            )

        perzentil = _iv_percentile(iv, list(referenz))
        if perzentil is None:
            return self._abstain(
                symbol, "reference distribution carries no distinct values"
            )

        score = self._clamp(1.0 - perzentil)
        datum = state.get("implied_vol_reference_date") or "unknown"
        reasoning = (
            f"Expected swing for this stock (30-day implied volatility) at "
            f"{iv:.4f} — rank {perzentil:.3f} against the {len(referenz)} symbols "
            f"of the previous session ({datum}), so calmer than "
            f"{100.0 * (1.0 - perzentil):.0f} % of them (score {score:.3f}). "
            f"Unlike the market-wide VIX this is about THIS stock. "
            f"[src: ATM-30d implied volatility, reference {datum}]"
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

    default_weight: float = (
        _LSTM_SIGNAL_WEIGHT  # #2815: config-gated, unset == historical literal
    )
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
        from core.report.lstm_panel_store import (
            active_cross_section,
            cross_section_standing,
            get_store,
        )
        from core.sim.clock import engine_now

        # #2630: read the panel AS OF the engine's current day — the sim clock's date under
        # SIM_MODE, else byte-identical to datetime.now(). Was datetime.now(): the sim queried the
        # wall-clock date (no panel → n=0 → LSTM abstains → strict-ML-gate hard-blocks every symbol).
        as_of = engine_now(timezone.utc).date()
        # #2680: the ONE ranking seam. LSTM_RANK_SMOOTHING_ENABLED off (default) →
        # cross_section_at, byte-identical. ON → the trend (median) table, so a
        # one-day spike can no longer produce a confident BUY vote (the IVZ case:
        # bought point-in-time top-10, rank 242 three hours later).
        xsec = active_cross_section(get_store(), as_of)

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

        # #3154: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("LSTM_SIGNAL_AGENT_ENABLED"):
            return _disabled_abstain("LSTMSignalAgent", symbol)

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
        # #3154 Rev. 3: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("RL_CONFIDENCE_AGENT_ENABLED"):
            return _disabled_abstain("RLConfidenceAgent", state["symbol"])

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

    default_weight: float = _NEWS_SENTIMENT_WEIGHT  # #3154: Env-Naht (Default 0.35)
    min_weight: float = 0.10
    max_weight: float = 1.50

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        symbol = state["symbol"]

        # #3154: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("NEWS_SENTIMENT_AGENT_ENABLED"):
            return _disabled_abstain("NewsSentimentAgent", symbol)

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
    symbol: str, close: Optional[float] = None, as_of: Optional[str] = None
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

        # #3145 (3): read fundamentals AS OF the evaluation date — state["current_time"]
        # during a backtest — NOT wall-clock now. Otherwise a walk-forward sees filings
        # that did not exist yet (look-ahead). Fall back to today when absent (live),
        # mirroring the graph/runner current_time handling.
        as_of_date = _dt.now(_tz.utc).date()
        if as_of:
            try:
                as_of_date = _dt.fromisoformat(str(as_of)).date()
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid as_of format '%s' for symbol %s, falling back to today",
                    as_of,
                    symbol,
                )
        lookup = {symbol: float(close)} if close and float(close) > 0 else None
        fund = CachedFundamentalsReader(close_lookup=lookup).get_fundamentals(
            symbol, as_of_date
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

    default_weight: float = (
        _FUNDAMENTALS_AGENT_WEIGHT  # #3145(3): config-gated, active default 0.35 (owner 2026-09-03)
    )
    min_weight: float = 0.0
    max_weight: float = 1.50

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        # #3154 Rev. 3: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("FUNDAMENTALS_AGENT_ENABLED"):
            return _disabled_abstain("FundamentalsAgent", state["symbol"])

        import asyncio

        symbol = state["symbol"]
        close = (state.get("ohlc") or {}).get("close")
        fund = await asyncio.to_thread(
            _read_pit_fundamentals, symbol, close, state.get("current_time")
        )
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


def _valuation_multimetric_enabled() -> bool:
    """#3146: gate for the ValuationAgent PEG -> P/S / P/B fallback. Default OFF (dark)
    — the agent is weight=0 anyway; ON only widens which names it can speak on."""
    try:
        return bool(
            getattr(config.get_config(), "VALUATION_MULTIMETRIC_ENABLED", False)
        )
    except Exception:  # noqa: BLE001 — a config read must never break a vote
        logger.exception("Fehler beim Lesen der VALUATION_MULTIMETRIC_ENABLED Config")
        return False


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

    # ADR-RT-VAL-02 (#3146): PEG-fallback multiples — redaktionelle Startkalibrierung,
    # KEINE Modell-Ausgabe (gehört in den Walk-forward, wie VAL-01). P/S<1 / P/B<1 =
    # günstig; über der oberen Grenze = gestreckt.
    PS_CHEAP: float = 1.0
    PS_STRETCHED: float = 10.0
    PB_CHEAP: float = 1.0
    PB_STRETCHED: float = 3.0

    default_weight: float = (
        _VALUATION_AGENT_WEIGHT  # #3146: config-gated, active default 0.35 (owner 2026-09-03)
    )
    min_weight: float = 0.0
    max_weight: float = 1.50

    def _multimetric_vote(self, fund, symbol, as_of_txt: str):
        """#3146: entity-appropriate PEG fallback. Returns a VoteResult or None.

        P/S for names without positive earnings (unprofitable growth); P/B where a
        book value exists (asset-heavy / financials). P/FFO (REITs) is Phase 2. Only
        a metric that is actually present produces a vote — never a fabricated one.
        """
        revenue = (fund or {}).get("revenue")
        ps = (fund or {}).get("ps")
        pb = (fund or {}).get("pb")
        if ps is not None and revenue:
            s = float(ps)
            score = (
                0.65 if s < self.PS_CHEAP else (0.35 if s > self.PS_STRETCHED else 0.5)
            )
            return VoteResult(
                agent_name="ValuationAgent",
                symbol=symbol,
                score=self._clamp(score),
                weight=self.weight,
                reasoning=(
                    f"Valuation (no positive earnings) — price-to-sales {s:.2f}"
                    f"{as_of_txt}. [src: audited annual filings]"
                ),
            )
        if pb is not None:
            b = float(pb)
            score = (
                0.65 if b < self.PB_CHEAP else (0.35 if b > self.PB_STRETCHED else 0.5)
            )
            return VoteResult(
                agent_name="ValuationAgent",
                symbol=symbol,
                score=self._clamp(score),
                weight=self.weight,
                reasoning=(
                    f"Valuation (book-value basis) — price-to-book {b:.2f}"
                    f"{as_of_txt}. [src: audited annual filings]"
                ),
            )
        return None

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        # #3154 Rev. 3: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("VALUATION_AGENT_ENABLED"):
            return _disabled_abstain("ValuationAgent", state["symbol"])

        import asyncio

        symbol = state["symbol"]
        # The price is what makes `pe` exist at all — the feed derives it from
        # price ÷ per-share earnings, so without it every price ratio is None.
        close = (state.get("ohlc") or {}).get("close")
        fund = await asyncio.to_thread(
            _read_pit_fundamentals, symbol, close, state.get("current_time")
        )
        pe = (fund or {}).get("pe")
        # PEG is defined on EARNINGS growth: `eps_yoy`, a FRACTION in the feed.
        growth = _pct((fund or {}).get("eps_yoy"))
        as_of_txt = ""
        if fund:
            as_of = fund.get("filing_date") or fund.get("as_of")
            if as_of:
                as_of_txt = f" (as of {as_of})"

        if not fund or pe is None or growth is None or float(growth) <= 0:
            # #3146: PEG is undefined (loss-makers / financials / REITs). Fall back to
            # an entity-appropriate multiple where the data supports one, instead of
            # abstaining. Flag OFF (default) => byte-identical PEG-only behaviour.
            if _valuation_multimetric_enabled():
                mm = self._multimetric_vote(fund, symbol, as_of_txt)
                if mm is not None:
                    return mm
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


def _upside_skew_enabled() -> bool:
    """#3095 (b): master gate for the UpsideSkewAgent (25Δ risk-reversal). Default
    OFF (dark) — the agent abstains, byte-identical, until an owner arms it."""
    try:
        return bool(getattr(config.get_config(), "UPSIDE_SKEW_AGENT_ENABLED", False))
    except Exception:  # noqa: BLE001 — a config read must never break a vote
        logger.exception("Fehler beim Lesen der UPSIDE_SKEW_AGENT_ENABLED Config")
        return False


class UpsideSkewAgent(VotingAgent):
    """#3095 (b): DIRECTIONAL upside signal from the options SKEW.

    The IV *level* (VIXAware) is directionless; the SKEW is not. The 25-delta
    risk-reversal ``RR = IV(25Δ Call) − IV(25Δ Put)`` is positive when the market
    pays up for UPSIDE — a bullish tilt. Score = percentile rank of this symbol's RR
    against the PREVIOUS session's cross-section (AC-7): high RR → high score → BUY
    pressure. This is a real direction voter (unlike the risk-only VIXAware), so it
    joins ``_DIRECTIONAL_VOTER_NAMES`` and is damped with the others in risk-off.

    Reads only completed-cycle state channels (producer = Step-2): ``risk_reversal``
    (today, this symbol), ``risk_reversal_reference`` (yesterday's distribution),
    ``risk_reversal_reference_date``. Missing data → abstain (weight 0.0, never a
    guessed vote). Flag OFF → abstain (dark, byte-identical).
    """

    default_weight: float = (
        _UPSIDE_SKEW_WEIGHT  # #3154: Env-Naht verdrahtet (Default 0.30)
    )
    min_weight: float = 0.10
    max_weight: float = 1.50

    def _abstain(self, symbol: str, grund: str) -> VoteResult:
        """Abstention with weight 0.0 DIRECTLY on the VoteResult (never via
        self.weight, which base_agent would clamp up to min_weight = a real vote)."""
        return VoteResult(
            agent_name="UpsideSkewAgent",
            symbol=symbol,
            score=0.5,
            weight=0.0,
            reasoning=f"EXCLUDED — {grund}",
        )

    async def vote(self, state: "SymbolEvalState") -> VoteResult:
        symbol = state["symbol"]
        if not _upside_skew_enabled():
            return self._abstain(symbol, "UpsideSkewAgent dark (flag off)")

        from core.options_skew import rr_percentile

        rr = state.get("risk_reversal")
        if rr is None:
            return self._abstain(symbol, "no risk-reversal for this symbol today")
        try:
            rr = float(rr)
        except (TypeError, ValueError):
            return self._abstain(symbol, f"risk-reversal not usable ({rr!r})")
        if not math.isfinite(rr):
            return self._abstain(symbol, "risk-reversal not finite")

        referenz = state.get("risk_reversal_reference")
        if not referenz or len(referenz) < 2:
            return self._abstain(
                symbol, "no reference distribution from a completed cycle yet"
            )

        perzentil = rr_percentile(rr, list(referenz))
        if perzentil is None:
            return self._abstain(
                symbol, "reference distribution carries no distinct values"
            )

        # HIGH risk-reversal (bullish skew) → HIGH score (unlike the risk-only,
        # inverted VIXAware). Direction, not magnitude.
        score = self._clamp(perzentil)
        datum = state.get("risk_reversal_reference_date") or "unknown"
        reasoning = (
            f"Options skew (25Δ risk-reversal) for this stock at {rr:+.4f} — rank "
            f"{perzentil:.3f} against the {len(referenz)} symbols of the previous "
            f"session ({datum}); a positive/high skew means the market pays up for "
            f"UPSIDE (score {score:.3f}). [src: 25Δ call/put implied vol, ref {datum}]"
        )
        return VoteResult(
            agent_name="UpsideSkewAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            reasoning=reasoning,
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
    # #3095 (b): flag-gated internally (abstains when UPSIDE_SKEW_AGENT_ENABLED off)
    # → byte-identical while dark, like VIXAwareRiskAgent's own #3038 switch.
    UpsideSkewAgent(),
]
