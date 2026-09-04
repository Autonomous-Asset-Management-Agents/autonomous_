#!/usr/bin/env python3
"""
gcs_sync_on_start.py — Container startup data sync (Python version).

Downloads ML models and persisted data **before the engine starts**.

Two sync sources, dispatched by env var:

* **Production (Cloud Run)** — when ``GCS_DATA_BUCKET`` is set, files are pulled
  from GCS (single source of truth on ephemeral filesystems).
* **OSS / Self-Host** — when ``GCS_DATA_BUCKET`` is unset and
  ``DATA_DIR/models_manifest.json`` exists, files are pulled from a public
  GitHub Release as listed in the manifest, with SHA256 integrity checks.

In both modes failures are non-blocking: the engine still boots, ML voting
agents fall back to neutral 0.5 if their model files are absent.

Called from: Dockerfile CMD (before ``python -m core.engine``)

Environment variables:
    GCS_DATA_BUCKET  — GCS bucket path, e.g. ``gs://aaa-trading-bot-models``.
                       If unset → OSS / GitHub-Release fallback path is tried.
    DATA_DIR         — Local directory to sync into (default: ``data``).
"""

import hashlib
import json
import logging
import os
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.request
import uuid

logging.basicConfig(
    level=logging.INFO,
    format="[gcs_sync] %(message)s",
)
log = logging.getLogger("gcs_sync")


# ---------------------------------------------------------------------------
# Production path: GCS sync (unchanged behaviour)
# ---------------------------------------------------------------------------


def _sync_from_gcs(raw_bucket: str, data_dir: str) -> None:
    """Pull all blobs under <bucket>/data/ into ``data_dir``. Non-blocking.

    Atomic write: download to ``<file>.<uuid8>.part`` then ``os.replace`` to the
    canonical name. A network drop mid-download therefore cannot leave a half
    PyTorch file at the canonical path that ``torch.load()`` would later try to
    deserialise (which crashes with a CUDA/coredump on the engine main loop).
    The UUID-8 suffix (uuid.uuid4().hex[:8]) guarantees uniqueness under
    ``docker-compose up --scale backend=N`` with a shared ``/data/models``
    volume — unlike os.getpid(), which is always 1 for every container replica.
    """
    bucket_name = raw_bucket.replace("gs://", "").rstrip("/")
    prefix = "data/"

    log.info("Syncing %s/%s → ./%s/ ...", raw_bucket, prefix, data_dir)
    os.makedirs(data_dir, exist_ok=True)

    try:
        from google.cloud import storage  # type: ignore[import-untyped]

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=prefix))

        if not blobs:
            log.warning("No blobs found under %s/%s — first start?", raw_bucket, prefix)
            return

        downloaded = 0
        for blob in blobs:
            if blob.name.endswith("/"):
                continue
            relative_name = blob.name[len(prefix) :]
            local_path = os.path.join(data_dir, relative_name)
            os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
            tmp_path = f"{local_path}.{uuid.uuid4().hex[:8]}.part"

            log.info("  ⬇ %s (%s bytes)", relative_name, blob.size)
            # Use download_to_file instead of download_to_filename to avoid
            # os.utime() EPERM on Cloud Run gVisor (Gen 1) filesystem.
            try:
                with open(tmp_path, "wb") as f:
                    blob.download_to_file(f)
                os.replace(tmp_path, local_path)
            except Exception:
                # Clean up partial .part file so it doesn't accumulate across
                # retries; re-raise so the outer except logs and we move on.
                try:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                except Exception:
                    pass
                raise
            downloaded += 1

        log.info(
            "✅ GCS sync complete — %d file(s) downloaded to %s/", downloaded, data_dir
        )

        for fname in sorted(os.listdir(data_dir)):
            fpath = os.path.join(data_dir, fname)
            if os.path.isfile(fpath):
                size_kb = os.path.getsize(fpath) / 1024
                log.info("  📄 %s (%.1f KB)", fname, size_kb)

    except ImportError:
        log.error("google-cloud-storage not installed — cannot sync from GCS!")
        log.error("Install with: pip install google-cloud-storage")
    except Exception as exc:
        log.warning("⚠️ GCS sync failed: %s: %s", type(exc).__name__, exc)
        log.warning("Engine will start without pre-synced data.")


