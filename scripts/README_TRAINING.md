# Training Pipeline (Out-of-Sample / No Look-Ahead Bias)

Training uses **Alpaca + Polygon only** (no Yahoo Finance) to avoid look-ahead bias and survivorship issues. Data is split by **time** (train / val / test), not randomly.

## Data depth (important)

- **Alpaca:** Historical API typically provides **about 7 years** of daily data (plan-dependent). Default `TRAINING_YEARS = 7` is safe.
- **Polygon free tier:** Only **about 2 years** of history. If you use Polygon only (no Alpaca), set `TRAINING_YEARS = 2` in `config.py`.
- **Polygon paid:** Deeper history (e.g. flat files back to 2003); adjust `TRAINING_YEARS` to match your plan.
- We do **not** use Yahoo Finance in training to avoid look-ahead bias.

## Goals

- **LSTM:** Validation direction accuracy ≥ 85%.
- **Combined (LSTM + RL):** Outperform S&P over the training period and Sharpe ratio > 1.5 (evaluate via backtest after training).

## Prerequisites

- `.env` with `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, and `POLYGON_API_KEY` (for VIX and fallback).
- Run from the **ai_trading_bot** directory:
  `cd "ai_trading_bot"`

## How to start training

**One command (recommended):** from the **ai_trading_bot** folder run:

```bash
python scripts/run_training.py
```

Or double‑click **`run_training.bat`** (Windows). This runs: 1) prepare data, 2) train LSTM, 3) train RL. **LSTM and RL use the GPU automatically** when available (CUDA).

**Optional:** skip steps if you already have data or LSTM:

```bash
python scripts/run_training.py --skip-data    # use existing data/training/ and clean_training_data/
python scripts/run_training.py --skip-lstm    # use existing LSTM (v1 or v2)
```

**Run steps manually:**

1. **Prepare data (time-based splits)**
   ```bash
   python scripts/prepare_training_data.py
   ```
   - Fetches data via Alpaca and Polygon only (`allow_yfinance=False`).
   - Produces `data/training/lstm_train.npz`, `lstm_val.npz`, `lstm_test.npz`, `lstm_metadata.json`.
   - Produces `clean_training_data/all_symbols_clean.pkl` for the RL env.

2. **Train LSTM v2 (target 85%+ val accuracy, GPU if available)**
   ```bash
   python scripts/train_lstm.py
   ```
   - Reads `data/training/lstm_*.npz` and `lstm_metadata.json`.
   - Saves **LSTM v2:** `data/lstm_model_v2.pth`, `scaler_x_v2.pkl`, `scaler_y_v2.pkl`, `model_metadata_v2.json`.
   - Uses CUDA when available; logs "Using device: cuda" or "cpu".

3. **Train RL v5 (RecurrentPPO, GPU if available)**
   ```bash
   python scripts/train_rl.py
   ```
   - Requires an LSTM (v1 or v2) so the env can load it; uses `config.LSTM_MODEL_VERSION` (default v1).
   - Reads `clean_training_data/all_symbols_clean.pkl`.
   - Saves **RL v5:** `data/rl_agent_v5.zip` and `data/rl_stats_v5.pkl`.
   - Uses CUDA when available; logs "Using device: cuda" or "cpu".

## Switching model versions (test v5 / v2)

After training, you can switch the **live** bot or **simulations** to the new models without overwriting the old ones.

- **Use RL v5 (live):** set env or `config`: `RL_MODEL_VERSION=rl_agent_v5` (or in `core/strategies.py`).
- **Use RL v5 in simulations:** `SIMULATION_RL_VERSION=rl_agent_v5` (or in `config.py`).
- **Use LSTM v2:** `LSTM_MODEL_VERSION=v2` in `config.py` or env.

Example (env vars when starting the bot):

```bash
set RL_MODEL_VERSION=rl_agent_v5
set LSTM_MODEL_VERSION=v2
python -m core.engine
```

To use the **previous** models again, set `RL_MODEL_VERSION=rl_agent_v3_dsr` and `LSTM_MODEL_VERSION=v1` (or leave unset).

## Config (config.py)

- `TRAINING_YEARS` – Years of history to request (default **7** for Alpaca; use **2** if only Polygon free).
- `TRAINING_TARGET_ACCURACY_LSTM` – LSTM val accuracy target (default 0.85).
- `TRAINING_TARGET_SHARPE` – Target Sharpe for combined strategy (default 1.5).
- `TRAINING_RL_TIMESTEPS` – RecurrentPPO `total_timesteps` (default 500_000).
- `LSTM_MODEL_VERSION` – `"v1"` (default) or `"v2"` to load the corresponding LSTM.
- `RL_MODEL_VERSION` – Live RL agent: `rl_agent_v3_dsr`, `rl_agent_v5`, etc. (env or strategies).
- `SIMULATION_RL_VERSION` – RL agent used in backtests/simulations (default `rl_agent_v3_dsr`).

## Data splits (prepare_training_data.py)

- **Train:** Through 2022-12-31.
- **Val:** 2023.
- **Test:** 2024–present (for final evaluation; not used in training).

After training, run a **backtest** on the test period (or full 8 years) and compare vs SPY to check outperformance and Sharpe > 1.5.

## Cloud Training (Vertex AI)

As an alternative to local training, you can run training jobs on **Google Cloud Vertex AI** with GPU acceleration.

### Prerequisites
- GCP project with Vertex AI API enabled
- `Dockerfile.train` and `train_cloud.py` in the project root
- Training data uploaded to a GCS bucket

### Submit a Training Job
```bash
# Build and push the training image
gcloud builds submit --tag gcr.io/$PROJECT_ID/trading-bot-train ./ -f Dockerfile.train

# Submit the job
gcloud ai custom-jobs create \
    --region=us-central1 \
    --display-name="lstm-training-$(date +%Y%m%d)" \
    --worker-pool-spec=machine-type=n1-standard-4,replica-count=1,accelerator-type=NVIDIA_TESLA_T4,accelerator-count=1,container-image-uri=gcr.io/$PROJECT_ID/trading-bot-train:latest \
    --args="--epochs=50,--batch_size=64,--data_gcs_path=gs://$PROJECT_ID-ml-artifacts/data/training_data.csv,--model_dir=gs://$PROJECT_ID-ml-artifacts/models/$(date +%Y%m%d)"
```

### Monitor
```bash
gcloud ai custom-jobs list --region=us-central1
gcloud ai custom-jobs stream-logs JOB_ID --region=us-central1
```

> 📖 **Full GCP setup:** [GCP_DEPLOYMENT_GUIDE.md](../../docs/5_engineering_and_devops/GCP_DEPLOYMENT_GUIDE.md)

## Live Logs (Cloud Run)

Monitor the bot or training job logs in real-time:

```bash
gcloud beta logging tail "resource.type=cloud_run_revision AND resource.labels.service_name=aaa-backend" \
  --project=aaagents-oss \
  --format="value(timestamp,jsonPayload.message)"
```

