"""Per-position risk-exit evaluation (#2066, Increment 1).

Pure, side-effect-free wrapper around ``intelligent_exit.analyze_exit``: given a held
position's broker-authoritative cost basis (``avg_entry_price``) and reconciled
``entry_time`` (#2046), decide whether a risk-driven exit (hard stop / loss-pressure /
trailing stop) should fire — independent of the round-table consensus.

This module does NOT execute anything and is not yet wired into the trading loop
(Increments 2/3 add the loop hook + compliance-gated execution). Keeping it pure makes
the stop logic unit-testable in the deterministic agent loop without broker mocks.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.intelligent_exit import ExitAnalysis, PositionContext, analyze_exit


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a broker position object or a dict (defensive)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def evaluate_position_stop(
    symbol: str,
    current_price: float,
    avg_entry_price: Optional[float],
    entry_time: Optional[datetime],
    high_water_mark: Optional[float] = None,
    now: Optional[datetime] = None,
) -> Optional[ExitAnalysis]:
    """Return the ``ExitAnalysis`` if a risk-exit should fire for this position, else None.

    Args:
        symbol:           Ticker.
        current_price:    Latest market price.
        avg_entry_price:  Cost basis from the broker position (Alpaca ``avg_entry_price``).
        entry_time:       Position entry time (durable, reconciled — #2046).
        high_water_mark:  Peak price since entry (for the trailing stop); defaults to
                          ``max(avg_entry_price, current_price)`` when unknown.
        now:              Injectable clock (defaults to ``datetime.now(timezone.utc)``).

    Defensive: missing/invalid cost basis (``avg_entry_price`` ``None`` or ``<= 0``), a
    missing ``entry_time``, or a non-positive ``current_price`` returns ``None`` — never a
    false stop on incomplete data.

    #2556: a fabricated ``current_price`` of ``0.0`` (a bad snapshot / an upstream price
    attribute that defaulted to zero) would otherwise read as a -100% loss and trip the
    hard stop, force-selling a healthy position at market. Guarding it here is a one-sided
    fail-safe: a bad price can only SUPPRESS an exit, never manufacture one.
    """
    if avg_entry_price is None or avg_entry_price <= 0:
        return None
    if entry_time is None:
        return None
    if current_price is None or current_price <= 0:
        logging.warning(
            "position_stop: skipping %s — non-positive current_price %r "
            "(bad snapshot / fabricated 0.0); no false stop fired.",
            symbol,
            current_price,
        )
        return None

    now = now or datetime.now(timezone.utc)
    entry = entry_time if entry_time.tzinfo else entry_time.replace(tzinfo=timezone.utc)
    hours_held = max(0.0, (now - entry).total_seconds() / 3600.0)

    hwm = (
        high_water_mark
        if (high_water_mark is not None and high_water_mark > 0)
        else max(float(avg_entry_price), float(current_price))
    )

    ctx = PositionContext(
        symbol=symbol,
        entry_price=float(avg_entry_price),
        current_price=float(current_price),
        high_water_mark=float(hwm),
        hours_held=hours_held,
        entry_time=entry,
    )
    analysis = analyze_exit(ctx)
    return analysis if analysis.should_sell else None


def plan_position_stops(
    positions: List[Any],
    trade_history: Dict[str, List[datetime]],
    high_water_marks: Optional[Dict[str, float]] = None,
    entry_times: Optional[Dict[str, datetime]] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Plan risk-exit SELLs for a set of held broker positions (#2066, Increment 2).

    Pure — no I/O, no execution. For each position (broker object or dict) with a real
    quantity, resolve the entry time and run ``evaluate_position_stop``. Returns one plan
    dict per triggered position: ``{symbol, qty, avg_entry_price, current_price, reason}``.
    Positions with qty<=0, no cost basis or no entry time are skipped (no false stop).
    The caller (trading loop) turns each plan into a SELL SignalEvent through the
    compliance-gated executor and excludes the symbol from the consensus that cycle.

    Entry-time source (#2555): ``entry_times[symbol]`` — the loop's per-cycle map of the
    CURRENT holding's entry (reset when the position goes flat, seeded from the #1994
    fill-reconcile) — takes precedence. Only when a symbol is unmapped do we fall back to
    ``min(trade_history[symbol])``. ``min()`` returns the OLDEST trade in the 30-day
    window, which goes stale across a sold-then-rebought symbol (``[old_entry, sell,
    new_buy]`` → ``min`` = old_entry) → inflated ``hours_held`` → panic-protection bypass
    and a tightened loss-cut on what is really a fresh position.
    """
    plans: List[Dict[str, Any]] = []
    for pos in positions or []:
        symbol = _field(pos, "symbol")
        qty = _f(_field(pos, "qty"))
        if not symbol or qty <= 0:
            continue
        avg = _f(_field(pos, "avg_entry_price"))
        current = _f(_field(pos, "current_price"))
        times = trade_history.get(symbol) if trade_history else None
        # #2555: prefer the reconciled current-holding entry; fall back to legacy min().
        entry_time = (entry_times or {}).get(symbol) or (min(times) if times else None)
        # #desktop-exit: a REAL remembered peak (from the loop's ratcheting map) so the trailing-stop
        # factor can fire on a winner that rolled over. None (flag OFF / not tracked) → evaluate_position_stop
        # defaults to max(entry,current)=current → drawdown 0 → byte-identical to the #2066 behaviour.
        hwm = (high_water_marks or {}).get(symbol)
        analysis = evaluate_position_stop(
            symbol=symbol,
            current_price=current,
            avg_entry_price=avg,
            entry_time=entry_time,
            high_water_mark=hwm,
            now=now,
        )
        if analysis is not None:
            plans.append(
                {
                    "symbol": symbol,
                    "qty": qty,
                    "avg_entry_price": avg,
                    "current_price": current,
                    "reason": analysis.reason,
                }
            )
    return plans
