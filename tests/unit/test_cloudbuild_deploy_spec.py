"""D1 deploy-spec guard: the backend Cloud Run deploy mounts the TFT serving tree
READ-ONLY and points the model registry at it — so the ~1.3 GB per-symbol tree is
read on demand via GCS-FUSE (LRU(50), ~3 MB resident) instead of copied into the
2 Gi gen2 in-memory filesystem (which would OOM the engine on boot).

DORMANT: the registry is only consulted when ``ML_PREDICTION_ENABLED=true`` (default
False), so the mount is inert — no trading-path behaviour change — until activation.

Parsed as TEXT (not a YAML loader): the deploy step is a flat ``gcloud run deploy``
arg list, so substring assertions are robust to formatting and key ordering.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLOUDBUILD = _REPO_ROOT / "cloudbuild-backend-deploy.yaml"


def _deploy_text() -> str:
    return _CLOUDBUILD.read_text(encoding="utf-8")


def test_cloudbuild_file_exists():
    assert _CLOUDBUILD.is_file(), f"missing {_CLOUDBUILD}"


def test_tft_models_volume_is_readonly():
    # A SECOND, read-only GCS volume over the same models bucket — defence-in-depth
    # for RF-3: even a compromised engine cannot mutate a checkpoint at the FS layer
    # (the hash gate already verifies the bytes; the RO mount removes the swap vector).
    text = _deploy_text()
    assert "name=tft-models" in text
    assert "type=cloud-storage" in text
    assert "bucket=aaa-trading-bot-models" in text
    assert "readonly=true" in text


def test_tft_models_mount_path():
    text = _deploy_text()
    assert "volume=tft-models" in text
    assert "mount-path=/gcs/models" in text


def test_tft_models_root_env_points_at_mount():
    # _models_root() honours TFT_MODELS_ROOT; the bucket's tft/ prefix surfaces at
    # /gcs/models/tft under the whole-bucket mount → <root>/<SYM>/checkpoint.pt.
    assert "TFT_MODELS_ROOT=/gcs/models/tft" in _deploy_text()


def test_existing_data_mount_untouched():
    # Regression guard: D1 must not disturb the pre-existing read-write data mount.
    text = _deploy_text()
    assert "name=gcs,type=cloud-storage,bucket=aaa-trading-bot-models" in text
    assert "volume=gcs,mount-path=/app/data" in text
