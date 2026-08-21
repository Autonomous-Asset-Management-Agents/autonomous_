"""Sim-Day runner (#2548 #82) — boots the engine in SIM_MODE, drives the trading loop across the
window (the loop self-terminates via the #2548 sim hook), and collects a ``SimResult``.

Order records have TWO sources, deliberately (#2632):
  * the VirtualLiveBroker's order book — ground truth for what was actually submitted and
    filled (side/qty/price/status), seeded opening fills excluded;
  * the execution-outcome recorder — the "blocked:*"/"error" outcomes and their reasons,
    which never reach the broker and are what explains a low-order day.
Deriving fills from the EVENTS instead (matching a broker order per symbol off a FIFO) is what
reported a rotation SELL as the seeded opening BUY — see ``_build_orders``.
"""

import logging
import time

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
                # #2632: marks the STARTING BOOK. The entry-time reconcile must still page
                # this fill (that is why it is in broker.orders at all), but the result must
                # not count it as a decision of the replayed day — see _build_orders.
                is_seed=True,
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
    # #2693: keyed on ``current_time``, NOT ``start_time`` — in a multi-day replay the panel is
    # re-seeded on every rollover, and day D's ranking may only use bars STRICTLY BEFORE D.
    # Reading start_time would freeze the whole run on day 1's cross-section and thereby disable
    # the rotation the multi-day run exists to observe. Identical for a single-day run.
    as_of = clock.current_time
    cutoff = pd.Timestamp(as_of.replace(tzinfo=None)).normalize()
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
        get_store().record_cycle(as_of.date(), ranked)
        logger.info(
            "Sim: seeded LSTM rank panel @ %s with %d symbols (prior-day momentum) — the "
            "round table now has a validated basis (else the strict-ML gate blocks all).",
            as_of.date().isoformat(),
            len(ranked),
        )
    else:
        logger.warning(
            "Sim: LSTM panel seed produced 0 ranked symbols (no prior corpus bars) — "
            "the strict-ML gate will still block every symbol."
        )
    return len(ranked)


_TIMEOUT_PER_DAY_S = 300.0


def _effective_timeout(timeout_s, n_days: int) -> float:
    """Wall-clock budget for the run: ``300s per replayed day`` unless the caller pinned one.

    The old flat 300s was sized for a single day (#2693). A multi-day run exceeds it, and the
    timeout path still WRITES a result — so a 20-day A/B could quietly compare 6 days against 9
    with nothing in the output to show it. A truncated run that looks complete is worse than a
    failed one, hence both this scaling and the ``timed_out`` flag on the result.
    """
    if timeout_s is not None:
        return float(timeout_s)
    return _TIMEOUT_PER_DAY_S * max(1, int(n_days or 1))


def _record_equity_point(broker, day) -> None:
    """Close out one replayed day on the equity curve (#2693).

    Never raises: a missing curve point costs a data point, a crash costs the whole run.
    """
    from core.sim.recorder import get_recorder

    try:
        get_recorder().record_daily_equity(
            day, broker._equity(), broker.cash, len(broker.positions)
        )
    except Exception:  # noqa: BLE001 — bookkeeping must not kill the run
        logger.warning(
            "Sim: could not record the equity point for %s.", day, exc_info=True
        )


def _corpus_trading_days(provider) -> list:
    """The corpus's trading calendar: the sorted UNION of every symbol's bar dates (#2693).

    Union, not intersection: a symbol that starts mid-corpus (IPO, or a partial fetch) must not
    shorten the calendar for everyone else. Data-derived, so weekends and market holidays are
    absent by construction instead of being guessed from weekday arithmetic.
    """
    days = set()
    for frame in getattr(provider, "frames", {}).values():
        if frame is None or getattr(frame, "empty", True):
            continue
        try:
            days.update(ts.date() for ts in frame.index)
        except Exception:  # noqa: BLE001 — one odd frame must not lose the calendar
            continue
    return sorted(days)


