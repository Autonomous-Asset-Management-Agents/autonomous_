"""VirtualLiveBroker — duck-typed alpaca ``TradingClient`` for the Alpaca Sim-Day.

#2548 H4-H6: completes the contract the LIVE execution path actually calls, so a real cycle
runs without AttributeError and without silently suppressing BUYs/SELLs:
  * ``get_account`` exposes the full attribute surface the BP/PDT gate reads (H5).
  * ``get_open_position`` RAISES ``flat_position_error()`` when flat, never returns None (H4).
  * ``get_order_by_id`` / ``get_orders`` / ``cancel_*`` / ``close_all_positions`` (dual shape) exist (H4).
  * positions carry ``unrealized_pl`` / ``unrealized_plpc`` / ``qty_available`` / ``current_price`` (H6).
  * orders carry ``submitted_at`` (+ ``filled_avg_price`` / ``filled_at`` stamped by the fill engine, H6).
"""

import logging
import uuid
from datetime import timedelta, timezone
from typing import Any, Dict, List

try:
    from core.sim.clock import get_sim_clock
    from core.sim.data_client import SimDataClient
    from core.sim.exceptions import flat_position_error
    from core.sim.fill_engine import SimFillEngine
except ImportError:  # pragma: no cover - packaged import path
    from ai_trading_bot.core.sim.clock import get_sim_clock
    from ai_trading_bot.core.sim.data_client import SimDataClient
    from ai_trading_bot.core.sim.exceptions import flat_position_error
    from ai_trading_bot.core.sim.fill_engine import SimFillEngine

logger = logging.getLogger(__name__)


class DummyAccount:
    """Duck-types alpaca Account with the FULL surface the live path reads (H5).

    Missing ``multiplier`` / ``daytrading_buying_power`` / ``pattern_day_trader`` makes the
    BP/PDT gate (strategies/base.py:224-270) throw → BUYs silently skipped; missing
    ``status`` / ``currency`` breaks the health probe + /portfolio-summary.
    """

    def __init__(self, equity: float, cash: float, last_equity: float):
        self.equity = str(equity)
        self.cash = str(cash)
        self.portfolio_value = str(equity)
        self.buying_power = str(cash)
        self.daytrading_buying_power = str(cash)
        self.multiplier = "1"
        self.pattern_day_trader = False
        self.last_equity = str(last_equity)
        self.status = "ACTIVE"
        self.currency = "USD"


class DummyClock:
    """Duck-types alpaca Clock. ``next_open`` / ``next_close`` are datetimes (``.strftime`` is
    called at trading_loop.py:497-500)."""

    def __init__(self, sim_clock):
        self.is_open = sim_clock.is_open
        self.timestamp = sim_clock.current_time
        self.next_open = sim_clock.current_time + timedelta(days=1)
        self.next_close = sim_clock.end_time


class SimOrder:
    """Duck-types the alpaca ``Order`` RESPONSE the live path gets back from ``submit_order``.

    Mutable by design: the fill engine stamps ``status`` / ``filled_qty`` /
    ``filled_avg_price`` / ``filled_at`` onto the record it finds in ``broker.orders``,
    which a frozen Pydantic request object can never carry (#2632).

    Copies whatever request fields are present rather than a fixed set, so a later request
    type (limit/stop/trailing, bracket legs) needs no change here — an unknown field is
    simply carried through, and an absent one stays absent (the live readers all use
    ``getattr(..., default)``).
    """

    # Fields the live readers touch on an order response. Copied when the request has them;
    # ``hasattr`` is False for undeclared Pydantic fields, so nothing is invented.
    _CARRIED = (
        "symbol",
        "qty",
        "notional",
        "side",
        "type",
        "order_type",
        "order_class",
        "time_in_force",
        "limit_price",
        "stop_price",
        "trail_price",
        "trail_percent",
        "client_order_id",
        "extended_hours",
        "legs",
        "position_intent",
        "take_profit",
        "stop_loss",
    )

    def __init__(self, request: Any, order_id: Any, submitted_at: Any):
        for name in self._CARRIED:
            value = getattr(request, name, None)
            if value is not None:
                setattr(self, name, value)
        # Identity + lifecycle (what the request cannot supply).
        # An inbound record that already carries an id keeps it — the seeded opening fills
        # (``runner._seed_positions``) build their own SimpleNamespace orders and the
        # entry-time reconcile pages them back by id.
        self.id = getattr(request, "id", None) or order_id
        self.status = getattr(request, "status", None) or "accepted"
        # H6: tz-aware submit time so pagination + durable entry-time reconcile work.
        self.submitted_at = submitted_at
        self.filled_qty = getattr(request, "filled_qty", None)
        self.filled_avg_price = getattr(request, "filled_avg_price", None)
        self.filled_at = getattr(request, "filled_at", None)
        # Seed marker carried through, so a record built from a seeded order keeps it and the
        # result never counts the starting book as a decision (runner._build_orders).
        self.is_seed = bool(getattr(request, "is_seed", False))

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"SimOrder(symbol={getattr(self, 'symbol', None)!r}, "
            f"qty={getattr(self, 'qty', None)!r}, side={getattr(self, 'side', None)!r}, "
            f"status={self.status!r})"
        )


