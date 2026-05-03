# simulation.py
# --- MODIFIED: Fixed Look-Ahead Bias via Next-Day Execution Queue ---
# --- ADDED: Parallel Downloading (Threading) & Disk Caching ---
# --- ADDED: Fundamental Data Fetching (marketCap, trailingPE) ---

import logging
import pandas as pd

from core.utils import ta
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Any
import concurrent.futures
from dataclasses import dataclass, field
import os
import csv
import json
import pickle
from config import POLYGON_API_KEY

try:
    from config import SLIPPAGE_PERCENT, COMMISSION_PER_TRADE
except ImportError:
    SLIPPAGE_PERCENT = 0.001
    COMMISSION_PER_TRADE = 0.50

from core.data_provider import HistoricalDataProvider
from core.polygon_data import fetch_fundamentals as polygon_fetch_fundamentals
from alpaca.data.historical import StockHistoricalDataClient


def inject_market_noise(df: pd.DataFrame, noise_level: float = 0.02) -> pd.DataFrame:
    """
    Injects random statistical noise into OHCLV data to test model robustness.

    Args:
        df: DataFrame containing OHCLV data.
        noise_level: The standard deviation of the percentage noise to add.

    Returns:
        DataFrame with perturbed prices.
    """
    df_noisy = df.copy()

    if len(df_noisy) == 0 or noise_level <= 0:
        return df_noisy

    # Generate random multiplicative noise normally distributed around 1.0
    # Standard deviation is noise_level (e.g., 0.02 for 2%)
    noise = np.random.normal(loc=1.0, scale=noise_level, size=len(df_noisy))

    # Apply noise to the core price series
    if "close" in df_noisy.columns:
        df_noisy["close"] = df_noisy["close"] * noise

    if "open" in df_noisy.columns:
        # Generate independent noise for open to simulate gap changes
        open_noise = np.random.normal(loc=1.0, scale=noise_level, size=len(df_noisy))
        df_noisy["open"] = df_noisy["open"] * open_noise

    # Re-calculate high/low to maintain candle validity
    # High must be >= max(open, close), Low must be <= min(open, close)
    if all(col in df_noisy.columns for col in ["open", "high", "low", "close"]):
        # Add slight positive noise to highs, negative to lows
        high_noise = np.abs(
            np.random.normal(loc=0.0, scale=noise_level / 2, size=len(df_noisy))
        )
        low_noise = np.abs(
            np.random.normal(loc=0.0, scale=noise_level / 2, size=len(df_noisy))
        )

        # Ensure structural integrity of the candle
        max_px = df_noisy[["open", "close"]].max(axis=1)
        min_px = df_noisy[["open", "close"]].min(axis=1)

        # Original high could have been lower than new noisy open/close. Fix it.
        df_noisy["high"] = np.maximum(df_noisy["high"], max_px) * (1 + high_noise)
        df_noisy["low"] = np.minimum(df_noisy["low"], min_px) * (1 - low_noise)

    # Add noise to volume (must remain >= 0)
    if "volume" in df_noisy.columns:
        vol_noise = np.random.normal(loc=1.0, scale=noise_level * 2, size=len(df_noisy))
        df_noisy["volume"] = np.maximum(0, df_noisy["volume"] * vol_noise)

    return df_noisy


@dataclass
class Trade:
    symbol: str
    side: str
    qty: float
    price: float
    timestamp: datetime
    order_id: str


@dataclass
class PendingOrder:
    symbol: str
    qty: float
    side: str
    timestamp_created: datetime
    order_id: str
    trade_context: Optional[Dict] = field(default_factory=dict)


class SimulationAccount:
    def __init__(self, initial_cash: float = 100000.0):
        self.cash = initial_cash
        self.initial_cash = initial_cash
        self.positions: Dict[str, Dict] = {}
        self.equity = initial_cash
        self.portfolio_value = 0.0
        self.trade_history: List[Trade] = []

    def update_portfolio_value(self, current_prices: Dict[str, float]):
        """Update portfolio value based on current prices"""
        self.portfolio_value = 0.0
        for symbol, position in self.positions.items():
            if symbol in current_prices:
                # Use current close for portfolio valuation
                position["market_value"] = position["qty"] * current_prices[symbol]
                self.portfolio_value += position["market_value"]
            else:
                # Fallback to last known value if price missing (rare)
                pass

        self.equity = self.cash + self.portfolio_value