def _select_replay_days(all_days: list, start, n_days: int) -> list:
    """Pick the ``n_days`` trading days to replay.

    ``start`` given  → that day and the following ``n_days - 1`` trading days.
    ``start`` None   → the LAST ``n_days`` available days. That is the only reading of
                       ``--days 20`` without a date that cannot run into a data hole: anchoring
                       at the corpus end always lands on days that have bars.

    Capped by what exists — asking for more days than the corpus carries runs what is there
    rather than inventing dates (a fabricated day would evaluate on stale prices).
    """
    if not all_days:
        return []
    n = max(1, int(n_days or 1))
    if start is None:
        return all_days[-n:]
    later = [d for d in all_days if d >= start]
    if not later:
        return [
            start
        ]  # start beyond the corpus — fail-soft to the single requested day
    return later[:n]


def _prewarm_report_imports() -> None:
    """Finish importing ``core.report`` while the process is still SINGLE-THREADED (#2632).

    Without this a sim run dies at boot with::

        _frozen_importlib._DeadlockError: deadlock detected by
        _ModuleLock('core.report.pipeline')

    Two threads first-import the same package at once: the main thread via
    ``_seed_lstm_panel`` (``from core.report.lstm_panel_store import get_store``) and the
    SpecialistRegistry refresh thread via its RPT-6 report path — the registry is started
    by the engine boot a few milliseconds earlier. CPython's per-module import lock detects
    the circular wait and raises, so the run exits 1 before cycle 1 and writes no result.

    It is a RACE (the same command succeeded on an earlier attempt), which is exactly why it
    has to be removed rather than retried: an A/B harness whose outcome depends on thread
    timing cannot decide anything. After the warm-up every later import — from any thread —
    is an uncontended ``sys.modules`` hit.

    Deliberately swallows failures: this is a boot-robustness measure, so it must never
    become the reason a sim run dies. A genuinely broken import surfaces at the real call
    site with its own error.
    """
    try:
        import core.report.lstm_panel_store  # noqa: F401 — warm parent + pipeline + module
    except Exception as exc:  # noqa: BLE001 — warm-up must never kill the run
        logger.warning(
            "Sim: report import warm-up failed (%s: %.120s) — continuing; a concurrent "
            "first import may still deadlock at boot.",
            type(exc).__name__,
            exc,
        )


