import os
import sys
import logging
from datetime import datetime

# Setup paths to import from core
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from core.simulation import RealisticSimulationClient
from core.ab_testing import ABTestExperiment

# Configure logging to clearly see the output
logging.basicConfig(level=logging.INFO, format="%(message)s")

# --- Dummy Strategies for Demonstration ---


def strategy_model_a_conservative(sim_client, current_date):
    """
    Model A (Conservative): Buys if the 200-day SMA is rising and price is above it.
    Closes positions if price drops below SMA200.
    """
    for symbol in sim_client.available_symbols:
        df = sim_client.get_bars(symbol, "1D", limit=1)
        if df.empty or "sma_200d" not in df.columns:
            continue

        current_data = df.iloc[-1]
        price = current_data["close"]
        sma200 = current_data["sma_200d"]

        pos = sim_client.get_position(symbol)

        if pos is None:  # Not in position
            if price > sma200:
                # Buy 10 shares
                sim_client.submit_order(symbol, 10, "buy")
        else:  # In position
            if price < sma200:
                # Close position
                sim_client.submit_order(symbol, pos["qty"], "sell")


def strategy_model_b_aggressive(sim_client, current_date):
    """
    Model B (Aggressive): Buys momentum. Fast MACD crossovers.
    """
    for symbol in sim_client.available_symbols:
        df = sim_client.get_bars(symbol, "1D", limit=1)
        if df.empty or "macd" not in df.columns or "macd_signal" not in df.columns:
            continue

        current_data = df.iloc[-1]
        macd = current_data["macd"]
        signal = current_data["macd_signal"]

        pos = sim_client.get_position(symbol)

        if pos is None:  # Not in position
            if macd > signal:
                # Buy 20 shares (Aggressive)
                sim_client.submit_order(symbol, 20, "buy")
        else:  # In position
            if macd < signal:
                # Close position
                sim_client.submit_order(symbol, pos["qty"], "sell")


def main():
    print("\n" + "=" * 50)
    print("🚀 Initiating Model A/B Test Simulation...")
    print("=" * 50 + "\n")

    start_date = "2023-01-01"
    end_date = "2023-12-31"

    # We use a small subset (nasdaq) to make the simulation run quickly for demonstration
    symbol_sample = "nasdaq"

    # -----------------------------------------------------------------
    # RUN SIMULATION A
    # -----------------------------------------------------------------
    print("--- [1/2] Running Model A (Conservative) Sandbox ---")
    sim_a = RealisticSimulationClient(api=None, initial_cash=100000.0)

    results_a = sim_a.run_simulation(
        start_date=start_date,
        end_date=end_date,
        strategy_callback=strategy_model_a_conservative,
        symbol_sample_mode=symbol_sample,
    )
    results_a["model_name"] = "Model A (Conservative SMA)"

    # -----------------------------------------------------------------
    # RUN SIMULATION B
    # -----------------------------------------------------------------
    print("\n--- [2/2] Running Model B (Aggressive) Sandbox ---")
    sim_b = RealisticSimulationClient(api=None, initial_cash=100000.0)

    results_b = sim_b.run_simulation(
        start_date=start_date,
        end_date=end_date,
        strategy_callback=strategy_model_b_aggressive,
        symbol_sample_mode=symbol_sample,
    )
    results_b["model_name"] = "Model B (Aggressive MACD)"

    # -----------------------------------------------------------------
    # COMPARE RESULTS VIA A/B TESTING FRAMEWORK
    # -----------------------------------------------------------------
    print("\n" + "=" * 50)
    print("📊 Generating A/B Testing Report...")
    print("=" * 50 + "\n")

    experiment = ABTestExperiment(model_a_results=results_a, model_b_results=results_b)
    experiment.print_summary()


if __name__ == "__main__":
    main()
