"""VirtualLiveBroker — duck-typed alpaca ``TradingClient`` for the Alpaca Sim-Day.

#2548 H4-H6: completes the contract the LIVE execution path actually calls, so a real cycle
runs without AttributeError and without silently suppressing BUYs/SELLs:
  * ``get_account`` exposes the full attribute surface the BP/PDT gate reads (H5).
  * ``get_open_position`` RAISES ``flat_position_error()`` when flat, never returns None (H4).
  * ``get_order_by_id`` / ``get_orders`` / ``cancel_*`` / ``close_all_positions`` (dual shape) exist (H4).
  * positions carry ``unrealized_pl`` / ``unrealized_plpc`` / ``qty_available`` / ``current_price`` (H6).
  * orders carry ``submitted_at`` (+ ``filled_avg_price`` / ``filled_at`` stamped by the fill engine, H6).
"""

import copy
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
        order = copy.copy(order_data)
        if not hasattr(order, "id"):
            order.id = uuid.uuid4()
        if not hasattr(order, "status"):
            order.status = "accepted"
        # H6: stamp submit time (tz-aware) so pagination + durable entry-time reconcile work.
        order.submitted_at = _aware(self.clock.current_time)
        self.orders.append(order)
        logger.info(
            "VirtualLiveBroker order: %s %s %s",
            order.symbol,
            order.qty,
            order.side,
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