def run_sim(timeout_s: float = None, seed_positions: dict = None):
    """Replay SIM_DAYS trading day(s) and return a SimResult. Reads SIM_* from config.

    ``timeout_s=None`` (the default) scales the wall-clock budget with the day count — see
    ``_effective_timeout``. Passing a value pins it.
    """
    from config import get_config
    from core.sim.clock import get_sim_clock, reset_sim_clock
    from core.sim.recorder import get_recorder
    from core.sim.result import SimResult

    # MUST precede the engine boot below — see _prewarm_report_imports (import-lock race).
    _prewarm_report_imports()

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
    # #2693 — MULTI-DAY calendar, set BEFORE anything reads the clock's dates. The clock is built
    # before the data client exists, so the trading days can only be handed over here — but it must
    # happen before ``_seed_positions``, which prices the starting book off ``clock.start_time``.
    # The calendar comes from the CORPUS, so weekends and market holidays are absent by
    # construction rather than guessed. SIM_DAYS default 1 → nothing is handed over and the clock
    # stays byte-identical to the single-day harness.
    _n_days = 1
    try:
        _requested = max(1, int(getattr(cfg, "SIM_DAYS", 1) or 1))
    except (TypeError, ValueError):
        _requested = 1
    if _requested > 1:
        _all_days = _corpus_trading_days(broker.data_client._provider)
        # An explicit --date anchors the START; without one, "the LAST N corpus days" is the only
        # reading that cannot land outside the data. The clock's own default (yesterday) must NOT
        # win here — the corpus may well not carry it, which is exactly how the first multi-day
        # run ended up replaying a single empty day.
        _start = (
            clock.start_time.date()
            if str(getattr(cfg, "SIM_DATE", "")).strip()
            else None
        )
        _n_days = clock.set_trading_days(
            _select_replay_days(_all_days, _start, _requested)
        )

    starting_equity = float(broker._equity())
    if seed_positions:
        _seed_positions(broker, seed_positions, clock)
        starting_equity = float(broker._equity())  # re-mark after seeding
    symbols = list(broker.data_client._provider.symbols)
    logger.info(
        "Sim boot OK — %d corpus symbols, window %s, %d day(s).",
        len(symbols),
        cfg.SIM_WINDOW,
        _n_days,
    )

    # Per-day bookkeeping on every rollover: close out the ENDING day's equity point and re-seed
    # the rank panel for the NEW day (day D may only use bars strictly before D). Registered as a
    # clock hook so the trading loop stays free of sim bookkeeping.
    # Ordering matters: the hook runs inside ``advance()``, i.e. BEFORE the loop's next
    # ``mark_to_market()``, so the book is still priced at the ending day's close — which is
    # exactly the equity that day should report.
    _day_state = {"day": clock.start_time.date()}

    def _on_new_day(new_day):
        _record_equity_point(broker, _day_state["day"])
        _day_state["day"] = new_day
        _seed_lstm_panel(broker, clock)

    clock.on_new_day = _on_new_day

    # #2632 last-mile: seed the LSTM rank panel BEFORE the first cycle so the round table
    # has a validated basis to vote on. Without it the panel is empty (the sim never runs
    # the live update_lstm_rankings producer) → LSTMSignalAgent abstains → the strict-ML
    # gate hard-blocks every symbol → 0 orders. One sim day = one panel date; a multi-day run
    # gets one per day via the rollover hook above, which is what makes the rank smoothing
    # (#2680, median over snapshot dates) testable at all.
    _seed_lstm_panel(broker, clock)

    # #2632 (P2b) — OFFLINE CONTRACT, stated here because a reader of this harness must be
    # able to trust it without grepping the engine: the boot above goes through
    # ``BotEngine.start_live_strategy``, which starts the **LatencyWatchdog**. That watchdog
    # actively pings the real Alpaca clock endpoint. Under SIM_MODE it now returns early
    # (``latency_watchdog.start`` → ``_sim_mode()``), so a sim run opens NO socket for it and
    # cannot be perturbed — or halted — by network weather. Together with the specialist's
    # offline degradation (#2663) the pinned corpus is the only data source, which is what
    # makes two arms of an A/B comparable. The PASSIVE latency path stays active; it only
    # records what a real order produced, and sim orders go through the virtual broker.

    # Drive the strategy loop. The #2548 sim hook advances the clock each cycle and, at the
    # window's end, sets ``engine._sim_complete``. Wait on THAT — never on ``strategy_running``,
    # which the monitor's initial strategy switch (None→RLAgent) transiently clears mid-run
    # (that clear was mistaken for "sim done" → the 0-cycle premature exit, #2630).
    import threading

    engine._sim_complete = threading.Event()
    engine._sim_cycles = 0
    budget = _effective_timeout(timeout_s, _n_days)
    timed_out = False
    engine.start_live_strategy()
    while not engine._sim_complete.is_set():
        time.sleep(0.2)
        if time.time() - t0 > budget:
            logger.warning(
                "Sim timeout (%.0fs) after %d/%d day(s) — stopping. The result is marked "
                "timed_out; do NOT compare it against a complete run.",
                budget,
                getattr(clock, "days_completed", 1),
                _n_days,
            )
            timed_out = True
            engine.strategy_running.clear()
            break

    # #2693 — shut the engine down BEFORE returning. Its threads (monitor loop, specialist
    # refresh, daily-report scheduler) are daemons that keep logging; when run_sim returned while
    # they were mid-cycle, the interpreter finalised underneath them and the process died with
    #   Fatal Python error: _enter_buffered_busy ... at interpreter shutdown
    # after the result had already been written — exit 127 on a run that had actually SUCCEEDED.
    # Any CI step or script checking $? would read that as a failure.
    try:
        engine.stop_strategy()
    except Exception:  # noqa: BLE001 — a shutdown hiccup must not lose a finished run
        logger.warning("Sim: engine shutdown raised — continuing.", exc_info=True)

    orders = _build_orders(get_recorder().events, broker.orders)
    end_positions = {s: float(p.qty) for s, p in broker.positions.items()}
    # #2632: final mark before reading equity. The per-cycle marks happen in the sim driver, but
    # the loop exits right after the last advance, so the closing book must be re-priced here —
    # otherwise a position held to the end is still valued at its entry price and the reported
    # P&L silently favours whichever arm sold more.
    try:
        broker.mark_to_market()
    except Exception:  # noqa: BLE001 — never fail the result over a mark
        logger.warning(
            "Sim: final mark-to-market failed — equity may be stale.", exc_info=True
        )
    ending_equity = float(broker._equity())
    # #2693: close out the LAST day. The loop exits right after the final advance, so no rollover
    # hook fires for it. ``record_daily_equity`` overwrites a same-date point, which is exactly
    # why that rule exists — re-recording the closing day after the final mark is safe.
    _record_equity_point(broker, _day_state["day"])
    daily_equity = list(get_recorder().daily_equity)

    return SimResult(
        # FIRST replayed day, not the clock's current one — after a multi-day run the clock has
        # advanced to the last day, and reporting that as "sim_date" would mislabel the run.
        sim_date=str(
            daily_equity[0]["date"] if daily_equity else clock.start_time.date()
        ),
        window=cfg.SIM_WINDOW,
        cadence=cfg.SIM_CADENCE,
        symbols=symbols,
        orders=orders,
        end_positions=end_positions,
        starting_equity=starting_equity,
        ending_equity=ending_equity,
        n_cycles=int(getattr(engine, "_sim_cycles", 0)),
        wall_time_s=round(time.time() - t0, 1),
        daily_equity=daily_equity if _n_days > 1 else [],
        timed_out=timed_out,
    )


