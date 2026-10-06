"""#3831 (ARC-E6 G-6a) — jeder heute aus ``core.round_table.agents`` importierbare Name bleibt es.

Die Agenten wandern in Teilen (G-6b … G-6d) nach ``core/round_table/agenten/``; ``agents.py``
importiert jeden verschobenen Namen zurueck. Die Liste unten ist ``dir(agents)`` vom
03.10.2026 (``origin/main`` vor G-6a), ohne Dunder-Namen, ohne Modulobjekte (``config``,
``logging``, ``math``, ``re``, ``time``) und ohne Typing-Hilfen (``Optional``, ``Tuple``,
``TYPE_CHECKING``, ``annotations``). Private Namen stehen mit drin: Tests importieren und
patchen sie (``_agent_enabled``, ``_consensus_weight``, ``_quality_agent_enabled`` …).

Jeder Agenten-Teil muss diese Liste grün halten. Ein Name faellt nur mit eigener Begruendung
im PR heraus.

Plan: ``docs/3831-*/implementation_plan.md``.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

NAMEN = (
    "ALL_AGENTS",
    "AbstainReason",
    "DRAWDOWN_WINDOW_TRADING_DAYS",
    "DependencyLostException",
    "DrawdownGuardAgent",
    "FundamentalsAgent",
    "LSTMSignalAgent",
    "MomentumAgent",
    "NewsSentimentAgent",
    "QualityAgent",
    "RLConfidenceAgent",
    "RegimeDetectionAgent",
    "SignalCandidate",
    "SpecialistAlphaAgent",
    "SuspectDataException",
    "TrendAgent",
    "UpsideSkewAgent",
    "VIXAwareRiskAgent",
    "ValuationAgent",
    "VolumeConfirmationAgent",
    "VoteResult",
    "VotingAgent",
    "_DRAWDOWN_GUARD_WEIGHT",
    "_FUNDAMENTALS_AGENT_WEIGHT",
    "_LOCAL_SENTIMENT_CACHE",
    "_LOCAL_SENTIMENT_CACHE_MAXSIZE",
    "_LSTM_SIGNAL_WEIGHT",
    "_LSTM_VOTE_SCALE",
    "_MIN_PANEL_SYMBOLS",
    "_MOMENTUM_ABSTAIN_WARNED",
    "_MOMENTUM_AGENT_WEIGHT",
    "_MOMENTUM_SCORE_SCALE",
    "_NEWS_SENTIMENT_WEIGHT",
    "_QUALITY_AGENT_WEIGHT",
    "_REGIME_DETECTION_WEIGHT",
    "_REGIME_LABEL_VIX",
    "_RL_CONFIDENCE_WEIGHT",
    "_SHARED_UNSET",
    "_SPECIALIST_ALPHA_WEIGHT",
    "_SPECIALIST_REGISTRY_AVAILABLE",
    "_SpecialistRegistry",
    "_TREND_AGENT_WEIGHT",
    "_UPSIDE_SKEW_WEIGHT",
    "_VALUATION_AGENT_WEIGHT",
    "_VIX_RISK_WEIGHT",
    "_VOLUME_CONFIRM_AGENT_WEIGHT",
    "_VOLUME_RATIO_SCALE",
    "_agent_enabled",
    "_consensus_weight",
    "_debt_to_equity",
    "_disabled_abstain",
    "_drawdown_conditioner",
    "_drawdown_guard_veto_enabled",
    "_iv_percentile",
    "_momentum_fallback_abstain",
    "_momentum_score_smooth",
    "_news_sentiment_cfg",
    "_pct",
    "_quality_agent_enabled",
    "_read_pit_fundamentals",
    "_regime_conditioner_cfg",
    "_rl_confidence_weight",
    "_specialist_alpha_weight",
    "_specialist_registry_instance",
    "_trend_agent_enabled",
    "_upside_skew_enabled",
    "_valuation_multimetric_enabled",
    "_vixaware_implied_vol",
    "_volume_confirm_agent_enabled",
    "_warmup_warned_symbols",
    "datetime",
    "get_global_registry",
    "get_llm_provider",
    "logger",
    "set_specialist_registry",
    "timezone",
)

# Der Unterbau, der mit G-6a nach agenten/_basis.py umzieht.
UNTERBAU = (
    "DependencyLostException",
    "SuspectDataException",
    "_agent_enabled",
    "_disabled_abstain",
    "_consensus_weight",
)


def fehlende_namen(modul: object, namen: tuple[str, ...]) -> list[str]:
    """Namen der Liste, die das Modul nicht (mehr) traegt."""
    return [n for n in namen if not hasattr(modul, n)]


def test_jeder_name_der_liste_bleibt_importierbar():
    from core.round_table import agents

    assert fehlende_namen(agents, NAMEN) == []


def test_gegenprobe_ein_unbekannter_name_ist_rot():
    from core.round_table import agents

    assert fehlende_namen(agents, NAMEN + ("GibtEsNichtAgent",)) == ["GibtEsNichtAgent"]


@pytest.mark.parametrize("name", UNTERBAU)
def test_der_unterbau_liegt_in_agenten_basis(name):
    from core.round_table import agents
    from core.round_table.agenten import _basis

    assert getattr(agents, name) is getattr(_basis, name)
    assert getattr(_basis, name).__module__ == "core.round_table.agenten._basis"


def test_ein_patch_auf_den_modulpfad_greift_im_agenten_modul(tmp_path, monkeypatch):
    """Ein Modul, das nach der Zugriffsregel liest, sieht den Patch auf ``agents``."""
    (tmp_path / "probe_agent_3831.py").write_text(
        textwrap.dedent(
            """\
            from core.round_table import agents as _ag

            def registry():
                return _ag.get_global_registry()
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "probe_agent_3831", raising=False)
    probe = importlib.import_module("probe_agent_3831")

    marke = object()
    with patch("core.round_table.agents.get_global_registry", return_value=marke):
        assert probe.registry() is marke