def _sync_tft_from_gcs(raw_bucket: str) -> None:
    """Pull the per-symbol TFT serving tree from ``<bucket>/tft/`` into ``TFT_MODELS_ROOT``.

    The cloud (GCP) pull side of TFT provisioning — counterpart to the OSS
    ``_sync_tft_bundle`` (public-release tar) and the operator ``upload_tft_models_to_gcs.sh``
    (which stages ``gs://…/tft/``). Mirrors :func:`_sync_from_gcs` for the ``tft/`` prefix.

    * **Dormant:** no ``tft/`` blobs in the bucket → no-op (cloud not yet TFT-provisioned).
    * **Idempotent:** a populated tree (``_tree_has_checkpoints``) → skip.
    * **Non-blocking:** any failure → WARN; the engine boots and degrades to LLM/rule.
    * **Path-safety:** the source is a trusted private bucket (no SSRF, no tar-extract), but a
      blob whose resolved path would escape ``TFT_MODELS_ROOT`` is skipped (defence-in-depth).
      ``_is_safe_filename`` is intentionally NOT reused — blob paths legitimately contain ``/``.
    * **Atomic write:** ``<file>.<uuid8>.part`` → ``os.replace`` (no half-checkpoint that
      ``torch.load`` would later crash on), exactly as in ``_sync_from_gcs``.

    **D2 explicit-flag guard (default OFF):** dormancy is gated by configuration, not by
    bucket state. Without ``TFT_GCS_SYNC_ENABLED`` this is a hard no-op — no GCS client, no
    listing, no download. This prevents the OOM footgun: on the 2 GiB Cloud Run instance the
    container filesystem is in-memory, so syncing the ~1.3 GB tree would exhaust the memory
    budget and crash-loop the engine. Self-host / fat instances opt in deliberately; the
    audited cloud profile is a GCS-FUSE read-only mount (separate brick) — not this copy.
    """
    if os.environ.get("TFT_GCS_SYNC_ENABLED", "false").strip().lower() not in (
        "1",
        "true",
        "yes",
    ):
        log.info(
            "[tft-sync] TFT_GCS_SYNC_ENABLED not enabled → skipping GCS tft/ sync "
            "(dormant)"
        )
        return

    root = _tft_models_root()
    if _tree_has_checkpoints(root):
        log.info("[tft-sync] %s already populated — skipping GCS tft/ sync", root)
        return

    bucket_name = raw_bucket.replace("gs://", "").rstrip("/")
    prefix = "tft/"
    root_abs = os.path.abspath(root)

    try:
        from google.cloud import storage  # type: ignore[import-untyped]

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=prefix))
        if not blobs:
            log.info(
                "[tft-sync] no blobs under %s/%s — cloud not TFT-provisioned",
                raw_bucket,
                prefix,
            )
            return

        os.makedirs(root, exist_ok=True)
        downloaded = 0
        for blob in blobs:
            if blob.name.endswith("/"):
                continue  # GCS folder marker
            rel = blob.name[len(prefix) :]
            local_path = os.path.join(root, rel)
            # Path-safety: a crafted blob name (``../…``) must not escape the models root.
            if not os.path.abspath(local_path).startswith(root_abs + os.sep):
                log.warning(
                    "[tft-sync] blob %r escapes models root — skipping", blob.name
                )
                continue
            os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
            tmp_path = f"{local_path}.{uuid.uuid4().hex[:8]}.part"
            try:
                with open(tmp_path, "wb") as f:
                    blob.download_to_file(f)
                os.replace(tmp_path, local_path)
                downloaded += 1
            except Exception as exc:
                # Per-blob non-fatal: clean the partial, WARN, keep syncing the rest.
                try:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                except OSError:
                    pass
                log.warning("[tft-sync] failed to sync %s: %s", rel, exc)

        log.info(
            "[tft-sync] ✅ TFT GCS sync complete — %d file(s) → %s/", downloaded, root
        )

    except ImportError:
        log.error(
            "[tft-sync] google-cloud-storage not installed — cannot sync tft/ from GCS"
        )
    except Exception as exc:
        log.warning(
            "[tft-sync] TFT GCS sync failed (non-fatal): %s: %s",
            type(exc).__name__,
            exc,
        )


