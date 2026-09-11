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

"""build_tft_serving_bundle.py — stage the SERVING-only per-symbol TFT tree.

Model-Provenance Issue 1 (fusion). The full training tree is ~5.6 GB (3 seed
checkpoints + seed training_ds + ``_v2_train_logs/`` per symbol). **Serving needs only
~1.3 GB**: per symbol, the promoted ``checkpoint.pt`` + ``metadata.json`` + the **one**
matched ``training_ds_*.pkl`` (ADR-ML-DS-01). This stages exactly those files into a
``<SYM>/`` layout that matches ``build_tft_manifest`` filenames, ready to tar and
publish as a single ≤2 GB release asset.

No model file is deserialised here — only copied. Integrity is asserted separately by
``build_tft_manifest --verify`` (SHA-256). Operator tool — not called at runtime.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import List

from scripts._tft_provenance import matched_training_ds

# The exact serving file set per symbol. The matched training_ds is resolved
# dynamically (ADR-ML-DS-01); everything else (seed checkpoints, seed training_ds,
# _v2_train_logs/) is excluded by simply not copying it.
_SERVING_STATIC_FILES = ("checkpoint.pt", "metadata.json")


def stage_serving_tree(src: Path, dest: Path) -> List[str]:
    """Copy the serving-only files of every servable symbol from ``src`` to ``dest``.

    A symbol is servable iff it has a ``checkpoint.pt``. Returns the sorted list of
    staged symbols. ``dest`` mirrors ``src`` layout (``<SYM>/…``) so it matches the
    manifest and the published TAR exactly.
    """
    src, dest = Path(src), Path(dest)
    staged: List[str] = []
    for sym_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        if not (sym_dir / "checkpoint.pt").exists():
            continue
        matched_ds = matched_training_ds(sym_dir)
        if matched_ds is None:
            # W-4: a checkpoint without its matched training_ds is not servable (it would
            # fail at load with the exact shape-mismatch ADR-ML-DS-01 prevents). Skip it
            # loudly rather than stage a half-symbol.
            print(
                f"WARNING: {sym_dir.name}: no matched training_ds — "
                "skipping (not servable)",
                file=sys.stderr,
            )
            continue
        out_dir = dest / sym_dir.name
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in _SERVING_STATIC_FILES:
            srcfile = sym_dir / name
            if srcfile.exists():
                shutil.copy2(srcfile, out_dir / name)
        shutil.copy2(matched_ds, out_dir / matched_ds.name)
        staged.append(sym_dir.name)
    return staged


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--src",
        required=True,
        help="Source per-symbol tree (the off-repo training-machine core/ml/models/).",
    )
    ap.add_argument("--dest", required=True, help="Staging dir for the serving tree.")
    ap.add_argument(
        "--tar",
        help="Optional: also write a gzipped tar (.tar.gz) of <dest> at this path.",
    )
    args = ap.parse_args()

    staged = stage_serving_tree(Path(args.src), Path(args.dest))
    print(f"staged {len(staged)} symbols → {args.dest}", file=sys.stderr)

    if args.tar:
        tar_path = Path(args.tar)
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        # gztar is portable + always available; zstd needs the optional backend.
        fmt = "gztar"
        base = str(tar_path)
        for suffix in (".tar.gz", ".tgz", ".tar"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        archive = shutil.make_archive(base, fmt, root_dir=str(args.dest))
        print(f"wrote {archive}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
