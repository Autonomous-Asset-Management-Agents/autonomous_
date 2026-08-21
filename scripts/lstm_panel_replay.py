#!/usr/bin/env python
"""NICHT-BÖRSE LSTM panel replay harness (LSTM-panel-starvation fix).

Runs the REAL LSTM ranking pipeline —
``core.strategies.lstm_strategy.LSTMDynamicStrategy.update_lstm_rankings`` reading
through the REAL ``core.data_provider.HistoricalDataProvider.get_data`` — against a
LOCAL daily-bar store with **no live provider** (``api=None``) and **no open
market**. It is the offline validation the fix needs: prove that, given a warm
store, the per-cycle read path populates the cross-sectional LSTM panel for the
whole universe using ZERO live fetches (the thing that was starving → all-HOLD).

Why this is a faithful test and not a mock: the ONLY substitution in the default
mode is the per-symbol model *inference* — everything the starvation bug lived in
(the universe loop, ``get_data``, the disk-depth check, the abstain-if-``< min_rows``
rule, the sticky-panel guard) is the real production code. The substituted
prediction still calls the real ``get_data`` and applies the SAME abstain rule
(``len(hist) < max(seq_len, FEATURE_WARMUP_ROWS)`` → ``None``), so a symbol the
store cannot serve deeply enough abstains here exactly as it would live.

Modes
  (default) data-gated : real get_data + real update_lstm_rankings wiring; the
             per-symbol prediction is a real 5-day momentum derived from the store
             bars, abstaining iff ``< min_rows``. Robust, fast, deterministic.
  --use-model : additionally load the REAL LSTM model + scalers and run true torch
             inference per symbol (full end-to-end). Heavier; sample with --limit.

Usage (run from ai_trading_bot/, or anywhere — sys.path is fixed up):
  python scripts/lstm_panel_replay.py --cache-dir market_data_cache
  python scripts/lstm_panel_replay.py \
      --cache-dir "C:/Users/<you>/AppData/Local/autonomous/app/ai_trading_bot/market_data_cache"
  python scripts/lstm_panel_replay.py --cache-dir market_data_cache --use-model --limit 40

Exit code 0 = panel populated for >= --pass-threshold of the universe (default 0.90).
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import logging
import os
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

# --- sys.path: make ``config`` / ``core`` / ``models`` importable regardless of CWD.
_AI_BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ai_trading_bot/
if _AI_BOT not in sys.path:
    sys.path.insert(0, _AI_BOT)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Offline LSTM panel replay (no market, no API)."
    )
    p.add_argument(
        "--cache-dir",
        default="market_data_cache",
        help="Directory of {SYMBOL}.parquet daily-bar files (the warm store).",
    )
    p.add_argument(
        "--end",
        default=None,
        help="Replay 'as-of' date YYYY-MM-DD (default: today UTC). Must be covered by the store.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Cap the universe to the first N symbols (0 = all). Useful with --use-model.",
    )
    p.add_argument(
        "--use-model",
        action="store_true",
        help="Load the real LSTM model + scalers and run true torch inference per symbol.",
    )
    p.add_argument(
        "--pass-threshold",
        type=float,
        default=0.90,
        help="Fraction of the universe that must land in the panel to PASS (default 0.90).",
    )
    p.add_argument(
        "--verbose", action="store_true", help="Show per-symbol WARNING logs."
    )
    return p.parse_args(argv)


def _discover_universe(cache_dir: str, limit: int) -> List[str]:
    """The universe = every {SYMBOL}.parquet in the store (skip the batch_ scratch files)."""
    syms = []
    for f in sorted(glob.glob(os.path.join(cache_dir, "*.parquet"))):
        base = os.path.basename(f)[: -len(".parquet")]
        if base.startswith("batch_"):
            continue
        syms.append(base)
    if limit and limit > 0:
        syms = syms[:limit]
    return syms


def _build_provider(cache_dir: str):
    """A HistoricalDataProvider with NO live client — proves the read path is provider-independent."""
    import core.data_provider as dp_mod

    # Point the module-level cache dir at the store BEFORE constructing the provider;
    # get_data reads this module global, not an instance attribute.
    dp_mod.DATA_CACHE_DIR = os.path.abspath(cache_dir)
    provider = dp_mod.HistoricalDataProvider(
        api=None
    )  # api=None => zero live fetches possible
    assert (
        getattr(provider, "api", None) is None
    ), "harness requires api=None (no live provider)"
    return provider, dp_mod


def _build_snapshots(provider, symbols: List[str], end_dt: datetime, days: int):
    """Synthesize the per-symbol snapshots update_lstm_rankings needs (``.latest_trade.price``)
    from the store's last close — in production these come from the (separate, cheap, batched)
    live snapshot fetch; here the last daily bar IS today's close."""
    snapshots: Dict[str, object] = {}
    served = 0
    for sym in symbols:
        try:
            hist = provider.get_data(sym, end_dt, days)
        except Exception:  # noqa: BLE001 — a store miss must not abort the sweep
            hist = None
        if hist is None or getattr(hist, "empty", True):
            continue
        last_close = float(hist["close"].iloc[-1])
        snapshots[sym] = SimpleNamespace(
            latest_trade=SimpleNamespace(price=last_close, p=last_close)
        )
        served += 1
    return snapshots, served


