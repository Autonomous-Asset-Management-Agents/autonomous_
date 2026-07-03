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

_MANIFEST_NAME = "models_manifest.json"


class ModelIntegrityError(RuntimeError):
    """A model asset's SHA-256 does not match its provenance manifest entry."""


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_manifest(asset_path):
    """models_manifest.json ships in the same data/ dir as the assets (make-release);
    AAA_MODELS_MANIFEST overrides for relocated bundles / tests."""
    override = os.environ.get("AAA_MODELS_MANIFEST")
    if override and Path(override).is_file():
        return Path(override)
    cand = Path(asset_path).resolve().parent / _MANIFEST_NAME
    return cand if cand.is_file() else None


def verify_asset(asset_path) -> None:
    """SHA-256-verify asset_path against data/models_manifest.json.

    Raises ModelIntegrityError on a hash mismatch (fail-closed — never deserialize a
    tampered asset). Logs a WARNING and returns when there is no manifest or no entry
    for the file (unprovisioned/dev tree): the gap is made visible without bricking.
    """
    manifest = _find_manifest(asset_path)
    if manifest is None:
        logging.warning(
            "model-integrity: no %s beside %s - loading UNVERIFIED (unprovisioned/dev).",
            _MANIFEST_NAME,
            asset_path,
        )
        return
    try:
        models = json.loads(manifest.read_text(encoding="utf-8")).get("models", [])
    except Exception as exc:  # an unreadable manifest must not brick a working install
        logging.warning(
            "model-integrity: could not read %s (%s) - loading UNVERIFIED.",
            manifest,
            exc,
        )
        return
    name = os.path.basename(str(asset_path))
    want = next((e.get("sha256") for e in models if e.get("filename") == name), None)
    if not want:
        logging.warning(
            "model-integrity: %s not listed in %s - loading UNVERIFIED.",
            name,
            _MANIFEST_NAME,
        )
        return
    got = _sha256(asset_path)
    if got != str(want).lower():
        raise ModelIntegrityError(
            f"model-integrity: {name} SHA-256 mismatch (got {got}, manifest {want}) "
            "- refusing to deserialize a tampered model asset (CWE-502)."
        )
    logging.debug("model-integrity: %s verified against %s.", name, _MANIFEST_NAME)


def safe_joblib_load(asset_path):
    """SHA-256-verify (fail-closed) THEN joblib.load. Use for every pickle (.pkl) asset."""
    import joblib

    verify_asset(asset_path)
    return joblib.load(asset_path)  # nosec B301 - provenance-verified above
