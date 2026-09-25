"""MLR-9 (#1909): IC demotion — walkforward_ic becomes diagnostic-only under flag.

Doctrine (feedback_sharpe_over_ic, ADR-ML-GATE-03): net-of-cost (deflated) Sharpe
is the price of a model, IC is diagnosis. When TFT_IC_DIAGNOSTIC_ONLY=True the
``wf_ic <= IC_MIN`` reject is skipped and only WARNED (CLAUDE.md §5.6); the field
itself stays in metadata for report/DecisionContext consumers. Default False →
today's reject behaviour byte-identical (regression pinned here + in
test_quality_gate_offline_layers.py).
"""

import json
import logging
from unittest.mock import patch

import pytest

import core.ml.quality_gate as qg
from config import RuntimeConfigState


def _model_dir(tmp_path, meta, name="AAPL"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def _cfg(**overrides):
    return RuntimeConfigState(**overrides)


@pytest.fixture(autouse=True)
def _fixed_floor(monkeypatch):
    monkeypatch.setattr(qg, "IC_MIN", 0.0)


_META_BAD_IC = {
    "val_loss": 0.01,
    "walkforward_ic": -0.5,  # far below any IC floor
    "wilcoxon_p": 0.01,
    "fdr_passed": True,
    "net_sharpe": 0.8,
}


# --- G8: diagnostic-only → negative IC no longer rejects, WARNING logged -------------
def test_ic_diagnostic_only_skips_reject_with_warning(tmp_path, caplog):
    md = _model_dir(tmp_path, dict(_META_BAD_IC))
    with patch(
        "core.ml.quality_gate.get_config",
        return_value=_cfg(TFT_IC_DIAGNOSTIC_ONLY=True),
    ), caplog.at_level(logging.WARNING):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True  # other gates (fdr/net_sharpe) decide, IC only warns
    assert any(
        "walkforward_ic" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), "demoted IC must still WARN for audit (CLAUDE.md §5.6)"


def test_ic_diagnostic_only_other_gates_still_reject(tmp_path):
    # IC demotion must not weaken the orthogonal gates: bad net_sharpe still rejects.
    md = _model_dir(tmp_path, {**_META_BAD_IC, "net_sharpe": -0.1})
    with patch(
        "core.ml.quality_gate.get_config",
        return_value=_cfg(TFT_IC_DIAGNOSTIC_ONLY=True),
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False
    assert "net_sharpe" in r.reason


def test_ic_diagnostic_only_non_numeric_ic_still_rejects(tmp_path):
    # Demotion covers the threshold only — a malformed field is still a data error.
    md = _model_dir(tmp_path, {**_META_BAD_IC, "walkforward_ic": "bad"})
    with patch(
        "core.ml.quality_gate.get_config",
        return_value=_cfg(TFT_IC_DIAGNOSTIC_ONLY=True),
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False
    assert "walkforward_ic" in r.reason


# --- G9: default False → IC reject byte-identical ------------------------------------
def test_default_ic_reject_unchanged(tmp_path):
    md = _model_dir(tmp_path, dict(_META_BAD_IC))
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False
    assert "walkforward_ic" in r.reason
    assert "-0.500" in r.reason  # historical reason format preserved