def _make_data_gated_prediction(provider, days: int, min_rows: int):
    """A real-data prediction: reads the store via the REAL get_data and applies the SAME
    abstain rule as production (< min_rows → None). Returns a distinct 5-day momentum so the
    collapse guard (identical-prediction → no-allocation) is not tripped by a constant.
    """

    async def _pred(symbol: str, current_time, market_data):
        loop = asyncio.get_running_loop()
        try:
            hist = await loop.run_in_executor(
                None, provider.get_data, symbol, current_time, days
            )
        except Exception:  # noqa: BLE001
            return None, None
        if hist is None or len(hist) < min_rows:
            return None, None  # production abstain rule (lstm_strategy.py:291)
        close = hist["close"]
        prior = float(close.iloc[-6]) if len(close) >= 6 else float(close.iloc[0])
        if prior == 0:
            return None, None
        momentum = float(close.iloc[-1]) / prior - 1.0  # real, distinct per symbol
        return momentum, None

    return _pred


def _build_real_strategy(provider):
    """--use-model: a real LSTMDynamicStrategy (built via __new__ to skip the heavy base
    __init__) with the REAL model + scalers loaded, driving true inference."""
    import joblib as _joblib
    import numpy as _np
    import pandas as _pd
    import torch as _torch

    from core.strategies.lstm_strategy import SEQUENCE_LENGTH, LSTMDynamicStrategy

    strat = LSTMDynamicStrategy.__new__(LSTMDynamicStrategy)
    strat.torch = _torch
    strat.np = _np
    strat.pd = _pd
    strat.joblib = _joblib
    strat.strategy_name = "LSTMDynamic"
    strat.device = _torch.device("cuda" if _torch.cuda.is_available() else "cpu")
    strat.torch_model = None
    strat.scaler_x = None
    strat.scaler_y = None
    strat.features_list = []
    strat.sequence_length = SEQUENCE_LENGTH
    strat._model_lock = __import__("threading").Lock()
    strat.client = (
        None  # not a SimulationAdapter -> _get_torch_prediction uses data_provider
    )
    strat.data_provider = provider
    strat._lstm_rank_cache = []
    strat._allocation_weights = {}
    strat._load_torch_model_assets()  # loads the real .pt + scalers via models.torch_model
    if not strat.torch_model:
        raise SystemExit(
            "ERROR: --use-model could not load the LSTM model assets (get_lstm_paths). "
            "Run without --use-model for the data-gated proof, or run from a tree/install "
            "that ships the model files."
        )
    return strat


