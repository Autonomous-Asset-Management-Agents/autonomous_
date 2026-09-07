# tests/unit/test_meta_label_filter.py
# #1955 TRD-4 — Meta-Labeling (López de Prado, AFML Ch. 3.6) Trade/No-Trade filter.
# TDD Red → Green (plan §8 R1–R5).
#
# Gherkin (plan §7):
#   - Mock model p < META_LABEL_MIN_PROBA → (False, p, reason) — precision gate fires.
#   - Mock model p >= threshold → (True, p, ...) — strong BUY passes unchanged.
#   - Missing/corrupt artifact → fail-OPEN (True, 1.0) + logging.warning (never DEBUG).
#   - Feature vector ONLY from the real-filled whitelist — the live neutral-default
#     features (rsi_14=50, atr_14d=0, lstm_prediction=0, rl_raw_action=0) must NEVER
#     leak in (forensics: they carry zero information live → the model would learn noise).
#   - Config via config.get_config() — no os.environ read in the finance-core (§2.10).

from __future__ import annotations

import logging
import pickle
from types import SimpleNamespace

import pytest


def _vote(name: str, score: float, weight: float = 1.0, vetoed: bool = False):
    return SimpleNamespace(
        agent_name=name, score=score, weight=weight, vetoed=vetoed, reasoning=""
    )


def _votes(drawdown: float = 0.8, regime: float = 0.6):
    return [
        _vote("LSTMSignalAgent", 0.9),
        _vote("RLConfidenceAgent", 0.9),
        _vote("DrawdownGuardAgent", drawdown),
        _vote("RegimeDetectionAgent", regime),
    ]


def _state(symbol: str = "AAPL") -> dict:
    return {"symbol": symbol, "ohlc": {"close": 100.0}}


class _FixedProbaModel:
    """Deterministic predict_proba stub — [p(0), p(1)] per row."""

    def __init__(self, p1: float):
        self.p1 = p1

    def predict_proba(self, x):
        return [[1.0 - self.p1, self.p1] for _ in x]


def _write_artifact(path, model, features=None):
    from core.round_table.meta_label import FEATURE_WHITELIST

    payload = {
        "schema_version": 1,
        "model": model,
        "features": list(features if features is not None else FEATURE_WHITELIST),
        "trained_at": "2026-07-23T00:00:00+00:00",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(payload, fh)
    return path


def _arm_config(monkeypatch, model_path, min_proba=0.55, enabled=True):
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            META_LABEL_FILTER_ENABLED=enabled,
            META_LABEL_MIN_PROBA=min_proba,
            META_LABEL_MODEL_PATH=str(model_path),
        ),
    )


# ---------------------------------------------------------------------------
# R1 — p below threshold → no-trade
# ---------------------------------------------------------------------------
def test_should_trade_below_threshold_blocks(tmp_path, monkeypatch):
    from core.round_table.meta_label import MetaLabelFilter

    artifact = _write_artifact(tmp_path / "meta.pkl", _FixedProbaModel(0.31))
    _arm_config(monkeypatch, artifact)

    trade, proba, reason = MetaLabelFilter().should_trade(_state(), _votes(), 0.70)
    assert trade is False
    assert proba == pytest.approx(0.31)
    assert "0.31" in reason and "0.55" in reason


# ---------------------------------------------------------------------------
# R2 — p at/above threshold → trade passes
# ---------------------------------------------------------------------------
def test_should_trade_above_threshold_passes(tmp_path, monkeypatch):
    from core.round_table.meta_label import MetaLabelFilter

    artifact = _write_artifact(tmp_path / "meta.pkl", _FixedProbaModel(0.82))
    _arm_config(monkeypatch, artifact)

    trade, proba, _ = MetaLabelFilter().should_trade(_state(), _votes(), 0.70)
    assert trade is True
    assert proba == pytest.approx(0.82)


def test_should_trade_exactly_at_threshold_passes(tmp_path, monkeypatch):
    """Boundary: p == threshold is NOT below it → trade (only p < thr blocks)."""
    from core.round_table.meta_label import MetaLabelFilter

    artifact = _write_artifact(tmp_path / "meta.pkl", _FixedProbaModel(0.55))
    _arm_config(monkeypatch, artifact)

    trade, _, _ = MetaLabelFilter().should_trade(_state(), _votes(), 0.70)
    assert trade is True


# ---------------------------------------------------------------------------
# R3 — missing / corrupt artifact → fail-OPEN (True, 1.0) + WARNING
# ---------------------------------------------------------------------------
def test_missing_artifact_fails_open_with_warning(tmp_path, monkeypatch, caplog):
    from core.round_table.meta_label import MetaLabelFilter

    _arm_config(monkeypatch, tmp_path / "does_not_exist.pkl")

    with caplog.at_level(logging.WARNING, logger="core.round_table.meta_label"):
        trade, proba, _ = MetaLabelFilter().should_trade(_state(), _votes(), 0.70)

    assert trade is True
    assert proba == pytest.approx(1.0)
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_corrupt_artifact_fails_open_with_warning(tmp_path, monkeypatch, caplog):
    from core.round_table.meta_label import MetaLabelFilter

    bad = tmp_path / "corrupt.pkl"
    bad.write_bytes(b"not a pickle")
    _arm_config(monkeypatch, bad)

    with caplog.at_level(logging.WARNING, logger="core.round_table.meta_label"):
        trade, proba, _ = MetaLabelFilter().should_trade(_state(), _votes(), 0.70)

    assert trade is True
    assert proba == pytest.approx(1.0)
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_artifact_with_wrong_feature_schema_fails_open(tmp_path, monkeypatch, caplog):
    """A model trained on a DIFFERENT feature list must never be fed our vector."""
    from core.round_table.meta_label import MetaLabelFilter

    artifact = _write_artifact(
        tmp_path / "meta.pkl", _FixedProbaModel(0.1), features=["rsi_14", "atr_14d"]
    )
    _arm_config(monkeypatch, artifact)

    with caplog.at_level(logging.WARNING, logger="core.round_table.meta_label"):
        trade, proba, _ = MetaLabelFilter().should_trade(_state(), _votes(), 0.70)

    assert trade is True
    assert proba == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# R4 — feature whitelist: ONLY real-filled fields, no neutral-default leak
