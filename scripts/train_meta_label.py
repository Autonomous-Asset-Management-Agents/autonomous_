"""
train_meta_label.py — #1955 TRD-4 CLI: offline meta-label model trainer.

Consumes the RTR-0 replay harness (deterministic agent-vote replay over
historical daily bars) to build López-de-Prado meta-labels — binary
net-of-cost outcome per PRIMARY BUY signal — and fits the lightweight
secondary Trade/No-Trade model served by ``core/round_table/meta_label.py``.

Offline analysis only — no runtime flag, no product code path. All parameters
are CLI arguments (BORA: zero config delta; the runtime reads only the
artifact). No paid APIs, no network: bars come from a local directory.

PREREQUISITE (documented, NOT faked — plan #1955 §11 Inc 2): a local daily-bar
panel ``{SYMBOL}.parquet|.csv`` covering 2024–2026 (e.g. from the #1907 PIT
panel builder). Without it this script exits with an honest error — it never
fabricates labels. The Inc-2 activation gate additionally requires the
net-of-cost OOS before/after proof from the v1/v3 benchmark harness.

Usage (from ai_trading_bot/):

    python scripts/train_meta_label.py \
        --data-dir <dir with {SYMBOL}.parquet|.csv daily bars> \
        --start 2024-06-03 --end 2026-03-25 \
        --horizon 5 --cost-bps 10 \
        --out data/meta_label_model.pkl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Run from ai_trading_bot/ or its scripts/ dir without installation
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.analysis.attribution.meta_label_training import (  # noqa: E402
    MIN_TRAIN_SAMPLES,
    build_meta_label_frame,
    save_meta_label_artifact,
    train_meta_label_model,
)
from core.analysis.attribution.replay import (  # noqa: E402
    ReplayBarsProvider,
    forward_returns,
    replay_votes,
)

logger = logging.getLogger("meta_label_trainer")


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Directory with {SYMBOL}.parquet|.csv daily bars (local, offline).",
    )
    parser.add_argument("--start", required=True, help="Replay start (YYYY-MM-DD).")
    parser.add_argument("--end", required=True, help="Replay end (YYYY-MM-DD).")
    parser.add_argument(
        "--horizon",
        type=int,
        default=5,
        help="Forward-return horizon in bars (label holding period).",
    )
    parser.add_argument(
        "--cost-bps",
        type=float,
        default=10.0,
        help="ONE-WAY transaction cost in bps (#1969 convention; round-trip = 2x).",
    )
    parser.add_argument(
        "--min-proba",
        type=float,
        default=0.55,
        help="Reporting threshold for OOS precision (mirrors META_LABEL_MIN_PROBA).",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=MIN_TRAIN_SAMPLES,
        help="Refuse to train below this many BUY-support samples (noise guard).",
    )
    parser.add_argument(
        "--test-fraction",
        type=float,
        default=0.25,
        help="Chronological OOS fraction of unique trading days.",
    )
    parser.add_argument(
        "--embargo-days",
        type=int,
        default=5,
        help="Trading days purged between train and OOS (~ label horizon).",
    )
    parser.add_argument("--seed", type=int, default=42, help="Deterministic seed.")
    parser.add_argument(
        "--out",
        default="data/meta_label_model.pkl",
        help="Artifact path (META_LABEL_MODEL_PATH serves this at runtime).",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)

    provider = ReplayBarsProvider(args.data_dir)
    if not provider.symbols:
        logger.error(
            "No readable {SYMBOL}.parquet|.csv bars in %r — the 2024–2026 panel "
            "is a documented prerequisite (plan #1955 §11 Inc 2); labels are "
            "never fabricated.",
            args.data_dir,
        )
        return 2

    logger.info(
        "Replaying votes over %d symbols [%s..%s] ...",
        len(provider.symbols),
        args.start,
        args.end,
    )
    replay = asyncio.run(replay_votes(provider, start=args.start, end=args.end))
    fwd = forward_returns(provider, horizon=args.horizon)
    frame = build_meta_label_frame(replay.votes, fwd, cost_bps=args.cost_bps)
    logger.info(
        "Label base: %d BUY-support samples (%d bars replayed, exclusions: %s)",
        len(frame),
        replay.n_bars,
        sorted(replay.exclusions),
    )

    try:
        result = train_meta_label_model(
            frame,
            min_samples=args.min_samples,
            test_fraction=args.test_fraction,
            embargo_days=args.embargo_days,
            threshold=args.min_proba,
            seed=args.seed,
        )
    except ValueError as exc:
        logger.error("Trainer refused: %s", exc)
        return 3

    payload = save_meta_label_artifact(
        result, args.out, horizon=args.horizon, cost_bps=args.cost_bps
    )
    summary = {
        "artifact": str(args.out),
        "features": payload["features"],
        "label": payload["label"],
        "training": payload["training"],
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
