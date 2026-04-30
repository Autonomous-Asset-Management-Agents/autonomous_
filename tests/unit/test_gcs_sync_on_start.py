# tests/unit/test_gcs_sync_on_start.py
# TDD: Tests geschrieben VOR der Implementierung von scripts/gcs_sync_on_start.py
#
# Deckt ab:
#   - Kein GCS_DATA_BUCKET gesetzt → lokaler Betrieb, exit 0
#   - data/ wird angelegt wenn nicht vorhanden
#   - Modelle werden von gs://bucket/data/ nach DATA_DIR/ heruntergeladen
#   - GCS-Fehler blockiert NICHT den Engine-Start (exit immer 0)
#   - Leerer Bucket → exit 0
#   - OSS / Self-Host fallback: GitHub-Release-Pull wenn kein GCS-Bucket
#     aber data/models_manifest.json vorhanden → Files via HTTPS + SHA256
#     Integrity-Check + URL allow-list + size cap + filename traversal guard.

import hashlib
import importlib
import io
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Pfad zum scripts/-Verzeichnis hinzufügen, damit gcs_sync_on_start importierbar ist
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "../../scripts")
sys.path.insert(0, os.path.abspath(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Hilfsfunktion: Modul nach Env-Var-Änderungen neu laden (Caching umgehen)
# ---------------------------------------------------------------------------
def _load_module():
    import gcs_sync_on_start

    importlib.reload(gcs_sync_on_start)
    return gcs_sync_on_start


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGcsSyncOnStart:
    """Unit-Tests für scripts/gcs_sync_on_start.py (GCS → Container Sync)."""

    def test_no_gcs_bucket_env_returns_zero(self, monkeypatch):
        """Wenn GCS_DATA_BUCKET nicht gesetzt ist, soll main() 0 zurückgeben (lokaler Betrieb).
        Der Engine-Startup darf nicht blockiert werden.
        """
        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        mod = _load_module()
        result = mod.main()
        assert (
            result == 0
        ), "main() MUSS 0 zurückgeben wenn kein GCS-Bucket konfiguriert ist"

    def test_empty_gcs_bucket_string_returns_zero(self, monkeypatch):
        """GCS_DATA_BUCKET='' (leerer String) → gleiches Verhalten wie nicht gesetzt."""
        monkeypatch.setenv("GCS_DATA_BUCKET", "")
        mod = _load_module()
        result = mod.main()
        assert result == 0

    def test_creates_data_dir_if_missing(self, monkeypatch, tmp_path):
        """Das DATA_DIR-Verzeichnis wird angelegt, falls es noch nicht existiert."""
        target_dir = tmp_path / "new_data"
        assert not target_dir.exists()

        monkeypatch.setenv("GCS_DATA_BUCKET", "gs://aaa-trading-bot-models")
        monkeypatch.setenv("DATA_DIR", str(target_dir))

        mock_blob = MagicMock()
        mock_blob.name = "data/rl_agent_v5.zip"
        mock_blob.download_to_file = MagicMock()

        with patch("google.cloud.storage.Client") as mock_client:
            mock_client.return_value.bucket.return_value.list_blobs.return_value = [
                mock_blob
            ]
            mod = _load_module()
            mod.main()

        assert target_dir.exists(), "DATA_DIR muss nach dem Sync existieren"

    def test_downloads_rl_and_lstm_models(self, monkeypatch, tmp_path):
        """RL-Modell (rl_agent_v5.zip) und LSTM-Modell (lstm_model_v2.pth) werden
        von gs://bucket/data/ nach DATA_DIR/ heruntergeladen.
        """
        data_dir = tmp_path / "data"
        monkeypatch.setenv("GCS_DATA_BUCKET", "gs://aaa-trading-bot-models")
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        mock_rl = MagicMock()
        mock_rl.name = "data/rl_agent_v5.zip"
        mock_lstm = MagicMock()
        mock_lstm.name = "data/lstm_model_v2.pth"

        with patch("google.cloud.storage.Client") as mock_client:
            mock_client.return_value.bucket.return_value.list_blobs.return_value = [
                mock_rl,
                mock_lstm,
            ]
            mod = _load_module()
            result = mod.main()

        assert result == 0
        # Verify each blob had download_to_file called exactly once.
        assert mock_rl.download_to_file.call_count == 1
        assert mock_lstm.download_to_file.call_count == 1
        # Verify the files were written to the correct paths by checking
        # that the expected output files exist on disk (created by open()).
        assert (data_dir / "rl_agent_v5.zip").exists()
        assert (data_dir / "lstm_model_v2.pth").exists()

    def test_correct_bucket_name_used(self, monkeypatch, tmp_path):
        """gs://-Präfix wird korrekt entfernt; nur der Bucket-Name wird übergeben."""
        monkeypatch.setenv("GCS_DATA_BUCKET", "gs://aaa-trading-bot-models")
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

        with patch("google.cloud.storage.Client") as mock_client:
            mock_client.return_value.bucket.return_value.list_blobs.return_value = []
            mod = _load_module()
            mod.main()

            mock_client.return_value.bucket.assert_called_once_with(
                "aaa-trading-bot-models"
            )

    def test_gcs_connection_error_does_not_block_engine(self, monkeypatch, tmp_path):
        """Wenn GCS nicht erreichbar ist, MUSS main() trotzdem 0 zurückgeben.
        Der Engine-Start wird nie durch einen GCS-Fehler blockiert.
        """
        monkeypatch.setenv("GCS_DATA_BUCKET", "gs://aaa-trading-bot-models")
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

        with patch("google.cloud.storage.Client") as mock_client:
            mock_client.return_value.bucket.side_effect = Exception(
                "Connection refused"
            )
            mod = _load_module()
            result = mod.main()

        assert result == 0, "GCS-Fehler darf den Engine-Start NICHT blockieren"

    def test_empty_bucket_returns_zero(self, monkeypatch, tmp_path):
        """Leerer GCS-Bucket (keine Dateien unter data/) → exit 0, kein Crash."""
        monkeypatch.setenv("GCS_DATA_BUCKET", "gs://aaa-trading-bot-models")
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

        with patch("google.cloud.storage.Client") as mock_client:
            mock_client.return_value.bucket.return_value.list_blobs.return_value = []
            mod = _load_module()
            result = mod.main()

        assert result == 0

    def test_directory_blob_skipped(self, monkeypatch, tmp_path):
        """Ein Blob der auf '/' endet (GCS-Ordner-Marker) wird übersprungen."""
        data_dir = tmp_path / "data"
        monkeypatch.setenv("GCS_DATA_BUCKET", "gs://aaa-trading-bot-models")
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        mock_dir_blob = MagicMock()
        mock_dir_blob.name = "data/"  # GCS-Ordner-Marker (endet mit /)
        mock_dir_blob.size = 0

        with patch("google.cloud.storage.Client") as mock_client:
            mock_client.return_value.bucket.return_value.list_blobs.return_value = [
                mock_dir_blob
            ]
            mod = _load_module()
            result = mod.main()

        assert result == 0
        mock_dir_blob.download_to_file.assert_not_called()


# OSS / Self-Host: GitHub Release Fallback
# ---------------------------------------------------------------------------
#
# Pfad-Dispatcher:
#   - GCS_DATA_BUCKET gesetzt  → existing GCS path (Production)
#   - GCS_DATA_BUCKET unset    → if data/models_manifest.json exists → GitHub-Release path
#                                else → no-op (vorheriges Verhalten)
#
# Verhalten unter Failure-Modi: IMMER exit 0 (Engine-Start nie blockieren).


def _make_manifest(
    files_with_payloads, base_url="https://github.com/o/r/releases/download/models-v1.0"
):
    """Build (manifest_dict, payloads_dict) for tests."""
    payloads = {}
    entries = []
    for fname, payload in files_with_payloads.items():
        sha = hashlib.sha256(payload).hexdigest()
        url = f"{base_url}/{fname}"
        entries.append(
            {"filename": fname, "url": url, "sha256": sha, "size_bytes": len(payload)}
        )
        payloads[url] = payload
    return ({"release_tag": "models-v1.0", "models": entries}, payloads)


class _FakeUrlOpen:
    """Replacement for urllib.request.urlopen — returns bytes from a URL→payload dict.

    Raises URLError-equivalent (Exception) for unknown URLs so we can simulate network errors.
    """

    def __init__(self, payloads):
        self._payloads = payloads

    def __call__(self, request_or_url, *args, **kwargs):
        url = (
            request_or_url
            if isinstance(request_or_url, str)
            else request_or_url.full_url
        )
        if url not in self._payloads:
            raise Exception(f"network unreachable: {url}")
        payload = self._payloads[url]
        bio = io.BytesIO(payload)
        # urlopen returns a context manager; emulate enough surface
        bio.__enter__ = lambda self_: self_  # type: ignore[assignment]
        bio.__exit__ = lambda self_, *a: None  # type: ignore[assignment]
        return bio


class TestGithubReleaseFallback:
    """OSS / self-host fallback: pull models from a GitHub Release when GCS is unset."""

    def test_github_path_chosen_when_no_gcs_bucket_and_manifest_exists(
        self, monkeypatch, tmp_path
    ):
        """No GCS bucket + manifest present → GitHub-Release path triggers, exit 0."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        manifest, payloads = _make_manifest({"lstm_model_v2.pth": b"\x00\x01\x02"})
        (data_dir / "models_manifest.json").write_text(json.dumps(manifest))

        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        with patch("urllib.request.urlopen", side_effect=_FakeUrlOpen(payloads)):
            mod = _load_module()
            result = mod.main()

        assert result == 0
        # File written with correct content
        assert (data_dir / "lstm_model_v2.pth").read_bytes() == b"\x00\x01\x02"

    def test_github_pull_writes_all_manifest_files(self, monkeypatch, tmp_path):
        """All files listed in manifest are downloaded to DATA_DIR with matching content."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        files = {
            "lstm_model_v2.pth": b"lstm-bytes",
            "rl_agent_v5.zip": b"rl-bytes",
            "scaler_x_v2.pkl": b"sx",
            "scaler_y_v2.pkl": b"sy",
            "model_metadata_v2.json": b'{"k":1}',
            "rl_stats_v5.pkl": b"stats",
        }
        manifest, payloads = _make_manifest(files)
        (data_dir / "models_manifest.json").write_text(json.dumps(manifest))

        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        with patch("urllib.request.urlopen", side_effect=_FakeUrlOpen(payloads)):
            mod = _load_module()
            result = mod.main()

        assert result == 0
        for fname, payload in files.items():
            assert (data_dir / fname).read_bytes() == payload

    def test_github_pull_sha_mismatch_skips_file(self, monkeypatch, tmp_path):
        """If downloaded bytes don't match manifest SHA256, the file is NOT written."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        manifest, payloads = _make_manifest({"corrupt.bin": b"original"})
        # Tamper with the URL payload to simulate corruption in transit.
        bad_url = manifest["models"][0]["url"]
        payloads[bad_url] = b"TAMPERED"
        (data_dir / "models_manifest.json").write_text(json.dumps(manifest))

        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        with patch("urllib.request.urlopen", side_effect=_FakeUrlOpen(payloads)):
            mod = _load_module()
            result = mod.main()

        assert result == 0
        assert not (
            data_dir / "corrupt.bin"
        ).exists(), "Corrupted file (SHA mismatch) MUST NOT be written to DATA_DIR"

    def test_github_pull_network_error_returns_zero(self, monkeypatch, tmp_path):
        """Network unreachable for any manifest URL → exit 0, no crash."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        manifest, _ = _make_manifest({"lstm_model_v2.pth": b"x"})
        # Empty payloads → every urlopen raises
        (data_dir / "models_manifest.json").write_text(json.dumps(manifest))

        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        with patch("urllib.request.urlopen", side_effect=_FakeUrlOpen({})):
            mod = _load_module()
            result = mod.main()

        assert result == 0
        assert not (data_dir / "lstm_model_v2.pth").exists()

    def test_github_pull_no_manifest_returns_zero(self, monkeypatch, tmp_path):
        """No GCS bucket and no manifest → no-op, exit 0 (preserves original behaviour)."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()  # exists but no manifest
        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        mod = _load_module()
        result = mod.main()

        assert result == 0
        assert list(data_dir.iterdir()) == [], "DATA_DIR must remain empty"

    def test_gcs_path_unchanged_when_bucket_set(self, monkeypatch, tmp_path):
        """Regression: when GCS_DATA_BUCKET is set, GitHub path MUST NOT be triggered.

        Even if a manifest exists in DATA_DIR, the GCS path takes precedence
        (production behaviour unchanged).
        """
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        manifest, payloads = _make_manifest({"would_not_pull.bin": b"nope"})
        (data_dir / "models_manifest.json").write_text(json.dumps(manifest))

        monkeypatch.setenv("GCS_DATA_BUCKET", "gs://aaa-trading-bot-models")
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        github_calls = []

        def _record(*a, **k):
            github_calls.append(a)
            raise Exception("must not be called")

        with patch("google.cloud.storage.Client") as mock_client, patch(
            "urllib.request.urlopen", side_effect=_record
        ):
            mock_client.return_value.bucket.return_value.list_blobs.return_value = []
            mod = _load_module()
            result = mod.main()

        assert result == 0
        assert (
            github_calls == []
        ), "GitHub path MUST NOT be triggered when GCS bucket is set"
        assert not (data_dir / "would_not_pull.bin").exists()


# ---------------------------------------------------------------------------
# Security & robustness regression tests
# (added after fresh-eyes review flagged: file:// scheme, path traversal,
#  unbounded resp.read(), malformed manifest, empty models[])
# ---------------------------------------------------------------------------


class TestGithubReleaseSecurityGuards:
    """Guards added because urlopen(url) accepts file://, ftp://, data:, etc.,
    and a malicious manifest could otherwise read host files or write outside
    DATA_DIR via a crafted filename. Plus: hostile mirror returning gigabytes.
    """

    def test_rejects_file_scheme_url(self, monkeypatch, tmp_path):
        """A manifest with file:///etc/passwd MUST NOT be opened."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        manifest = {
            "release_tag": "evil",
            "models": [
                {
                    "filename": "ok.bin",
                    "url": "file:///etc/passwd",
                    "sha256": "0" * 64,
                    "size_bytes": 1,
                }
            ],
        }
        (data_dir / "models_manifest.json").write_text(json.dumps(manifest))

        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        called = []

        def _record(*a, **k):
            called.append(a)
            raise Exception("must not be called")

        with patch("urllib.request.urlopen", side_effect=_record):
            mod = _load_module()
            result = mod.main()

        assert result == 0
        assert called == [], "urlopen MUST NOT be called for file:// scheme"
        assert not (data_dir / "ok.bin").exists()

    def test_rejects_non_github_https_url(self, monkeypatch, tmp_path):
        """Even https:// is rejected if the host is not on the allow-list."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        manifest = {
            "release_tag": "evil",
            "models": [
                {
                    "filename": "ok.bin",
                    "url": "https://evil.example.com/payload",
                    "sha256": "0" * 64,
                    "size_bytes": 1,
                }
            ],
        }
        (data_dir / "models_manifest.json").write_text(json.dumps(manifest))

        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        called = []

        def _record(*a, **k):
            called.append(a)
            raise Exception("must not be called")

        with patch("urllib.request.urlopen", side_effect=_record):
            mod = _load_module()
            result = mod.main()

        assert result == 0
        assert called == [], "urlopen MUST NOT be called for non-GitHub host"

    def test_rejects_path_traversal_filename(self, monkeypatch, tmp_path):
        """A manifest entry filename containing ``../`` must be skipped."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # Pre-create a sentinel one level up so we'd notice if traversal happened.
        outside = tmp_path / "outside.bin"
        manifest = {
            "release_tag": "evil",
            "models": [
                {
                    "filename": "../outside.bin",
                    "url": "https://github.com/o/r/releases/download/v/x",
                    "sha256": "0" * 64,
                    "size_bytes": 1,
                }
            ],
        }
        (data_dir / "models_manifest.json").write_text(json.dumps(manifest))

        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        with patch("urllib.request.urlopen", side_effect=Exception("net")):
            mod = _load_module()
            result = mod.main()

        assert result == 0
        assert not outside.exists(), "Path-traversal MUST NOT escape DATA_DIR"

    def test_rejects_absolute_filename(self, monkeypatch, tmp_path):
        """Filenames containing path separators are rejected."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        manifest = {
            "release_tag": "evil",
            "models": [
                {
                    "filename": "subdir/inner.bin",
                    "url": "https://github.com/o/r/releases/download/v/x",
                    "sha256": "0" * 64,
                    "size_bytes": 1,
                }
            ],
        }
        (data_dir / "models_manifest.json").write_text(json.dumps(manifest))

        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        with patch("urllib.request.urlopen", side_effect=Exception("net")):
            mod = _load_module()
            result = mod.main()

        assert result == 0
        assert not (data_dir / "subdir").exists()

    def test_size_cap_rejects_oversized_payload(self, monkeypatch, tmp_path):
        """Hostile mirror returning more than (size_bytes + slack) is rejected."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # Manifest claims 100 bytes; mirror returns 100 MB.
        oversize = b"X" * (100 * 1024 * 1024)
        sha = hashlib.sha256(b"X" * 100).hexdigest()  # SHA of the legit payload
        url = "https://github.com/o/r/releases/download/v/oversized.bin"
        manifest = {
            "release_tag": "evil",
            "models": [
                {
                    "filename": "oversized.bin",
                    "url": url,
                    "sha256": sha,
                    "size_bytes": 100,
                }
            ],
        }
        (data_dir / "models_manifest.json").write_text(json.dumps(manifest))

        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        with patch("urllib.request.urlopen", side_effect=_FakeUrlOpen({url: oversize})):
            mod = _load_module()
            result = mod.main()

        assert result == 0
        assert not (
            data_dir / "oversized.bin"
        ).exists(), (
            "Oversized payload MUST NOT be written even if claimed size was tiny"
        )

    def test_malformed_json_manifest_returns_zero(self, monkeypatch, tmp_path):
        """Manifest with invalid JSON → WARN, no-op, exit 0."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "models_manifest.json").write_text("{not valid json")

        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        mod = _load_module()
        result = mod.main()

        assert result == 0

    def test_empty_models_array_returns_zero(self, monkeypatch, tmp_path):
        """Manifest with `models: []` → WARN, no-op, exit 0."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "models_manifest.json").write_text(
            json.dumps({"release_tag": "v0", "models": []})
        )

        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        mod = _load_module()
        result = mod.main()

        assert result == 0

    def test_manifest_entry_missing_sha256_skipped(self, monkeypatch, tmp_path):
        """Entry without sha256 field is skipped (not silently downloaded)."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        url = "https://github.com/o/r/releases/download/v/x.bin"
        manifest = {
            "release_tag": "v0",
            "models": [
                {
                    "filename": "x.bin",
                    "url": url,
                    # sha256 omitted
                    "size_bytes": 3,
                }
            ],
        }
        (data_dir / "models_manifest.json").write_text(json.dumps(manifest))

        monkeypatch.delenv("GCS_DATA_BUCKET", raising=False)
        monkeypatch.setenv("DATA_DIR", str(data_dir))

        called = []

        def _record(*a, **k):
            called.append(a)
            raise Exception("must not be called")

        with patch("urllib.request.urlopen", side_effect=_record):
            mod = _load_module()
            result = mod.main()

        assert result == 0
        assert called == [], "urlopen MUST NOT be called for entry without sha256"
        assert not (data_dir / "x.bin").exists()
