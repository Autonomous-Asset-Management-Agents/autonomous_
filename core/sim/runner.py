"""Sim-Day runner (#2548 #82) — boots the engine in SIM_MODE, drives the trading loop across the
window (the loop self-terminates via the #2548 sim hook), and collects a ``SimResult``.

Order records come from the execution-outcome recorder (every "executed"/"blocked:*"/"error"
outcome + reason), enriched with fill price/qty from the VirtualLiveBroker — so blocked orders and
their reasons are visible, not only the fills that reached the broker.
"""

import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)


def _side(v) -> str:
    s = getattr(v, "value", v)
    return str(s).upper().replace("ORDERSIDE.", "")


def _fill_price(o):
    fp = getattr(o, "filled_avg_price", None)
    try:
        return float(fp) if fp is not None else None
    except (TypeError, ValueError):
        return None


def _prior_close(provider, symbol, before_ts):
    """Last daily close strictly before ``before_ts`` (a fair seed entry / mark price)."""
    frame = provider.frames.get(str(symbol).upper())
    if frame is None or frame.empty:
        return None
    import pandas as pd

    prior = frame[frame.index < pd.Timestamp(before_ts).normalize()]
    return float(prior.iloc[-1]["close"]) if not prior.empty else None


def _seed_positions(broker, seed, clock):
    """Inject starting positions (symbol -> qty) at the prior-day close, so the strategy has
    something to manage (a trading scenario, not a flat book). Also seed the OPENING BUY FILL into
    the order history (design §H4/#2630): durable-entry-time reconcile
    (entry_time_reconcile._collect_fills) + min-hold need a filled BUY, else the SELL path aborts as
    'no BUY fill found — entry-time unknown' and never dispatches."""
    import uuid
    from datetime import timedelta
    from types import SimpleNamespace

    from alpaca.trading.enums import OrderSide

    from core.sim.fill_engine import DummyPosition

    provider = broker.data_client._provider
    seeded = {}
    entry_ts = clock.start_time - timedelta(
        days=1
    )  # tz-aware; "held since yesterday" → min-hold ok
    for sym, qty in (seed or {}).items():
        px = _prior_close(provider, sym, clock.start_time.replace(tzinfo=None))
        if px is None:
            logger.warning("seed: no price for %s — skipped.", sym)
            continue
        s = sym.upper()
        broker.positions[s] = DummyPosition(s, float(qty), px)
        broker.cash -= float(qty) * px
        broker.orders.append(
            SimpleNamespace(
                id=uuid.uuid4(),
                symbol=s,
                side=OrderSide.BUY,
                qty=str(qty),
                filled_qty=str(qty),
                filled_avg_price=str(px),
                filled_at=entry_ts,
                submitted_at=entry_ts,
                status="filled",
            )
        )
        seeded[s] = (float(qty), px)
    if seeded:
        logger.info(
            "Seeded %d starting positions (+ opening BUY fills): %s",
            len(seeded),
            seeded,
        )


def _seed_lstm_panel(broker, clock) -> int:
    """#2632 last-mile: seed the LSTM rank panel so the round table can vote.

    The panel's ONLY live writer is ``LSTMDynamic.update_lstm_rankings`` (driven by the
    monitor loop / the strategy-independent rank producer), which the sim does not run —
    so under SIM_MODE the panel is EMPTY. An empty panel makes ``LSTMSignalAgent`` abstain
    (weight 0, agents.py) → ``lstm_valid`` is False → the strict-ML gate hard-blocks EVERY
    symbol (``runner._strict_ml_gate_blocks``: ``if not lstm_valid: return True``) → 0 orders,
    however strong the signal. We seed a deterministic cross-section from the corpus so the
    board has a validated basis to vote on, exactly as it would live.

    Score = prior-day return (last two daily closes STRICTLY BEFORE the sim day — no
    look-ahead, matching ``_prior_close``); single-bar symbols fall back to the close level;
    symbols with no prior bar are skipped. Returns the number of ranked symbols. SIM-only —
    the live panel path (real ``update_lstm_rankings``) is untouched.
    """
    import pandas as pd

    from core.report.lstm_panel_store import get_store

    provider = broker.data_client._provider
    # Naive, day-normalized cutoff — identical no-look-ahead filter to ``_prior_close``.
    cutoff = pd.Timestamp(clock.start_time.replace(tzinfo=None)).normalize()
    ranked = []
    for sym in provider.symbols:
        frame = provider.frames.get(str(sym).upper())
        if frame is None or frame.empty:
            continue
        closes = frame[frame.index < cutoff]["close"].dropna()
        if len(closes) >= 2:
            prev = float(closes.iloc[-2])
            score = (float(closes.iloc[-1]) / prev - 1.0) if prev else 0.0
        elif len(closes) == 1:
            score = float(closes.iloc[-1])  # single bar → level (last resort)
        else:
            continue
        ranked.append((str(sym).upper(), score))
    if ranked:
        get_store().record_cycle(clock.start_time.date(), ranked)
        logger.info(
            "Sim: seeded LSTM rank panel @ %s with %d symbols (prior-day momentum) — the "
            "round table now has a validated basis (else the strict-ML gate blocks all).",
            clock.start_time.date().isoformat(),
            len(ranked),
        )
    else:
        logger.warning(
            "Sim: LSTM panel seed produced 0 ranked symbols (no prior corpus bars) — "
            "the strict-ML gate will still block every symbol."
        )
    return len(ranked)