# ---------------------------------------------------------------------------
def test_feature_whitelist_is_exactly_the_real_filled_set():
    from core.round_table.meta_label import FEATURE_WHITELIST

    assert FEATURE_WHITELIST == ("consensus_score", "drawdown_score", "regime_score")
    # Forensics (plan §12.2): these are neutral-default live → must NEVER be features.
    for banned in ("rsi_14", "atr_14d", "lstm_prediction", "rl_raw_action"):
        assert banned not in FEATURE_WHITELIST


def test_missing_whitelist_vote_fails_open_never_neutral_fills(
    tmp_path, monkeypatch, caplog
):
    """No DrawdownGuard/Regime vote → fail-open; NEVER fabricate a neutral 0.5."""
    from core.round_table.meta_label import MetaLabelFilter

    artifact = _write_artifact(tmp_path / "meta.pkl", _FixedProbaModel(0.1))
    _arm_config(monkeypatch, artifact)

    votes = [_vote("LSTMSignalAgent", 0.9)]  # whitelist agents absent
    with caplog.at_level(logging.WARNING, logger="core.round_table.meta_label"):
        trade, proba, _ = MetaLabelFilter().should_trade(_state(), votes, 0.70)

    assert trade is True
    assert proba == pytest.approx(1.0)
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_extract_features_order_matches_whitelist():
    from core.round_table.meta_label import extract_features

    feats = extract_features(0.70, _votes(drawdown=0.8, regime=0.6))
    assert feats == [0.70, 0.8, 0.6]


# ---------------------------------------------------------------------------
# ADR-SEC-ML2 — optional SHA256 sidecar pin (#2418 review P1 hardening)
# ---------------------------------------------------------------------------
def test_sha256_sidecar_mismatch_blocks_load(tmp_path, monkeypatch, caplog):
    """Sidecar present but hash mismatch → artifact unusable (NO pickle.load
    result is ever used) → existing fail path (observation mode: fail-open)."""
    from core.round_table.meta_label import MetaLabelFilter

    # p=0.10 would BLOCK the trade if the model were loaded — trade=True proves
    # the mismatching artifact was never used.
    artifact = _write_artifact(tmp_path / "meta.pkl", _FixedProbaModel(0.10))
    (tmp_path / "meta.pkl.sha256").write_text("0" * 64, encoding="utf-8")
    _arm_config(monkeypatch, artifact)

    with caplog.at_level(logging.WARNING, logger="core.round_table.meta_label"):
        trade, proba, reason = MetaLabelFilter().should_trade(_state(), _votes(), 0.70)

    assert trade is True
    assert proba == pytest.approx(1.0)
    assert "unavailable" in reason
    assert any("sha256" in r.getMessage().lower() for r in caplog.records)


def test_sha256_sidecar_match_loads(tmp_path, monkeypatch):
    """Sidecar with the correct hash → artifact loads and the model decides."""
    import hashlib

    from core.round_table.meta_label import MetaLabelFilter

    artifact = _write_artifact(tmp_path / "meta.pkl", _FixedProbaModel(0.31))
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    # sha256sum file format: "<hex>  <filename>" — only the hash token counts.
    (tmp_path / "meta.pkl.sha256").write_text(f"{digest}  meta.pkl\n", encoding="utf-8")
    _arm_config(monkeypatch, artifact)

    trade, proba, _ = MetaLabelFilter().should_trade(_state(), _votes(), 0.70)
    assert trade is False  # model WAS loaded: p=0.31 < 0.55 blocks
    assert proba == pytest.approx(0.31)


def test_missing_sidecar_loads_with_unpinned_warning(tmp_path, monkeypatch, caplog):
    """No sidecar → load as before, but a one-time WARNING flags the unpinned
    artifact (cached per path — no per-BUY log flood)."""
    from core.round_table.meta_label import MetaLabelFilter

    artifact = _write_artifact(tmp_path / "meta.pkl", _FixedProbaModel(0.82))
    _arm_config(monkeypatch, artifact)

    flt = MetaLabelFilter()
    with caplog.at_level(logging.WARNING, logger="core.round_table.meta_label"):
        trade, proba, _ = flt.should_trade(_state(), _votes(), 0.70)
        flt.should_trade(_state(), _votes(), 0.70)  # second call: cached load

    assert trade is True
    assert proba == pytest.approx(0.82)
    unpinned = [r for r in caplog.records if "unpinned" in r.getMessage().lower()]
    assert len(unpinned) == 1


# ---------------------------------------------------------------------------
# R5 — config via get_config() only, no os.environ in the finance-core
# ---------------------------------------------------------------------------
def test_no_environ_read_in_meta_label_module():
    import inspect

    import core.round_table.meta_label as mod

    src = inspect.getsource(mod)
    assert "os.environ" not in src and "os.getenv" not in src
