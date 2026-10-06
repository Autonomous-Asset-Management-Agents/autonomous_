"""MLR-3 (#1903): serving gate consults the offline gate-stack results when present.

The offline layers already compute the *correct* serve/no-serve verdicts and stamp
them into the per-symbol gate metadata:

  - Layer 2 — ``scripts/apply_fdr_layer.py`` (Benjamini-Hochberg FDR across the
    universe) writes top-level ``fdr_passed`` (bool, or None when the symbol had
    no usable p-value and was excluded from the correction).
  - Layer 4 — ``scripts/apply_cost_model.py`` (Almgren-Chriss cost model) writes a
    ``layer4`` block with ``net_sharpe`` / ``net_ic``.

Until now the runtime gate (core/ml/quality_gate.py) never read either field, so
models with NEGATIVE net-of-cost Sharpe were served (AAPL walkforward_sharpe=-0.114).
Doctrine: feedback_sharpe_over_ic — net Sharpe is the price, IC is not.

Contract under test (additive + fail-safe):
  - fields PRESENT → additionally require fdr_passed == True AND
    net_sharpe > TFT_NET_SHARPE_FLOOR (config, default 0.0);
  - fields ABSENT (legacy metadata) → behaviour identical to today, plus a
    WARNING 'gate fields missing — serving on IC only (legacy metadata)';
  - ``fdr_passed: null`` (excluded from BH) is treated as absent, not as a fail;
  - the ALLOW_WALKFORWARD_ONLY default-True val_loss bypass is UNCHANGED.

Pure JSON — no torch / no pytorch_forecasting → runs in CI.
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
    # Pin IC_MIN so tests are independent of DEPLOYMENT_MODE (0.0 LOCAL / 0.05 cloud).
    monkeypatch.setattr(qg, "IC_MIN", 0.0)


# Metadata that passes every legacy check (val_loss + IC + wilcoxon) — the new
# offline-layer fields are layered on top per test.
_GOOD_LEGACY = {"val_loss": 0.01, "walkforward_ic": 0.10, "wilcoxon_p": 0.01}


# --- (a) fdr_passed=False → NOT served ---------------------------------------------------
def test_fdr_failed_rejected(tmp_path):
    md = _model_dir(tmp_path, {**_GOOD_LEGACY, "fdr_passed": False, "net_sharpe": 1.2})
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False
    assert "fdr_passed" in r.reason  # structured, auditable reason


# --- (b) net_sharpe below floor → NOT served ----------------------------------------------
def test_negative_net_sharpe_rejected(tmp_path):
    md = _model_dir(tmp_path, {**_GOOD_LEGACY, "fdr_passed": True, "net_sharpe": -0.1})
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False
    assert "net_sharpe" in r.reason
    assert "-0.100" in r.reason  # value logged for audit


def test_net_sharpe_nested_in_layer4_is_found(tmp_path):
    # apply_cost_model.py writes net_sharpe inside the `layer4` block — the gate
    # must find it there too, not only at top level.
    md = _model_dir(
        tmp_path,
        {**_GOOD_LEGACY, "fdr_passed": True, "layer4": {"net_sharpe": -0.114}},
    )
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False
    assert "net_sharpe" in r.reason


def test_net_sharpe_at_exact_floor_rejected(tmp_path):
    # Floor is exclusive: net_sharpe must be > floor, not >=.
    md = _model_dir(tmp_path, {**_GOOD_LEGACY, "fdr_passed": True, "net_sharpe": 0.0})
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False


def test_net_sharpe_floor_configurable(tmp_path):
    # Cloud can raise the floor via TFT_NET_SHARPE_FLOOR (env → config).
    md = _model_dir(tmp_path, {**_GOOD_LEGACY, "fdr_passed": True, "net_sharpe": 0.3})
    with patch(
        "core.ml.quality_gate.get_config",
        return_value=_cfg(TFT_NET_SHARPE_FLOOR=0.5),
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False
    assert "0.5" in r.reason


def test_non_numeric_net_sharpe_rejected(tmp_path):
    md = _model_dir(
        tmp_path, {**_GOOD_LEGACY, "fdr_passed": True, "net_sharpe": "high"}
    )
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False
    assert "net_sharpe" in r.reason


# --- (c) both good → served ----------------------------------------------------------------
def test_fdr_passed_and_positive_net_sharpe_served(tmp_path):
    md = _model_dir(tmp_path, {**_GOOD_LEGACY, "fdr_passed": True, "net_sharpe": 0.8})
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True


# --- (d) fields absent (legacy metadata) → behaviour unchanged + WARNING -------------------
def test_missing_fields_legacy_passthrough_with_warning(tmp_path, caplog):
    md = _model_dir(tmp_path, dict(_GOOD_LEGACY))  # no fdr_passed / net_sharpe
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()), caplog.at_level(
        logging.WARNING
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True  # exactly today's behaviour — no hard break
    assert any(
        "serving on IC only" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), "legacy metadata must log a WARNING (CLAUDE.md §5.6)"


def test_fdr_null_treated_as_absent(tmp_path, caplog):
    # apply_fdr_layer.py writes fdr_passed=null when the symbol had no usable
    # p-value (excluded from BH) — that is "not evaluated", not "failed".
    md = _model_dir(tmp_path, {**_GOOD_LEGACY, "fdr_passed": None, "net_sharpe": 0.8})
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()), caplog.at_level(
        logging.WARNING
    ):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True
    assert any("serving on IC only" in rec.message for rec in caplog.records)


def test_legacy_reject_reasons_unchanged(tmp_path):
    # Regression: a legacy IC reject must keep its historical reason (the new
    # checks run only after the existing ones).
    md = _model_dir(tmp_path, {"walkforward_ic": -0.5, "fdr_passed": True})
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False
    assert "walkforward_ic" in r.reason


# --- (f) ALLOW_WALKFORWARD_ONLY path unchanged (hard preserve-constraint) -------------------
def test_allow_walkforward_only_path_unchanged_without_fields(tmp_path, monkeypatch):
    # No val_loss + walkforward-only metadata + default-True bypass → still served.
    monkeypatch.setattr(qg, "ALLOW_WALKFORWARD_ONLY", True)
    monkeypatch.setattr(qg, "STRICT", True)
    md = _model_dir(tmp_path, {"walkforward_ic": 0.10})  # no val_loss, no new fields
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True


def test_allow_walkforward_only_path_with_good_fields(tmp_path, monkeypatch):
    # The bypass composes with the new checks: walkforward-only model with good
    # offline-layer verdicts is served; with a bad net_sharpe it is not.
    monkeypatch.setattr(qg, "ALLOW_WALKFORWARD_ONLY", True)
    monkeypatch.setattr(qg, "STRICT", True)
    good = _model_dir(
        tmp_path,
        {"walkforward_ic": 0.10, "fdr_passed": True, "net_sharpe": 0.9},
        name="GOOD",
    )
    bad = _model_dir(
        tmp_path,
        {"walkforward_ic": 0.10, "fdr_passed": True, "net_sharpe": -0.114},
        name="BAD",
    )
    with patch("core.ml.quality_gate.get_config", return_value=_cfg()):
        assert qg.evaluate("GOOD", good).passed is True
        assert qg.evaluate("BAD", bad).passed is False


def test_config_default_floor_is_zero():
    # ADR default: 0.0 — "must at least not lose money after costs".
    assert RuntimeConfigState().TFT_NET_SHARPE_FLOOR == 0.0
