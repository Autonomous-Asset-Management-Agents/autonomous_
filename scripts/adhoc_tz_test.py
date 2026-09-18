import pandas as pd

from core.analysis.attribution.lstm_offline import (
    LstmOfflineScorer,
    _pit_day_end,
    load_lstm_bundle,
)
from core.analysis.attribution.replay import ReplayBarsProvider


def main():
    data_dir = r"C:\Users\andre\AppData\Local\autonomous\app\ai_trading_bot\market_data_cache"  # noqa: E501
    model_dir = (
        r"C:\Users\andre\AppData\Local\autonomous\app\ai_trading_bot\data"  # noqa: E501
    )

    provider = ReplayBarsProvider(data_dir)
    bundle = load_lstm_bundle(model_dir)
    scorer = LstmOfflineScorer(bundle)

    date = pd.Timestamp("2026-02-23")
    symbols = ["WDC", "STX", "DDOG"]

    as_of_norm = date.normalize()
    print(
        f"Testing date: {date}, as_of_norm={as_of_norm} (tz={as_of_norm.tz})"
    )  # noqa: E501

    for symbol in symbols:
        hist = provider.get_data(
            symbol, _pit_day_end(date), scorer.history_days
        )  # noqa: E501
        if hist is None or len(hist) == 0:
            print(f"[{symbol}] hist is None or empty")
            continue
        hist_max = pd.Timestamp(hist.index.max())
        hist_max_norm = hist_max.normalize()
        print(f"[{symbol}] hist_max={hist_max} (tz={hist_max.tz})")
        print(
            f"[{symbol}] hist_max_norm={hist_max_norm} (tz={hist_max_norm.tz})"  # noqa: E501
        )
        print(f"[{symbol}] Match? {hist_max_norm == as_of_norm}")
        print(
            f"[{symbol}] Fix Match? {hist_max_norm.tz_localize(None) == as_of_norm.tz_localize(None)}"  # noqa: E501
        )


if __name__ == "__main__":
    main()
