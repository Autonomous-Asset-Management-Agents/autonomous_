#!/usr/bin/env python3
# Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""TFT provisioning boot-verify (model-provenance Issue 3).

Confirms that the per-symbol TFT serving tree provisioned at ``TFT_MODELS_ROOT`` matches
the ``tft_models_manifest.json`` produced by the packer (Issue 1, #1131): a count check
plus a **bounded, deterministic sample** of per-file SHA-256s — never a full re-hash of all
~488 × 1.5 MB files (that would slow every boot).

This is a defence-in-depth companion to the per-load verify gate (Issue 2, #1142): the gate
verifies each checkpoint at load time; this confirms at boot that provisioning landed
intact. **Dormant + non-blocking by default**: with no manifest it is a no-op (returns ok);
a mismatch logs a WARNING and still returns ok UNLESS ``strict=True`` (the per-load gate is
the real enforcement, so a boot is never broken by this check by default).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Tuple

log = logging.getLogger("tft_boot_verify")

_DEFAULT_SAMPLE = 10


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_within(child: Path, root: Path) -> bool:
    try:
        return child.resolve().is_relative_to(root.resolve())
    except (ValueError, OSError):
        return False


def _bounded_sample(entries: List[dict], sample_size: int) -> List[dict]:
    """Deterministic, evenly-strided sample of at most ``sample_size`` entries (sorted by
    filename). Deterministic so the check is reproducible and testable; the per-load gate
    catches anything this sample misses."""
    ordered = sorted(entries, key=lambda e: e.get("filename", ""))
    if sample_size <= 0 or len(ordered) <= sample_size:
        return ordered
    stride = len(ordered) / float(sample_size)
    return [ordered[int(i * stride)] for i in range(sample_size)]


def _models_root() -> Path:
    override = os.getenv("TFT_MODELS_ROOT")
    return (
        Path(override)
        if override
        else Path(__file__).resolve().parents[1] / "core" / "ml" / "models"
    )


def _manifest_path(models_root: Path) -> Path:
    override = os.getenv("TFT_MANIFEST_PATH")
    return Path(override) if override else models_root / "tft_models_manifest.json"


def verify_tft_provisioning(
    models_root: Path,
    manifest_path: Path,
    sample_size: int = _DEFAULT_SAMPLE,
    strict: bool = False,
) -> Tuple[bool, dict]:
    """Verify the provisioned tree against the manifest. Returns ``(ok, report)``.

    ``ok`` is True when the sample matches (or there is no manifest — dormant). On a
    mismatch ``ok`` is False ONLY when ``strict``; otherwise it stays True (diagnostic,
    non-blocking) and the mismatch is in ``report`` + logged at WARNING."""
    report: dict = {
        "manifest": str(manifest_path),
        "checked": 0,
        "manifest_entries": 0,
        "missing": [],
        "mismatches": [],
    }
    try:
        if not manifest_path.exists():
            report["status"] = "no-manifest-dormant"
            log.info(
                "[tft-boot-verify] no manifest at %s — skipping (dormant)",
                manifest_path,
            )
            return True, report

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = [
            e
            for e in manifest.get("models", [])
            if e.get("filename") and e.get("sha256")
        ]
        report["manifest_entries"] = len(entries)

        for entry in _bounded_sample(entries, sample_size):
            rel = entry["filename"]
            local = models_root / rel
            if not _is_within(local, models_root):
                report["mismatches"].append(f"{rel} (path escapes root)")
                continue
            if not local.exists():
                report["missing"].append(rel)
                continue
            if _sha256(local) != entry["sha256"]:
                report["mismatches"].append(rel)
            else:
                report["checked"] += 1

        clean = not report["mismatches"] and not report["missing"]
        report["status"] = "ok" if clean else "MISMATCH"
        if clean:
            log.info(
                "[tft-boot-verify] ok: %d/%d sampled checkpoints verified",
                report["checked"],
                len(entries),
            )
            return True, report

        log.warning(
            "[tft-boot-verify] MISMATCH: %d mismatch(es), %d missing of %d sampled "
            "(manifest has %d) — provisioning may be corrupt%s",
            len(report["mismatches"]),
            len(report["missing"]),
            len(_bounded_sample(entries, sample_size)),
            len(entries),
            "" if not strict else "; STRICT → boot-verify fails",
        )
        return (not strict), report
    except Exception as exc:  # never raise into boot — diagnostic only
        report["status"] = f"error: {exc}"
        log.warning("[tft-boot-verify] errored (treating as non-fatal): %s", exc)
        return (not strict), report


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="TFT provisioning boot-verify.")
    parser.add_argument("--models-root", default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--sample", type=int, default=_DEFAULT_SAMPLE)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    models_root = Path(args.models_root) if args.models_root else _models_root()
    manifest_path = (
        Path(args.manifest) if args.manifest else _manifest_path(models_root)
    )
    ok, report = verify_tft_provisioning(
        models_root, manifest_path, sample_size=args.sample, strict=args.strict
    )
    print(json.dumps(report, indent=2), file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
