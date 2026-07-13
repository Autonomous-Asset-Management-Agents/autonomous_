# core/ml/asset_integrity.py
# Security M3 (audit: model-deserialization / CWE-502). joblib.load == pickle.load,
# so deserialising a swapped/tampered .pkl is arbitrary code execution. The LSTM/RL
# model assets (scaler_x.pkl, scaler_y.pkl, rl_agent_v3_dsr_stats.pkl, ...) are listed
# with a SHA-256 in data/models_manifest.json and verified at INSTALL time (bootstrap
# SHA256SUMS + verify-staged-models.cjs). This re-verifies at LOAD time — closing the
# gap where a .pkl is swapped AFTER install (local tamper, or a model-swap/update path
# that does not re-verify). Fail-closed on mismatch; WARN + proceed when there is no
# manifest / no entry (an unprovisioned dev tree must never be bricked, CLAUDE.md 5.6).

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Union

_MANIFEST_NAME = "models_manifest.json"


class ModelIntegrityError(RuntimeError):
    """A model asset's SHA-256 does not match its provenance manifest entry."""


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _find_manifest(asset_path):
    """models_manifest.json ships in the same data/ dir as the assets (make-release);
    AAA_MODELS_MANIFEST overrides for relocated bundles / tests."""
    override = os.environ.get("AAA_MODELS_MANIFEST")
    if override and Path(override).is_file():
        return Path(override)
    cand = Path(asset_path).resolve().parent / _MANIFEST_NAME
    return cand if cand.is_file() else None


def _require_manifest() -> bool:
    """Whether an ABSENT manifest is fatal. Explicit TFT_REQUIRE_MANIFEST or
    AAA_REQUIRE_MANIFEST wins; otherwise strict everywhere EXCEPT DEPLOYMENT_MODE=LOCAL
    so local dev without provisioned checkpoints works.

    Note: Keep the LSTM/RL verification permissive under LOCAL to ease local model development
    (frequent scaler swaps without rebuilding manifests), unlike the strict default for TFT.
    """
    override = os.getenv("TFT_REQUIRE_MANIFEST") or os.getenv("AAA_REQUIRE_MANIFEST")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes")
    return os.environ.get("DEPLOYMENT_MODE", "").upper() != "LOCAL"


def verify_asset_bytes(data: bytes, asset_path: Union[str, Path]) -> None:
    """SHA-256-verify data buffer against models_manifest.json.

    Raises ModelIntegrityError on a hash mismatch (fail-closed).
    Under LOCAL deployment, missing/unreadable manifest warnings are logged but
    not fatal. In production, missing manifests are fatal (fail-closed).
    """
    manifest = _find_manifest(asset_path)
    if manifest is None:
        if _require_manifest():
            raise ModelIntegrityError(
                f"model-integrity: {_MANIFEST_NAME} not found beside {asset_path} (fail-closed)"
            )
        logging.warning(
            "model-integrity: no %s beside %s - loading UNVERIFIED (unprovisioned/dev).",
            _MANIFEST_NAME,
            asset_path,
        )
        return
    try:
        models = json.loads(manifest.read_text(encoding="utf-8")).get("models", [])
    except Exception as exc:
        if _require_manifest():
            raise ModelIntegrityError(
                f"model-integrity: could not read {manifest} (fail-closed): {exc}"
            )
        logging.warning(
            "model-integrity: could not read %s (%s) - loading UNVERIFIED.",
            manifest,
            exc,
        )
        return

    name = os.path.basename(str(asset_path))
    want = next((e.get("sha256") for e in models if e.get("filename") == name), None)
    if not want:
        if _require_manifest():
            raise ModelIntegrityError(
                f"model-integrity: {name} not listed in {manifest} (fail-closed)"
            )
        logging.warning(
            "model-integrity: %s not listed in %s - loading UNVERIFIED.",
            name,
            _MANIFEST_NAME,
        )
        return

    got = _sha256_bytes(data)
    if got != str(want).lower():
        raise ModelIntegrityError(
            f"model-integrity: {name} SHA-256 mismatch (got {got}, manifest {want}) "
            "- refusing to deserialize a tampered model asset (CWE-502)."
        )
    logging.debug("model-integrity: %s verified against %s.", name, manifest)


def verify_asset(asset_path) -> None:
    """Legacy wrapper. Reads path bytes and verifies them.
    Warning: using verify_asset followed by a separate load is vulnerable to TOCTOU.
    Use safe_joblib_load or safe_torch_load instead.
    """
    data = Path(asset_path).read_bytes()
    verify_asset_bytes(data, asset_path)


def safe_read_bytes(asset_path: str) -> bytes:
    """SHA-256-verify (fail-closed) from read-once bytes, then return the bytes.
    Prevents TOCTOU (CWE-367).
    """
    data = Path(asset_path).read_bytes()
    verify_asset_bytes(data, asset_path)
    return data


def safe_joblib_load(asset_path: str) -> Any:
    """SHA-256-verify (fail-closed) from read-once bytes, then joblib.load via io.BytesIO.
    Use for every pickle (.pkl) asset. Prevents TOCTOU (CWE-367).
    """
    import io

    import joblib

    data = safe_read_bytes(asset_path)
    return joblib.load(io.BytesIO(data))  # nosec B301 - provenance-verified above


def safe_torch_load(asset_path: str, **kwargs) -> Any:
    """SHA-256-verify (fail-closed) from read-once bytes, then torch.load via io.BytesIO.
    Use for PyTorch (.pth / .pt) weights. Prevents TOCTOU (CWE-367).
    """
    import io

    import torch

    data = safe_read_bytes(asset_path)
    return torch.load(
        io.BytesIO(data), **kwargs
    )  # nosec B614 - provenance-verified above
