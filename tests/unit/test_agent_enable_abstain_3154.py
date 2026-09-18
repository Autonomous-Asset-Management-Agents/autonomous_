# tests/unit/test_agent_enable_abstain_3154.py
# UXC-1 S1 (#3154, Epic #3151) — per-Agent-Enable-Flags mit Abstain-Semantik
# + Gewichts-Nähte (NEWS_SENTIMENT_WEIGHT, UPSIDE_SKEW_WEIGHT) + kanonische
# Sizing-/Stop-Env-Nähte. Plan Rev. 2 (Archon-Audit eingearbeitet):
#
#   - Toggle-Mechanik = Option B: EIN parametrisierter Gate-Helper
#     `_agent_enabled(flag_name)` + Gate am vote()-Anfang der 5 direktionalen
#     Voter (Hausmuster _upside_skew_enabled/_abstain, agents.py:2061-2100).
#     NICHT Weight→0 (min_weight-Klemme base_agent.py:104), NICHT Vote-Drop
#     (Record-Sichtbarkeit), NICHT Konsens-Filter (Inferenz-Verschwendung).
#   - FAIL-CLOSED (Audit-Korrektur): get_config-Lesefehler ⇒ ABSTAIN + WARNING
#     (nie DEBUG, §5.6) — bewusste Abweichung vom Fail-open-Hausmuster
#     agents.py:2066, weil "Default zurückgeben" bei Default-true-Enable-Flags
#     fail-open wäre. Eine Exception statt Abstain würde im MLWatchdog als
#     Modellausfall maskiert (runner.py:594-607).
#   - Alles default-inert: Flags default true (unset ⇒ byte-identisch),
#     Gewichts-/Sizing-Defaults unverändert.

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/

# (Agent-Klasse, Flag-Name) — die 5 gegateten direktionalen Voter (S1 Inkrement 1).
# UpsideSkew hat sein Gate bereits (UPSIDE_SKEW_AGENT_ENABLED, dark) — Regression R9.
AGENT_FLAGS = [
    ("MomentumAgent", "MOMENTUM_AGENT_ENABLED"),
    ("LSTMSignalAgent", "LSTM_SIGNAL_AGENT_ENABLED"),
    ("SpecialistAlphaAgent", "SPECIALIST_ALPHA_AGENT_ENABLED"),
    ("NewsSentimentAgent", "NEWS_SENTIMENT_AGENT_ENABLED"),
    ("VIXAwareRiskAgent", "VIX_RISK_AGENT_ENABLED"),
    # Rev. 3 (Owner-Entscheidung 02.09.2026, ersetzt Epic A1/A2): ALLE Agenten
    # schaltbar — auch Overlay (DrawdownGuard-Veto, Regime) und die dormanten
    # Fundamentals/Valuation/RL-Stimmen.
    ("DrawdownGuardAgent", "DRAWDOWN_GUARD_AGENT_ENABLED"),
    ("RegimeDetectionAgent", "REGIME_DETECTION_AGENT_ENABLED"),
    ("FundamentalsAgent", "FUNDAMENTALS_AGENT_ENABLED"),
    ("ValuationAgent", "VALUATION_AGENT_ENABLED"),
    ("RLConfidenceAgent", "RL_CONFIDENCE_AGENT_ENABLED"),
]

MINIMAL_STATE = {
    "symbol": "BNY",
    "ohlc": {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1.0},
    "market_data_keys": [],
    "current_time": "2026-09-02T14:00:00+00:00",
    "signal": None,
    "error": None,
}


def _cfg(monkeypatch, **overrides):
    """get_config auf ein SimpleNamespace pinnen (alle 3 Importpfade — CI-Falle)."""
    import config

    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(**overrides))


def _agent(name):
    from core.round_table import agents as agents_mod

    return getattr(agents_mod, name)()


# ---------------------------------------------------------------------------
# 1. Gate-Helper — drei Zustände, fail-closed (R1)
# ---------------------------------------------------------------------------


def test_r1a_helper_unset_defaults_true(monkeypatch):
    from core.round_table.agents import _agent_enabled

    _cfg(monkeypatch)  # Flag nicht gesetzt → getattr-Default true, kein Log
    assert _agent_enabled("MOMENTUM_AGENT_ENABLED") is True


