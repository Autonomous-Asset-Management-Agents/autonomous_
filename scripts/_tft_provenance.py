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

"""Shared TFT-provenance helper (operator tools only — not called at runtime).

Single source of truth for resolving the matched ``training_ds`` so the manifest
builder, the serving-bundle packer, and the runtime loader never drift
(``build_tft_manifest.py`` and ``build_tft_serving_bundle.py`` both import this).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def matched_training_ds(model_dir: Path) -> Optional[Path]:
    """Resolve the training_ds whose feature schema matches the promoted checkpoint
    (ADR-ML-DS-01), reusing the runtime's own resolver
    (``TFTInferenceEngine._resolve_training_ds_path``) so packer/builder/loader agree.

    Returns the path only if it exists on disk. Returns ``None`` if it cannot be
    resolved (no metadata + no legacy ``training_ds.pkl``, the resolved file is absent,
    or the import fails because the operator env lacks ``pandas``). **The caller MUST
    treat ``None`` as an incomplete / non-servable symbol (W-4)** — a checkpoint without
    a verifiable training_ds is not shippable.
    """
    try:
        from core.ml.tft_inference import TFTInferenceEngine

        path = TFTInferenceEngine(model_dir.name, model_dir)._resolve_training_ds_path()
    except Exception as exc:
        # Loud, not silent (Rule 5): a resolve failure means W-4 cannot be honoured for
        # this symbol → the caller will exclude it and fail loudly.
        print(
            f"WARNING: {model_dir.name}: training_ds resolve failed: {exc}",
            file=sys.stderr,
        )
        return None
    return path if (path is not None and path.exists()) else None
