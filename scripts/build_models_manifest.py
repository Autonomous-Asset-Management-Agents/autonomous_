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

# Files that the OSS bundle expects, in the order they appear in the manifest.
KNOWN_FILES = (
    "lstm_model_v2.pth",
    "scaler_x_v2.pkl",
    "scaler_y_v2.pkl",
    "model_metadata_v2.json",
    "rl_agent_v5.zip",
    "rl_stats_v5.pkl",
)


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
        entries.append(
            {
                "filename": fname,
                "url": f"{base_url}/{fname}",
                "sha256": _sha256(local),
                "size_bytes": os.path.getsize(local),
            }
        )
    return {
        "release_tag": release_tag,
        "release_url": f"https://github.com/{repo}/releases/tag/{release_tag}",
        "schema_version": 1,
        "models": entries,
    }


def _verify_manifest(manifest_path: str) -> int:
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    bad = 0
    for entry in manifest.get("models", []):
        url = entry["url"]
        expected = entry["sha256"].lower()
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:  # nosec
                payload = resp.read()
        except Exception as exc:
            print(f"FAIL {entry['filename']}: download error: {exc}", file=sys.stderr)
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
        default="Autonomous-Asset-Management-Agents/aaagents-oss",
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