# ---------------------------------------------------------------------------
# OSS / Self-Host path: GitHub Release fallback
# ---------------------------------------------------------------------------

# URL allow-list. urlopen() accepts file://, ftp://, data:, etc. by default —
# a malicious manifest with file:///etc/passwd or a redirect to a tarpit would
# otherwise be honoured. Lock the dispatcher to GitHub Release Asset hosts.
#
# `release-assets.githubusercontent.com` is GitHub's CDN host that
# `github.com/.../releases/download/...` URLs redirect to. The legacy
# `objects.githubusercontent.com` entry is preserved for older assets that may
# still resolve through that host. Both are part of GitHub's release-asset
# infrastructure (see https://docs.github.com/en/rest/releases/assets).
_ALLOWED_URL_PREFIXES = (
    "https://github.com/",
    "https://objects.githubusercontent.com/",
    "https://release-assets.githubusercontent.com/",
)

# E2: the host allow-list above matches ANY github.com repo. The TFT bundle must come
# from OUR public release path specifically. Checked IN ADDITION to the host allow-list.
# Operators can widen/override via ``TFT_BUNDLE_ALLOWED_PREFIX`` (empty → host-only).
_DEFAULT_TFT_BUNDLE_PREFIX = (
    "https://github.com/Autonomous-Asset-Management-Agents/autonomous_/"
    "releases/download/"
)

# Size cap: refuse downloads larger than (claimed size_bytes + slack) to defuse
# OOM-on-boot via hostile mirror returning gigabytes.
_DOWNLOAD_SIZE_SLACK_BYTES = 1 * 1024 * 1024  # 1 MiB

# Hard ceiling for any single asset, even if size_bytes is unset/insane in the
# manifest. The current LSTM (11.8 MB) and RL agent (9.8 MB) sit comfortably
# under 64 MiB. Tune up if a future asset grows past this.
_DOWNLOAD_HARD_MAX_BYTES = 64 * 1024 * 1024


