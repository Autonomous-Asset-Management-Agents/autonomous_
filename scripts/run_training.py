# run_training.py
# Runs the full training pipeline in order: prepare_data -> train_lstm -> train_rl.
# Uses GPU for LSTM and RL when available. Run from "ai_trading_bot" directory.
#
# Usage:  python scripts/run_training.py
# Or:     python scripts/run_training.py --skip-data   # if data already prepared
#        python scripts/run_training.py --skip-lstm    # if LSTM already trained

import argparse
import os
import subprocess
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)


def run(cmd, name):
    print("\n" + "=" * 60)
    print(f"  {name}")
    print("=" * 60)
    ret = subprocess.run(cmd)
    if ret.returncode != 0:
        print(f"\n[FAILED] {name} exited with code {ret.returncode}")
        sys.exit(ret.returncode)
    print(f"\n[OK] {name} finished.\n")


def main():
    ap = argparse.ArgumentParser(
        description="Run full training pipeline (prepare_data -> train_lstm -> train_rl)"
    )
    ap.add_argument(
        "--skip-data",
        action="store_true",
        help="Skip prepare_training_data.py (use existing data)",
    )
    ap.add_argument(
        "--skip-lstm",
        action="store_true",
        help="Skip train_lstm.py (use existing LSTM)",
    )
    args = ap.parse_args()

    python = sys.executable
    scripts = os.path.join(_PROJECT_ROOT, "scripts")

    if not args.skip_data:
        run(
            [python, os.path.join(scripts, "prepare_training_data.py")],
            "1. Prepare training data (Alpaca/Polygon)",
        )
    else:
        print("Skipping prepare_training_data.py (--skip-data)")

    if not args.skip_lstm:
        run(
            [python, os.path.join(scripts, "train_lstm.py")],
            "2. Train LSTM (GPU if available)",
        )
    else:
        print("Skipping train_lstm.py (--skip-lstm)")

    run(
        [python, os.path.join(scripts, "train_rl.py")],
        "3. Train RL agent (GPU if available)",
    )

    print("=" * 60)
    print("  All steps completed. Models saved in data/")
    print("=" * 60)


if __name__ == "__main__":
    main()
