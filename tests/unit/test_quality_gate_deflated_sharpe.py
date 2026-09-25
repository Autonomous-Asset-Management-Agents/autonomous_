"""MLR-9 (#1909): serving gate consults the Layer-5 deflated Sharpe when enabled.

Contract under test (additive + default-safe, mirrors the MLR-3 net_sharpe idiom
in test_quality_gate_offline_layers.py):

  - TFT_DEFLATED_SHARPE_GATE_ENABLED=True AND field present →
    additionally require deflated_sharpe > TFT_DSR_FLOOR (default 0.0).
    ``deflated_sharpe`` is the SHARPE-SCALE deflation margin SR - E[max SR]
    stamped by scripts/apply_deflated_sharpe_layer.py (Layer 5) — negative ⇒
    the backtest Sharpe does not clear the expected max Sharpe of N random
    trials ⇒ overfit (Bailey/Lopez de Prado).
  - field read top-level first, then nested ``layer5.deflated_sharpe``;
  - field ABSENT (legacy metadata) → passthrough + WARNING (fail-safe);
  - flag OFF (default) → byte-identical to MLR-3: DSR ignored even when
    present-and-negative, no new reject reason possible;
  - FDR and DSR are orthogonal: fdr_passed=True does NOT save a negative DSR.

Pure JSON — no torch, runs in CI.
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


# Metadata passing every legacy + MLR-3 check; DSR fields layered on per test.
_GOOD_MLR3 = {
    "val_loss": 0.01,
    "walkforward_ic": 0.10,
    "wilcoxon_p": 0.01,
    "fdr_passed": True,
    "net_sharpe": 0.8,
}


# --- G1: DSR <= floor → reject, value in reason (audit) ------------------------------
def test_negative_dsr_rejected_when_enabled(tmp_path):
    md = _model_dir(tmp_path, {**_GOOD_MLR3, "deflated_sharpe": -0.05})
    with patch(
        "core.ml.quality_gate.get_config",
        return_value=_cfg(TFT_DEFLATED_SHARPE_GATE_ENABLED=True),
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False
    assert "deflated_sharpe" in r.reason
    assert "-0.050" in r.reason  # plan Gherkin: value logged for audit


def test_dsr_at_exact_floor_rejected(tmp_path):
    # Floor is exclusive (mirrors net_sharpe): must be > floor, not >=.
    md = _model_dir(tmp_path, {**_GOOD_MLR3, "deflated_sharpe": 0.0})
    with patch(
        "core.ml.quality_gate.get_config",
        return_value=_cfg(TFT_DEFLATED_SHARPE_GATE_ENABLED=True),
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False


def test_dsr_floor_configurable(tmp_path):
    md = _model_dir(tmp_path, {**_GOOD_MLR3, "deflated_sharpe": 0.3})
    with patch(
        "core.ml.quality_gate.get_config",
        return_value=_cfg(TFT_DEFLATED_SHARPE_GATE_ENABLED=True, TFT_DSR_FLOOR=0.5),
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False
    assert "0.5" in r.reason


# --- G2: DSR > floor + FDR true → served ---------------------------------------------
def test_positive_dsr_served(tmp_path):
    md = _model_dir(tmp_path, {**_GOOD_MLR3, "deflated_sharpe": 0.4})
    with patch(
        "core.ml.quality_gate.get_config",
        return_value=_cfg(TFT_DEFLATED_SHARPE_GATE_ENABLED=True),
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True


# --- G3: field absent → passthrough + WARNING (fail-safe) ----------------------------
def test_missing_dsr_passthrough_with_warning(tmp_path, caplog):
    md = _model_dir(tmp_path, dict(_GOOD_MLR3))  # no deflated_sharpe anywhere
    with patch(
        "core.ml.quality_gate.get_config",
        return_value=_cfg(TFT_DEFLATED_SHARPE_GATE_ENABLED=True),
    ), caplog.at_level(logging.WARNING):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True  # fail-safe: absent field must not reject
    assert any(
        "deflated_sharpe" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), "absent deflated_sharpe must WARN (CLAUDE.md §5.6)"


# --- G4: nested layer5.deflated_sharpe found -----------------------------------------
def test_dsr_nested_in_layer5_is_found(tmp_path):
    md = _model_dir(tmp_path, {**_GOOD_MLR3, "layer5": {"deflated_sharpe": -0.2}})
    with patch(
        "core.ml.quality_gate.get_config",
        return_value=_cfg(TFT_DEFLATED_SHARPE_GATE_ENABLED=True),
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False
    assert "deflated_sharpe" in r.reason


def test_top_level_dsr_wins_over_layer5(tmp_path):
    # Promotion may flatten to top level — top level wins (net_sharpe idiom).
    md = _model_dir(
        tmp_path,
        {**_GOOD_MLR3, "deflated_sharpe": 0.4, "layer5": {"deflated_sharpe": -0.2}},
    )
    with patch(
        "core.ml.quality_gate.get_config",
        return_value=_cfg(TFT_DEFLATED_SHARPE_GATE_ENABLED=True),
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True


# --- G5: non-numeric → reject --------------------------------------------------------
def test_non_numeric_dsr_rejected(tmp_path):
    md = _model_dir(tmp_path, {**_GOOD_MLR3, "deflated_sharpe": "high"})
    with patch(
        "core.ml.quality_gate.get_config",
        return_value=_cfg(TFT_DEFLATED_SHARPE_GATE_ENABLED=True),
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False
    assert "deflated_sharpe" in r.reason


# --- G6: flag off (default) → byte-identical, DSR ignored ----------------------------
def test_flag_off_ignores_negative_dsr(tmp_path):
    md = _model_dir(tmp_path, {**_GOOD_MLR3, "deflated_sharpe": -0.5})
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True  # exactly MLR-3 behaviour — no DSR reject


def test_flag_off_no_dsr_warning_for_legacy_metadata(tmp_path, caplog):
    # Byte-identity includes logs: flag off must not add deflated_sharpe to the
    # missing-fields WARNING that MLR-3 emits today.
    md = _model_dir(
        tmp_path, {"val_loss": 0.01, "walkforward_ic": 0.10, "wilcoxon_p": 0.01}
    )
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()), caplog.at_level(
        logging.WARNING
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True
    assert not any("deflated_sharpe" in rec.message for rec in caplog.records)


def test_config_defaults_are_safe():
    cfg = RuntimeConfigState()
    assert cfg.TFT_DEFLATED_SHARPE_GATE_ENABLED is False
    assert cfg.TFT_DSR_FLOOR == 0.0
    assert cfg.TFT_IC_DIAGNOSTIC_ONLY is False


# --- G7: FDR and DSR orthogonal ------------------------------------------------------
def test_fdr_passed_does_not_save_negative_dsr(tmp_path):
    md = _model_dir(
        tmp_path,
        {**_GOOD_MLR3, "fdr_passed": True, "deflated_sharpe": -0.2},
    )
    with patch(
        "core.ml.quality_gate.get_config",
        return_value=_cfg(TFT_DEFLATED_SHARPE_GATE_ENABLED=True),
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False  # FDR alone does not admit (plan §1: orthogonal errors)
    assert "deflated_sharpe" in r.reason
