# core/stock_specialist — TFT specialist wiring (fusion, two-flag, dormant)
# TDD Red → Green. implementation_plan 2026-06-09-model-registry (Issues 2-4), bar
# source = core.data_provider.get_data (Georg-approved; not the bundle's _fetch_bars_alpaca).
#
# Two-flag decoupling:
#   - ML_PREDICTION_ENABLED (default False): runs _fetch_ml_prediction → populates the
#     SpecialistReport.ml_* fields (what the dormant Shadow-TFT-Vote reads).
#   - ML_SENTIMENT_BLEND_ENABLED (default False): gates the convergence blend that changes
#     sentiment_score — so the shadow vote can measure WITHOUT any decision change.
#
# Async interfaces (research, _fetch_ml_prediction, model_registry.get_or_train) use AsyncMock.

import types
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from config import get_config
from core.stock_specialist import SpecialistReport, StockSpecialistAgent


def _agent():
    return StockSpecialistAgent("AAPL", gemini_api_key="x")


def _ml_dict():
    return {
        "direction": "up",
        "bear_return_pct": -0.5,
        "base_return_pct": 1.2,
        "bull_return_pct": 3.0,
        "confidence": 0.7,
        "forecast_vol": 0.03,
    }


# ---------------------------------------------------------------------------
# 1. Config flags exist and default False (dormant)
# ---------------------------------------------------------------------------
def test_ml_flags_default_false():
    cfg = get_config()
    assert cfg.ML_PREDICTION_ENABLED is False
    assert cfg.ML_SENTIMENT_BLEND_ENABLED is False


# ---------------------------------------------------------------------------
# 2. SpecialistReport carries the ml_* fields with safe defaults
# ---------------------------------------------------------------------------
def test_specialist_report_has_ml_fields():
    fields = SpecialistReport.__dataclass_fields__
    for name in (
        "ml_direction",
        "ml_confidence",
        "ml_base_return_pct",
        "ml_bear_return_pct",
        "ml_bull_return_pct",
        "forecast_vol",
    ):
        assert name in fields


# ---------------------------------------------------------------------------
# 3. _fetch_ml_prediction is flag-FIRST: off → None before any data I/O
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_fetch_ml_prediction_flag_off_no_io():
    agent = _agent()
    cfg = types.SimpleNamespace(ML_PREDICTION_ENABLED=False)
    with patch("core.stock_specialist.get_config", return_value=cfg), patch(
        "core.stock_specialist._get_data_provider"
    ) as dp:
        out = await agent._fetch_ml_prediction()
    assert out is None
    dp.assert_not_called()  # flag-first: no data provider touched


# ---------------------------------------------------------------------------
# 4. _fetch_ml_prediction flag-on: bars → features → get_or_train → ml_dict
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_fetch_ml_prediction_flag_on_returns_dict():
    agent = _agent()
    cfg = types.SimpleNamespace(ML_PREDICTION_ENABLED=True)
    bars = pd.DataFrame({"close": [1.0] * 400})
    provider = MagicMock()
    provider.get_data.return_value = bars
    features = pd.DataFrame({"f": [1.0] * 100})
    prediction = types.SimpleNamespace(
        direction="up",
        bear_return_pct=-0.5,
        base_return_pct=1.2,
        bull_return_pct=3.0,
        confidence=0.7,
        attention_weights=None,
    )
    fb = MagicMock()
    fb.build.return_value = features

    with patch("core.stock_specialist.get_config", return_value=cfg), patch(
        "core.stock_specialist._get_data_provider", return_value=provider
    ), patch("core.ml.feature_builder.FeatureBuilder", return_value=fb), patch(
        "core.ml.model_registry.model_registry"
    ) as registry:
        registry.get_or_train = AsyncMock(return_value=prediction)
        out = await agent._fetch_ml_prediction()

    assert out is not None
    assert out["direction"] == "up"
    assert out["base_return_pct"] == 1.2
    registry.get_or_train.assert_awaited_once()
    provider.get_data.assert_called_once()  # bars actually sourced from data_provider


@pytest.mark.anyio
async def test_fetch_ml_prediction_none_when_get_or_train_none():
    agent = _agent()
    cfg = types.SimpleNamespace(ML_PREDICTION_ENABLED=True)
    bars = pd.DataFrame({"close": [1.0] * 400})
    provider = MagicMock()
    provider.get_data.return_value = bars
    fb = MagicMock()
    fb.build.return_value = pd.DataFrame({"f": [1.0] * 100})

    with patch("core.stock_specialist.get_config", return_value=cfg), patch(
        "core.stock_specialist._get_data_provider", return_value=provider
    ), patch("core.ml.feature_builder.FeatureBuilder", return_value=fb), patch(
        "core.ml.model_registry.model_registry"
    ) as registry:
        registry.get_or_train = AsyncMock(return_value=None)
        out = await agent._fetch_ml_prediction()

    assert out is None


# ---------------------------------------------------------------------------
# 5. _build_report populates ml_* from gathered["ml_prediction"]
# ---------------------------------------------------------------------------
def test_build_report_populates_ml_fields():
    agent = _agent()
    gathered = _minimal_gathered()
    gathered["ml_prediction"] = _ml_dict()
    report = agent._build_report(gathered, {"text": ""})
    assert report.ml_direction == "up"
    assert report.ml_base_return_pct == 1.2
    assert report.ml_confidence == 0.7
    assert (
        report.forecast_vol == 0.03
    )  # flows through the returned dict (no instance state)


def test_build_report_ml_unavailable_when_no_prediction():
    agent = _agent()
    report = agent._build_report(_minimal_gathered(), {"text": ""})
    assert report.ml_direction == "unavailable"
    assert report.ml_base_return_pct is None


# ---------------------------------------------------------------------------
# 6. Decoupling: prediction present + blend flag OFF → sentiment unchanged
# ---------------------------------------------------------------------------
def test_blend_flag_off_keeps_sentiment_byte_identical():
    agent = _agent()
    base = _minimal_gathered()

    cfg_off = types.SimpleNamespace(ML_SENTIMENT_BLEND_ENABLED=False)
    with patch("core.stock_specialist.get_config", return_value=cfg_off):
        no_ml = agent._build_report({**base}, {"text": ""})
        with_ml_no_blend = agent._build_report(
            {**base, "ml_prediction": _ml_dict()}, {"text": ""}
        )
    # ml_* populated but the decision score is untouched (validate-before-activate)
    assert with_ml_no_blend.ml_direction == "up"
    assert with_ml_no_blend.sentiment_score == no_ml.sentiment_score


def test_blend_flag_on_changes_sentiment():
    agent = _agent()
    base = _minimal_gathered()
    cfg_on = types.SimpleNamespace(ML_SENTIMENT_BLEND_ENABLED=True)
    cfg_off = types.SimpleNamespace(ML_SENTIMENT_BLEND_ENABLED=False)
    ml = _ml_dict()
    with patch("core.stock_specialist.get_config", return_value=cfg_off):
        off = agent._build_report({**base, "ml_prediction": ml}, {"text": ""})
    with patch("core.stock_specialist.get_config", return_value=cfg_on):
        on = agent._build_report({**base, "ml_prediction": ml}, {"text": ""})
    # base_return_pct=1.2, sat=2.0 → ml_score=80; llm=50; agreement=0.70 (<0.75 hi, ≥0.50
    # mid) → partial blend 80*0.40 + 50*0.60 = 62.0. off stays at the llm-only 50.0.
    assert off.sentiment_score == 50.0
    assert on.sentiment_score == 62.0


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
