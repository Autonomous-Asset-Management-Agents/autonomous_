#!/usr/bin/env python3
# Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""build_tft_manifest.py — per-symbol TFT checkpoint SHA-256 manifest.

Model-Provenance Issue 1 (fusion). The flat ``build_models_manifest.py`` covers the
6+6 ``KNOWN_FILES`` LSTM/RL artifacts; the ~488 per-symbol TFT checkpoints need a
**per-symbol** manifest. This emits ``data/tft_models_manifest.json`` with a SHA-256 +
size for the two **executable** artifacts of each symbol:

- ``<SYM>/checkpoint.pt``                  (loaded via ``torch.load(weights_only=False)``)
- ``<SYM>/training_ds_*.pkl`` (matched)    (loaded via ``pickle.load``)

**W-4:** ``training_ds`` is unpickled at load time → the same arbitrary-code-execution
risk as ``torch.load(weights_only=False)``, so it is SHA-verified too (not optional).
``metadata.json`` is tiny / non-executable and is intentionally not security-gated.

The matched training_ds is resolved via the canonical ADR-ML-DS-01 logic
(``TFTInferenceEngine._resolve_training_ds_path``) so the manifest never drifts from
what the runtime actually loads.

Operator tool — not called at runtime. The runtime verify-before-load gate (Issue 2)
consumes this manifest inside ``model_registry`` once #1129 is on ``main``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Reuse the exact streaming SHA-256 of the existing manifest builder (DRY) and the
# shared matched-training_ds resolver (so packer + builder never drift).
from scripts._tft_provenance import matched_training_ds
from scripts.build_models_manifest import _sha256

_DEFAULT_RELEASE_TAG = "tft-models-v1"
_DEFAULT_REPO = "Autonomous-Asset-Management-Agents/Dev-Enviroment"


def build_tft_manifest(
    tree: Path,
    release_tag: str = _DEFAULT_RELEASE_TAG,
    repo: str = _DEFAULT_REPO,
) -> dict:
    """Walk a per-symbol tree (``<SYM>/checkpoint.pt`` …) and emit the manifest dict.

    Symbols without a ``checkpoint.pt`` are skipped (they are not servable). For each
    servable symbol the manifest carries the checkpoint and the matched training_ds.
    """
    tree = Path(tree)
    base_url = f"https://github.com/{repo}/releases/download/{release_tag}"
    models = []
    incomplete = []
    for sym_dir in sorted(p for p in tree.iterdir() if p.is_dir()):
        checkpoint = sym_dir / "checkpoint.pt"
        if not checkpoint.exists():
            continue
        symbol = sym_dir.name
        matched_ds = matched_training_ds(sym_dir)
        if matched_ds is None:
            # W-4: a servable checkpoint REQUIRES a verifiable training_ds (pickle.load
            # = RCE risk). Without one the symbol is not servable — refuse a partial
            # checkpoint-only entry (which the Issue-2 gate would trust as complete) and
            # record it so the operator fails loudly instead of publishing a hole.
            incomplete.append(symbol)
            continue
        for artifact in (checkpoint, matched_ds):
            rel = f"{symbol}/{artifact.name}"
            models.append(
                {
                    "symbol": symbol,
                    "filename": rel,
                    "url": f"{base_url}/{rel}",
                    "sha256": _sha256(str(artifact)),
                    "size_bytes": artifact.stat().st_size,
                }
            )
    return {
        "release_tag": release_tag,
        "release_url": f"https://github.com/{repo}/releases/tag/{release_tag}",
        "schema_version": 1,
        "kind": "tft-per-symbol",
        "models": models,
        "incomplete": sorted(incomplete),
    }


def verify_tft_manifest(manifest: dict, tree: Path) -> int:
    """Re-hash each manifest entry against the local ``tree`` (operator/CI gate).

    Returns 0 if every file is present and matches, else 1. Used after staging or after
    a download to catch corruption / tampering before the runtime would ``torch.load`` /
    ``pickle.load`` it.
    """
    tree = Path(tree).resolve()
    bad = 0
    for entry in manifest.get("models", []):
        rel = entry["filename"]
        local = (tree / rel).resolve()
        # Defense-in-depth: a crafted manifest filename ("../…", absolute path) must not
        # let _sha256 read outside the tree on a CI runner.
        if not local.is_relative_to(tree):
            print(f"FAIL {rel}: path escapes tree — rejected", file=sys.stderr)
            bad += 1
            continue
        if not local.exists():
            print(f"FAIL {rel}: missing", file=sys.stderr)
            bad += 1
            continue
        actual = _sha256(str(local))
        if actual != entry["sha256"]:
            print(
                f"FAIL {rel}: sha mismatch (expected {entry['sha256']}, got {actual})",
                file=sys.stderr,
            )
            bad += 1
        else:
            print(f"ok   {rel}", file=sys.stderr)
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--from-dir",
        "--models-dir",
        dest="from_dir",
        help="Per-symbol tree (<SYM>/checkpoint.pt …) to build the manifest from.",
    )
    ap.add_argument("--verify", help="Manifest JSON to re-hash against --tree")
    ap.add_argument("--tree", help="Local per-symbol tree to verify against")
    ap.add_argument("--release-tag", default=_DEFAULT_RELEASE_TAG)
    ap.add_argument("--repo", default=_DEFAULT_REPO)
    ap.add_argument("--output", help="Write manifest here (else stdout)")
    args = ap.parse_args()

    if args.verify:
        if not args.tree:
            print("error: --verify requires --tree", file=sys.stderr)
            return 2
        with open(args.verify, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        return verify_tft_manifest(manifest, Path(args.tree))

    if args.from_dir:
        manifest = build_tft_manifest(Path(args.from_dir), args.release_tag, args.repo)
        text = json.dumps(manifest, indent=2) + "\n"
        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            print(
                f"wrote {args.output} ({len(manifest['models'])} entries)",
                file=sys.stderr,
            )
        else:
            sys.stdout.write(text)
        if manifest["incomplete"]:
            print(
                f"ERROR: {len(manifest['incomplete'])} symbol(s) have a checkpoint but "
                f"no verifiable training_ds (W-4) — excluded: "
                f"{manifest['incomplete'][:10]}",
                file=sys.stderr,
            )
            return 1
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
