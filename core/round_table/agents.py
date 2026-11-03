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

import importlib
import logging
import math
import re
import sys
import time
from datetime import datetime, timezone
from types import ModuleType
from typing import TYPE_CHECKING, Optional, Tuple

import config
from core.contracts.signal_candidate import AbstainReason, SignalCandidate
from core.round_table.base_agent import VoteResult, VotingAgent

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

logger = logging.getLogger(__name__)


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

# #1968 audit follow-up: on history-poor desktops the fallback fires for most of the
# ~500-symbol universe EVERY cycle — an unthrottled per-abstain WARNING floods the log.
# Once-per-symbol-per-session throttle: the FIRST abstain per symbol logs WARNING
# (CODING_POLICY §5.6 — the fallback substitution itself is announced at WARNING),
# repeats for the same symbol drop to DEBUG. Session-scoped module state by design.
_MOMENTUM_ABSTAIN_WARNED: set = set()


def set_specialist_registry(registry: object) -> None:
    """Injects the active StockSpecialistRegistry into the Round Table (Epic 3.3)."""
    global _specialist_registry_instance
    _specialist_registry_instance = registry  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Gemeinsamer Unterbau — #3831 (G-6a): liegt in agenten/_basis.py, hier zurückimportiert,
# damit `from core.round_table.agents import X` für alle Importeure gültig bleibt.
# Custom Exceptions — Fail-Fast Architecture (Anti-Watermelon) — dort.
# ---------------------------------------------------------------------------

from core.round_table.agenten._basis import (  # noqa: E402,F401 — Re-Export (#3831)
    _SHARED_UNSET,
    DependencyLostException,
    SuspectDataException,
    _agent_enabled,
    _consensus_weight,
    _disabled_abstain,
)

# #4085 (G-6c): DRAWDOWN_WINDOW_TRADING_DAYS und die Gewichte von DrawdownGuard, Regime,
# Momentum und VIX-Aware stehen jetzt in ihren Agenten-Modulen (Re-Export unten).

# #4086 (G-6d): die Gewichte von Fundamentals, Valuation und NewsSentiment stehen jetzt in
# ihren Agenten-Modulen (Re-Export unten).

# #4110 (G-6e): die Gewichte von Upside-Skew, Trend, Volume und Quality stehen jetzt in
# ihren Agenten-Modulen (Re-Export unten).


# ---------------------------------------------------------------------------
# #4084 (G-6b): LSTM-, RL-Confidence- und Specialist-Alpha-Agent liegen in agenten/, hier
# zurückimportiert. Neuladetreu: Lädt ein Test dieses Modul neu (importlib.reload), um ein
# anderes Gewicht zu prüfen, lädt es die Agenten-Module mit neu — Klassen und Gewichte
# entstehen dann neu wie vor dem Umzug, und der Re-Export zeigt auf die neuen Klassen.
# Beim ersten Import ist das ein gewöhnlicher Import. Steht vor ALL_AGENTS, das die
# Klassen instanziiert.
# ---------------------------------------------------------------------------


def _frisch(name: str) -> ModuleType:
    mod = sys.modules.get(name)
    return importlib.reload(mod) if mod is not None else importlib.import_module(name)


_sa = _frisch("core.round_table.agenten.specialist_alpha")
SpecialistAlphaAgent = _sa.SpecialistAlphaAgent
_SPECIALIST_ALPHA_WEIGHT = _sa._SPECIALIST_ALPHA_WEIGHT
_specialist_alpha_weight = _sa._specialist_alpha_weight

_ls = _frisch("core.round_table.agenten.lstm_signal")
LSTMSignalAgent = _ls.LSTMSignalAgent
_LSTM_SIGNAL_WEIGHT = _ls._LSTM_SIGNAL_WEIGHT
_LSTM_VOTE_SCALE = _ls._LSTM_VOTE_SCALE
_MIN_PANEL_SYMBOLS = _ls._MIN_PANEL_SYMBOLS

_rl = _frisch("core.round_table.agenten.rl_confidence")
RLConfidenceAgent = _rl.RLConfidenceAgent
_RL_CONFIDENCE_WEIGHT = _rl._RL_CONFIDENCE_WEIGHT
_rl_confidence_weight = _rl._rl_confidence_weight

# #4085 (G-6c): Drawdown-Guard-, Regime-, Momentum- und VIX-Aware-Agent liegen in agenten/,
# hier ebenso neuladetreu zurueckimportiert.
_dd = _frisch("core.round_table.agenten.drawdown_guard")
DrawdownGuardAgent = _dd.DrawdownGuardAgent
DRAWDOWN_WINDOW_TRADING_DAYS = _dd.DRAWDOWN_WINDOW_TRADING_DAYS
_DRAWDOWN_GUARD_WEIGHT = _dd._DRAWDOWN_GUARD_WEIGHT
_drawdown_conditioner = _dd._drawdown_conditioner
_drawdown_guard_veto_enabled = _dd._drawdown_guard_veto_enabled