def test_r1b_helper_false(monkeypatch):
    from core.round_table.agents import _agent_enabled

    _cfg(monkeypatch, MOMENTUM_AGENT_ENABLED=False)
    assert _agent_enabled("MOMENTUM_AGENT_ENABLED") is False


def test_r1c_helper_config_error_fails_closed_and_warns(monkeypatch, caplog):
    """FAIL-CLOSED (Audit): Lesefehler ⇒ False (Abstain) + WARNING, nie DEBUG."""
    import config
    from core.round_table.agents import _agent_enabled

    def _boom():
        raise RuntimeError("config kaputt")

    monkeypatch.setattr(config, "get_config", _boom)
    with caplog.at_level(logging.WARNING):
        assert _agent_enabled("MOMENTUM_AGENT_ENABLED") is False
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# 2. Abstain am vote()-Anfang je Agent (R2/R3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls_name,flag", AGENT_FLAGS)
async def test_r2_flag_false_abstains(monkeypatch, cls_name, flag):
    """Flag aus ⇒ Abstain-VoteResult (weight 0.0 direkt, score 0.5, EXCLUDED) —
    OHNE Daten-/Modellzugriff (Gate sitzt vor allem anderen)."""
    _cfg(monkeypatch, **{flag: False})
    vote = await _agent(cls_name).vote(dict(MINIMAL_STATE))

    assert vote.weight == 0.0, "Abstain muss weight 0.0 DIREKT tragen (Klemmen-Bypass)"
    assert vote.score == 0.5
    assert "EXCLUDED" in vote.reasoning
    assert (
        "configuration" in vote.reasoning.lower()
        or "deaktiviert" in vote.reasoning.lower()
    )
    assert vote.agent_name == cls_name


@pytest.mark.parametrize("cls_name,flag", AGENT_FLAGS)
async def test_r3_config_error_abstains_and_warns(monkeypatch, caplog, cls_name, flag):
    """FAIL-CLOSED je Agent: get_config wirft ⇒ Abstain + WARNING."""
    import config

    def _boom():
        raise RuntimeError("config kaputt")

    monkeypatch.setattr(config, "get_config", _boom)
    with caplog.at_level(logging.WARNING):
        vote = await _agent(cls_name).vote(dict(MINIMAL_STATE))

    assert vote.weight == 0.0
    assert "EXCLUDED" in vote.reasoning
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# 3. Config-Parität: Flags default true, UpsideSkew bleibt dark (R4/R9)
# ---------------------------------------------------------------------------


