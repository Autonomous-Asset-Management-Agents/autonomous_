# core/stock_specialist - ML<->LLM convergence-blend parity + signal_quality (RPAR T5)
# Epic #1262, Task T5 (#1269). P1 / DECISION-PATH - lands DORMANT behind the existing
# ML_SENTIMENT_BLEND_ENABLED flag (default OFF). Activation needs human sign-off + the #76
# shadow harness; this PR only makes the blend tuning-constants auditable in config and sets
# signal_quality / forwards the walk-forward + attention passthrough.
#
# TDD Red -> Green. Pure, deterministic, no net / LLM / GPU:
#   * _blend_ml_sentiment is a pure fn (ml_pred dict + llm_score -> float).
#   * The 8 tuning constants now live in config.py / config.oss.py (audit + tunable);
#     the getattr default in _blend_ml_sentiment is only a defensive forward-compat fallback.
#   * signal_quality / walkforward_* / ml_attention_features are asserted off _build_report.
#
# get_config() is patched with types.SimpleNamespace (same pattern as
# test_specialist_ml_wiring.py). The blend reads the tuning constants via getattr(cfg, ...),
# so the SimpleNamespace must carry them when we want config-driven math.

from __future__ import annotations

import importlib.util
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from config import get_config
from core.engine.api_routes import _serialize_specialist_report
from core.stock_specialist import StockSpecialistAgent, _blend_ml_sentiment

# The 8 tuning constants and their canonical defaults (P3-B convergence math).
# Single source of truth for the parity + math tests below.
_BLEND_CONSTANTS = {
    "SPECIALIST_ML_SATURATION_PCT": 2.0,
    "SPECIALIST_ML_LLM_AGREEMENT_HIGH": 0.75,
    "SPECIALIST_ML_LLM_AGREEMENT_MID": 0.50,
    "SPECIALIST_BLEND_CONVERGED_ML_W": 0.55,
    "SPECIALIST_BLEND_CONVERGED_LLM_W": 0.45,
    "SPECIALIST_BLEND_PARTIAL_ML_W": 0.40,
    "SPECIALIST_BLEND_PARTIAL_LLM_W": 0.60,
    "SPECIALIST_BLEND_DIVERGED_SHRINK": 0.30,
}

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
_OSS_CONFIG = _AI_BOT / "config.oss.py"


def _agent():
    return StockSpecialistAgent("AAPL", gemini_api_key="x")


def _minimal_gathered():
    """The curated gathered dict shape research() builds (no ml_prediction)."""
    return {
        "insider_trades": [],
        "material_events": [],
        "activist_stakes": [],
        "political_trades": [],
        "recent_headlines": [],
        "wiki_spike": False,
        "wiki_views_7d": 0,
        "reddit_mentions_24h": 0,
        "reddit_sentiment": "neutral",
        "short_interest_pct": None,
        "google_trend_score": None,
    }


def _ml_dict(**overrides):
    d = {
        "direction": "up",
        "bear_return_pct": -0.5,
        "base_return_pct": 1.2,
        "bull_return_pct": 3.0,
        "confidence": 0.7,
        "forecast_vol": 0.03,
    }
    d.update(overrides)
    return d


def _cfg(blend_enabled: bool):
    """A get_config() stand-in that carries the real tuning constants (read via getattr)."""
    ns = types.SimpleNamespace(
        ML_SENTIMENT_BLEND_ENABLED=blend_enabled,
        SPECIALIST_COUNT_BONUS_ENABLED=True,
    )
    for name, value in _BLEND_CONSTANTS.items():
        setattr(ns, name, value)
    return ns


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_blend_under_test", _OSS_CONFIG
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. Blend math - table-driven (converged / partial / diverged) reads config constants
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "base_return_pct, llm, expected, branch",
    [
        # ml_score = clamp((base+2)/4*100). agreement = 1 - |ml-llm|/100.
        # converged: base=1.96 -> ml=99; llm=99 -> agreement~=1.0 >=0.75 -> 99*0.55+99*0.45=99.0
        (1.96, 99.0, 99.0, "converged"),
        # partial: base=1.2 -> ml=80; llm=50 -> agreement=0.70 (>=0.50, <0.75) -> 80*0.4+50*0.6=62.0
        (1.2, 50.0, 62.0, "partial"),
        # diverged: base=2.0 -> ml=100; llm=0 -> agreement=0.0 (<0.50) -> 50+(0-50)*0.30=35.0
        (2.0, 0.0, 35.0, "diverged"),
    ],
)
def test_blend_math_branches_read_config(base_return_pct, llm, expected, branch):
    cfg = _cfg(blend_enabled=True)
    with patch("core.stock_specialist.get_config", return_value=cfg):
        out = _blend_ml_sentiment(
            _ml_dict(base_return_pct=base_return_pct), llm, "AAPL"
        )
    assert out == pytest.approx(expected), f"{branch} branch mismatch"


