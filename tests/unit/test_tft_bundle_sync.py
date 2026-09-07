# scripts/gcs_sync_on_start — TFT serving-bundle download (model-provenance Issue 3, OSS)
# Dormant unless TFT_BUNDLE_URL set; idempotent; non-blocking; allow-listed host; optional
# whole-tar SHA-256; SAFE extraction (path-traversal / symlink members rejected).

import hashlib
import io
import sys
import tarfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import gcs_sync_on_start as g  # noqa: E402

# E2: the default TFT_BUNDLE_ALLOWED_PREFIX is the exact autonomous_ release path,
# so the test URL must live under it (a bare github.com URL is now refused).
_URL = (
    "https://github.com/Autonomous-Asset-Management-Agents/autonomous_/"
    "releases/download/tft-v1/tft-serving-models.tar.gz"
)
_OFF_REPO_URL = "https://github.com/org/repo/releases/download/tft-v1/bundle.tar.gz"


def _make_tar(members):
    """members: list of (arcname, bytes) → returns the .tar.gz bytes."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for arcname, data in members:
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _bundle(with_manifest=True):
    # E1: a valid bundle carries tft_models_manifest.json at the tree root.
    members = [
        ("AAPL/checkpoint.pt", b"CK"),
        ("AAPL/training_ds.pkl", b"DS"),
        ("AAPL/metadata.json", b"{}"),
    ]
    if with_manifest:
        members.append(("tft_models_manifest.json", b'{"models": []}'))
    return _make_tar(members)


def _install_download(monkeypatch, tar_bytes, spy=None):
    def _dl(url, dest, max_bytes):
        if spy is not None:
            spy(url, dest, max_bytes)
        with open(dest, "wb") as f:
            f.write(tar_bytes)
        return True

    monkeypatch.setattr(g, "_stream_download_capped", _dl)


def _root(tmp_path, monkeypatch):
    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    monkeypatch.delenv("TFT_BUNDLE_SHA256", raising=False)
    return tmp_path


def test_dormant_without_url(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.delenv("TFT_BUNDLE_URL", raising=False)
    spy = MagicMock()
    _install_download(monkeypatch, _bundle(), spy)
    g._sync_tft_bundle()
    spy.assert_not_called()  # dormant: no download attempted
    assert not (tmp_path / "AAPL").exists()


def test_happy_path_extracts(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setenv("TFT_BUNDLE_URL", _URL)
    _install_download(monkeypatch, _bundle())
    g._sync_tft_bundle()
    assert (tmp_path / "AAPL" / "checkpoint.pt").read_bytes() == b"CK"
    assert (tmp_path / "AAPL" / "training_ds.pkl").exists()
    assert not (tmp_path / ".tft_bundle.partial").exists()  # temp cleaned up


def test_idempotent_skips_when_populated(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    (tmp_path / "AAPL").mkdir()
    (tmp_path / "AAPL" / "checkpoint.pt").write_bytes(b"existing")
    monkeypatch.setenv("TFT_BUNDLE_URL", _URL)
    spy = MagicMock()
    _install_download(monkeypatch, _bundle(), spy)
    g._sync_tft_bundle()
    spy.assert_not_called()  # already provisioned → no re-download
    assert (tmp_path / "AAPL" / "checkpoint.pt").read_bytes() == b"existing"


def test_sha_mismatch_refuses_extract(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setenv("TFT_BUNDLE_URL", _URL)
    monkeypatch.setenv("TFT_BUNDLE_SHA256", "deadbeef" * 8)
    _install_download(monkeypatch, _bundle())
    g._sync_tft_bundle()
    assert not (tmp_path / "AAPL").exists()  # tampered tar never extracted


def test_sha_match_extracts(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    tar = _bundle()
    monkeypatch.setenv("TFT_BUNDLE_URL", _URL)
    monkeypatch.setenv("TFT_BUNDLE_SHA256", hashlib.sha256(tar).hexdigest())
    _install_download(monkeypatch, tar)
    g._sync_tft_bundle()
    assert (tmp_path / "AAPL" / "checkpoint.pt").exists()


def test_url_not_allowlisted_skips(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setenv("TFT_BUNDLE_URL", "http://evil.example.com/x.tar.gz")
    spy = MagicMock()
    _install_download(monkeypatch, _bundle(), spy)
    g._sync_tft_bundle()
    spy.assert_not_called()
    assert not (tmp_path / "AAPL").exists()


def test_path_traversal_member_rejected(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setenv("TFT_BUNDLE_URL", _URL)
    evil = _make_tar([("../evil.txt", b"PWNED"), ("AAPL/checkpoint.pt", b"CK")])
    _install_download(monkeypatch, evil)
    g._sync_tft_bundle()  # _safe_extract raises → caught → non-fatal
    assert not (tmp_path.parent / "evil.txt").exists()  # never escaped the root
    # the whole extraction is aborted on the bad member → AAPL not written either
    assert not (tmp_path / "AAPL" / "checkpoint.pt").exists()


def test_symlink_member_rejected(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setenv("TFT_BUNDLE_URL", _URL)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        link = tarfile.TarInfo(name="AAPL/evil_link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    _install_download(monkeypatch, buf.getvalue())
    g._sync_tft_bundle()
    assert not (tmp_path / "AAPL" / "evil_link").exists()


def test_absolute_path_member_rejected(tmp_path):
    # _safe_extract_tft_tar must reject an ABSOLUTE-path member before any extraction.
    tar_path = tmp_path / "evil.tar.gz"
    tar_path.write_bytes(
        _make_tar([("/etc/pwned.txt", b"PWN"), ("AAPL/checkpoint.pt", b"CK")])
    )
    dest = tmp_path / "models"
    dest.mkdir()
    with pytest.raises(ValueError):
        g._safe_extract_tft_tar(str(tar_path), str(dest))
    assert not (dest / "AAPL" / "checkpoint.pt").exists()  # aborted → nothing extracted


# ---------------------------------------------------------------------------
# E (provenance hardening) — TDD Red first.
# E1: a bundle WITHOUT tft_models_manifest.json is refused (unverified tree
#     would otherwise be torch.load()ed under DEPLOYMENT_MODE=LOCAL).
# E2: TFT_BUNDLE_URL must live under the exact autonomous_ release prefix,
#     not just any github.com host.
# ---------------------------------------------------------------------------
def test_bundle_without_manifest_is_refused(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setenv("TFT_BUNDLE_URL", _URL)
    _install_download(monkeypatch, _bundle(with_manifest=False))
    g._sync_tft_bundle()
    # the unverified tree must NOT be promoted into the models root
    assert not (tmp_path / "AAPL" / "checkpoint.pt").exists()
    assert not (tmp_path / ".tft_staging").exists()  # staging discarded


def test_bundle_with_manifest_promotes(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setenv("TFT_BUNDLE_URL", _URL)
    _install_download(monkeypatch, _bundle(with_manifest=True))
    g._sync_tft_bundle()
    assert (tmp_path / "AAPL" / "checkpoint.pt").read_bytes() == b"CK"
    assert (tmp_path / "tft_models_manifest.json").exists()
    assert not (tmp_path / ".tft_staging").exists()  # staging cleaned
    assert not (tmp_path / ".tft_bundle.partial").exists()


def test_url_off_repo_prefix_skipped(tmp_path, monkeypatch):
    # github.com host but NOT the autonomous_ release prefix → refused (E2).
    _root(tmp_path, monkeypatch)
    monkeypatch.setenv("TFT_BUNDLE_URL", _OFF_REPO_URL)
    spy = MagicMock()
    _install_download(monkeypatch, _bundle(), spy)
    g._sync_tft_bundle()
    spy.assert_not_called()
    assert not (tmp_path / "AAPL").exists()


def test_custom_allowed_prefix_env(tmp_path, monkeypatch):
    # Operator can widen/override the release prefix via env.
    _root(tmp_path, monkeypatch)
    monkeypatch.setenv("TFT_BUNDLE_URL", _OFF_REPO_URL)
    monkeypatch.setenv(
        "TFT_BUNDLE_ALLOWED_PREFIX",
        "https://github.com/org/repo/releases/download/",
    )
    _install_download(monkeypatch, _bundle())
    g._sync_tft_bundle()
    assert (tmp_path / "AAPL" / "checkpoint.pt").read_bytes() == b"CK"
