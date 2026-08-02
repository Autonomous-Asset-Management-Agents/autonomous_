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
    something to manage (a trading scenario, not a flat book)."""
    from core.sim.fill_engine import DummyPosition

    provider = broker.data_client._provider
    seeded = {}
    for sym, qty in (seed or {}).items():
        px = _prior_close(provider, sym, clock.start_time.replace(tzinfo=None))
        if px is None:
            logger.warning("seed: no price for %s — skipped.", sym)
            continue
        broker.positions[sym.upper()] = DummyPosition(sym.upper(), float(qty), px)
        broker.cash -= float(qty) * px
        seeded[sym.upper()] = (float(qty), px)
    if seeded:
        logger.info("Seeded %d starting positions: %s", len(seeded), seeded)


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

    # Drive the strategy loop; the #2548 sim hook advances the clock + clears strategy_running.
    engine.start_live_strategy()
    while engine.strategy_running.is_set():
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
        n_cycles=0,
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
