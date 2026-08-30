"""
rtr0_attribution_benchmark.py — RTR-0 (#1947) CLI: per-agent attribution gate.

Replays every offline-replayable Round-Table agent over historical daily bars
and writes the versioned gate artifact (truth table, IC/t/hit-rate, LOO
delta-Sharpe net-of-cost, vote correlation matrix) that every RTR-1..4
activation PR must cite.

Offline analysis only — no runtime flag, no product code path (plan #1947 §5).
All parameters are CLI arguments (BORA: zero config delta, plan §9).

Usage (from ai_trading_bot/):

    python scripts/rtr0_attribution_benchmark.py \
        --data-dir <dir with {SYMBOL}.parquet|.pkl daily bars> \
        --start 2024-06-03 --end 2026-03-25 \
        --cost-bps 10 --ic-horizon 5 \
        --out ../docs/rtr/rtr0-attribution-benchmark/results

``--source capture`` is the DORMANT #2113 online cross-check (Inc 2) and
fails cleanly until #2113 is merged.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Run from ai_trading_bot/ or its scripts/ dir without installation
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from core.analysis.attribution.book_quality import (  # noqa: E402
    DEFAULT_N_BOOTSTRAP,
    book_quality_payload,
    build_ranking_specs,
    closes_from_provider,
    compute_book_quality,
    parse_book_bootstrap,
    render_book_quality_markdown,
)
from core.analysis.attribution.lstm_offline import (  # noqa: E402
    LstmArtifactError,
    LstmOfflineScorer,
    OfflineLstmStrategy,
    load_lstm_bundle,
)
from core.analysis.attribution.replay import (  # noqa: E402
    ReplayBarsProvider,
    forward_returns,
    replay_votes,
)
from core.analysis.attribution.report import (  # noqa: E402
    build_agent_rows,
    build_run_meta,
    render_markdown,
    rows_to_payload,
    write_gate_artifact,
)
from core.analysis.attribution.source_capture import load_capture_votes  # noqa: E402

logger = logging.getLogger("rtr0")


def _git_sha() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=Path(__file__).resolve().parents[1],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:  # noqa: BLE001 — metadata only, never fatal
        return "unknown"


def _load_vix(path: str | None) -> pd.Series | None:
    """Optional VIX close series ({date -> close}) from parquet/pkl/csv."""
    if not path:
        return None
    p = Path(path)
    if p.suffix.lower() == ".parquet":
        df = pd.read_parquet(p)
    elif p.suffix.lower() == ".pkl":
        df = pd.read_pickle(p)
    else:
        df = pd.read_csv(p, index_col=0, parse_dates=True)
    col = "close" if "close" in df.columns else df.columns[0]
    series = df[col]
    series.index = pd.to_datetime(series.index).normalize()
    if getattr(series.index, "tz", None) is not None:
        series.index = series.index.tz_localize(None)
    return series.sort_index()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", required=True, help="dir of {SYMBOL}.parquet|.pkl daily bars"
    )
    parser.add_argument(
        "--symbols", default=None, help="comma-separated subset (default: all files)"
    )
    parser.add_argument("--start", required=True, help="replay start (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="replay end (YYYY-MM-DD)")
    parser.add_argument(
        "--cost-bps",
        type=float,
        default=10.0,
        help="one-way cost (bps, #1969 convention)",
    )
    parser.add_argument(
        "--ic-horizon",
        type=int,
        default=5,
        help="forward MtM horizon (bars) for IC/hit-rate",
    )
    parser.add_argument(
        "--fdr-alpha", type=float, default=0.10, help="Benjamini-Hochberg FDR level"
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="seed for the shuffled-target sanity IC"
    )
    parser.add_argument(
        "--vix-file", default=None, help="optional VIX close series (parquet/pkl/csv)"
    )
    parser.add_argument("--source", choices=("replay", "capture"), default="replay")
    parser.add_argument(
        "--lstm-artifacts",
        default=None,
        help=(
            "Inc 3 (#2409): dir with lstm_model.pth + scaler_x.pkl + "
            "scaler_y.pkl + model_metadata.json (the repo data/ bundle). "
            "Enables the offline LSTM scorer — LSTMSignalAgent (and the "
            "RLConfidence row, which reads the same signal) become "
            "measurable instead of EXCLUDED. Missing/broken artifacts AND "
            "a 100%-abstention replay fail LOUDLY (exit 2), never a silent "
            "abstention."
        ),
    )
    parser.add_argument(
        "--book-quality",
        action="store_true",
        help=(
            "Inc 2 (#2402): append Top-N book-quality metrics (LSTM vs "
            "consensus ranking, precision@N, Random-N bootstrap) as an "
            "ADDITIVE book_quality field + RESULTS.md section"
        ),
    )
    parser.add_argument(
        "--book-bootstrap",
        type=parse_book_bootstrap,
        default=DEFAULT_N_BOOTSTRAP,
        help=(
            "random books per (N, horizon) for vs_random_pctile — hard "
            "floor 1000 (plan #2402 §1), the CLI rejects less (Rev 2 F4)"
        ),
    )
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parents[2]
            / "docs"
            / "rtr"
            / "rtr0-attribution-benchmark"
            / "results"
        ),
    )
    args = parser.parse_args()
    # ERROR level: the REAL ConsensusEngine logs a WARNING for every dormant
    # weight-0 vote it excludes (expected by construction in the replay —
    # thousands of them); real failures still surface at ERROR.
    logging.basicConfig(
        level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger().setLevel(logging.ERROR)
    # Replay must stay hermetic: never talk to a real Redis from an offline run.
    os.environ.setdefault("REDIS_URL", "")

    if args.source == "capture":
        load_capture_votes()  # raises CaptureSourceUnavailable until #2113
        return 1

    symbols = args.symbols.split(",") if args.symbols else None
    provider = ReplayBarsProvider(args.data_dir, symbols=symbols)
    if not provider.symbols:
        print(f"ERROR: no readable {{SYMBOL}}.parquet|.pkl bars in {args.data_dir}")
        return 2

    coverage = {
        s: {
            "bars": int(len(f)),
            "from": str(f.index.min().date()),
            "to": str(f.index.max().date()),
        }
        for s, f in provider.frames.items()
    }
    vix_series = _load_vix(args.vix_file)

    # #2409: model-backed LSTM replay via the replay_votes strategy hook.
    strategy = None
    lstm_meta: dict = {"enabled": False}
    if args.lstm_artifacts:
        try:
            bundle = load_lstm_bundle(args.lstm_artifacts)
        except LstmArtifactError as exc:
            print(f"ERROR: --lstm-artifacts: {exc}")
            return 2
        strategy = OfflineLstmStrategy(provider, LstmOfflineScorer(bundle))
        lstm_meta = {
            "enabled": True,
            "artifacts_dir": bundle.artifacts_dir,
            "sequence_length": bundle.sequence_length,
            "input_dim": bundle.input_dim,
            "n_features": len(bundle.features_list),
        }

    result = asyncio.run(
        replay_votes(
            provider,
            start=args.start,
            end=args.end,
            vix_series=vix_series,
            strategy=strategy,
        )
    )
    fwd_ic = forward_returns(provider, horizon=args.ic_horizon)
    fwd_1d = forward_returns(provider, horizon=1)

    rows, corr = build_agent_rows(
        result.votes,
        fwd_ic=fwd_ic,
        fwd_1d=fwd_1d,
        exclusions=result.exclusions,
        cost_bps=args.cost_bps,
        fdr_alpha=args.fdr_alpha,
        seed=args.seed,
        ic_horizon=args.ic_horizon,
    )

    # Rev 3 (#2421 owner repro): an operator who requested LSTM scoring must
    # NEVER get an EXIT-0 artifact whose LSTM row silently abstained on every
    # bar — same loud-failure contract as missing artifacts (exit 2).
    if lstm_meta.get("enabled"):
        from models.torch_model import FEATURE_WARMUP_ROWS  # lazy, like the scorer

        lstm_row = next(r for r in rows if r.agent == "LSTMSignalAgent")
        if lstm_row.n_votes > 0 and lstm_row.n_opinions == 0:
            min_rows = max(bundle.sequence_length, FEATURE_WARMUP_ROWS)
            print(
                "ERROR: --lstm-artifacts: the offline LSTM scorer produced "
                f"0 opinions over {lstm_row.n_votes} replayed bars (100% "
                "abstention). Likely causes: --data-dir lacks warm-up "
                "history before --start (each replay date needs "
                f"max(sequence_length, FEATURE_WARMUP_ROWS) = {min_rows}+ "
                f"bars within {bundle.sequence_length + 200} calendar "
                "days), or the collapse guard voided every cross-section. "
                "Fix the data window — a silent EXCLUDED row is not an "
                "attribution result."
            )
            return 2

    # M2 — build_run_meta enforces the reproducibility contract (raises on a
    # missing field) and embeds the flag posture the replay actually ran under.
    run_meta = build_run_meta(
        issue="#1947 RTR-0",
        generated_utc=datetime.now(timezone.utc).isoformat(),
        git_sha=_git_sha(),
        source=args.source,
        data_dir=str(Path(args.data_dir).resolve()),
        symbols=provider.symbols,
        coverage=coverage,
        start=args.start,
        end=args.end,
        n_bars_replayed=result.n_bars,
        cost_bps=args.cost_bps,
        ic_horizon_bars=args.ic_horizon,
        hac_lag_days=args.ic_horizon,
        fdr_alpha=args.fdr_alpha,
        seed=args.seed,
        vix_series=bool(vix_series is not None),
        lstm=lstm_meta,
        target=(
            f"forward {args.ic_horizon}-bar close-to-close MtM return "
            "(IC/hit-rate; significance = per-date cross-sectional Spearman "
            f"ICs + HAC/Newey-West t, lag {args.ic_horizon} trading days); "
            "1-bar forward return, daily rebalance, for the LOO consensus book"
        ),
    )

    md = render_markdown(rows, correlation=corr, run_meta=run_meta)
    payload = rows_to_payload(rows, correlation=corr, run_meta=run_meta)

    if args.book_quality:
        # Inc 2 (#2402) — ADDITIVE: report.py stays untouched; the section is
        # appended to the Markdown and the JSON gains one book_quality field.
        # Rev 3 (#2421): with the offline scorer active, the 'lstm' ranking
        # consumes its RAW per-date cross-sections (provenance-labeled)
        # instead of falling back to the tanh-mapped vote scores.
        lstm_kwargs = (
            {
                "lstm_panel": strategy.score_panel(),
                "lstm_basis": "offline scorer, parity-pinned",
            }
            if strategy is not None
            else {}
        )
        bq = compute_book_quality(
            build_ranking_specs(result.votes, **lstm_kwargs),
            closes_from_provider(provider),
            cost_bps=args.cost_bps,
            n_bootstrap=args.book_bootstrap,
            seed=args.seed,
        )
        payload["book_quality"] = book_quality_payload(bq)
        md = md + render_book_quality_markdown(bq)

    md_path, json_path = write_gate_artifact(args.out, markdown=md, payload=payload)
    print(f"Gate artifact written:\n  {md_path}\n  {json_path}")
    print(json.dumps({r.agent: r.verdict for r in rows}, indent=2))
    if args.book_quality:
        print(
            json.dumps(
                {
                    "book_quality": {
                        "verdict": payload["book_quality"]["verdict"],
                        "rankings": {
                            src: data["verdict"]
                            for src, data in payload["book_quality"]["rankings"].items()
                        },
                    }
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