def run_sim(timeout_s: float = 300.0, seed_positions: dict = None):
    """Run one sim day and return a SimResult. Reads SIM_* from config (set by the CLI)."""
    from config import get_config
    from core.sim.clock import get_sim_clock, reset_sim_clock
    from core.sim.recorder import get_recorder
    from core.sim.result import SimResult

    reset_sim_clock()
    get_recorder().reset()  # arm outcome capture (blocked + reasons + fills)
    cfg = get_config()
    clock = get_sim_clock()
    t0 = time.time()

    # Boot the engine in SIM_MODE (C1 seam → VirtualLiveBroker + SimDataClient, offline).
    from core.engine import api_routes

    api_routes._init_trading_clients()
    engine = api_routes.engine
    broker = engine.api
    if type(broker).__name__ != "VirtualLiveBroker":
        raise RuntimeError(
            f"sim boot did not inject the VirtualLiveBroker (got {type(broker).__name__}). "
            "Is AAA_SIM_MODE=true set before import?"
        )
    starting_equity = float(broker._equity())
    if seed_positions:
        _seed_positions(broker, seed_positions, clock)
        starting_equity = float(broker._equity())  # re-mark after seeding
    symbols = list(broker.data_client._provider.symbols)
    logger.info(
        "Sim boot OK — %d corpus symbols, window %s.", len(symbols), cfg.SIM_WINDOW
    )

    # #2632 last-mile: seed the LSTM rank panel BEFORE the first cycle so the round table
    # has a validated basis to vote on. Without it the panel is empty (the sim never runs
    # the live update_lstm_rankings producer) → LSTMSignalAgent abstains → the strict-ML
    # gate hard-blocks every symbol → 0 orders. One sim day = one panel date.
    _seed_lstm_panel(broker, clock)

    # Drive the strategy loop. The #2548 sim hook advances the clock each cycle and, at the
    # window's end, sets ``engine._sim_complete``. Wait on THAT — never on ``strategy_running``,
    # which the monitor's initial strategy switch (None→RLAgent) transiently clears mid-run
    # (that clear was mistaken for "sim done" → the 0-cycle premature exit, #2630).
    import threading

    engine._sim_complete = threading.Event()
    engine._sim_cycles = 0
    engine.start_live_strategy()
    while not engine._sim_complete.is_set():
        time.sleep(0.2)
        if time.time() - t0 > timeout_s:
            logger.warning("Sim timeout (%.0fs) — stopping.", timeout_s)
            engine.strategy_running.clear()
            break

    orders = _build_orders(get_recorder().events, broker.orders)
    end_positions = {s: float(p.qty) for s, p in broker.positions.items()}
    ending_equity = float(broker._equity())

    return SimResult(
        sim_date=str(clock.start_time.date()),
        window=cfg.SIM_WINDOW,
        cadence=cfg.SIM_CADENCE,
        symbols=symbols,
        orders=orders,
        end_positions=end_positions,
        starting_equity=starting_equity,
        ending_equity=ending_equity,
        n_cycles=int(getattr(engine, "_sim_cycles", 0)),
        wall_time_s=round(time.time() - t0, 1),
    )


def _build_orders(events, broker_orders):
    """Merge outcome events (blocked + reasons + executed) with the broker's fills (side/qty/price)."""
    fills_by_symbol = defaultdict(list)
    for o in broker_orders:
        fills_by_symbol[str(getattr(o, "symbol", "?")).upper()].append(o)

    orders = []
    for ev in events:
        code = ev["outcome"]
        sym = str(ev["symbol"]).upper()
        rec = {
            "symbol": sym,
            "side": None,
            "qty": None,
            "status": code,
            "fill_price": None,
            "blocked_reason": None,
        }
        if code == "executed":
            rec["status"] = "filled"
            queue = fills_by_symbol.get(sym)
            if queue:
                o = queue.pop(0)
                rec["side"] = _side(getattr(o, "side", None))
                rec["qty"] = float(getattr(o, "qty", 0) or 0)
                rec["fill_price"] = _fill_price(o)
        elif code.startswith("blocked:"):
            rec["status"] = "blocked"
            rec["blocked_reason"] = code + (
                f" — {ev['reason']}" if ev["reason"] else ""
            )
        else:
            rec["blocked_reason"] = ev["reason"] or None
        orders.append(rec)
    return orders
