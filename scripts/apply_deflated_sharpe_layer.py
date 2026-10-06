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

"""apply_deflated_sharpe_layer.py — Layer 5: deflated Sharpe stamping (MLR-9, #1909).

Offline OPERATOR tool — never imported by the live trading path. Walks a
per-symbol model tree (``<SYM>/metadata.json``), reads each symbol's
net-of-cost walk-forward return series, computes the Bailey/Lopez de Prado
deflation via :mod:`core.ml.deflated_sharpe` and stamps the gate inputs into
``metadata.json`` (non-destructive merge, atomic replace):

  top-level: ``deflated_sharpe``  — ANNUALIZED Sharpe-scale margin
             ``(SR - E[max SR]) * sqrt(annualization)``; negative ⇒ overfit.
             This is the field ``core/ml/quality_gate.py`` compares against
             ``TFT_DSR_FLOOR`` when ``TFT_DEFLATED_SHARPE_GATE_ENABLED``.
             ``psr`` — probability P[true SR > 0]; ``n_trials``.
  layer5:    full audit block (dsr_prob, pbo, moments, sr_variance, timestamp)
             — mirrors the MLR-3 ``layer4`` idiom.

Return-series sources per symbol, in priority order (fail-safe — a symbol
without a series is SKIPPED untouched, which the runtime gate treats as legacy
metadata → passthrough + WARNING):

  1. ``layer4.net_returns`` in metadata.json (Layer-4 cost model output)
  2. ``<SYM>/net_returns.json`` sidecar (JSON list of per-period net returns)

``n_trials`` is NEVER hard-coded (plan §12.2): ``--n-trials`` wins, else
(unique manifest symbols | usable tree symbols) x ``--seeds-per-symbol``
(default 3 — best-of-3-seed selection per symbol). The cross-trial SR variance
``V`` defaults to the population variance of the per-period SRs across the
usable universe; override with ``--sr-variance``.

``pbo`` is stamped ``null``: CSCV needs the IS/OOS split matrix which the
current walk-forward artefacts do not retain (plan §12.7 — TODO once the
backtest persists per-split candidate rankings; the utility function
``probability_of_backtest_overfitting`` is already available).

Usage:
  python scripts/apply_deflated_sharpe_layer.py --models-dir core/ml/models \\
      [--manifest data/tft_models_manifest.json] [--n-trials N] \\
      [--seeds-per-symbol 3] [--sr-variance V] [--annualization 252] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import math
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.ml.deflated_sharpe import (  # noqa: E402
    deflated_sharpe_excess,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    sharpe_moments,
)

logger = logging.getLogger(__name__)

_DEFAULT_SEEDS_PER_SYMBOL = 3  # best-of-3-seed selection per symbol (plan §12.2)
_DEFAULT_ANNUALIZATION = 252


def load_net_returns(sym_dir: Path, meta: dict) -> Optional[list]:
    """Net-of-cost return series: ``layer4.net_returns`` else sidecar, else None."""
    layer4 = meta.get("layer4")
    if isinstance(layer4, dict):
        series = layer4.get("net_returns")
        if isinstance(series, list) and len(series) >= 2:
            return series
    sidecar = sym_dir / "net_returns.json"
    if sidecar.exists():
        try:
            series = json.loads(sidecar.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "[Layer5] %s: net_returns.json unreadable: %s", sym_dir.name, exc
            )
            return None
        if isinstance(series, list) and len(series) >= 2:
            return series
    return None


def resolve_n_trials(
    explicit: Optional[int],
    manifest_path: Optional[Path],
    n_symbols_fallback: int,
    seeds_per_symbol: int = _DEFAULT_SEEDS_PER_SYMBOL,
) -> int:
    """Trials N for the E[max SR] deflation — never a hard-coded literal.

    Priority: explicit ``--n-trials`` > unique symbols in the walk-forward
    manifest x seeds > usable tree symbols x seeds.
    """
    if explicit is not None:
        return int(explicit)
    if manifest_path is not None:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        symbols = {m["symbol"] for m in manifest.get("models", []) if "symbol" in m}
        if symbols:
            return len(symbols) * seeds_per_symbol
    return int(n_symbols_fallback) * seeds_per_symbol


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def apply_layer(
    tree: Path,
    n_trials: Optional[int] = None,
    manifest: Optional[Path] = None,
    seeds_per_symbol: int = _DEFAULT_SEEDS_PER_SYMBOL,
    annualization: int = _DEFAULT_ANNUALIZATION,
    sr_variance: Optional[float] = None,
    dry_run: bool = False,
) -> dict:
    """Stamp Layer-5 deflation fields across a per-symbol tree; return summary."""
    tree = Path(tree)
    # Pass 1: collect usable symbols + per-period moments (also feeds V estimate).
    usable: list = []  # (sym_dir, meta, moments)
    skipped: dict = {}
    for sym_dir in sorted(p for p in tree.iterdir() if p.is_dir()):
        meta_path = sym_dir / "metadata.json"
        if not meta_path.exists():
            skipped[sym_dir.name] = "no metadata.json"
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            skipped[sym_dir.name] = f"metadata unreadable: {exc}"
            continue
        series = load_net_returns(sym_dir, meta)
        if series is None:
            skipped[sym_dir.name] = "no net-of-cost return series (layer4/sidecar)"
            logger.warning(
                "[Layer5] %s: no net return series — skipped (gate will passthrough "
                "as legacy metadata)",
                sym_dir.name,
            )
            continue
        moments = sharpe_moments(series)
        if moments is None:
            skipped[sym_dir.name] = "degenerate return series"
            logger.warning("[Layer5] %s: degenerate series — skipped", sym_dir.name)
            continue
        usable.append((sym_dir, meta, moments))

    resolved_n = resolve_n_trials(n_trials, manifest, len(usable), seeds_per_symbol)

    # Cross-trial SR variance V: explicit override, else population variance of
    # the per-period SRs across the usable universe (needs >= 2 points).
    if sr_variance is None:
        if len(usable) < 2:
            raise ValueError(
                "sr_variance cannot be estimated from < 2 usable symbols — "
                "pass --sr-variance explicitly"
            )
        srs = [m["sr"] for _, _, m in usable]
        mean_sr = sum(srs) / len(srs)
        sr_variance = sum((s - mean_sr) ** 2 for s in srs) / len(srs)

    stamped = []
    ann = math.sqrt(annualization)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    for sym_dir, meta, m in usable:
        sr, n_obs = m["sr"], m["n_obs"]
        excess = deflated_sharpe_excess(sr, resolved_n, sr_variance)
        dsr_annual = excess * ann
        psr = probabilistic_sharpe_ratio(sr, n_obs, m["skew"], m["kurtosis"])
        dsr_prob = deflated_sharpe_ratio(
            sr, n_obs, m["skew"], m["kurtosis"], resolved_n, sr_variance
        )
        if not math.isfinite(dsr_annual):
            skipped[sym_dir.name] = "non-finite deflation result"
            logger.warning("[Layer5] %s: non-finite result — skipped", sym_dir.name)
            continue
        meta["deflated_sharpe"] = dsr_annual
        meta["psr"] = psr if math.isfinite(psr) else None
        meta["n_trials"] = resolved_n
        meta["layer5"] = {
            "deflated_sharpe": dsr_annual,
            "dsr_prob": dsr_prob if math.isfinite(dsr_prob) else None,
            "psr": psr if math.isfinite(psr) else None,
            # CSCV split matrix not retained by current walk-forward artefacts
            # (plan §12.7) — stays null until the backtest persists per-split ranks.
            "pbo": None,
            "n_trials": resolved_n,
            "sr_variance": sr_variance,
            "expected_max_sharpe": expected_max_sharpe(resolved_n, sr_variance),
            "sr_period": sr,
            "sr_annual": sr * ann,
            "skew": m["skew"],
            "kurtosis": m["kurtosis"],
            "n_obs": n_obs,
            "annualization": annualization,
            "stamped_at": now,
        }
        if not dry_run:
            _atomic_write_json(sym_dir / "metadata.json", meta)
        stamped.append(sym_dir.name)

    return {
        "stamped": stamped,
        "skipped": skipped,
        "n_trials": resolved_n,
        "sr_variance": sr_variance,
        "dry_run": dry_run,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--models-dir", required=True, help="Per-symbol tree (<SYM>/metadata.json ...)"
    )
    ap.add_argument(
        "--manifest", help="tft_models_manifest.json — n_trials = symbols x seeds"
    )
    ap.add_argument(
        "--n-trials",
        type=int,
        help="Explicit trials N (overrides manifest/tree derivation)",
    )
    ap.add_argument(
        "--seeds-per-symbol",
        type=int,
        default=_DEFAULT_SEEDS_PER_SYMBOL,
        help="Seeds per symbol in the selection (default 3)",
    )
    ap.add_argument(
        "--sr-variance",
        type=float,
        help="Cross-trial SR variance V (default: estimated across "
        "the usable universe)",
    )
    ap.add_argument("--annualization", type=int, default=_DEFAULT_ANNUALIZATION)
    ap.add_argument(
        "--dry-run", action="store_true", help="Compute + report, write nothing"
    )
    args = ap.parse_args()

    try:
        summary = apply_layer(
            Path(args.models_dir),
            n_trials=args.n_trials,
            manifest=Path(args.manifest) if args.manifest else None,
            seeds_per_symbol=args.seeds_per_symbol,
            annualization=args.annualization,
            sr_variance=args.sr_variance,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({k: v for k, v in summary.items() if k != "stamped"}, indent=2))
    print(
        f"stamped {len(summary['stamped'])} symbol(s), "
        f"skipped {len(summary['skipped'])}, n_trials={summary['n_trials']}, "
        f"V={summary['sr_variance']:.6g}"
        + (" [dry-run]" if summary["dry_run"] else ""),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
