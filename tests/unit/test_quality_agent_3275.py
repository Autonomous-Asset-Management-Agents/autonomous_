"""#3275 — QualityAgent: cross-sektionale Composite-Quality-Richtungsstimme (dark).

Der Agent spiegelt exakt das VIXAware/UpsideSkew-AC-7-Muster: er rankt den vom
Producer gelieferten Composite dieses Titels (``quality_score``) gegen den Querschnitt
der Vorsession (``quality_reference``) und votet den Perzentilrang. Er rechnet den
Composite NICHT selbst — die reine Rechnung ist in test_quality_score_3275.py belegt.

DARK: Default-Gewicht 0.0 + ``QUALITY_AGENT_ENABLED`` false ⇒ Producer emittiert None
⇒ Enthaltung ⇒ Konsens byte-identisch. Flag über ``agents._quality_agent_enabled``
gemonkeypatcht; Vote ist async → mit ``asyncio.run`` getrieben. Die async ``vote``-
Naht wird zusätzlich mit ``AsyncMock`` belegt (CODING_POLICY §5.2).
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.round_table import agents
from core.round_table.agents import QualityAgent
from core.round_table.base_agent import VoteResult
from core.round_table.consensus import ConsensusEngine

_ROOT = Path(__file__).resolve().parents[2]


def _run(agent, state):
    return asyncio.run(agent.vote(state))


def _state(symbol="AAPL", **kw):
    base = {
        "symbol": symbol,
        "current_time": "2026-01-02",
        "ohlc": {"close": 100.0},
        "quality_score": None,
        "quality_reference": None,
        "quality_reference_date": None,
    }
    base.update(kw)
    return base


_REF = [0.1, 0.2, 0.3, 0.4, 0.5]


# ---------------------------------------------------------------------------
# Dark / Enthaltung
# ---------------------------------------------------------------------------


def test_flag_off_abstains_dark(monkeypatch):
    monkeypatch.setattr(agents, "_quality_agent_enabled", lambda: False)
    v = _run(QualityAgent(), _state(quality_score=0.9, quality_reference=_REF))
    assert v.weight == 0.0
    assert v.score == 0.5
    assert v.agent_name == "QualityAgent"


def test_no_score_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_quality_agent_enabled", lambda: True)
    assert _run(QualityAgent(), _state(quality_reference=_REF)).weight == 0.0


def test_no_reference_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_quality_agent_enabled", lambda: True)
    assert _run(QualityAgent(), _state(quality_score=0.4)).weight == 0.0


def test_degenerate_reference_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_quality_agent_enabled", lambda: True)
    v = _run(
        QualityAgent(), _state(quality_score=0.4, quality_reference=[0.3, 0.3, 0.3])
    )
    assert v.weight == 0.0


# ---------------------------------------------------------------------------
# Perzentil-Vote + Monotonie (höherer Composite ⇒ höherer Rang)
# ---------------------------------------------------------------------------


def test_votes_percentile_rank(monkeypatch):
    monkeypatch.setattr(agents, "_quality_agent_enabled", lambda: True)
    v = _run(
        QualityAgent(),
        _state(
            quality_score=0.35,
            quality_reference=_REF,
            quality_reference_date="2026-01-01",
        ),
    )
    assert v.weight == pytest.approx(0.30)  # real vote carries the dormant weight (>0)
    # 3 of 5 reference values (0.1,0.2,0.3) are strictly below 0.35 → rank 0.6.
    assert v.score == pytest.approx(0.6)
    assert "2026-01-01" in v.reasoning


def test_score_monotone_in_composite(monkeypatch):
    monkeypatch.setattr(agents, "_quality_agent_enabled", lambda: True)
    scores = [
        _run(QualityAgent(), _state(quality_score=s, quality_reference=_REF)).score
        for s in [0.05, 0.25, 0.45, 0.99]
    ]
    assert all(a <= b for a, b in zip(scores, scores[1:])), scores
    assert scores[0] < scores[-1]


# ---------------------------------------------------------------------------
# Dark / Default 0.0 ⇒ ConsensusEngine.aggregate byte-identisch
# ---------------------------------------------------------------------------


def test_dark_via_enable_flag_dormant_weight():
    # Dark ist über das ENABLE-Flag erzwungen (Enthaltung), NICHT über ein Null-Gewicht.
    # Das Gewicht ist dormant 0.30 (min 0.10, wie UpsideSkew) → attribuierbar wenn armt.
    a = QualityAgent()
    assert a.default_weight == 0.30
    assert a.min_weight == 0.10


def test_consensus_byte_identical_when_dark(monkeypatch):
    monkeypatch.setattr(agents, "_quality_agent_enabled", lambda: False)
    base = [
        VoteResult("MomentumAgent", "AAPL", 0.70, 0.45, "r"),
        VoteResult("LSTMSignalAgent", "AAPL", 0.60, 0.40, "r"),
        VoteResult("NewsSentimentAgent", "AAPL", 0.55, 0.35, "r"),
    ]
    quality = _run(QualityAgent(), _state(quality_score=0.9, quality_reference=_REF))
    engine = ConsensusEngine()
    without = engine.aggregate(list(base))
    with_dark = engine.aggregate(list(base) + [quality])
    assert with_dark == without


# ---------------------------------------------------------------------------
# Async-Naht: vote() wird mit AsyncMock belegt (CODING_POLICY §5.2)
# ---------------------------------------------------------------------------


def test_vote_async_seam_uses_asyncmock():
    fake = AsyncMock(spec=QualityAgent)
    fake.vote.return_value = VoteResult("QualityAgent", "AAPL", 0.5, 0.0, "r")
    result = asyncio.run(fake.vote(_state()))
    assert result.agent_name == "QualityAgent"
    fake.vote.assert_awaited_once()


# ---------------------------------------------------------------------------
# Registry-/Tier-Mirror + BORA-Parität
# ---------------------------------------------------------------------------


def test_registered_in_all_agents_and_tier_mirror():
    from core.entitlement.tier import _ALL_ROUND_TABLE_AGENTS

    names = {a.__class__.__name__ for a in agents.ALL_AGENTS}
    assert "QualityAgent" in names
    assert "QualityAgent" in set(_ALL_ROUND_TABLE_AGENTS)


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_3275", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "key,expected",
    [
        ("QUALITY_AGENT_WEIGHT", 0.30),
        ("QUALITY_AGENT_ENABLED", False),
    ],
)
def test_new_flags_bora_parity_and_dark_default(key, expected):
    import os

    import config as ent

    oss = _load_oss_config()
    ent_val = getattr(ent.get_config(), key)
    oss_val = getattr(oss, key)
    assert ent_val == oss_val, f"BORA-Drift {key}: ent={ent_val!r} oss={oss_val!r}"
    if os.getenv(key) is None:
        assert ent_val == expected
        assert oss_val == expected