class VirtualLiveBroker:
    """Duck-typed TradingClient for the Alpaca Sim-Day (name has NO 'Simulation' by design —
    strategies/base.py:140-143 would otherwise take the sim/backtest path)."""

    def __init__(self):
        logger.info("VirtualLiveBroker initialized.")
        self.clock = get_sim_clock()
        self.data_client = SimDataClient()
        self.fill_engine = SimFillEngine(self)

        self.cash = 100000.0
        self.starting_equity = 100000.0
        self.positions: Dict[str, Any] = {}
        self.orders: List[Any] = []

    # ---- account / positions -------------------------------------------------
    def _equity(self) -> float:
        equity = self.cash
        for pos in self.positions.values():
            equity += float(pos.qty) * float(pos.current_price)
        return equity

    def mark_to_market(self) -> int:
        """Re-price every HELD position from the corpus bar at the current sim time (#2632).

        Without this, ``DummyPosition`` keeps the price it was constructed with — the seed's
        prior-day close — for the entire run, and reports ``unrealized_pl/plpc = 0``. The
        ``mark()`` method existed for exactly this purpose but was never called anywhere.

        Two things were broken as a result, both found by running the first real A/B:

        1. **Equity rewarded selling.** ``_equity()`` sums ``qty * current_price``, so a SOLD
           position booked the prior→sim-day move into cash while a HELD one showed none of it.
           Measured: two arms differing only in the rotation session cap came out 90.90 apart,
           which was *exactly* the one extra exit (PG, 144.97 → 148.00, 30 shares). The P&L was
           confounded with the number of exits — the very variable under test.
        2. **Exit logic was blind.** ``core/intelligent_exit.py`` decides loss tiers and trailing
           stops from ``unrealized_plpc``. Flat forever means those paths can never fire — which
           the runbook attributed to the static daily bar, though an intraday corpus alone would
           not have fixed it.

        Fail-soft per symbol: a corpus gap or unreadable bar leaves the previous mark in place.
        Zeroing a position out would read as a total loss and could trip risk gates, and a
        pricing helper must never be the reason a sim run dies.

        Returns the number of positions re-priced (diagnostics/tests).
        """
        if not self.positions:
            return 0
        marked = 0
        for symbol, pos in list(self.positions.items()):
            if not hasattr(pos, "mark"):
                continue  # duck-typed stand-in (fixtures) — nothing to re-price
            try:
                price = self._current_price(symbol)
            except Exception as exc:  # noqa: BLE001 — never kill a run over a mark
                logger.warning(
                    "VirtualLiveBroker: mark-to-market failed for %s (%s: %.100s) — keeping "
                    "the previous price.",
                    symbol,
                    type(exc).__name__,
                    exc,
                )
                continue
            if price is None:
                continue
            pos.mark(price)
            marked += 1
        return marked

    def _current_price(self, symbol: str):
        """Latest corpus close at or before the current sim time, or None if unavailable."""
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=self.clock.current_time,
            end=self.clock.current_time,
        )
        bars = self.data_client.get_stock_bars(request).data.get(symbol) or []
        if not bars:
            return None
        try:
            return float(bars[-1].close)
        except (TypeError, ValueError):
            return None  # non-numeric bar → keep the previous mark

    def get_account(self) -> DummyAccount:
        return DummyAccount(self._equity(), self.cash, self.starting_equity)

    def get_all_positions(self) -> List[Any]:
        return list(self.positions.values())

    def get_open_position(self, symbol: str) -> Any:
        # H4: a flat symbol MUST raise (APIError code 40410000), never return None — the live SELL
        # abort + post-SELL hard-sync (order_executor.py:651/1701) key off exactly this.
        sym = symbol.upper() if isinstance(symbol, str) else str(symbol)
        pos = self.positions.get(sym)
        if pos is None:
            raise flat_position_error()
        return pos

    def get_position(self, symbol: str) -> Any:
        return self.get_open_position(symbol)

    def close_all_positions(self, *args, **kwargs) -> None:
        # H4: dual call shape — close_all_positions(cancel_orders=True) (risk_manager) AND
        # close_all_positions(ClosePositionRequest(...)) (monitor_loop). Flatten either way.
        self.positions.clear()
        for o in self.orders:
            if getattr(o, "status", None) not in ("filled", "canceled", "rejected"):
                o.status = "canceled"

    # ---- orders --------------------------------------------------------------
    def submit_order(self, order_data: Any) -> Any:
        # #2632: build a SEPARATE order record — never mutate the caller's request.
        # The old ``copy.copy(order_data)`` + ``order.id = uuid4()`` assumed the request
        # object tolerates new attributes. ``MarketOrderRequest`` is a Pydantic **v2**
        # model with 13 declared fields and no ``id``, so v2's strict ``__setattr__``
        # raised ``ValidationError: Object has no attribute 'id'`` for EVERY order —
        # the executor caught it as "Multi-tenant execution failed" and the whole sim
        # reported 0 orders / 0 fills while the decision log showed approved SELLs.
        # (Live never hit this: real Alpaca returns an Order object, it does not echo
        # the request.) A request is an INPUT; the response is a different thing.
        order = SimOrder(order_data, uuid.uuid4(), _aware(self.clock.current_time))
        self.orders.append(order)
        # getattr, not attribute access: a notional (fractional-dollar) order carries no
        # `qty`, and a diagnostic log line must never be what kills an order.
        logger.info(
            "VirtualLiveBroker order: %s qty=%s notional=%s %s",
            getattr(order, "symbol", "?"),
            getattr(order, "qty", None),
            getattr(order, "notional", None),
            getattr(order, "side", "?"),
        )
        # Fill-at-submit: the poll budget (order_executor:1082-1170) is real wall-clock.
        self.fill_engine.process_orders(self.orders)
        return order

    def get_order_by_id(self, order_id: Any) -> Any:
        for o in self.orders:
            if str(o.id) == str(order_id):
                return o
        raise flat_position_error()  # unknown id — same "not found" shape

    def get_orders(self, *args, **kwargs) -> List[Any]:
        # #2630: honour the request's status filter. entry_time_reconcile._collect_fills pages
        # get_orders(GetOrdersRequest(status=ALL)) to reconstruct the FILLED BUY history — the old
        # unconditional "exclude terminal orders" returned nothing there, so seeded/held positions had
        # no entry-time and the SELL path could not validate min-hold. Default (OPEN) is unchanged.
        req = args[0] if args else (kwargs.get("filter") or kwargs.get("request"))
        status_s = str(getattr(getattr(req, "status", None), "value", "") or "").lower()
        if status_s == "all":
            return list(self.orders)
        if status_s == "closed":
            return [
                o
                for o in self.orders
                if getattr(o, "status", None) in ("filled", "canceled", "rejected")
            ]
        return [
            o
            for o in self.orders
            if getattr(o, "status", None) not in ("filled", "canceled", "rejected")
        ]

    def cancel_orders(self, *args, **kwargs) -> None:
        for o in self.orders:
            if getattr(o, "status", None) not in ("filled", "canceled", "rejected"):
                o.status = "canceled"

    def cancel_order_by_id(self, order_id: Any) -> None:
        for o in self.orders:
            if str(o.id) == str(order_id):
                o.status = "canceled"

    # ---- assets / clock / data ----------------------------------------------
    def get_all_assets(self, *args, **kwargs) -> List[Any]:
        return [
            type("Asset", (), {"symbol": s, "tradable": True, "exchange": "SIM"})()
            for s in self.data_client._provider.symbols
        ]

    def get_clock(self) -> DummyClock:
        return DummyClock(self.clock)

    # engine.api is also used as a data client on some paths — delegate to the offline SimDataClient.
    def get_stock_bars(self, request):
        return self.data_client.get_stock_bars(request)

    def get_stock_snapshot(self, request):
        return getattr(self.data_client, "get_stock_snapshot", lambda r: {})(request)

    def get_snapshot(self, symbol):
        # NewsPoller is off in sim v1; return None so any caller degrades gracefully.
        return None


def _aware(dt):
    """Return a tz-aware datetime (UTC) — the sim clock is naive; several live readers do
    ``.astimezone`` (portfolio_context.py:107, a fail-closed anti-churn gate)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