def _build_orders(events, broker_orders):
    """Rows for the result: the broker's order book (what happened) + the outcome events
    that never reached the broker (why something did not happen).

    #2632 — the previous version walked the EVENTS and pulled a matching broker order per
    symbol off a FIFO (``queue.pop(0)``). Two ways that reported fiction:

      * the SEEDED opening BUY sits at the head of that queue, so the first event for a
        seeded symbol was answered with the seed. A real rotation SELL of AAPL (filled at
        309.335) was reported as ``side='BUY', fill_price=303.41`` — an EXIT shown as an
        ENTRY, on the very axis this harness exists to compare;
      * a broker order with no matching event was dropped entirely, and a symbol traded
        twice in one window could only ever produce one row.

    Ground truth is therefore the broker: it is the component that actually accepted and
    filled the orders. Events remain the (only) source for blocked/other outcomes, which by
    definition have no broker order. Seeds are excluded — they are the starting book, not
    decisions of the replayed day, and counting them would inflate ``n_orders``/``n_filled``
    and make two arms with different seeds incomparable.
    """
    orders = []
    for o in broker_orders:
        if getattr(o, "is_seed", False):
            continue  # starting book, not a decision of the replayed day
        status = str(getattr(o, "status", "") or "accepted")
        orders.append(
            {
                "symbol": str(getattr(o, "symbol", "?")).upper(),
                "side": _side(getattr(o, "side", None)),
                "qty": _qty(o),
                "status": status,
                "fill_price": _fill_price(o),
                "blocked_reason": None,
                # churn A/B evidence (#1957 guard hardening): fill timestamps let
                # core.sim.churn_report detect same-minute pairs + <1d roundtrips.
                # Additive keys - result compare ignores them; None-safe downstream.
                "submitted_at": _iso_or_none(getattr(o, "submitted_at", None)),
                "filled_at": _iso_or_none(getattr(o, "filled_at", None)),
            }
        )

    for ev in events:
        code = ev["outcome"]
        if code == "executed":
            continue  # already represented by the broker row above (with its true side)
        rec = {
            "symbol": str(ev["symbol"]).upper(),
            "side": None,
            "qty": None,
            "status": code,
            "fill_price": None,
            "blocked_reason": None,
        }
        if code.startswith("blocked:"):
            rec["status"] = "blocked"
            rec["blocked_reason"] = code + (
                f" — {ev['reason']}" if ev["reason"] else ""
            )
        else:
            rec["blocked_reason"] = ev["reason"] or None
        orders.append(rec)
    return orders


def _iso_or_none(value):
    """ISO-8601 string for a datetime-ish value, else None (never crash the result)."""
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _qty(o):
    """Filled quantity if the order filled, else the requested quantity (never crash on a
    notional order, which carries no ``qty``)."""
    for attr in ("filled_qty", "qty"):
        raw = getattr(o, attr, None)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None
