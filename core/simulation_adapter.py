# simulation_adapter.py
# --- FIX: Made submit_order and close_all_positions async ---
from typing import Any, Dict, List, Optional, Union

from alpaca.trading.enums import OrderSide
from alpaca.trading.requests import MarketOrderRequest


class SimulationAdapter:
    """
    Adapter to make RealisticSimulationClient compatible with existing strategy interface
    """

    def __init__(self, simulation_client):
        self.client = simulation_client

    def get_account(self):
        """Get account in format expected by strategies"""
        account = self.client.get_account()
        # Return an object with the expected attributes
        return type(
            "Account",
            (),
            {
                "equity": account.equity,
                "cash": account.cash,
                "portfolio_value": account.portfolio_value,
            },
        )()

    def list_positions(self):
        """Get positions in format expected by strategies"""
        positions = self.client.list_positions()
        adapted_positions = []

        for pos in positions:
            # Create position object with expected attributes
            market_val = float(pos.get("market_value", 0))
            avg_entry = float(pos.get("avg_entry_price", 0))
            qty = float(pos.get("qty", 0))
            unrealized_pl = (market_val - (avg_entry * qty)) if qty > 0 else 0

            position_obj = type(
                "Position",
                (),
                {
                    "symbol": pos["symbol"],
                    "qty": qty,
                    "avg_entry_price": avg_entry,
                    "market_value": market_val,
                    "side": pos.get("side", "long"),
                    "unrealized_pl": unrealized_pl,
                },
            )()
            adapted_positions.append(position_obj)

        return adapted_positions

    def get_all_positions(self):
        """Alias for alpaca-py compatibility"""
        return self.list_positions()

    def get_position(self, symbol: str) -> Optional[Any]:
        """Get specific position in expected format"""
        pos_dict = self.client.get_position(symbol)
        if pos_dict:
            market_val = float(pos_dict.get("market_value", 0))
            avg_entry = float(
                pos_dict.get("avg_entry_price", pos_dict.get("avg_price", 0))
            )
            qty = float(pos_dict.get("qty", 0))
            unrealized_pl = (market_val - (avg_entry * qty)) if qty > 0 else 0
            return type(
                "Position",
                (),
                {
                    "symbol": symbol,
                    "qty": qty,
                    "avg_entry_price": avg_entry,
                    "market_value": market_val,
                    "side": pos_dict.get("side", "long"),
                    "unrealized_pl": unrealized_pl,
                },
            )()
        return None

    def get_open_position(self, symbol: str):
        """Alias for alpaca-py compatibility"""
        return self.get_position(symbol)

    # --- FIX: Made this function async ---
    async def submit_order(
        self,
        symbol_or_request: Union[str, MarketOrderRequest],
        qty: Optional[float] = None,
        side: Optional[Union[str, OrderSide]] = None,
        **kwargs
    ):
        """Submit order with compatible interface (handles both legacy and alpaca-py request objects)"""
        if isinstance(symbol_or_request, MarketOrderRequest):
            symbol = symbol_or_request.symbol
            qty = symbol_or_request.qty
            side = symbol_or_request.side
            # Extract other fields if needed, but simulation only needs these
        else:
            symbol = symbol_or_request

        if isinstance(side, OrderSide):
            side = side.value.lower()

        # Remove trade_context as Alpaca API doesn't accept it
        kwargs.pop("trade_context", None)
        order_response = self.client.submit_order(symbol, qty, side, **kwargs)
        if order_response:
            return type(
                "Order",
                (),
                {
                    "id": order_response.get("id"),
                    "symbol": order_response.get("symbol"),
                    "qty": order_response.get("qty"),
                    "side": order_response.get("side"),
                },
            )()
        return None  # Return None on failure (like insufficient cash)

    # --- FIX: Made this function async ---
    async def close_all_positions(self, request=None, **kwargs):
        """Close all positions with alpaca-py compatible signature"""
        cancel_orders = False
        if request and hasattr(request, "cancel_orders"):
            cancel_orders = request.cancel_orders
        return self.client.close_all_positions(cancel_orders)

    def get_bars(self, symbol: str, timeframe: str, limit: int = 100):
        """Get bars with compatible interface"""
        return self.client.get_bars(symbol, timeframe, limit)

    def get_snapshots(self, symbols: List[str]) -> Dict:
        """Get snapshots in format expected by strategies"""
        snapshots = self.client.get_snapshots(symbols)
        adapted_snapshots = {}

        for symbol, snapshot in snapshots.items():
            daily_bar = snapshot["latest_trade"]
            bar_obj = type(
                "DailyBar",
                (),
                {
                    "o": daily_bar["o"],
                    "h": daily_bar["h"],
                    "l": daily_bar["l"],
                    "c": daily_bar["c"],
                    "v": daily_bar["v"],
                    "open": daily_bar["o"],
                    "high": daily_bar["h"],
                    "low": daily_bar["l"],
                    "close": daily_bar["c"],
                    "volume": daily_bar["v"],
                    "p": daily_bar["c"],
                },
            )()

            snapshot_obj = type("Snapshot", (), {"latest_trade": bar_obj})()

            adapted_snapshots[symbol] = snapshot_obj

        return adapted_snapshots

    def get_news(self, symbols: List[str]) -> List[Dict]:
        """Get news in compatible format"""
        return self.client.get_news(symbols)

    @property
    def current_date(self):
        return self.client.current_date
