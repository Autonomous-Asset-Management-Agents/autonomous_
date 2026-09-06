"""M3b remedy: gate TFT models on the honest OOS IC behind TFT_QUALITY_GATE_HONEST_IC.

The 250-obs `walkforward_ic` (SE ≈ 0.063) sits inside the 0.0/0.05 floors, so best-of-3-seed
selection leaks ~21% winner's-curse models with NEGATIVE honest OOS IC into the served set.
When the flag is ON, the gate judges a model by the honest ~506-obs decoder-step-0 OOS IC
(`walkforward_ic_oos506`, the same step #1170 serves), preferring a future-proof alias
`walkforward_ic_honest` and warning + falling back to `walkforward_ic` when neither is present.

OFF (default) = byte-identical to the historical gate (reads `walkforward_ic`). Pure JSON —
no torch / no pytorch_forecasting → runs in CI.
"""

import json
import logging
from unittest.mock import patch

import pytest

import core.ml.quality_gate as qg
from config import RuntimeConfigState


def _model_dir(tmp_path, meta):
    (tmp_path / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return tmp_path


def _cfg(honest):
    return RuntimeConfigState(TFT_QUALITY_GATE_HONEST_IC=honest)


@pytest.fixture(autouse=True)
def _fixed_floor(monkeypatch):
    # Pin IC_MIN so the test is independent of DEPLOYMENT_MODE (0.0 LOCAL / 0.05 cloud).
    monkeypatch.setattr(qg, "IC_MIN", 0.05)


# --- a) flag OFF → byte-identical: reads walkforward_ic, ignores the honest field --------
def test_off_reads_walkforward_ic(tmp_path):
    md = _model_dir(
        tmp_path,
        {"val_loss": 0.01, "walkforward_ic": 0.06, "walkforward_ic_oos506": -0.03},
    )
    with patch("core.ml.quality_gate.get_config", return_value=_cfg(False)):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True  # old 0.06 > 0.05 → PASS; honest -0.03 ignored when OFF


# --- b/c) flag ON → the M3b core: honest OOS below floor → REJECT (old metric ignored) ---
def test_on_rejects_when_honest_oos_below_floor(tmp_path):
    md = _model_dir(
        tmp_path,
        {"val_loss": 0.01, "walkforward_ic": 0.06, "walkforward_ic_oos506": -0.03},
    )
    with patch("core.ml.quality_gate.get_config", return_value=_cfg(True)):
        r = qg.evaluate("AAPL", md)
    assert r.passed is False  # honest -0.03 <= 0.05 → REJECT (the winner's-curse drop)
    assert "0.05" in r.reason


# --- e) flag ON → honest above floor → PASS ----------------------------------------------
def test_on_passes_when_honest_above_floor(tmp_path):
    md = _model_dir(
        tmp_path,
        {"val_loss": 0.01, "walkforward_ic": 0.06, "walkforward_ic_oos506": 0.09},
    )
    with patch("core.ml.quality_gate.get_config", return_value=_cfg(True)):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True


# --- Cond 3) priority alias: walkforward_ic_honest preferred over the obs-coupled key -----
def test_on_prefers_honest_alias_over_oos506(tmp_path):
    md = _model_dir(
        tmp_path,
        {
            "val_loss": 0.01,
            "walkforward_ic": 0.06,
            "walkforward_ic_honest": 0.09,  # preferred
            "walkforward_ic_oos506": -0.03,  # would reject if used
        },
    )
    with patch("core.ml.quality_gate.get_config", return_value=_cfg(True)):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True  # uses the alias 0.09, not oos506 -0.03


# --- d) + Cond 1) flag ON but no honest field → fallback to walkforward_ic + WARNING ------
def test_on_falls_back_to_walkforward_ic_with_warning(tmp_path, caplog):
    md = _model_dir(
        tmp_path, {"val_loss": 0.01, "walkforward_ic": 0.06}
    )  # no honest key
    with patch(
        "core.ml.quality_gate.get_config", return_value=_cfg(True)
    ), caplog.at_level(logging.WARNING):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True  # falls back to walkforward_ic 0.06 > 0.05
    assert any(
        "walkforward_ic" in rec.message and "fall" in rec.message.lower()
        for rec in caplog.records
    ), "fallback must log at WARNING (CLAUDE.md §5.6)"


# --- harmonization: an honest-ONLY model (no legacy walkforward_ic, no val_loss) is a
# walk-forward signal too → the val_loss bypass lets it through to the IC gate, not reject -
def test_on_honest_only_model_reaches_ic_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(qg, "ALLOW_WALKFORWARD_ONLY", True)
    monkeypatch.setattr(qg, "STRICT", True)
    md = _model_dir(tmp_path, {"walkforward_ic_oos506": 0.09})  # no val_loss, no legacy
    with patch("core.ml.quality_gate.get_config", return_value=_cfg(True)):
        r = qg.evaluate("AAPL", md)
    assert r.passed is True  # val_loss bypass via honest signal → honest IC 0.09 > 0.05
