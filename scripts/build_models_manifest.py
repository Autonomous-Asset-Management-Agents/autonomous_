#!/usr/bin/env python3
"""
build_models_manifest.py — Maintain ``data/models_manifest.json``.

Two modes:

1. ``--from-dir <DIR>`` (default ``data/``)
   Walks the directory, computes SHA256 + size for each known model file,
   and emits a manifest stub to stdout (or to ``--output`` if given).
   Use this **before** uploading to a GitHub Release to lock the SHAs that
   the release assets MUST match.

2. ``--verify <PATH>`` (manifest file)
   Re-downloads each manifest URL and verifies the SHA. Useful as a CI gate
   after the release is created, to catch upload corruption.

Used by operator at deploy time. Not called at runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from typing import Iterable

# ---------------------------------------------------------------------------
# Size-cap constants (mirrors gcs_sync_on_start.py — operator tool uses the
# same guards to prevent OOM when a hostile mirror returns gigabytes).
# ---------------------------------------------------------------------------
_DOWNLOAD_SIZE_SLACK_BYTES = 1 * 1024 * 1024  # 1 MiB
_DOWNLOAD_HARD_MAX_BYTES = 64 * 1024 * 1024  # 64 MiB hard ceiling


def _read_capped(resp, max_bytes: int) -> "bytes | None":
    """Read at most ``max_bytes`` from an open HTTP response.

    Returns None if the server sends more than ``max_bytes`` (overflow signal).
    Reads in 64 KiB chunks so the whole stream is never buffered before the
    cap check. Deliberately reads one extra byte to detect exact-boundary
    overflow.
    """
    chunks = []
    remaining = max_bytes + 1  # one extra byte to detect overflow
    while remaining > 0:
        chunk = resp.read(min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    return None if len(payload) > max_bytes else payload


# Files that the OSS bundle expects, in the order they appear in the manifest.
KNOWN_FILES = (
    "lstm_model_v2.pth",
    "scaler_x_v2.pkl",
    "scaler_y_v2.pkl",
    "model_metadata_v2.json",
    "rl_agent_v5.zip",
    "rl_stats_v5.pkl",
)

# Human-readable purpose annotations preserved across manifest rebuilds.
# If a new file is added to KNOWN_FILES, add its purpose here to avoid silent
# data loss when re-running --from-dir.
KNOWN_FILE_PURPOSES: dict[str, str] = {
    "lstm_model_v2.pth": (
        "LSTM 5-day return predictor; consumed by LSTMSignalAgent (w=0.40) "
        "via active strategy in AgentRegistry"
    ),
    "scaler_x_v2.pkl": (
        "StandardScaler for the 34 input features "
        "(matches model_metadata_v2.json features_list)"
    ),
    "scaler_y_v2.pkl": "StandardScaler for the LSTM target (5-day return)",
    "model_metadata_v2.json": (
        "Feature list + LSTM hyper-parameters "
        "(input_dim=34, hidden_dim=128, num_layers=3, sequence_length=60)"
    ),
    "rl_agent_v5.zip": (
        "RecurrentPPO RL agent (sb3-contrib); consumed by RLConfidenceAgent (w=0.40) "
        "via active strategy"
    ),
    "rl_stats_v5.pkl": (
        "VecNormalize observation stats matching the RL training environment"
    ),
}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_manifest(
    src_dir: str, release_tag: str, repo: str, files: Iterable[str]
) -> dict:
    base_url = f"https://github.com/{repo}/releases/download/{release_tag}"
    entries = []
    for fname in files:
        local = os.path.join(src_dir, fname)
        if not os.path.isfile(local):
            print(f"warn: {local} missing — skipping", file=sys.stderr)
            continue
        entry: dict = {
            "filename": fname,
            "url": f"{base_url}/{fname}",
            "sha256": _sha256(local),
            "size_bytes": os.path.getsize(local),
        }
        # Preserve purpose annotations so --from-dir rebuilds don't silently
        # discard documentation baked into the committed manifest.
        purpose = KNOWN_FILE_PURPOSES.get(fname, "")
        if purpose:
            entry["purpose"] = purpose
        entries.append(entry)
    return {
        "release_tag": release_tag,
        "release_url": f"https://github.com/{repo}/releases/tag/{release_tag}",
        "schema_version": 1,
        "models": entries,
    }


def _verify_manifest(manifest_path: str) -> int:
    """Re-download each manifest URL and verify SHA256. Operator tool, not runtime."""
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    bad = 0
    for entry in manifest.get("models", []):
        url = entry["url"]
        expected = entry["sha256"].lower()
        claimed_size = int(entry.get("size_bytes") or 0)
        # Cap: same logic as the runtime downloader — prevents OOM when used
        # as a CI gate and a hostile mirror returns gigabytes.
        cap = min(
            (
                claimed_size + _DOWNLOAD_SIZE_SLACK_BYTES
                if claimed_size > 0
                else _DOWNLOAD_HARD_MAX_BYTES
            ),
            _DOWNLOAD_HARD_MAX_BYTES,
        )
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:  # nosec
                payload = _read_capped(resp, cap)
        except Exception as exc:
            print(f"FAIL {entry['filename']}: download error: {exc}", file=sys.stderr)
            bad += 1
            continue
        if payload is None:
            print(
                f"FAIL {entry['filename']}: exceeded size cap ({cap} bytes)",
                file=sys.stderr,
            )
            bad += 1
            continue
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected:
            print(
                f"FAIL {entry['filename']}: sha mismatch "
                f"(expected {expected}, got {actual})",
                file=sys.stderr,
            )
            bad += 1
        else:
            print(f"ok   {entry['filename']}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--from-dir",
        help="Build manifest from a directory of pre-trained models",
    )
    ap.add_argument(
        "--verify",
        help="Path to manifest JSON to verify against the live release",
    )
    ap.add_argument(
        "--release-tag",
        default="models-v1.0",
        help="Release tag (default: models-v1.0)",
    )
    ap.add_argument(
        "--repo",
        default="Autonomous-Asset-Management-Agents/Dev-Enviroment",
        help="GitHub repo (default: %(default)s)",
    )
    ap.add_argument(
        "--output",
        help="Write manifest to this path (otherwise: stdout)",
    )
    args = ap.parse_args()

    if args.verify:
        return _verify_manifest(args.verify)

    if args.from_dir:
        manifest = _build_manifest(
            args.from_dir, args.release_tag, args.repo, KNOWN_FILES
        )
        text = json.dumps(manifest, indent=2) + "\n"
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"wrote {args.output}", file=sys.stderr)
        else:
            sys.stdout.write(text)
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
