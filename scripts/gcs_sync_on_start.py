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
import sys
import uuid
import urllib.request
import urllib.error

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


# ---------------------------------------------------------------------------
# OSS / Self-Host path: GitHub Release fallback
# ---------------------------------------------------------------------------

# URL allow-list. urlopen() accepts file://, ftp://, data:, etc. by default —
# a malicious manifest with file:///etc/passwd or a redirect to a tarpit would
# otherwise be honoured. Lock the dispatcher to GitHub Release Asset hosts.
_ALLOWED_URL_PREFIXES = (
    "https://github.com/",
    "https://objects.githubusercontent.com/",
)

# Size cap: refuse downloads larger than (claimed size_bytes + slack) to defuse
# OOM-on-boot via hostile mirror returning gigabytes.
_DOWNLOAD_SIZE_SLACK_BYTES = 1 * 1024 * 1024  # 1 MiB

# Hard ceiling for any single asset, even if size_bytes is unset/insane in the
# manifest. The current LSTM (11.8 MB) and RL agent (9.8 MB) sit comfortably
# under 64 MiB. Tune up if a future asset grows past this.
_DOWNLOAD_HARD_MAX_BYTES = 64 * 1024 * 1024


class _NoRedirectOpener(urllib.request.HTTPRedirectHandler):
    """Block all HTTP redirects from the URL opener.

    ``urllib.request.urlopen()`` follows 301/302/307/308 redirects without
    re-validating the *final* URL against ``_ALLOWED_URL_PREFIXES``. A crafted
    manifest could therefore host a GitHub-prefixed URL that redirects to an
    internal endpoint (AWS IMDS, Cloud Run metadata, localhost admin) and
    bypass the allow-list entirely.

    This handler raises ``URLError`` on the *first* redirect, so the validated
    URL is always the URL that is actually fetched.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise urllib.error.URLError(
            f"Security policy: HTTP redirect to {newurl!r} disallowed. "
            "Manifest URLs must be direct asset links without redirects."
        )


# Module-level opener singleton — avoids re-creating the opener on every download.
_no_redirect_opener = urllib.request.build_opener(_NoRedirectOpener())


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
    raw_bucket = os.environ.get("GCS_DATA_BUCKET", "").strip()
    data_dir = os.environ.get("DATA_DIR", "data").strip()

    if raw_bucket:
        _sync_from_gcs(raw_bucket, data_dir)
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
