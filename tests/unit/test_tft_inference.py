"""Per-symbol TFT inference engine (fusion S1-E1, core/ml brick — dormant).

The engine loads a pytorch-forecasting checkpoint and turns a feature frame into
a quantile-derived `TFTPrediction`. It is designed to **degrade silently**: any
failure (pytorch_forecasting absent, checkpoint missing, dataset/arch mismatch)
makes `predict()` return None so the caller falls back to its rule-based path —
"purely additive, cannot break the live trading path".

These tests exercise that contract WITHOUT a real checkpoint or pytorch_forecasting:
the dataclass, the graceful degraded path (→ None, no crash), and the
ADR-ML-DS-01 training-dataset resolution (pure path/json logic). Lands DORMANT:
nothing on main imports the engine yet (model_registry is a later brick).
"""

import json
import pickle

import pandas as pd
import pytest

from core.ml.tft_inference import TFTInferenceEngine, TFTPrediction


def test_tft_prediction_dataclass_fields():
    pred = TFTPrediction(
        direction="up",
        base_return_pct=1.2,
        bear_return_pct=-0.5,
        bull_return_pct=3.0,
        confidence=0.7,
    )
    assert pred.direction == "up"
    assert pred.base_return_pct == 1.2
    assert pred.attention_weights is None


def test_engine_degrades_to_none_no_checkpoint_or_pf(tmp_path):
    # Empty model dir (no checkpoint.pt, and/or pytorch_forecasting absent) →
    # load() fails gracefully and predict() returns None with no exception.
    engine = TFTInferenceEngine("AAPL", tmp_path)
    assert engine.loaded is False
    out = engine.predict(pd.DataFrame({"close": [1.0, 2.0, 3.0]}))
    assert out is None
    assert engine.loaded is False
    assert engine._load_error is not None  # a failure reason was recorded


def test_resolve_training_ds_maps_promoted_checkpoint(tmp_path):
    # ADR-ML-DS-01: metadata.promoted_from → the sibling training_ds whose feature
    # schema matches the promoted checkpoint (not a stale top-level training_ds.pkl).
    (tmp_path / "metadata.json").write_text(
        json.dumps({"promoted_from": "checkpoint_v2_seed0_10y_full491.pt"}),
        encoding="utf-8",
    )
    matching = tmp_path / "training_ds_v2_seed0_10y_full491.pkl"
    matching.write_bytes(b"placeholder")
    engine = TFTInferenceEngine("AAPL", tmp_path)
    assert engine._resolve_training_ds_path() == matching


def test_resolve_training_ds_falls_back_to_default(tmp_path):
    # No metadata.json → legacy top-level training_ds.pkl path.
    engine = TFTInferenceEngine("AAPL", tmp_path)
    assert engine._resolve_training_ds_path() == tmp_path / "training_ds.pkl"


# --- D3: load() consumes the gate's read-once VERIFIED buffers (TOCTOU close) ----------
def test_load_consumes_pinned_buffers_then_releases(tmp_path):
    # load() reaches the buffer/path branch only AFTER importing pytorch_forecasting; skip
    # where it's absent (CI runs ML-deps-slim) — test_engine_degrades_to_none covers that case.
    pytest.importorskip("pytorch_forecasting")
    # With the gate's read-once buffers pinned, load() unpickles the DS from the BUFFER
    # (not a path) and torch.loads the checkpoint from BytesIO — proving the TOCTOU is
    # closed (the bytes are captured; the underlying file is never re-opened). The dir is
    # EMPTY: the path branch would error "checkpoint.pt missing", so reaching a
    # "verified-buffer" error proves the buffer branch was taken. The ckpt buffer is bogus
    # so torch.load degrades gracefully — and the buffers must be RELEASED regardless.
    engine = TFTInferenceEngine("AAPL", tmp_path)
    engine._pinned_ds_bytes = pickle.dumps({"schema": "v2"})
    engine._pinned_ckpt_bytes = b"not-a-real-torch-checkpoint"
    assert engine.load() is False  # bogus ckpt buffer → graceful degrade
    assert "verified-buffer" in (engine._load_error or "")  # buffer branch, not path
    assert engine._training_ds == {"schema": "v2"}  # DS came from the pinned BUFFER
    # buffers freed on the failure path too (finally) — ~6 MB transient, not retained
    assert engine._pinned_ckpt_bytes is None
    assert engine._pinned_ds_bytes is None


def test_load_without_buffers_uses_path_branch(tmp_path):
    # Needs pytorch_forecasting to reach the path branch; skip where absent (CI ML-slim).
    pytest.importorskip("pytorch_forecasting")
    # Back-compat (desktop/OSS direct use, no verify gate): without pinned buffers, load()
    # takes the PATH branch — an empty dir errors on the missing checkpoint, NOT a buffer.
    engine = TFTInferenceEngine("AAPL", tmp_path)
    assert engine._pinned_ckpt_bytes is None and engine._pinned_ds_bytes is None
    assert engine.load() is False
    assert "checkpoint.pt missing" in (engine._load_error or "")
