import os
import sys
import json
import asyncio

sys.path.append(os.path.abspath("."))
import dotenv

dotenv.load_dotenv()


async def test_all():
    print("=== A) MODELS LOAD CHECK ===")
    try:
        from models.torch_model import LSTMPredictor, LSTMConfig

        print("[OK] LSTM Module imported.")
    except Exception as e:
        print(f"[FAIL] LSTM Module: {e}")

    try:
        from models.trading_environment import TradingEnvironment

        print("[OK] RL Module imported.")
    except Exception as e:
        print(f"[FAIL] RL Module: {e}")

    print("\n=== B) ALPACA DATA CHECK ===")
    try:
        from alpaca.data.requests import StockSnapshotRequest
        from alpaca.data.historical import StockHistoricalDataClient
        import config

        client = StockHistoricalDataClient(
            config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY
        )
        req = StockSnapshotRequest(symbol_or_symbols=["AAPL"])
        snaps = client.get_stock_snapshot(req)
        print(
            f"[OK] Alpaca API Connection SUCCESS. AAPL price: {snaps['AAPL'].latest_trade.price}"
        )
    except Exception as e:
        print(f"[FAIL] Alpaca Data Error: {e}")

    print("\n=== C) ROUND TABLE CONSENSUS STATE ===")
    try:
        from core.redis_client import RedisClient
        from core.round_table.consensus import ConsensusEngine

        print(f"[OK] Consensus Engine imported.")
        r = RedisClient.get_sync_redis()
        if r:
            keys = r.keys("*")
            print(f"Total keys in Redis: {len(keys)}")

            # Print a few example keys to see what exists
            if keys:
                print(
                    f"Sample keys: {[k.decode() if isinstance(k, bytes) else k for k in keys[:5]]}"
                )

            rt_keys = r.keys("*round_table*")
            print(f"Found {len(rt_keys)} round table keys.")
            if len(rt_keys) > 0:
                state = r.get(rt_keys[0])
                try:
                    parsed = json.loads(state)
                    print(f"Example State ({rt_keys[0].decode()}):")
                    print(json.dumps(parsed, indent=2)[:500] + "...")
                except:
                    print(f"Raw data: {state}")
        else:
            print("[FAIL] Redis Client returned None.")
    except Exception as e:
        print(f"[FAIL] Redis / Round Table state check failed: {e}")


if __name__ == "__main__":
    asyncio.run(test_all())