def test_blend_saturation_clamps_to_0_100():
    cfg = _cfg(blend_enabled=True)
    with patch("core.stock_specialist.get_config", return_value=cfg):
        # base far above +sat -> ml_score clamps to 100 (not >100)
        hi = _blend_ml_sentiment(_ml_dict(base_return_pct=1000.0), 100.0, "AAPL")
        # base far below -sat -> ml_score clamps to 0 (not <0)
        lo = _blend_ml_sentiment(_ml_dict(base_return_pct=-1000.0), 0.0, "AAPL")
    # both perfectly agree with their llm -> converged -> equals the clamped llm
    assert hi == pytest.approx(100.0)
    assert lo == pytest.approx(0.0)


def test_blend_except_fallback_is_fail_loud(caplog):
    """Missing base_return_pct -> KeyError -> return unchanged llm_score + exactly one WARNING."""
    cfg = _cfg(blend_enabled=True)
    bad = {"direction": "up"}  # no base_return_pct
    with patch("core.stock_specialist.get_config", return_value=cfg):
        with caplog.at_level("WARNING"):
            out = _blend_ml_sentiment(bad, 73.0, "AAPL")
    assert out == 73.0  # unchanged llm_score, never silent-neutral
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1


def test_blend_zero_saturation_guards_division(caplog):
    """#1316 F-02: a misconfigured SPECIALIST_ML_SATURATION_PCT <= 0 must not
    raise ZeroDivisionError; the sat>0 guard fails loud -> unchanged llm_score +
    exactly one descriptive WARNING."""
    cfg = _cfg(blend_enabled=True)
    cfg.SPECIALIST_ML_SATURATION_PCT = 0.0  # would make `/ (2.0 * sat)` divide by zero
    with patch("core.stock_specialist.get_config", return_value=cfg):
        with caplog.at_level("WARNING"):
            out = _blend_ml_sentiment(_ml_dict(base_return_pct=1.2), 73.0, "AAPL")
    assert out == 73.0  # fallback to unchanged llm_score, no crash
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "SPECIALIST_ML_SATURATION_PCT" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# 2. P0-1 - a blended 0.0 is a legitimate maximally-bearish score, never masked
# ---------------------------------------------------------------------------
def test_blend_zero_is_legitimate_not_masked():
    cfg = _cfg(blend_enabled=True)
    # base=-2.0 -> ml_score=0; llm=0 -> agreement=1.0 converged -> 0*0.55+0*0.45 = 0.0
    with patch("core.stock_specialist.get_config", return_value=cfg):
        out = _blend_ml_sentiment(_ml_dict(base_return_pct=-2.0), 0.0, "AAPL")
    assert out == 0.0
    assert out is not None


# ---------------------------------------------------------------------------
# 3. Flag OFF (default) - byte-identical: signal_quality llm_only, score untouched
# ---------------------------------------------------------------------------
def test_blend_flag_off_byte_identical():
    agent = _agent()
    base = _minimal_gathered()
    cfg_off = _cfg(blend_enabled=False)
    with patch("core.stock_specialist.get_config", return_value=cfg_off):
        no_ml = agent._build_report({**base}, {"text": ""})
        with_ml = agent._build_report(
            {**base, "ml_prediction": _ml_dict()}, {"text": ""}
        )

    # decision fields identical to the no-ml run (blend never applied)
    assert with_ml.sentiment_score == no_ml.sentiment_score
    assert with_ml.recommendation == no_ml.recommendation
    assert with_ml.escalate == no_ml.escalate
    # signal_quality stays at the V0 default
    assert with_ml.signal_quality == "llm_only"
    assert no_ml.signal_quality == "llm_only"
    # walk-forward / attention untouched (no IC keys in the prediction)
    assert with_ml.walkforward_ic is None
    assert with_ml.walkforward_sharpe is None
    assert with_ml.ml_attention_features == []

    # serialized DTO byte-identical between the two runs
    dto_no_ml = _serialize_specialist_report("AAPL", no_ml)
    dto_with_ml = _serialize_specialist_report("AAPL", with_ml)
    dto_no_ml.pop("updated_at", None)
    dto_with_ml.pop("updated_at", None)
    # ml_* provenance fields differ (populated regardless of blend), so compare the
    # decision-relevant serializer surface, which is what dormancy guarantees.
    for k in (
        "sentiment_score",
        "recommendation",
        "escalate",
        "signal_quality",
        "walkforward_ic",
        "walkforward_sharpe",
        "ml_attention_features",
    ):
        assert (
            dto_with_ml[k] == dto_no_ml[k]
        ), f"DTO field {k} drifted with ml_prediction"