_rg = _frisch("core.round_table.agenten.regime")
RegimeDetectionAgent = _rg.RegimeDetectionAgent
_REGIME_DETECTION_WEIGHT = _rg._REGIME_DETECTION_WEIGHT
_REGIME_LABEL_VIX = _rg._REGIME_LABEL_VIX
_regime_conditioner_cfg = _rg._regime_conditioner_cfg

_mo = _frisch("core.round_table.agenten.momentum")
MomentumAgent = _mo.MomentumAgent
_MOMENTUM_AGENT_WEIGHT = _mo._MOMENTUM_AGENT_WEIGHT
_MOMENTUM_SCORE_SCALE = _mo._MOMENTUM_SCORE_SCALE
_momentum_score_smooth = _mo._momentum_score_smooth
_momentum_fallback_abstain = _mo._momentum_fallback_abstain

_vx = _frisch("core.round_table.agenten.vix_aware")
VIXAwareRiskAgent = _vx.VIXAwareRiskAgent
_VIX_RISK_WEIGHT = _vx._VIX_RISK_WEIGHT
_vixaware_implied_vol = _vx._vixaware_implied_vol
_iv_percentile = _vx._iv_percentile

# #4086 (G-6d): News-, Fundamentals- und Valuation-Agent liegen in agenten/, die geteilten
# Fundamentaldaten-Helfer in agenten/_fundamentaldaten.py; hier ebenso neuladetreu
# zurueckimportiert. _fundamentaldaten zuerst — fundamentals und valuation lesen es.
# Tests patchen die Helfer dort, wo der Agent sie nachschlaegt, nicht hier
# (bewacht von tests/unit/test_agenten_keine_toten_patches.py).
_fd = _frisch("core.round_table.agenten._fundamentaldaten")
_read_pit_fundamentals = _fd._read_pit_fundamentals
_pct = _fd._pct
_debt_to_equity = _fd._debt_to_equity

_ns = _frisch("core.round_table.agenten.news_sentiment")
NewsSentimentAgent = _ns.NewsSentimentAgent
_NEWS_SENTIMENT_WEIGHT = _ns._NEWS_SENTIMENT_WEIGHT
_LOCAL_SENTIMENT_CACHE = _ns._LOCAL_SENTIMENT_CACHE
_LOCAL_SENTIMENT_CACHE_MAXSIZE = _ns._LOCAL_SENTIMENT_CACHE_MAXSIZE
_news_sentiment_cfg = _ns._news_sentiment_cfg
_news_sentiment_model_dir = _ns._news_sentiment_model_dir

_fu = _frisch("core.round_table.agenten.fundamentals")
FundamentalsAgent = _fu.FundamentalsAgent
_FUNDAMENTALS_AGENT_WEIGHT = _fu._FUNDAMENTALS_AGENT_WEIGHT

_va = _frisch("core.round_table.agenten.valuation")
ValuationAgent = _va.ValuationAgent
_VALUATION_AGENT_WEIGHT = _va._VALUATION_AGENT_WEIGHT
_valuation_multimetric_enabled = _va._valuation_multimetric_enabled

# #4110 (G-6e): Upside-Skew-, Quality-, Trend- und Volume-Agent liegen samt Enable-Gate und
# Gewicht in agenten/, hier ebenso neuladetreu zurueckimportiert. Tests patchen die Gates dort,
# wo der Agent sie nachschlaegt, nicht hier (bewacht von test_agenten_keine_toten_patches.py).
_us = _frisch("core.round_table.agenten.upside_skew")
UpsideSkewAgent = _us.UpsideSkewAgent
_UPSIDE_SKEW_WEIGHT = _us._UPSIDE_SKEW_WEIGHT
_upside_skew_enabled = _us._upside_skew_enabled

_qu = _frisch("core.round_table.agenten.quality")
QualityAgent = _qu.QualityAgent
_QUALITY_AGENT_WEIGHT = _qu._QUALITY_AGENT_WEIGHT
_quality_agent_enabled = _qu._quality_agent_enabled

_tr = _frisch("core.round_table.agenten.trend")
TrendAgent = _tr.TrendAgent
_TREND_AGENT_WEIGHT = _tr._TREND_AGENT_WEIGHT
_trend_agent_enabled = _tr._trend_agent_enabled

_vc = _frisch("core.round_table.agenten.volume_confirmation")
VolumeConfirmationAgent = _vc.VolumeConfirmationAgent
_VOLUME_CONFIRM_AGENT_WEIGHT = _vc._VOLUME_CONFIRM_AGENT_WEIGHT
_VOLUME_RATIO_SCALE = _vc._VOLUME_RATIO_SCALE
_volume_confirm_agent_enabled = _vc._volume_confirm_agent_enabled


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
    # #3250: TA-Feature-Voter — dark (weight 0.0 + *_ENABLED false), abstain byte-
    # identical bis ein Owner sie armt. Lesen nur state["features"]-Skalare.
    TrendAgent(),
    VolumeConfirmationAgent(),
    # #3275: Composite-Quality-Richtungsstimme — dark (weight 0.0 + QUALITY_AGENT_ENABLED
    # false → abstain byte-identisch). Liest denselben PIT-Fundamentals-Feed wie
    # FundamentalsAgent (kein neuer Datenbezug). Mirror in entitlement/tier.py.
    QualityAgent(),
]
