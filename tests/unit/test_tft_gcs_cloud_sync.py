# scripts/gcs_sync_on_start — cloud GCS TFT pull (_sync_tft_from_gcs).
# Mirrors _sync_from_gcs for the tft/ prefix → TFT_MODELS_ROOT. Dormant (no tft/ blobs → no-op),
# idempotent, non-blocking, path-safety on blob names. Mocks google.cloud.storage; no network.

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import gcs_sync_on_start as g  # noqa: E402

_BUCKET = "gs://aaa-trading-bot-models"


def _blob(name, data=b"x"):
    b = MagicMock()
    b.name = name
    b.size = len(data)
    b.download_to_file.side_effect = lambda f: f.write(data)
    return b


def _root(tmp_path, monkeypatch):
    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    # D2: the cloud TFT sync is now explicitly flag-gated (default off). The
    # functional tests below exercise the sync behaviour, so they opt in.
    monkeypatch.setenv("TFT_GCS_SYNC_ENABLED", "true")
    return tmp_path


def test_disabled_by_default_is_noop(tmp_path, monkeypatch):
    # D2 (the OOM footgun fix): without TFT_GCS_SYNC_ENABLED the sync must be a
    # pure no-op — NO GCS client is even constructed — EVEN WHEN tft/ blobs exist.
    # Today the function arms purely on bucket state, so a first tft/ upload would
    # OOM the 2 GiB Cloud Run instance (1.3 GB tree into the in-memory FS).
    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    monkeypatch.delenv("TFT_GCS_SYNC_ENABLED", raising=False)
    with patch("google.cloud.storage.Client") as client:
        client.return_value.bucket.return_value.list_blobs.return_value = [
            _blob("tft/AAPL/checkpoint.pt", b"CK")
        ]
        g._sync_tft_from_gcs(_BUCKET)
    client.assert_not_called()  # gated off → no client, no listing, no download
    assert list(tmp_path.iterdir()) == []


def test_flag_false_string_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    monkeypatch.setenv("TFT_GCS_SYNC_ENABLED", "false")
    with patch("google.cloud.storage.Client") as client:
        client.return_value.bucket.return_value.list_blobs.return_value = [
            _blob("tft/AAPL/checkpoint.pt", b"CK")
        ]
        g._sync_tft_from_gcs(_BUCKET)
    client.assert_not_called()


def test_dormant_no_tft_blobs(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    with patch("google.cloud.storage.Client") as client:
        client.return_value.bucket.return_value.list_blobs.return_value = []
        g._sync_tft_from_gcs(_BUCKET)
    assert (
        list(tmp_path.iterdir()) == []
    )  # no tft/ prefix → cloud not TFT-provisioned → no-op


def test_happy_syncs_tree(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    blobs = [
        _blob("tft/AAPL/checkpoint.pt", b"CK"),
        _blob("tft/AAPL/training_ds.pkl", b"DS"),
        _blob("tft/tft_models_manifest.json", b"{}"),
        _blob("tft/", b""),  # GCS folder marker — must be skipped
    ]
    with patch("google.cloud.storage.Client") as client:
        client.return_value.bucket.return_value.list_blobs.return_value = blobs
        g._sync_tft_from_gcs(_BUCKET)
    assert (tmp_path / "AAPL" / "checkpoint.pt").read_bytes() == b"CK"
    assert (tmp_path / "AAPL" / "training_ds.pkl").exists()
    assert (
        tmp_path / "tft_models_manifest.json"
    ).read_bytes() == b"{}"  # manifest lands at root
    assert not any(
        p.name.endswith(".part") for p in (tmp_path / "AAPL").iterdir()
    )  # atomic


def test_idempotent_skips_when_populated(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    (tmp_path / "AAPL").mkdir()
    (tmp_path / "AAPL" / "checkpoint.pt").write_bytes(b"existing")
    with patch("google.cloud.storage.Client") as client:
        g._sync_tft_from_gcs(_BUCKET)
        client.assert_not_called()  # populated tree → no GCS client created at all
    assert (tmp_path / "AAPL" / "checkpoint.pt").read_bytes() == b"existing"


def test_path_traversal_blob_rejected(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    blobs = [_blob("tft/../evil.pt", b"PWN"), _blob("tft/AAPL/checkpoint.pt", b"CK")]
    with patch("google.cloud.storage.Client") as client:
        client.return_value.bucket.return_value.list_blobs.return_value = blobs
        g._sync_tft_from_gcs(_BUCKET)
    assert not (tmp_path.parent / "evil.pt").exists()  # never escaped the models root
    assert (
        tmp_path / "AAPL" / "checkpoint.pt"
    ).read_bytes() == b"CK"  # good blob still synced


def test_non_blocking_on_download_error(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    bad = _blob("tft/AAPL/checkpoint.pt")
    bad.download_to_file.side_effect = RuntimeError("network drop")
    good = _blob("tft/MSFT/checkpoint.pt", b"OK")
    with patch("google.cloud.storage.Client") as client:
        client.return_value.bucket.return_value.list_blobs.return_value = [bad, good]
        g._sync_tft_from_gcs(_BUCKET)  # must not raise — boot continues
    assert not (tmp_path / "AAPL" / "checkpoint.pt").exists()  # failed one skipped
    assert not any(tmp_path.rglob("*.part"))  # partial cleaned up
    assert (
        tmp_path / "MSFT" / "checkpoint.pt"
    ).read_bytes() == b"OK"  # the rest still synced


def test_gcs_client_unavailable_non_fatal(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    with patch("google.cloud.storage.Client", side_effect=ImportError("no gcs")):
        g._sync_tft_from_gcs(_BUCKET)  # ImportError handled → no crash
    assert list(tmp_path.iterdir()) == []
