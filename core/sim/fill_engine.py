import logging
from datetime import timezone

from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.enums import OrderSide

try:
    from core.sim.data_client import SimDataClient
except ImportError:
    from ai_trading_bot.core.sim.data_client import SimDataClient

logger = logging.getLogger(__name__)


def _fill_time(dt):
    """tz-aware fill timestamp (H6): several live readers do ``.astimezone`` / ``.date`` on it
    (daily_report.py:294, portfolio_context.py:107 — the fail-closed anti-churn gate).
    """
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


class DummyPosition:
    """Duck-types alpaca Position with the surface portfolio_manager / risk / api_routes read (H6).

    Missing ``unrealized_pl`` / ``unrealized_plpc`` / ``qty_available`` makes ``refresh_positions``
    crash (``float(pos.unrealized_pl)``) → ``_last_refresh_ok=False`` → cascades into cash-aware sizing.
    """

    def __init__(self, symbol, qty, price):
        self.symbol = symbol
        self.qty = str(qty)
        self.qty_available = str(qty)
        self.side = "long"
        self.avg_entry_price = str(price)
        self.current_price = str(price)
        self.market_value = str(float(qty) * float(price))
        self.unrealized_pl = "0"
        self.unrealized_plpc = "0"  # fraction (callers ×100) — flat at entry

    def mark(self, price):
        """Re-price to ``price`` and recompute market value + unrealized P&L (fraction)."""
        self.current_price = str(price)
        qty = float(self.qty)
        entry = float(self.avg_entry_price)
        self.market_value = str(qty * price)
        self.unrealized_pl = str((price - entry) * qty)
        self.unrealized_plpc = str((price / entry - 1.0) if entry else 0.0)


class SimFillEngine:
    """
    Simulates filling of orders for the Alpaca Sim-Day.
    """

    def __init__(self, broker):
        logger.info("SimFillEngine initialized.")
        self.broker = broker
        self.data_client = SimDataClient()

    def process_orders(self, orders):
        """
        Process a list of pending orders and simulate fills.
        """
        filled_orders = []
        for order in orders:
            if order.status == "filled":
                continue

            # Fetch the latest price for the symbol
            try:
                request = StockBarsRequest(
                    symbol_or_symbols=order.symbol,
                    timeframe=TimeFrame.Minute,
                    start=self.broker.clock.current_time,
                    end=self.broker.clock.current_time,
                )
                bars_response = self.data_client.get_stock_bars(request)
                latest_bars = bars_response.data.get(order.symbol, [])
                if not latest_bars:
                    logger.debug(
                        f"SimFillEngine: No price data available yet for {order.symbol} to fill order."
                    )
                    continue

                fill_price = float(latest_bars[-1].close)

            except Exception as e:
                logger.warning(
                    f"SimFillEngine failed to fetch price for {order.symbol}: {e}"
                )
                continue

            order_qty = float(order.qty)
            cost = order_qty * fill_price

            if order.side == OrderSide.BUY:
                if self.broker.cash >= cost:
                    self.broker.cash -= cost

                    if order.symbol in self.broker.positions:
                        pos = self.broker.positions[order.symbol]
                        new_qty = float(pos.qty) + order_qty
                        # Simplified avg entry price
                        pos.avg_entry_price = str(
                            ((float(pos.qty) * float(pos.avg_entry_price)) + cost)
                            / new_qty
                        )
                        pos.qty = str(new_qty)
                        pos.current_price = str(fill_price)
                        pos.market_value = str(new_qty * fill_price)
                    else:
                        self.broker.positions[order.symbol] = DummyPosition(
                            order.symbol, order_qty, fill_price
                        )

                    order.status = "filled"
                    order.filled_qty = str(order_qty)
                    order.filled_avg_price = str(fill_price)
                    order.filled_at = _fill_time(self.broker.clock.current_time)
                    filled_orders.append(order)
                    logger.info(
                        f"Simulated BUY fill for {order.symbol}: {order_qty} @ {fill_price}"
                    )
                else:
                    logger.warning(
                        f"Simulated BUY failed for {order.symbol}: Insufficient cash ({self.broker.cash} < {cost})"
                    )

            elif order.side == OrderSide.SELL:
                if (
                    order.symbol in self.broker.positions
                    and float(self.broker.positions[order.symbol].qty) >= order_qty
                ):
                    self.broker.cash += cost

                    pos = self.broker.positions[order.symbol]
                    new_qty = float(pos.qty) - order_qty
                    if new_qty <= 0:
                        del self.broker.positions[order.symbol]
                    else:
                        pos.qty = str(new_qty)
                        pos.current_price = str(fill_price)
                        pos.market_value = str(new_qty * fill_price)

                    order.status = "filled"
                    order.filled_qty = str(order_qty)
                    order.filled_avg_price = str(fill_price)
                    order.filled_at = _fill_time(self.broker.clock.current_time)
                    filled_orders.append(order)
                    logger.info(
                        f"Simulated SELL fill for {order.symbol}: {order_qty} @ {fill_price}"
                    )
                else:
                    logger.warning(
                        f"Simulated SELL failed for {order.symbol}: Insufficient position"
                    )

        return filled_orders
