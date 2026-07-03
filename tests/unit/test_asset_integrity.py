"""M3 / model-deserialization (CWE-502): joblib.load == pickle.load. safe_joblib_load
SHA-256-verifies a model asset against data/models_manifest.json BEFORE deserialising
— fail-closed on a tampered/swapped .pkl, warn + proceed when unprovisioned (dev).
"""

from __future__ import annotations

import hashlib
import json

import joblib
import pytest

from core.ml.asset_integrity import ModelIntegrityError, safe_joblib_load, verify_asset


def _sha256(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _manifest(d, entries) -> None:
    (d / "models_manifest.json").write_text(
        json.dumps({"models": entries}), encoding="utf-8"
    )


def test_verify_passes_when_hash_matches(tmp_path):
    f = tmp_path / "scaler_x.pkl"
    joblib.dump({"ok": 1}, f)
    _manifest(tmp_path, [{"filename": "scaler_x.pkl", "sha256": _sha256(f)}])
    verify_asset(str(f))  # must NOT raise


def test_verify_raises_on_hash_mismatch(tmp_path):
    f = tmp_path / "scaler_x.pkl"
    joblib.dump({"ok": 1}, f)
    _manifest(tmp_path, [{"filename": "scaler_x.pkl", "sha256": "0" * 64}])
    with pytest.raises(ModelIntegrityError):
        verify_asset(str(f))


def test_verify_warns_and_proceeds_without_manifest(tmp_path):
    f = tmp_path / "scaler_x.pkl"
    joblib.dump({"ok": 1}, f)
    verify_asset(str(f))  # no manifest -> unprovisioned, must not brick


def test_verify_proceeds_when_asset_absent_from_manifest(tmp_path):
    f = tmp_path / "scaler_x.pkl"
    joblib.dump({"ok": 1}, f)
    _manifest(tmp_path, [{"filename": "other.pkl", "sha256": "abc"}])
    verify_asset(str(f))  # no entry for this file -> must not raise


def test_safe_joblib_load_refuses_tampered_asset(tmp_path):
    f = tmp_path / "scaler_x.pkl"
    joblib.dump({"ok": 1}, f)
    _manifest(tmp_path, [{"filename": "scaler_x.pkl", "sha256": "0" * 64}])
    with pytest.raises(ModelIntegrityError):
        safe_joblib_load(str(f))  # must refuse BEFORE deserialising


def test_safe_joblib_load_returns_object_when_verified(tmp_path):
    f = tmp_path / "scaler_x.pkl"
    joblib.dump({"ok": 42}, f)
    _manifest(tmp_path, [{"filename": "scaler_x.pkl", "sha256": _sha256(f)}])
    assert safe_joblib_load(str(f)) == {"ok": 42}