class NewsSimulator:
    def __init__(self):
        self.news_cache = {}

    def get_historical_news(self, symbol: str, date: datetime) -> List[Dict]:
        cache_key = f"{symbol}_{date.strftime('%Y%m%d')}"
        if cache_key in self.news_cache:
            return self.news_cache[cache_key]
        news_items = []
        # Random simulation for now - in real usage, this might query your SQL/CSV news db
        num_news = np.random.randint(0, 4)
        for i in range(num_news):
            sentiment = np.random.choice(
                ["positive", "negative", "neutral"], p=[0.3, 0.3, 0.4]
            )
            score = np.random.uniform(-1, 1) if sentiment != "neutral" else 0
            news_items.append(
                {
                    "timestamp": date.replace(hour=np.random.randint(9, 16)),
                    "headline": f"Simulated news for {symbol}",
                    "symbols": [symbol],
                    "sentiment": sentiment,
                    "sentiment_score": score,
                    "reason": f"Simulated {sentiment} news",
                }
            )
        self.news_cache[cache_key] = news_items
        return news_items


class RealisticSimulationClient:
    """
    Realistic simulation client with Parallel Downloading, Caching,
    and NEXT-DAY Execution to prevent Look-Ahead Bias.
    """

    def __init__(
        self,
        api: Optional[StockHistoricalDataClient],
        initial_cash: float = 100000.0,
        symbols: Optional[List[str]] = None,
        data_provider: Optional[HistoricalDataProvider] = None,
    ):
        self.account = SimulationAccount(initial_cash)
        self.data_provider = data_provider or HistoricalDataProvider(api=api)
        self.news_simulator = NewsSimulator()

        # Store the provided symbols list
        self.symbols = symbols or []

        self.current_date: Optional[datetime] = None
        self.simulation_data: Dict[str, pd.DataFrame] = {}
        self.available_symbols: List[str] = []
        self.date_range: List[datetime] = []
        self.current_index: int = -1

        self._order_id_counter = 0

        # --- NEW: Pending Order Queue for Next-Day Execution ---
        self.pending_orders: List[PendingOrder] = []

        self.trade_log_file = "simulation_trades.csv"
        self.equity_log_file = "simulation_equity_log.csv"
        self.prognosis_log_file = "simulation_prognosis_log.csv"

        self.cache_dir = "market_data_cache"
        os.makedirs(self.cache_dir, exist_ok=True)

        self._initialize_log_files()

        # From config for realistic backtests (slippage and commission)
        self.SLIPPAGE_PERCENT = SLIPPAGE_PERCENT
        self.COMMISSION_PER_TRADE = COMMISSION_PER_TRADE

    def _initialize_log_files(self):
        """Clears log files and writes headers."""
        try:
            with open(self.trade_log_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["Timestamp", "Symbol", "Side", "Qty", "Price", "TradeContext"]
                )
            with open(self.equity_log_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Timestamp", "Equity"])
            with open(self.prognosis_log_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "Timestamp",
                        "Symbol",
                        "Side",
                        "Prognosis_Scaled",
                        "Prognosis_Unscaled_5D_Return_Pct",
                        "Target_Date",
                        "Actual_5D_Return_Pct",
                    ]
                )
            logging.info("Simulation log files initialized.")
        except Exception as e:
            logging.error("Failed to initialize log files: %s", e)

    def _log_trade(self, trade: Trade, trade_context: Optional[Dict]):
        try:
            context_str = json.dumps(trade_context) if trade_context else "{}"
            with open(self.trade_log_file, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        trade.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        trade.symbol,
                        trade.side,
                        trade.qty,
                        trade.price,
                        context_str,
                    ]
                )
        except Exception as e:
            logging.error("Failed to log trade: %s", e)

    def _log_equity(self, timestamp: datetime, equity: float):
        try:
            with open(self.equity_log_file, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([timestamp.strftime("%Y-%m-%d %H:%M:%S"), equity])
        except Exception as e:
            logging.error("Failed to log equity: %s", e)

    def _log_prognosis(
        self, trade_context: Dict, current_date: datetime, symbol: str, side: str
    ):
        try:
            indicators = trade_context.get("indicators", {})
            prognosis_scaled = indicators.get("torch_pred_scaled")
            prognosis_unscaled = indicators.get("torch_pred_unscaled_5d_return")

            if prognosis_scaled is None or prognosis_unscaled is None:
                return

            current_price = self.simulation_data[symbol].loc[current_date]["close"]
            target_index = self.current_index + 5
            actual_return_pct_val = np.nan
            target_date_str = "N/A"

            if target_index < len(self.date_range):
                target_date = self.date_range[target_index]
                target_date_str = target_date.strftime("%Y-%m-%d")

                if target_date in self.simulation_data[symbol].index:
                    target_price = self.simulation_data[symbol].loc[target_date][
                        "close"
                    ]

                    if (
                        pd.notna(current_price)
                        and pd.notna(target_price)
                        and current_price != 0
                    ):
                        actual_return_pct_val = (
                            (target_price - current_price) / current_price
                        ) * 100

            prognosis_unscaled_pct = prognosis_unscaled * 100

            with open(self.prognosis_log_file, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        current_date.strftime("%Y-%m-%d %H:%M:%S"),
                        symbol,
                        side,
                        f"{prognosis_scaled:.6f}",
                        f"{prognosis_unscaled_pct:.4f}",
                        target_date_str,
                        (
                            f"{actual_return_pct_val:.4f}"
                            if pd.notna(actual_return_pct_val)
                            else "NaN"
                        ),
                    ]
                )
        except Exception as e:
            logging.error(f"Failed to log prognosis for {symbol}: {e}", exc_info=True)

    def _fetch_fundamentals(self, symbols: List[str]) -> Dict[str, Dict[str, float]]:
        """Fundamentals from Polygon (marketCap, trailingPE); placeholders if Polygon not configured."""
        default = {"marketCap": 0.0, "trailingPE": 0.0}
        if POLYGON_API_KEY:
            try:
                raw = polygon_fetch_fundamentals(POLYGON_API_KEY, symbols)
                return {sym: raw.get(sym, default) for sym in symbols}
            except Exception as e:
                logging.debug("Polygon fundamentals failed: %s", e)
        return {sym: default for sym in symbols}

    def _fetch_single_symbol_data(self, symbol, end_dt, days_to_load):
        date_str = end_dt.strftime("%Y%m%d")
        cache_file = os.path.join(
            self.cache_dir, f"{symbol}_{date_str}_{days_to_load}d.pkl"
        )

        if os.path.exists(cache_file):
            try:
                with open(cache_file, "rb") as f:
                    df = pickle.load(f)
                return symbol, df
            except Exception:
                pass

        try:
            df = self.data_provider.get_data(symbol, end_dt, days=days_to_load)
            if df is not None and not df.empty:
                with open(cache_file, "wb") as f:
                    pickle.dump(df, f)
            return symbol, df
        except Exception as e:
            logging.warning("Failed to download %s: %s", symbol, e)
            return symbol, pd.DataFrame()

    def load_simulation_data(
        self, start_date: str, end_date: str, symbol_sample_mode: str = "full_market"
    ) -> bool:
        try:
            sim_start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            data_start_dt = sim_start_dt - timedelta(days=500)
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            days_to_load = (end_dt - data_start_dt).days + 10  # Buffer

            # --- FIX: Nutze symbol_sample_mode Parameter ZUERST, dann Fallback auf self.symbols ---
            if symbol_sample_mode == "sp500":
                # ADR-D01: Use point-in-time membership to prevent survivorship bias.
                # get_sp500_symbols_at_date() adds historically-removed stocks (e.g. SIVB)
                # back into the backtest universe for dates when they were still members.
                logging.info(
                    "Using S&P 500 symbol sample (point-in-time for %s via sp500_historical_membership.csv).",
                    sim_start_dt.strftime("%Y-%m-%d"),
                )
                symbols = self.data_provider.get_sp500_symbols_at_date(sim_start_dt)
            elif symbol_sample_mode == "nasdaq":
                logging.info("Using NASDAQ symbol sample.")
                symbols = self.data_provider.get_nasdaq_symbols()
            elif self.symbols:
                # Verwende die Symbole, die im Konstruktor übergeben wurden
                # TODO(PR-D): Complex f-string, review manually:                 logging.info(f"Using provided symbol list: {len(self.symbols)} symbols")
                logging.info(f"Using provided symbol list: {len(self.symbols)} symbols")
                symbols = self.symbols
            else:
                logging.info("Using Full US Market symbol sample.")
                symbols = self.data_provider.get_available_symbols()

            self.available_symbols = symbols

            fundamentals_future = concurrent.futures.ThreadPoolExecutor(
                max_workers=1
            ).submit(self._fetch_fundamentals, symbols)

            # TODO(PR-D): Complex f-string, review manually:             logging.info(f"Loading price data for {len(symbols)} symbols...")
            logging.info(f"Loading price data for {len(symbols)} symbols...")
            spy_sym, spy_data = self._fetch_single_symbol_data(
                "SPY", end_dt, days_to_load
            )

            if spy_data.empty:
                logging.error("Could not load SPY data.")
                return False

            self.simulation_data["SPY"] = spy_data
            self.date_range = spy_data[spy_data.index >= sim_start_dt].index.tolist()

            if not self.date_range:
                logging.error("No SPY trading days found for simulation.")
                return False

            symbols_to_fetch = [s for s in symbols if s != "SPY"]

            batch_results = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                future_to_symbol = {
                    executor.submit(
                        self._fetch_single_symbol_data, sym, end_dt, days_to_load
                    ): sym
                    for sym in symbols_to_fetch
                }

                completed_count = 0
                total_count = len(symbols_to_fetch)

                for future in concurrent.futures.as_completed(future_to_symbol):
                    sym, df = future.result()
                    if df is not None and not df.empty and len(df) > 50:
                        batch_results[sym] = df

                    completed_count += 1
                    if completed_count % 50 == 0:
                        logging.info(
                            f"Loaded {completed_count}/{total_count} price data symbols..."
                        )

            with_data = len(batch_results) + 1  # +1 for SPY (fetched separately)
            no_data = (
                total_count + 1 - with_data
            )  # total symbols = SPY + symbols_to_fetch
            if no_data > 0:
                logging.info(
                    f"Price data: {with_data} symbols with sufficient data; {no_data} had no or insufficient data (Alpaca/Polygon/Databento)."
                )
            fundamental_data = fundamentals_future.result()

            logging.info("Pre-calculating indicators & integrating fundamentals...")
            processed_count = 0
            for sym, df in batch_results.items():
                try:
                    fund_data = fundamental_data.get(
                        sym, {"marketCap": 0.0, "trailingPE": 0.0}
                    )
                    df["marketCap"] = fund_data["marketCap"]
                    df["trailingPE"] = fund_data["trailingPE"]

                    df["rsi_14d"] = ta.rsi(df["close"], length=14)
                    df["volatility_20d"] = df["close"].rolling(window=20).std()
                    df["volume_sma_20d"] = ta.sma(df["volume"], length=20)
                    df["sma_200d"] = ta.sma(df["close"], length=200)

                    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
                    if macd is not None:
                        df["macd"] = macd.iloc[:, 0]
                        df["macd_signal"] = macd.iloc[:, 1]
                        df["macd_hist"] = macd.iloc[:, 2]

                    bbands = ta.bbands(df["close"], length=20, std=2)
                    if bbands is not None:
                        df["bb_percent"] = (df["close"] - bbands.iloc[:, 0]) / (
                            bbands.iloc[:, 2] - bbands.iloc[:, 0]
                        )
                        df["bb_width"] = (
                            bbands.iloc[:, 2] - bbands.iloc[:, 0]
                        ) / bbands.iloc[:, 1]

                    df["atr_14d"] = ta.atr(
                        df["high"], df["low"], df["close"], length=14
                    )
                    adx = ta.adx(df["high"], df["low"], df["close"], length=14)
                    if adx is not None:
                        df["adx_14d"] = adx.iloc[:, 0]

                    df["price_change_1d"] = df["close"].pct_change(1, fill_method=None)
                    df["price_change_5d"] = df["close"].pct_change(5, fill_method=None)
                    df["price_change_20d"] = df["close"].pct_change(
                        20, fill_method=None
                    )
                    df["volume_change_1d"] = df["volume"].pct_change(
                        1, fill_method=None
                    )

                    processed_count += 1
                except Exception as e:
                    logging.error("Pre-calc or integration failed for %s: %s", sym, e)

            logging.info("Data load complete. Ready: %s symbols.", processed_count)

            self.simulation_data.update(batch_results)
            self.current_index = 0
            self._initialize_log_files()

            return processed_count > 0

        except Exception as e:
            logging.error(f"Error loading simulation data: {e}", exc_info=True)
            return False

    def get_account(self) -> SimulationAccount:
        return self.account

    def list_positions(self) -> List[Dict]:
        positions = []
        for symbol, position in self.account.positions.items():
            positions.append(
                {
                    "symbol": symbol,
                    "qty": position["qty"],
                    "avg_entry_price": position["avg_price"],
                    "market_value": position.get("market_value", 0),
                    "side": "long" if position["qty"] > 0 else "short",
                }
            )
        return positions

    def get_position(self, symbol: str) -> Optional[Dict]:
        return self.account.positions.get(symbol)

    def submit_order(
        self, symbol: str, qty: float, side: str, **kwargs
    ) -> Optional[Dict]:
        """
        Submits an order.
        CRITICAL CHANGE: This now queues the order for NEXT DAY execution.
        """
        if self.current_date is None:
            return None

        # Create a PendingOrder
        order_id = f"sim_{symbol}_{self._order_id_counter}"
        self._order_id_counter += 1

        pending = PendingOrder(
            symbol=symbol,
            qty=float(qty),
            side=side,
            timestamp_created=self.current_date,
            order_id=order_id,
            trade_context=kwargs.get("trade_context", {}),
        )

        self.pending_orders.append(pending)

        # Return a "fake" order object so strategy thinks it submitted
        return {
            "id": order_id,
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "status": "accepted",  # Important: It is accepted, not filled yet
        }

    def _execute_pending_orders(self):
        """
        Called at the START of a new day.
        Executes orders queued from the previous day using TODAY'S OPEN price.
        """
        if not self.pending_orders:
            return

        orders_to_process = list(self.pending_orders)
        self.pending_orders.clear()  # Clear queue

        for order in orders_to_process:
            self._process_single_order(order)

    def _process_single_order(self, order: PendingOrder):
        symbol = order.symbol
        if symbol not in self.simulation_data:
            logging.warning("Order skipped: No data for %s", symbol)
            return

        try:
            # We are now at the START of the day 'self.current_date'.
            # We execute at the OPEN price.
            if self.current_date not in self.simulation_data[symbol].index:
                logging.warning(
                    f"Order skipped: {symbol} not trading on {self.current_date}"
                )
                return

            current_bar = self.simulation_data[symbol].loc[self.current_date]
            execution_price = current_bar["open"]

            if pd.isna(execution_price):
                return

            # Apply Slippage
            if order.side.lower() == "buy":
                execution_price = execution_price * (1 + self.SLIPPAGE_PERCENT)
            elif order.side.lower() == "sell":
                execution_price = execution_price * (1 - self.SLIPPAGE_PERCENT)

            # Check Cash
            if order.side.lower() == "buy":
                cost = (order.qty * execution_price) + self.COMMISSION_PER_TRADE
                if cost > self.account.cash:
                    logging.warning(
                        f"Insufficient cash for {symbol} (Req: {cost:.2f}, Avail: {self.account.cash:.2f}). Skipped."
                    )
                    return

            # Execute
            if order.side.lower() == "buy":
                self._process_buy_execution(symbol, order.qty, execution_price)
            elif order.side.lower() == "sell":
                self._process_sell_execution(symbol, order.qty, execution_price)

            # Log Trade (Timestamp is NOW - the execution time)
            trade = Trade(
                symbol=symbol,
                side=order.side,
                qty=order.qty,
                price=execution_price,
                timestamp=self.current_date,
                order_id=order.order_id,
            )
            self.account.trade_history.append(trade)
            self._log_trade(trade, order.trade_context)

            # Log Prognosis (if it was a predictive trade)
            if order.trade_context:
                self._log_prognosis(
                    order.trade_context, self.current_date, symbol, order.side
                )

        except Exception as e:
            logging.error("Failed to execute pending order for %s: %s", symbol, e)

    def _process_buy_execution(self, symbol: str, qty: float, price: float):
        trade_basis = qty * price
        total_cost = trade_basis + self.COMMISSION_PER_TRADE
        self.account.cash -= total_cost

        if symbol in self.account.positions:
            old_pos = self.account.positions[symbol]
            new_qty = old_pos["qty"] + qty
            total_basis = (old_pos["qty"] * old_pos["avg_price"]) + trade_basis
            new_avg = total_basis / new_qty
            self.account.positions[symbol] = {
                "qty": new_qty,
                "avg_price": new_avg,
                "market_value": new_qty * price,
            }
        else:
            self.account.positions[symbol] = {
                "qty": qty,
                "avg_price": price,
                "market_value": qty * price,
            }

    def _process_sell_execution(self, symbol: str, qty: float, price: float):
        trade_proceeds = qty * price
        net_proceeds = trade_proceeds - self.COMMISSION_PER_TRADE
        self.account.cash += net_proceeds

        if symbol not in self.account.positions:
            # Short selling (simplified)
            self.account.positions[symbol] = {
                "qty": -qty,
                "avg_price": price,
                "market_value": -qty * price,
            }
            return

        position = self.account.positions[symbol]
        if position["qty"] > 0:
            # Closing Long
            if qty >= position["qty"]:
                del self.account.positions[symbol]
            else:
                position["qty"] -= qty
                position["market_value"] = position["qty"] * price
        else:
            # Adding to Short (or closing short, simplified logic here)
            # For simplicity in this fix, we assume closing if direction opposes
            pass

    def close_all_positions(self, cancel_orders: bool = False):
        if cancel_orders:
            self.pending_orders.clear()

        logging.info("Closing all positions")
        positions_to_close = list(self.account.positions.keys())
        for symbol in positions_to_close:
            position = self.account.positions[symbol]
            qty_to_close = abs(position["qty"])
            side = "sell" if position["qty"] > 0 else "buy"
            self.submit_order(symbol, qty_to_close, side)

    def get_bars(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        if self.current_date is None or symbol not in self.simulation_data:
            return pd.DataFrame()
        try:
            data = self.simulation_data[symbol]
            # Strategy sees data UP TO the current date (Close included)
            # This is fine because the order won't execute until TOMORROW Open
            historical_data = data[data.index <= self.current_date].tail(limit)
            return historical_data
        except Exception:
            return pd.DataFrame()

    def get_snapshots(self, symbols: List[str]) -> Dict[str, Dict]:
        snapshots = {}
        if self.current_date is None:
            return snapshots
        for symbol in symbols:
            if (
                symbol in self.simulation_data
                and self.current_date in self.simulation_data[symbol].index
            ):
                try:
                    day_data = self.simulation_data[symbol].loc[self.current_date]
                    snapshot = {
                        "latest_trade": {
                            "o": day_data["open"],
                            "h": day_data["high"],
                            "l": day_data["low"],
                            "c": day_data["close"],
                            "v": day_data.get("volume", 0),
                            "p": day_data["close"],
                        }
                    }
                    snapshots[symbol] = snapshot
                except Exception:
                    pass
        return snapshots

    def get_news(self, symbols: List[str]) -> List[Dict]:
        if self.current_date is None:
            return []
        all_news = []
        for symbol in symbols:
            news_items = self.news_simulator.get_historical_news(
                symbol, self.current_date
            )
            all_news.extend(news_items)
        return all_news

    def advance_day(self) -> bool:
        """
        Advances the simulation timeline.
        1. Move index forward.
        2. Update current date.
        3. EXECUTE PENDING ORDERS (at the new Open).
        4. Update Portfolio Valuation (at the new Close).
        """
        if self.current_index >= len(self.date_range) - 1:
            if self.current_date:
                self._log_equity(self.current_date, self.account.equity)
            return False

        # 1. Log End of Previous Day Equity
        if self.current_date:
            self._log_equity(self.current_date, self.account.equity)

        # 2. Advance Time
        self.current_index += 1
        self.current_date = self.date_range[self.current_index]

        # 3. EXECUTE ORDERS from Yesterday (at Today's Open)
        self._execute_pending_orders()

        # 4. Mark-to-Market Portfolio (at Today's Close)
        current_prices = {}
        for symbol in self.account.positions.keys():
            if (
                symbol in self.simulation_data
                and self.current_date in self.simulation_data[symbol].index
            ):
                try:
                    current_prices[symbol] = self.simulation_data[symbol].loc[
                        self.current_date
                    ]["close"]
                except KeyError:
                    pass
        self.account.update_portfolio_value(current_prices)

        return True

    def run_simulation(
        self,
        start_date: str,
        end_date: str,
        strategy_callback: callable,
        symbol_sample_mode: str = "full_market",
        progress_callback: Optional[callable] = None,
    ) -> Dict[str, Any]:
        logging.info("Starting simulation from %s to %s", start_date, end_date)

        if not self.load_simulation_data(
            start_date, end_date, symbol_sample_mode=symbol_sample_mode
        ):
            return {"error": "Failed to load simulation data"}

        results = {
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": self.account.initial_cash,
            "final_equity": self.account.initial_cash,
            "total_return": 0.0,
            "trades": [],
            "daily_equity": [],
        }

        self.current_index = 0
        self.current_date = self.date_range[0]
        self._log_equity(self.current_date, self.account.initial_cash)

        total_days = len(self.date_range)

        while self.current_index < total_days:
            current_progress = (self.current_index / total_days) * 100
            if progress_callback:
                progress_callback(
                    current_progress, self.current_date.strftime("%Y-%m-%d")
                )

            try:
                # STRATEGY RUNS HERE (After orders are executed in advance_day)
                # It sees Today's Close, decides, and queues orders for Tomorrow Open.
                strategy_callback(self, self.current_date)

                results["daily_equity"].append(
                    {
                        "date": self.current_date.strftime("%Y-%m-%d"),
                        "equity": self.account.equity,
                    }
                )

            except Exception as e:
                logging.error(
                    f"Error in strategy execution on {self.current_date}: {e}"
                )

            if not self.advance_day():
                break

        results["final_equity"] = self.account.equity
        results["total_return"] = (
            (self.account.equity - self.account.initial_cash)
            / self.account.initial_cash
            * 100
        )
        results["trades"] = [trade.__dict__ for trade in self.account.trade_history]
        trades_count = len(results["trades"])

        logging.info(
            f"Simulation completed. Final equity: ${results['final_equity']:.2f}, "
            f"Return: {results['total_return']:.2f}% | Trades: {trades_count} over {len(self.date_range)} days"
        )
        if trades_count == 0:
            logging.warning(
                "No trades executed. Possible causes: RL/LSTM often output HOLD, insufficient history for state, "
                "or risk manager returned 0 size. Try LSTMDynamic strategy or check model/features."
            )

        return results