# ---------------------------------------------------------------------------
# 4. Flag ON - signal_quality set, blend applied, decision-path proven (escalation kip)
# ---------------------------------------------------------------------------
def test_blend_flag_on_sets_signal_quality_and_blends():
    agent = _agent()
    base = _minimal_gathered()
    cfg_on = _cfg(blend_enabled=True)
    ml = _ml_dict()  # base=1.2 -> ml_score=80; llm=50 -> partial -> 62.0
    with patch("core.stock_specialist.get_config", return_value=cfg_on):
        on = agent._build_report({**base, "ml_prediction": ml}, {"text": ""})
    assert on.signal_quality == "llm_plus_ml"
    assert on.sentiment_score == 62.0


def test_blend_flag_on_is_decision_path_escalation_flip():
    """A prediction that lifts the blended score >=82 must flip escalate=True - proves P1."""
    agent = _agent()
    base = _minimal_gathered()
    cfg_on = _cfg(blend_enabled=True)
    # base=1.96 -> ml_score=99; llm=99 -> converged -> 99.0 >= 82 -> escalate
    ml = _ml_dict(base_return_pct=1.96)
    with patch("core.stock_specialist.get_config", return_value=cfg_on):
        on = agent._build_report({**base, "ml_prediction": ml}, {"text": "SCORE: 99"})
    assert on.sentiment_score >= 82
    assert on.escalate is True
    assert on.escalate_reason.startswith("Very high sentiment")
    assert on.signal_quality == "llm_plus_ml"


def test_blend_flag_on_does_not_mutate_inputs():
    agent = _agent()
    base = _minimal_gathered()
    cfg_on = _cfg(blend_enabled=True)
    ml = _ml_dict()
    ml_snapshot = dict(ml)
    with patch("core.stock_specialist.get_config", return_value=cfg_on):
        agent._build_report({**base, "ml_prediction": ml}, {"text": ""})
    assert ml == ml_snapshot  # blend never writes back into the prediction dict


# ---------------------------------------------------------------------------
# 5. walk-forward / attention forward-compat passthrough (P0-1 on 0.0)
# ---------------------------------------------------------------------------
def test_walkforward_fields_absent_today():
    """Prediction without IC keys (today's shape) -> None/[] = current serializer behaviour."""
    agent = _agent()
    base = _minimal_gathered()
    cfg_on = _cfg(blend_enabled=True)
    with patch("core.stock_specialist.get_config", return_value=cfg_on):
        rep = agent._build_report({**base, "ml_prediction": _ml_dict()}, {"text": ""})
    assert rep.walkforward_ic is None
    assert rep.walkforward_sharpe is None
    assert rep.ml_attention_features == []


def test_walkforward_passthrough_when_present_p0_1_zero():
    """When the prediction carries IC keys they flow through; 0.0 must survive as real 0.0."""
    agent = _agent()
    base = _minimal_gathered()
    cfg_off = _cfg(blend_enabled=False)  # passthrough is blend-independent
    ml = _ml_dict(
        walkforward_ic=0.0,  # P0-1: legitimate zero IC, never masked to None
        walkforward_sharpe=1.25,
        ml_attention_features=[{"feature": "rsi_14", "weight": 0.3}],
    )
    with patch("core.stock_specialist.get_config", return_value=cfg_off):
        rep = agent._build_report({**base, "ml_prediction": ml}, {"text": ""})
    assert rep.walkforward_ic == 0.0  # NOT None - 0.0 preserved
    assert rep.walkforward_ic is not None
    assert rep.walkforward_sharpe == 1.25
    assert rep.ml_attention_features == [{"feature": "rsi_14", "weight": 0.3}]

    # and the serializer keeps the 0.0 (does not mask via `or`)
    dto = _serialize_specialist_report("AAPL", rep)
    assert dto["walkforward_ic"] == 0.0


# ---------------------------------------------------------------------------
# 6. Config parity - all 8 constants present in BOTH editions with identical defaults
# ---------------------------------------------------------------------------
def test_blend_config_constants_present_in_config_py():
    cfg = get_config()
    for name, expected in _BLEND_CONSTANTS.items():
        assert hasattr(cfg, name), f"config.py get_config() missing {name}"
        assert getattr(cfg, name) == expected, f"config.py {name} default drifted"


def test_blend_config_constants_present_in_config_oss():
    cfg = _load_oss_config().get_config()
    for name, expected in _BLEND_CONSTANTS.items():
        assert hasattr(cfg, name), f"config.oss.py get_config() missing {name}"
        assert getattr(cfg, name) == expected, f"config.oss.py {name} default drifted"


def test_blend_config_parity_both_editions_identical():
    py_cfg = get_config()
    oss_cfg = _load_oss_config().get_config()
    for name in _BLEND_CONSTANTS:
        assert getattr(py_cfg, name) == getattr(
            oss_cfg, name
        ), f"{name} differs between config.py and config.oss.py"
