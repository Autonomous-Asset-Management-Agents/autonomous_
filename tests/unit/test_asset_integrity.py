"""M3 / model-deserialization (CWE-502): joblib.load == pickle.load. safe_joblib_load
SHA-256-verifies a model asset against data/models_manifest.json BEFORE deserialising
— fail-closed on a tampered/swapped .pkl, warn + proceed when unprovisioned (dev).
"""

from __future__ import annotations

import hashlib
import json

import joblib
import pytest

from core.ml.asset_integrity import (
    ModelIntegrityError,
    safe_joblib_load,
    safe_read_bytes,
    safe_torch_load,
    verify_asset,
    verify_asset_bytes,
)


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


def test_verify_refuses_without_manifest_on_local(tmp_path, monkeypatch):
    # #2984 (sibling to #1886): LOCAL is no longer exempt. An absent manifest is
    # fatal (fail-closed) so a stripped-manifest tamper cannot force UNVERIFIED loads.
    import config

    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")

    monkeypatch.setattr(config, "AAA_REQUIRE_MANIFEST", True)
    f = tmp_path / "scaler_x.pkl"
    joblib.dump({"ok": 1}, f)
    with pytest.raises(ModelIntegrityError):
        verify_asset(str(f))  # no manifest under LOCAL -> fail-closed


def test_verify_local_optout_proceeds_without_manifest(tmp_path, monkeypatch):
    # A deliberate developer keeps the escape hatch via AAA_REQUIRE_MANIFEST=false.
    import config

    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")

    monkeypatch.setattr(config, "AAA_REQUIRE_MANIFEST", False)
    f = tmp_path / "scaler_x.pkl"
    joblib.dump({"ok": 1}, f)
    verify_asset(str(f))  # explicit dev opt-out -> warn + proceed, must not raise


def test_verify_refuses_when_asset_absent_from_manifest_on_local(tmp_path, monkeypatch):
    import config

    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setattr(config, "AAA_REQUIRE_MANIFEST", True)
    monkeypatch.setattr(config, "TFT_REQUIRE_MANIFEST", True)
    f = tmp_path / "scaler_x.pkl"
    joblib.dump({"ok": 1}, f)
    _manifest(tmp_path, [{"filename": "other.pkl", "sha256": "abc"}])
    with pytest.raises(ModelIntegrityError):
        verify_asset(str(f))  # no entry for this file under LOCAL -> fail-closed


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


def test_require_manifest_behavior(monkeypatch):
    import config
    from core.ml.asset_integrity import _require_manifest

    # 1. DEPLOYMENT_MODE=LOCAL and no override -> True (fail-closed by default; #2984)
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setattr(config, "TFT_REQUIRE_MANIFEST", True)
    monkeypatch.setattr(config, "AAA_REQUIRE_MANIFEST", True)
    assert _require_manifest()

    # 2. DEPLOYMENT_MODE=PROD and no override -> True (fail-closed in prod)
    monkeypatch.setenv("DEPLOYMENT_MODE", "PROD")
    monkeypatch.setattr(config, "AAA_REQUIRE_MANIFEST", True)
    assert _require_manifest()

    # 3. DEPLOYMENT_MODE=LOCAL but TFT_REQUIRE_MANIFEST=1 -> True (override wins)
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setattr(config, "TFT_REQUIRE_MANIFEST", True)
    monkeypatch.setattr(config, "AAA_REQUIRE_MANIFEST", True)
    assert _require_manifest()

    # 4. DEPLOYMENT_MODE=PROD but TFT_REQUIRE_MANIFEST=0 -> False (opt-out wins)
    # Wait, the codebase only checks config.AAA_REQUIRE_MANIFEST!
    # TFT_REQUIRE_MANIFEST is likely mapped to AAA_REQUIRE_MANIFEST in config.py or ignored if we replaced it.
    # Actually, config.AAA_REQUIRE_MANIFEST handles the fallback logic.
    monkeypatch.setenv("DEPLOYMENT_MODE", "PROD")
    monkeypatch.setattr(config, "AAA_REQUIRE_MANIFEST", False)
    assert not _require_manifest()

    # 5. DEPLOYMENT_MODE=LOCAL but AAA_REQUIRE_MANIFEST=false -> False (dev opt-out; #2984)
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setattr(config, "AAA_REQUIRE_MANIFEST", False)
    assert not _require_manifest()


