"""TFT model quality gate (fusion S1-E1, core/ml brick — dormant).

The gate reads a per-symbol model's ``metadata.json`` and decides whether the
checkpoint may be served (val_loss / walkforward_ic / wilcoxon_p thresholds).
It lands DORMANT: nothing on main imports it yet (model_registry, its only
consumer, is a later brick), so it is purely additive.

Thresholds are env-tunable and read at import; these tests use metric values
that are unambiguous against the defaults (LOCAL IC floor 0.0 / cloud 0.05,
VAL_LOSS_MAX 5.0, ALLOW_WALKFORWARD_ONLY default True).
"""

import json
from pathlib import Path

from core.ml.quality_gate import GateResult, evaluate, evaluate_and_log, stats


def _model_dir(tmp_path: Path, meta: dict | None, name: str = "AAPL") -> Path:
    d = tmp_path / name
    d.mkdir()
    if meta is not None:
        (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def test_missing_metadata_rejected_in_strict_mode(tmp_path):
    result = evaluate("AAPL", _model_dir(tmp_path, None))
    assert isinstance(result, GateResult)
    assert result.passed is False
    assert "metadata" in result.reason.lower()


def test_negative_walkforward_ic_rejected(tmp_path):
    # IC clearly below both floors (LOCAL 0.0 / cloud 0.05) → no out-of-sample edge.
    result = evaluate("AAPL", _model_dir(tmp_path, {"walkforward_ic": -0.5}))
    assert result.passed is False
    assert "walkforward_ic" in result.reason


def test_strong_walkforward_ic_passes_without_val_loss(tmp_path):
    # No val_loss but a strong positive IC → ALLOW_WALKFORWARD_ONLY (default) admits it.
    result = evaluate("AAPL", _model_dir(tmp_path, {"walkforward_ic": 0.5}))
    assert result.passed is True


def test_high_val_loss_rejected(tmp_path):
    result = evaluate(
        "AAPL", _model_dir(tmp_path, {"val_loss": 99.0, "walkforward_ic": 0.5})
    )
    assert result.passed is False
    assert "val_loss" in result.reason


def test_evaluate_and_log_tracks_counters(tmp_path):
    before = stats()["checked"]
    evaluate_and_log("PASS", _model_dir(tmp_path, {"walkforward_ic": 0.5}, name="PASS"))
    evaluate_and_log(
        "FAIL", _model_dir(tmp_path, {"walkforward_ic": -0.5}, name="FAIL")
    )
    after = stats()
    assert after["checked"] == before + 2
    assert after["passed"] >= 1
    assert after["rejected"] >= 1
