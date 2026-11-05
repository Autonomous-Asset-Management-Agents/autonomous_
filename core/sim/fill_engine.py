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

    # -- #3377 (ARC-E1.1): order types the CH-3 acceptance needs ----------------
    # The engine used to know exactly one shape: fill-at-submit, all-or-nothing.
    # A resting stop, a broker rejection and a partial fill could not be expressed,
    # so the protective-exit scenario was not writable at all. The three knobs below
    # live on the BROKER instance and default to "off" — with none of them set the
    # behaviour is byte-identical to before, which keeps the existing sim tests green.
    # They are test fixtures, not product switches: nothing in config.py refers to them.

    @staticmethod
    def _is_triggered(order, price: float) -> bool:
        """A resting stop fills only once the price crosses its trigger.

        SELL stop (the protective exit): triggers at or below ``stop_price``.
        BUY stop (breakout): triggers at or above it. An order without ``stop_price``
        is immediate — that is every order the engine handled before this change.
        """
        stop_price = getattr(order, "stop_price", None)
        if stop_price in (None, ""):
            return True
        stop = float(stop_price)
        return price <= stop if order.side == OrderSide.SELL else price >= stop

    def _rejects(self, order) -> bool:
        """Deterministic rejection, driven by the test, never by chance."""
        return getattr(order, "symbol", None) in getattr(
            self.broker, "sim_reject_symbols", ()
        )

    def _fill_quantity(self, order, order_qty: float) -> float:
        """Partial fill: the configured share of what is still outstanding.

        Returns the quantity to fill NOW. The remainder stays on the order, so a later
        ``process_orders`` call fills it — that is what makes a partial fill observable
        instead of a rounding artefact.
        """
        already = float(getattr(order, "filled_qty", None) or 0.0)
        outstanding = order_qty - already
        if outstanding <= 0:
            return 0.0

        ratios = getattr(self.broker, "sim_partial_fill_ratio", None)
        ratio = None
        if ratios:
            ratio = ratios.get(order.symbol) if isinstance(ratios, dict) else ratios
        if not ratio or not (0.0 < float(ratio) < 1.0):
            # Kein Teil-Fill konfiguriert: der REST geht raus, nicht die volle Menge.
            # (Bei einer unberuehrten Order ist der Rest die volle Menge — das bisherige
            # Verhalten. Nach einer Teilausfuehrung waere die volle Menge dagegen mehr,
            # als die Position noch hergibt: der Verkauf schluege fehl.)
            return outstanding
        return round(outstanding * float(ratio), 6)

    def _record_fill(self, order, qty, full_qty, fill_price, filled_orders, side_label):
        """Book a (possibly partial) fill onto the order.

        ``filled_qty`` accumulates; the status stays ``partially_filled`` until the
        whole quantity is done. Only a completed order is reported as filled, because
        that is what the poll loop in the executor waits for.
        """
        already = float(getattr(order, "filled_qty", None) or 0.0)
        total = round(already + qty, 6)
        order.filled_qty = str(total)
        order.filled_avg_price = str(fill_price)
        order.filled_at = _fill_time(self.broker.clock.current_time)
        if total + 1e-9 >= full_qty:
            order.status = "filled"
            filled_orders.append(order)
            logger.info(
                f"Simulated {side_label} fill for {order.symbol}: {total} @ {fill_price}"
            )
        else:
            order.status = "partially_filled"
            logger.info(
                f"Simulated {side_label} partial fill for {order.symbol}: "
                f"{total} of {full_qty} @ {fill_price}"
            )

    def process_orders(self, orders):
        """
        Process a list of pending orders and simulate fills.
        """
        filled_orders = []
        for order in orders:
            if order.status in ("filled", "canceled", "rejected"):
                continue

            if self._rejects(order):
                order.status = "rejected"
                # CODING_POLICY §5.6: a substitution/refusal is announced, never swallowed.
                logger.warning(
                    "SimFillEngine: order for %s rejected by the simulated broker",
                    order.symbol,
                )
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

            if not self._is_triggered(order, fill_price):
                # A resting stop whose trigger the price has not crossed. It stays on the
                # book — that is the whole point of a broker-side stop: it survives us.
                continue

            full_qty = float(order.qty)
            order_qty = self._fill_quantity(order, full_qty)
            if order_qty <= 0:
                continue
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

                    self._record_fill(
                        order, order_qty, full_qty, fill_price, filled_orders, "BUY"
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

                    self._record_fill(
                        order, order_qty, full_qty, fill_price, filled_orders, "SELL"
                    )
                else:
                    logger.warning(
                        f"Simulated SELL failed for {order.symbol}: Insufficient position"
                    )

        return filled_orders