class _AllowlistedRedirectOpener(urllib.request.HTTPRedirectHandler):
    """Re-validate every HTTP redirect target against ``_ALLOWED_URL_PREFIXES``.

    ``urllib.request.urlopen()`` follows 301/302/307/308 redirects without
    re-validating the *final* URL. A crafted manifest could therefore host a
    GitHub-prefixed URL that redirects to an internal endpoint (AWS IMDS,
    Cloud Run metadata, localhost admin) and bypass the allow-list entirely.

    This handler intercepts each redirect, checks the target against the
    allow-list, and raises ``URLError`` if the target is outside the allow-list.
    Allow-listed redirects are followed via the parent class.

    Concrete example: GitHub's release-download URLs return ``302 Found`` to
    a signed asset URL on ``release-assets.githubusercontent.com``. Both the
    initial ``github.com/.../releases/download/...`` URL and the redirect
    target are in ``_ALLOWED_URL_PREFIXES``, so the redirect is followed and
    the asset is downloaded. A malicious redirect to ``http://169.254.169.254``
    (IMDS) or ``http://localhost:8080`` (admin) is rejected because neither
    matches the allow-list.

    History: previously implemented as ``_NoRedirectOpener`` which blocked
    *all* redirects unconditionally. That broke GitHub Release downloads
    because GitHub always serves them via a 302 to the CDN. Surfaced by the
    2026-05-07 senior-engineering review (top show-stopper #3 of 3).
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not _is_allowed_url(newurl):
            raise urllib.error.URLError(
                f"Security policy: HTTP redirect target {newurl!r} not in "
                f"allow-list ({_ALLOWED_URL_PREFIXES}). Manifest URL "
                f"redirected from {req.get_full_url()!r}."
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Module-level opener singleton — avoids re-creating the opener on every download.
# Name preserved (`_no_redirect_opener`) for back-compat with existing test
# monkeypatch sites in ``tests/unit/test_gcs_sync_on_start.py``.
_no_redirect_opener = urllib.request.build_opener(_AllowlistedRedirectOpener())


def _is_safe_filename(name: str) -> bool:
    """Reject path-traversal / absolute paths in manifest entries.

    Rejects: empty, contains ``/`` or ``\\``, starts with ``..`` (double-dot
    is the path-traversal sequence; single-dot hidden files like
    ``.hidden_model.pth`` are intentionally allowed).
    Accepts: simple filenames like ``lstm_model_v2.pth`` or ``.oss_meta.json``.
    """
    if not name or not isinstance(name, str):
        return False
    if "/" in name or "\\" in name:
        return False
    if name in ("", ".", "..") or name.startswith(".."):
        return False
    return True


def _is_allowed_url(url: str) -> bool:
    """Reject non-https / non-GitHub URLs (defends against file://, ftp://, etc.)."""
    if not isinstance(url, str):
        return False
    return any(url.startswith(p) for p in _ALLOWED_URL_PREFIXES)


def _read_capped(resp, max_bytes: int) -> "bytes | None":
    """Read at most ``max_bytes`` from ``resp``. Returns None if cap exceeded.

    Read in 64 KiB chunks to avoid buffering the whole stream before the cap
    check. If the server returns one byte more than the cap, abort.
    """
    chunks = []
    remaining = max_bytes + 1  # read one extra byte to detect overflow
    while remaining > 0:
        chunk = resp.read(min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > max_bytes:
        return None
    return payload


# ---------------------------------------------------------------------------
# TFT serving bundle (model-provenance Issue 3 — OSS/self-host provisioning)
# ---------------------------------------------------------------------------
# The ~488 per-symbol TFT checkpoints ship as ONE ~1.3 GB tar release asset (too big for
# the 64 MiB per-file manifest downloader above), so they get a dedicated, DORMANT path:
# download (streamed to disk, capped) → optional SHA-256 of the whole tar → SAFE extract
# into TFT_MODELS_ROOT. Activated only when ``TFT_BUNDLE_URL`` is set; idempotent (skips a
# populated tree); non-blocking (any failure → WARN, the engine boots and degrades to the
# LLM/rule path). The per-load verify gate (#1142) + boot-verify (#1144) check integrity of
# the extracted tree against the manifest.

# 2 GiB ceiling — the serving bundle is ~1.3 GB and a GitHub release asset caps at 2 GB.
_TFT_BUNDLE_HARD_MAX_BYTES = 2 * 1024 * 1024 * 1024

# Total-download deadline (slow-loris guard): the per-read socket timeout alone can't bound
# a server that trickles bytes just under it, so cap the whole transfer. 10 min is ample for
# a 1.3 GB asset off a GitHub CDN.
_TFT_DOWNLOAD_MAX_SECONDS = 600


def _tft_models_root() -> str:
    """``TFT_MODELS_ROOT`` if set, else the module-relative ``core/ml/models`` (matches
    ``model_registry._models_root``)."""
    override = os.environ.get("TFT_MODELS_ROOT", "").strip()
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "core", "ml", "models")


def _tree_has_checkpoints(root: str) -> bool:
    """True if any ``<root>/<SYM>/checkpoint.pt`` exists (idempotency guard)."""
    try:
        for name in os.listdir(root):
            if os.path.isfile(os.path.join(root, name, "checkpoint.pt")):
                return True
    except OSError:
        return False
    return False


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _stream_download_capped(url: str, dest: str, max_bytes: int) -> bool:
    """Stream ``url`` to ``dest`` (1 MiB chunks), aborting if it exceeds ``max_bytes`` —
    never buffers the whole 1.3 GB bundle in memory. Uses the allow-listed redirect opener.
    The socket ``timeout`` only bounds each individual read, so a slow-loris server that
    trickles 1 byte just under the timeout could hang boot for hours; a **total-download
    deadline** caps the whole transfer. Returns True on success, False on cap / deadline /
    network error."""
    written = 0
    deadline = time.monotonic() + _TFT_DOWNLOAD_MAX_SECONDS
    try:
        with _no_redirect_opener.open(url, timeout=60) as resp, open(dest, "wb") as out:
            while True:
                if time.monotonic() > deadline:
                    log.warning(
                        "[tft-sync] download exceeded %ds total deadline — aborting "
                        "(slow server?)",
                        _TFT_DOWNLOAD_MAX_SECONDS,
                    )
                    return False
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    log.warning(
                        "[tft-sync] download exceeded cap %d bytes — aborting",
                        max_bytes,
                    )
                    return False
                out.write(chunk)
        return True
    except Exception as exc:
        log.warning("[tft-sync] bundle download failed: %s", exc)
        return False


def _safe_extract_tft_tar(tar_path: str, dest: str) -> None:
    """Extract a downloaded tar into ``dest``, rejecting any member that escapes ``dest``
    or is a link/device (classic tarfile path-traversal / symlink attack — the bundle is an
    untrusted download). Members are validated BEFORE extraction; the 3.12+ ``data`` filter
    is defence-in-depth."""
    dest_abs = os.path.abspath(dest)
    with tarfile.open(tar_path, "r:*") as tar:
        for member in tar.getmembers():
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError(f"unsafe tar member (link/device): {member.name!r}")
            target = os.path.abspath(os.path.join(dest_abs, member.name))
            if target != dest_abs and not target.startswith(dest_abs + os.sep):
                raise ValueError(f"tar member escapes dest: {member.name!r}")
        try:
            tar.extractall(
                dest_abs, filter="data"
            )  # nosec B202 — members pre-validated
        except TypeError:  # py < 3.12 has no filter kwarg
            tar.extractall(dest_abs)  # nosec B202 — members pre-validated above


def _sync_tft_bundle() -> None:
    """DORMANT unless ``TFT_BUNDLE_URL`` is set. Download + SHA-verify + safely extract the
    public TFT serving bundle into ``TFT_MODELS_ROOT``. Idempotent + non-blocking."""
    url = os.environ.get("TFT_BUNDLE_URL", "").strip()
    if not url:
        return  # dormant — no TFT serving bundle configured
    root = _tft_models_root()
    if _tree_has_checkpoints(root):
        log.info("[tft-sync] %s already populated — skipping bundle download", root)
        return
    if not _is_allowed_url(url):
        log.warning(
            "[tft-sync] TFT_BUNDLE_URL not in host allow-list — skipping: %s", url
        )
        return
    # E2: pin to OUR exact release path (not just any github.com repo).
    allowed_prefix = os.environ.get(
        "TFT_BUNDLE_ALLOWED_PREFIX", _DEFAULT_TFT_BUNDLE_PREFIX
    ).strip()
    if allowed_prefix and not url.startswith(allowed_prefix):
        log.warning(
            "[tft-sync] TFT_BUNDLE_URL not under the allowed release prefix %s — "
            "skipping: %s",
            allowed_prefix,
            url,
        )
        return

    tmp = os.path.join(root, ".tft_bundle.partial")
    # E1: extract into a STAGING dir, require the provenance manifest, and only then
    # promote into the models root. A bundle without tft_models_manifest.json is
    # refused — otherwise it would load UNVERIFIED via torch.load() under
    # DEPLOYMENT_MODE=LOCAL (the per-load gate refuses it by default). RCE vector closed.
    staging = os.path.join(root, ".tft_staging")
    try:
        os.makedirs(root, exist_ok=True)
        shutil.rmtree(staging, ignore_errors=True)  # clear any aborted prior run
        if not _stream_download_capped(url, tmp, _TFT_BUNDLE_HARD_MAX_BYTES):
            return
        expected = os.environ.get("TFT_BUNDLE_SHA256", "").strip().lower()
        if expected and _sha256_file(tmp) != expected:
            log.warning(
                "[tft-sync] bundle SHA-256 mismatch — refusing to extract (RF-3)"
            )
            return
        _safe_extract_tft_tar(tmp, staging)
        if not os.path.exists(os.path.join(staging, "tft_models_manifest.json")):
            log.warning(
                "[tft-sync] extracted bundle has no tft_models_manifest.json — "
                "refusing unverified tree (E1)"
            )
            return
        # Promote the validated tree into the root, manifest LAST: if an os.replace
        # is interrupted mid-loop, the partially-promoted tree is left WITHOUT the
        # manifest → fail-closed (the per-load gate refuses it outside LOCAL).
        _manifest = "tft_models_manifest.json"
        names = [n for n in os.listdir(staging) if n != _manifest] + [_manifest]
        for name in names:
            dst = os.path.join(root, name)
            if os.path.isdir(dst):
                shutil.rmtree(dst, ignore_errors=True)
            elif os.path.exists(dst):
                os.remove(dst)
            os.replace(os.path.join(staging, name), dst)
        log.info("[tft-sync] ✅ TFT serving bundle extracted to %s", root)
    except Exception as exc:
        log.warning("[tft-sync] TFT bundle provisioning failed (non-fatal): %s", exc)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        shutil.rmtree(staging, ignore_errors=True)


def _pull_from_github_release(manifest_path: str, data_dir: str) -> int:
    """Download files listed in ``manifest_path`` into ``data_dir``.

    Manifest schema::

        {
          "release_tag": "models-v1.0",
          "models": [
            {"filename": "lstm_model_v2.pth",
             "url": "https://github.com/<org>/<repo>/releases/download/models-v1.0/lstm_model_v2.pth",
             "sha256": "<hex>",
             "size_bytes": 12345}
          ]
        }

    Security guards (each violation logs WARN and skips the entry):

    - ``filename`` must be a simple filename (no ``/``, ``\\``, ``..``)
    - ``url`` must start with one of ``_ALLOWED_URL_PREFIXES`` (GitHub only)
    - download is capped at ``size_bytes + 1 MiB`` slack, hard-ceiling 64 MiB
    - SHA256 must match the manifest entry; mismatched files are NOT written

    Per-file failures (network, SHA mismatch, size cap, filename, url scheme)
    WARN but do not raise — the function always returns the count of
    successfully written files. The engine boot must never be blocked.
    """
    log.info("OSS path: reading manifest %s", manifest_path)
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as exc:
        log.warning(
            "⚠️ Could not read manifest %s: %s: %s",
            manifest_path,
            type(exc).__name__,
            exc,
        )
        return 0

    entries = manifest.get("models", [])
    if not isinstance(entries, list):
        # Defensive: a malformed manifest with `models: {}` (dict) or
        # `models: "x"` (string) would otherwise be iterated, producing
        # AttributeError on entry.get(...) and crashing the boot script.
        log.warning(
            "Manifest %s 'models' field is %s, expected list — skipping.",
            manifest_path,
            type(entries).__name__,
        )
        return 0
    if not entries:
        log.warning("Manifest %s contains no 'models' entries.", manifest_path)
        return 0

    os.makedirs(data_dir, exist_ok=True)
    log.info(
        "Pulling %d file(s) from GitHub Release '%s' → ./%s/ ...",
        len(entries),
        manifest.get("release_tag", "<unknown>"),
        data_dir,
    )

    written = 0
    for entry in entries:
        if not isinstance(entry, dict):
            log.warning(
                "⚠️ Skipping non-dict manifest entry (got %s): %r",
                type(entry).__name__,
                entry,
            )
            continue
        # Coerce to str defensively — a manifest with sha256: 123 (int) would
        # otherwise crash on `.lower()`, and url: 42 would crash str-prefix
        # checks. str-coercion + isinstance guards keep the boot path total.
        filename_raw = entry.get("filename")
        url_raw = entry.get("url")
        sha_raw = entry.get("sha256")
        filename = filename_raw if isinstance(filename_raw, str) else None
        url = url_raw if isinstance(url_raw, str) else None
        expected_sha = sha_raw.lower() if isinstance(sha_raw, str) else ""
        if not (filename and url and expected_sha):
            log.warning(
                "⚠️ Skipping malformed manifest entry (need str filename/url/sha256): %r",
                entry,
            )
            continue

        if not _is_safe_filename(filename):
            log.warning(
                "⚠️ Refusing unsafe filename in manifest: %r — entry skipped",
                filename,
            )
            continue

        if not _is_allowed_url(url):
            log.warning(
                "⚠️ Refusing URL outside GitHub allow-list: %r — entry skipped",
                url,
            )
            continue

        # Compute size cap: claimed size + slack, but never above hard ceiling.
        try:
            claimed = int(entry.get("size_bytes") or 0)
        except (TypeError, ValueError):
            claimed = 0
        cap = min(claimed + _DOWNLOAD_SIZE_SLACK_BYTES, _DOWNLOAD_HARD_MAX_BYTES)
        if claimed <= 0:
            cap = _DOWNLOAD_HARD_MAX_BYTES

        local_path = os.path.join(data_dir, filename)

        try:
            log.info("  ⬇ %s ← %s", filename, url)
            with _no_redirect_opener.open(url, timeout=60) as resp:  # nosec: B310
                payload = _read_capped(resp, cap)
        except Exception as exc:
            log.warning(
                "⚠️ Download failed for %s: %s: %s — file skipped",
                filename,
                type(exc).__name__,
                exc,
            )
            continue

        if payload is None:
            log.warning(
                "⚠️ Download for %s exceeded size cap (%d bytes) — file skipped",
                filename,
                cap,
            )
            continue

        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != expected_sha:
            log.warning(
                "⚠️ SHA256 mismatch for %s (expected %s, got %s) — file NOT written",
                filename,
                expected_sha,
                actual_sha,
            )
            continue

        # Atomic write: tmpfile + rename, so partial downloads can't poison data/.
        # UUID suffix guarantees uniqueness when multiple replicas share the same
        # bind-mount volume (e.g. ``docker-compose up --scale backend=3``).
        # PID cannot be used here: Docker PID-namespace isolation means every
        # container entrypoint gets PID 1, so all replicas would collide on the
        # same `<file>.1.part` name and os.replace would publish corrupted data.
        # Note: os.replace is atomic only within the same filesystem. On Docker
        # bind mounts crossing fs boundaries it falls back to copy+unlink — still
        # safe (the target is replaced or unchanged) but not strictly atomic.
        tmp_path = f"{local_path}.{uuid.uuid4().hex[:8]}.part"
        try:
            with open(tmp_path, "wb") as f:
                f.write(payload)
            os.replace(tmp_path, local_path)
            written += 1
            size_kb = len(payload) / 1024
            log.info("  ✅ %s (%.1f KB, sha verified)", filename, size_kb)
        except Exception as exc:
            log.warning(
                "⚠️ Could not write %s: %s: %s",
                filename,
                type(exc).__name__,
                exc,
            )
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except Exception:
                pass

    log.info(
        "✅ OSS sync complete — %d/%d file(s) written to %s/",
        written,
        len(entries),
        data_dir,
    )
    return written


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def sync() -> None:
    """Pick sync source by env, run it. Always non-blocking."""
    # TFT serving bundle (Issue 3) — dormant unless TFT_BUNDLE_URL is set. Runs on every
    # deployment (OSS download here; cloud provisions the per-symbol tree via GCS upstream).
    _sync_tft_bundle()

    raw_bucket = os.environ.get("GCS_DATA_BUCKET", "").strip()
    data_dir = os.environ.get("DATA_DIR", "data").strip()

    if raw_bucket:
        _sync_from_gcs(raw_bucket, data_dir)
        # Cloud pull of the per-symbol TFT serving tree (gs://…/tft/ → TFT_MODELS_ROOT).
        # Dormant: no tft/ prefix in the bucket → no-op. Non-blocking.
        _sync_tft_from_gcs(raw_bucket)
        return

    # OSS / self-host path: try GitHub Release manifest if present.
    manifest_path = os.path.join(data_dir, "models_manifest.json")
    if os.path.isfile(manifest_path):
        _pull_from_github_release(manifest_path, data_dir)
        return

    log.info(
        "GCS_DATA_BUCKET not set and no models_manifest.json — "
        "skipping sync (local dev mode)."
    )


def main() -> int:
    """Entry point: runs sync() and always returns 0 (engine startup must never be blocked).

    Top-level catch-all: any unhandled exception from the dispatcher (incl.
    ones the per-path try/except missed) is logged but never propagates. The
    Dockerfile CMD chains this script with ``&& python -m core.engine``;
    a non-zero exit here would prevent the engine from booting at all, which
    is strictly worse than running with stale or missing model files.
    """
    try:
        sync()
    except Exception as exc:
        log.error(
            "CRITICAL: Unhandled exception in sync dispatcher: %s: %s",
            type(exc).__name__,
            exc,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
