"""#3250 — TA-Features → Richtungsstimmen: TrendAgent + VolumeConfirmationAgent.

Zwei schlanke, DARK-geshippte Voter (Gewicht 0.0 + ``*_ENABLED`` false), die die
bereits berechneten Last-Row-Skalare aus ``state["features"]`` lesen
(``core/round_table/features.py`` via ``_compute_features_node``):

* TrendAgent      → ``macd_hist`` (einziges MACD-Signal im Feature-Dict; #3250-Analyse
                    bestätigt: ``macd_line`` wird NICHT nach state["features"] geschrieben).
* VolumeConfirmationAgent → ``vol_ratio_5_20`` (5d/20d-Volumenmittel = die „Volumen-Ratio").

Fehlender Schlüssel / ``features`` None → Enthaltung (weight 0.0 DIREKT auf dem
VoteResult, wie ``UpsideSkewAgent._abstain``).

Flag über ``agents._trend_agent_enabled`` / ``agents._volume_confirm_agent_enabled``
gemonkeypatcht (kein Env-Churn). Vote ist async → mit ``asyncio.run`` getrieben
(Hausmuster test_upside_skew_agent_3095.py; kein async-Plugin nötig). Wo async-
Interfaces gemockt würden, käme ``AsyncMock`` zum Einsatz (CODING_POLICY §5.2) —
hier wird die echte ``vote``-Koroutine direkt ausgeführt, nichts async gemockt.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from core.round_table import agents
from core.round_table.agents import TrendAgent, VolumeConfirmationAgent
from core.round_table.base_agent import VoteResult
from core.round_table.consensus import ConsensusEngine

_ROOT = Path(__file__).resolve().parents[2]


def _run(agent, state):
    return asyncio.run(agent.vote(state))


def _state(**kw):
    base = {"symbol": "AAPL", "features": {"macd_hist": 0.0, "vol_ratio_5_20": 1.0}}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# (c) Fehlendes ``features`` / fehlender Schlüssel → Enthaltung
# ---------------------------------------------------------------------------


def test_trend_flag_off_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_trend_agent_enabled", lambda: False)
    v = _run(TrendAgent(), _state(features={"macd_hist": 5.0}))
    assert v.weight == 0.0
    assert v.score == 0.5
    assert v.agent_name == "TrendAgent"


def test_trend_features_none_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_trend_agent_enabled", lambda: True)
    assert _run(TrendAgent(), _state(features=None)).weight == 0.0


def test_trend_missing_macd_hist_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_trend_agent_enabled", lambda: True)
    assert _run(TrendAgent(), _state(features={"rsi_14": 55.0})).weight == 0.0


def test_trend_non_finite_macd_hist_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_trend_agent_enabled", lambda: True)
    assert (
        _run(TrendAgent(), _state(features={"macd_hist": float("nan")})).weight == 0.0
    )


def test_volume_flag_off_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_volume_confirm_agent_enabled", lambda: False)
    v = _run(VolumeConfirmationAgent(), _state(features={"vol_ratio_5_20": 3.0}))
    assert v.weight == 0.0
    assert v.score == 0.5
    assert v.agent_name == "VolumeConfirmationAgent"


def test_volume_features_none_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_volume_confirm_agent_enabled", lambda: True)
    assert _run(VolumeConfirmationAgent(), _state(features=None)).weight == 0.0


def test_volume_missing_key_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_volume_confirm_agent_enabled", lambda: True)
    assert (
        _run(VolumeConfirmationAgent(), _state(features={"rsi_14": 55.0})).weight == 0.0
    )


# ---------------------------------------------------------------------------
# (b) TrendAgent — Score monoton im MACD-Vorzeichen/-Impuls
# ---------------------------------------------------------------------------


def test_trend_score_monotone_in_macd_hist(monkeypatch):
    monkeypatch.setattr(agents, "_trend_agent_enabled", lambda: True)
    hists = [-3.0, -1.0, -0.2, 0.0, 0.2, 1.0, 3.0]
    scores = [
        _run(TrendAgent(), _state(features={"macd_hist": h})).score for h in hists
    ]
    # strikt monoton steigend
    assert all(a < b for a, b in zip(scores, scores[1:])), scores
    # Vorzeichen: negativ < neutral(0.5) < positiv
    neutral = _run(TrendAgent(), _state(features={"macd_hist": 0.0})).score
    assert neutral == pytest.approx(0.5)
    assert _run(TrendAgent(), _state(features={"macd_hist": -2.0})).score < 0.5
    assert _run(TrendAgent(), _state(features={"macd_hist": 2.0})).score > 0.5


# ---------------------------------------------------------------------------
# (d) VolumeConfirmationAgent — Score monoton in vol_ratio_5_20
# ---------------------------------------------------------------------------


def test_volume_score_monotone_in_vol_ratio(monkeypatch):
    monkeypatch.setattr(agents, "_volume_confirm_agent_enabled", lambda: True)
    ratios = [0.3, 0.7, 1.0, 1.5, 2.5, 5.0]
    scores = [
        _run(VolumeConfirmationAgent(), _state(features={"vol_ratio_5_20": r})).score
        for r in ratios
    ]
    assert all(a < b for a, b in zip(scores, scores[1:])), scores
    # vol_ratio == 1.0 (5d-Mittel == 20d-Mittel) ist der neutrale Punkt
    neutral = _run(
        VolumeConfirmationAgent(), _state(features={"vol_ratio_5_20": 1.0})
    ).score
    assert neutral == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# (a) Default 0.0 / dark ⇒ Konsens byte-identisch
# ---------------------------------------------------------------------------


def test_default_weight_is_zero_dark():
    assert TrendAgent().default_weight == 0.0
    assert VolumeConfirmationAgent().default_weight == 0.0


def test_consensus_byte_identical_when_dark(monkeypatch):
    """Die beiden dark-Voter (Flag off ⇒ Enthaltung, weight 0.0) dürfen den
    gewichteten Konsens-Mittelwert nicht verändern (§Field(gt=0.0) filtert sie)."""
    monkeypatch.setattr(agents, "_trend_agent_enabled", lambda: False)
    monkeypatch.setattr(agents, "_volume_confirm_agent_enabled", lambda: False)

    base = [
        VoteResult("MomentumAgent", "AAPL", 0.70, 0.45, "r"),
        VoteResult("LSTMSignalAgent", "AAPL", 0.60, 0.40, "r"),
        VoteResult("NewsSentimentAgent", "AAPL", 0.55, 0.35, "r"),
    ]
    trend = _run(TrendAgent(), _state(features={"macd_hist": 4.0}))
    vol = _run(VolumeConfirmationAgent(), _state(features={"vol_ratio_5_20": 4.0}))

    engine = ConsensusEngine()
    without = engine.aggregate(list(base))
    with_dark = engine.aggregate(list(base) + [trend, vol])
    assert with_dark == without


# ---------------------------------------------------------------------------
# Registry-/Tier-Mirror + ALL_AGENTS
# ---------------------------------------------------------------------------


def test_both_agents_registered_in_all_agents_and_tier_mirror():
    from core.entitlement.tier import _ALL_ROUND_TABLE_AGENTS

    names = {a.__class__.__name__ for a in agents.ALL_AGENTS}
    assert {"TrendAgent", "VolumeConfirmationAgent"} <= names
    assert {"TrendAgent", "VolumeConfirmationAgent"} <= set(_ALL_ROUND_TABLE_AGENTS)


# ---------------------------------------------------------------------------
# BORA-Parität config.py ↔ config.oss.py (Muster test_config_cash_aware_slots_parity)
# ---------------------------------------------------------------------------


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_3250", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "key,expected",
    [
        ("TREND_AGENT_WEIGHT", 0.0),
        ("VOLUME_CONFIRM_AGENT_WEIGHT", 0.0),
        ("TREND_AGENT_ENABLED", False),
        ("VOLUME_CONFIRM_AGENT_ENABLED", False),
    ],
)
def test_new_flags_bora_parity_and_dark_default(key, expected):
    import os

    import config as ent

    oss = _load_oss_config()
    ent_val = getattr(ent.get_config(), key)
    oss_val = getattr(oss, key)
    assert ent_val == oss_val, f"BORA-Drift {key}: ent={ent_val!r} oss={oss_val!r}"
    # Nur ohne Env-Override den Auslieferungs-Default prüfen (dark).
    if os.getenv(key) is None:
        assert ent_val == expected
        assert oss_val == expected