def _load_oss():
    spec = importlib.util.spec_from_file_location(
        "config_oss_s1_parity", _AI_BOT / "config.oss.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r4_enable_flags_default_true_both_editions():
    import config

    cfg = config.get_config()
    oss = _load_oss()
    for _, flag in AGENT_FLAGS:
        assert getattr(cfg, flag) is True, f"config.py: {flag} muss default true sein"
        assert (
            getattr(oss, flag) is True
        ), f"config.oss.py: {flag} muss default true sein"


def test_r9_upside_skew_stays_dark():
    import config

    assert config.get_config().UPSIDE_SKEW_AGENT_ENABLED is False
    assert _load_oss().UPSIDE_SKEW_AGENT_ENABLED is False


# ---------------------------------------------------------------------------
# 4. Gewichts-Nähte (R5/R6)
# ---------------------------------------------------------------------------


def test_r5_news_sentiment_weight_seam():
    """NEWS_SENTIMENT_WEIGHT: kanonisch in beiden Configs (0.35) + im
    Agenten-Modul über den _consensus_weight-Seam aufgelöst."""
    import config
    from core.round_table import agents as agents_mod

    assert config.get_config().NEWS_SENTIMENT_WEIGHT == pytest.approx(0.35)
    assert _load_oss().NEWS_SENTIMENT_WEIGHT == pytest.approx(0.35)
    assert agents_mod._NEWS_SENTIMENT_WEIGHT == pytest.approx(0.35)
    assert agents_mod.NewsSentimentAgent.default_weight == pytest.approx(
        agents_mod._NEWS_SENTIMENT_WEIGHT
    )


def test_r6_upside_skew_weight_wired():
    """UPSIDE_SKEW_WEIGHT (config existiert, Default 0.30) wird vom Agenten
    konsumiert statt hardcoded (Review B4)."""
    from core.round_table import agents as agents_mod

    assert agents_mod._UPSIDE_SKEW_WEIGHT == pytest.approx(0.30)
    assert agents_mod.UpsideSkewAgent.default_weight == pytest.approx(
        agents_mod._UPSIDE_SKEW_WEIGHT
    )


# ---------------------------------------------------------------------------
# 5. Kanonische Sizing-/Stop-Nähte (R7/R8)
# ---------------------------------------------------------------------------

SIZING_DEFAULTS = {
    "STOP_LOSS_PCT": 7.0,
    "TAKE_PROFIT_PCT": 25.0,
    "MIN_POSITION_PERCENT": 0.05,
    "MAX_POSITION_PERCENT": 0.25,
    "MAX_TOTAL_EXPOSURE_PCT": 0.95,
    "MAX_POSITION_PERCENT_SIZING": 0.30,
}


def test_r7_sizing_defaults_unchanged_both_editions():
    import config

    cfg = config.get_config()
    oss = _load_oss()
    for key, default in SIZING_DEFAULTS.items():
        assert getattr(cfg, key) == pytest.approx(default), f"config.py {key}"
        assert getattr(oss, key) == pytest.approx(default), f"config.oss.py {key}"


def test_r7b_sizing_env_seam_effective_oss(monkeypatch):
    """Env-Override erreicht die OSS-Config (frischer Modul-Load mit env)."""
    monkeypatch.setenv("STOP_LOSS_PCT", "5.5")
    monkeypatch.setenv("MAX_POSITION_PERCENT_SIZING", "0.28")
    oss = _load_oss()
    assert oss.STOP_LOSS_PCT == pytest.approx(5.5)
    assert oss.MAX_POSITION_PERCENT_SIZING == pytest.approx(0.28)


def test_r8_risk_manager_finds_canonical_sizing(monkeypatch):
    """Der getattr-Fallback im Sizer findet jetzt das kanonische Attribut —
    ein via get_config gepinnter Wert wirkt (kein stiller 0.30-Fallback mehr)."""
    import config

    _cfg(
        monkeypatch,
        ENABLE_DYNAMIC_SIZING=True,
        MIN_POSITION_PERCENT=0.05,
        MAX_POSITION_PERCENT_SIZING=0.22,
        MAX_POSITION_PERCENT=0.25,
    )
    cfg = config.get_config()
    assert getattr(cfg, "MAX_POSITION_PERCENT_SIZING", 0.30) == pytest.approx(0.22)


# ---------------------------------------------------------------------------
# 6. Rev. 3 — Overlay-Gates sind wirklich inert (R10)
# ---------------------------------------------------------------------------


async def test_r10_disabled_drawdown_guard_cannot_veto(monkeypatch):
    """DRAWDOWN_GUARD_AGENT_ENABLED=false ⇒ Abstain OHNE vetoed-Flag — der
    Pre-trade-Veto-Pfad ist komplett aus (Gate sitzt vor der Drawdown-Rechnung)."""
    _cfg(monkeypatch, DRAWDOWN_GUARD_AGENT_ENABLED=False)
    # Zustand, der bei aktivem Guard veto-verdaechtig waere (tiefer Tageskanal):
    state = dict(MINIMAL_STATE)
    state["ohlc"] = {
        "open": 100.0,
        "high": 100.0,
        "low": 80.0,
        "close": 81.0,
        "volume": 1.0,
    }
    vote = await _agent("DrawdownGuardAgent").vote(state)

    assert vote.weight == 0.0
    assert not getattr(vote, "vetoed", False), "deaktivierter Guard darf NIE vetoen"
    assert "EXCLUDED" in vote.reasoning


async def test_r10b_disabled_regime_is_no_conditioner(monkeypatch):
    """REGIME_DETECTION_AGENT_ENABLED=false ⇒ Abstain (weight 0.0) — die
    Konsens-Konditionierung filtert weight-0-Stimmen (consensus.py #1949:
    kein Regime-Vote in validated_votes ⇒ keine Daempfung)."""
    _cfg(monkeypatch, REGIME_DETECTION_AGENT_ENABLED=False)
    vote = await _agent("RegimeDetectionAgent").vote(dict(MINIMAL_STATE))

    assert vote.weight == 0.0
    assert vote.score == 0.5
    assert "EXCLUDED" in vote.reasoning