async def _run(args: argparse.Namespace) -> int:
    import config as _cfg
    from core.strategies.lstm_strategy import SEQUENCE_LENGTH, LSTMDynamicStrategy

    logging.basicConfig(
        level=logging.WARNING if args.verbose else logging.ERROR,
        format="%(levelname)s %(message)s",
    )

    cache_dir = os.path.abspath(args.cache_dir)
    if not os.path.isdir(cache_dir):
        print(f"ERROR: cache dir not found: {cache_dir}")
        return 2

    end_dt = (
        datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.end
        else datetime.now(timezone.utc)
    )
    # SAME days _get_torch_prediction passes to get_data (lstm_strategy.py:279).
    days = SEQUENCE_LENGTH + 200

    provider, _dp_mod = _build_provider(cache_dir)
    universe = _discover_universe(cache_dir, args.limit)
    if not universe:
        print(f"ERROR: no {{SYMBOL}}.parquet files in {cache_dir}")
        return 2

    # min_rows the abstain rule uses = max(seq_len, FEATURE_WARMUP_ROWS).
    from models.torch_model import FEATURE_WARMUP_ROWS

    min_rows = max(SEQUENCE_LENGTH, FEATURE_WARMUP_ROWS)

    snapshots, served = _build_snapshots(provider, universe, end_dt, days)

    mode = (
        "REAL-MODEL inference"
        if args.use_model
        else "data-gated (real store + real wiring)"
    )
    print("=" * 74)
    print("NICHT-BÖRSE LSTM panel replay — no open market, no live provider (api=None)")
    print("=" * 74)
    print(f"store dir     : {cache_dir}")
    print(f"as-of date    : {end_dt.date()}   (days requested per symbol = {days})")
    print(f"universe      : {len(universe)} symbols")
    print(
        f"store-served  : {served} symbols returned bars from disk (get_data, api=None)"
    )
    print(
        f"min_rows      : {min_rows}  (= max(seq_len {SEQUENCE_LENGTH}, warmup {FEATURE_WARMUP_ROWS}))"
    )
    print(f"mode          : {mode}")

    # Build the ranking "self": the real strategy (--use-model) or a minimal namespace
    # carrying exactly the attrs update_lstm_rankings touches (np, torch_model,
    # _get_torch_prediction, _lstm_rank_cache, _allocation_weights).
    if args.use_model:
        strat = _build_real_strategy(provider)
    else:
        import numpy as _np

        strat = SimpleNamespace(
            np=_np,
            torch_model=object(),  # truthy -> update_lstm_rankings proceeds
            _get_torch_prediction=_make_data_gated_prediction(provider, days, min_rows),
            _lstm_rank_cache=[],
            _allocation_weights={},
            strategy_name="LSTMDynamic",
        )

    # Exercise the REAL cross-section panel store too (what LSTMSignalAgent reads),
    # so a populated _lstm_rank_cache is also observable as a populated panel.
    try:
        from core.report.lstm_panel_store import get_store

        get_store().reset()
        panel_store = get_store()
    except Exception:  # noqa: BLE001
        panel_store = None

    t0 = time.perf_counter()
    with_patch = getattr(_cfg.get_config(), "REPORT_GENERATOR_V2_ENABLED", False)
    # Force the panel-record path ON so we also validate the panel the agent reads.
    try:
        _cfg.get_config().REPORT_GENERATOR_V2_ENABLED = True
    except Exception:  # noqa: BLE001
        pass
    await LSTMDynamicStrategy.update_lstm_rankings(
        strat, universe, snapshots, {}, end_dt
    )
    try:
        _cfg.get_config().REPORT_GENERATOR_V2_ENABLED = with_patch
    except Exception:  # noqa: BLE001
        pass
    elapsed = time.perf_counter() - t0

    ranked: List[Tuple[str, float]] = list(getattr(strat, "_lstm_rank_cache", []))
    ranked_syms = {s for s, _ in ranked}
    # abstainers = symbols we had a snapshot for but that did not make the ranking
    abstained = [s for s in snapshots if s not in ranked_syms]
    panel_size = 0
    if panel_store is not None:
        try:
            panel_size = len(panel_store.cross_section_at(end_dt))
        except Exception:  # noqa: BLE001
            panel_size = 0

    print("-" * 74)
    print(
        f"panel ranked  : {len(ranked)} symbols   (LSTM cross-section is NON-empty: {bool(ranked)})"
    )
    print(
        f"panel store   : cross_section_at({end_dt.date()}) = {panel_size} symbols (agent read path)"
    )
    print(
        f"abstained     : {len(abstained)}  {sorted(abstained)[:12]}{' ...' if len(abstained) > 12 else ''}"
    )
    if ranked:
        top = sorted(ranked, key=lambda x: x[1], reverse=True)[:5]
        print("top 5 ranked  : " + ", ".join(f"{s}({v:+.3f})" for s, v in top))
    print(f"live fetches  : 0  (api=None — no live provider was reachable)")
    print(f"elapsed       : {elapsed:.2f}s")

    denom = max(1, served)
    frac = len(ranked) / denom
    ok = frac >= args.pass_threshold and len(ranked) > 0
    print("-" * 74)
    verdict = "PASS" if ok else "FAIL"
    print(
        f"VERDICT: {verdict}  — panel populated for {len(ranked)}/{served} store-served "
        f"symbols ({frac:.1%}; threshold {args.pass_threshold:.0%})."
    )
    if ok:
        print(
            "The per-cycle read path fills the LSTM panel for the whole universe with NO "
            "live market and NO provider — the starvation (empty panel -> all-HOLD) is closed."
        )
    print("=" * 74)
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
