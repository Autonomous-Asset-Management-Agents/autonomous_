"""One-command Alpaca Sim-Day CLI (#2548 #82/#83).

    python -m core.sim run      [--date 2026-07-28] [--window "09:30-10:30 ET"] [--cadence 15m]
                                [--corpus sim_corpus] [--out sim_result.json] [--compare prev.json]
    python -m core.sim fetch    [--date 2026-07-28] [--symbols AAPL,MSFT] [--corpus sim_corpus]
                                [--cache-dir <market_data_cache>]
    python -m core.sim compare  a.json b.json

Defaults are zero-config: last-trading-day window = the open hour, flat-out pacing. Offline —
data comes from the local corpus (build it once with `fetch`).
"""

import argparse
import os
import sys


def _add_run_env(args):
    os.environ["AAA_SIM_MODE"] = "true"
    os.environ["AAA_SIM_DATE"] = args.date
    os.environ["AAA_SIM_WINDOW"] = args.window
    os.environ["AAA_SIM_CADENCE"] = args.cadence
    os.environ["AAA_SIM_PACING"] = "flat-out"
    os.environ["AAA_SIM_CORPUS_DIR"] = args.corpus


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m core.sim")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a sim day offline")
    r.add_argument("--date", default="", help="YYYY-MM-DD (empty → last weekday)")
    r.add_argument("--window", default="09:30-10:30 ET")
    r.add_argument("--cadence", default="15m")
    r.add_argument("--corpus", default="sim_corpus")
    r.add_argument("--out", default="sim_result.json")
    r.add_argument(
        "--compare",
        default="",
        help="path to a previous sim_result.json to diff against",
    )
    r.add_argument(
        "--seed-positions",
        default="",
        help="start with positions to manage, e.g. AAPL:20,MSFT:10 (else a flat 100k book)",
    )

    f = sub.add_parser("fetch", help="build the offline corpus (once)")
    f.add_argument("--date", default="", help="pin bars to <= this date")
    f.add_argument(
        "--symbols", default="", help="comma-separated; empty → default universe"
    )
    f.add_argument("--corpus", default="sim_corpus")
    f.add_argument(
        "--cache-dir",
        default="",
        help="existing market_data_cache dir (default: desktop)",
    )

    c = sub.add_parser("compare", help="diff two sim_result.json files")
    c.add_argument("a")
    c.add_argument("b")

    args = ap.parse_args(argv)

    if args.cmd == "fetch":
        from core.sim.fetch import build_corpus_from_cache

        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
        n = build_corpus_from_cache(
            out_dir=args.corpus,
            cache_dir=args.cache_dir or None,
            symbols=syms,
            up_to_date=args.date or None,
        )
        print(f"corpus: wrote {n} symbols to {args.corpus}")
        return 0

    if args.cmd == "compare":
        from core.sim.result import SimResult, compare

        print(compare(SimResult.from_json(args.a), SimResult.from_json(args.b)))
        return 0

    # run
    _add_run_env(args)  # env MUST be set before importing config/engine
    from core.sim.result import SimResult, compare
    from core.sim.runner import run_sim

    seed = {}
    for pair in args.seed_positions.split(","):
        pair = pair.strip()
        if ":" in pair:
            sym, qty = pair.split(":", 1)
            try:
                seed[sym.strip().upper()] = float(qty)
            except ValueError:
                pass
    result = run_sim(seed_positions=seed or None)
    result.to_json(args.out)
    print(result.summary())
    print(f"\n→ {args.out}")
    if args.compare:
        print()
        print(compare(SimResult.from_json(args.compare), result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