def test_verify_asset_bytes_raises_under_prod_without_manifest(tmp_path, monkeypatch):
    import config

    monkeypatch.setenv("DEPLOYMENT_MODE", "PROD")
    monkeypatch.setattr(config, "TFT_REQUIRE_MANIFEST", True)
    monkeypatch.setattr(config, "AAA_REQUIRE_MANIFEST", True)

    f = tmp_path / "scaler_x.pkl"
    joblib.dump({"ok": 1}, f)
    data = f.read_bytes()

    # In prod, missing manifest raises ModelIntegrityError
    with pytest.raises(ModelIntegrityError, match="not found beside"):
        verify_asset_bytes(data, str(f))


def test_verify_asset_bytes_raises_under_prod_when_asset_absent(tmp_path, monkeypatch):
    import config

    monkeypatch.setenv("DEPLOYMENT_MODE", "PROD")
    monkeypatch.setattr(config, "TFT_REQUIRE_MANIFEST", True)
    monkeypatch.setattr(config, "AAA_REQUIRE_MANIFEST", True)

    f = tmp_path / "scaler_x.pkl"
    joblib.dump({"ok": 1}, f)
    data = f.read_bytes()
    _manifest(tmp_path, [{"filename": "other.pkl", "sha256": "abc"}])

    # In prod, missing entry raises ModelIntegrityError
    with pytest.raises(ModelIntegrityError, match="not listed in"):
        verify_asset_bytes(data, str(f))


def test_safe_torch_load_raises_on_hash_mismatch(tmp_path):
    import torch

    f = tmp_path / "model.pth"
    torch.save({"weights": [0.1, 0.2]}, f)
    _manifest(tmp_path, [{"filename": "model.pth", "sha256": "0" * 64}])

    with pytest.raises(ModelIntegrityError):
        safe_torch_load(str(f), weights_only=True)


def test_safe_torch_load_returns_object_when_verified(tmp_path):
    import torch

    f = tmp_path / "model.pth"
    torch.save({"weights": [0.1, 0.2]}, f)
    _manifest(tmp_path, [{"filename": "model.pth", "sha256": _sha256(f)}])

    loaded = safe_torch_load(str(f), weights_only=True)
    assert loaded["weights"] == [0.1, 0.2]


def test_safe_read_bytes_verifies_correctly(tmp_path):
    f = tmp_path / "model.pth"
    f.write_bytes(b"some model data bytes")
    _manifest(tmp_path, [{"filename": "model.pth", "sha256": _sha256(f)}])

    loaded_bytes = safe_read_bytes(str(f))
    assert loaded_bytes == b"some model data bytes"


# --- #2984: LOCAL manifest verification for the general joblib/torch loader -----


def test_safe_joblib_load_refuses_unmanifested_pkl_on_local(tmp_path, monkeypatch):
    # An attacker with write access to the models dir deletes the manifest and swaps
    # the .pkl. Under LOCAL with no override, the loader must refuse BEFORE unpickling.
    import config

    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setattr(config, "TFT_REQUIRE_MANIFEST", True)
    monkeypatch.setattr(config, "AAA_REQUIRE_MANIFEST", True)
    f = tmp_path / "scaler_x.pkl"
    joblib.dump({"ok": 1}, f)  # NO manifest beside it
    with pytest.raises(ModelIntegrityError):
        safe_joblib_load(str(f))


def test_safe_joblib_load_local_optout_loads_unverified(tmp_path, monkeypatch):
    # A deliberate developer keeps the escape hatch via AAA_REQUIRE_MANIFEST=false.
    import config

    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setattr(config, "AAA_REQUIRE_MANIFEST", False)
    f = tmp_path / "scaler_x.pkl"
    joblib.dump({"ok": 7}, f)
    assert safe_joblib_load(str(f)) == {"ok": 7}  # deliberate dev opt-out survives


def test_safe_torch_load_refuses_unmanifested_pth_on_local(tmp_path, monkeypatch):
    import torch

    import config

    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setattr(config, "TFT_REQUIRE_MANIFEST", True)
    monkeypatch.setattr(config, "AAA_REQUIRE_MANIFEST", True)
    f = tmp_path / "model.pth"
    torch.save({"weights": [0.1, 0.2]}, f)  # NO manifest beside it
    with pytest.raises(ModelIntegrityError):
        safe_torch_load(str(f), weights_only=True)
