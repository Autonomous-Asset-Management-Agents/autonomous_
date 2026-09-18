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


## Point-in-Time Universe & Full-Universe Panel (#1907, MLR-7)

The cross-sectional retrains (#2388) require a survivorship-free, look-ahead-free
**per-day universe** and a reproducible **offline panel**. Both are built from free
sources — no paid APIs.

### 1. Rebuild the PIT membership table (open intervals)

```bash
# fetch + rebuild in one step (source: Wikipedia "List of S&P 500 companies"):
python scripts/rebuild_sp500_membership.py --fetch \
    --out data/sp500_historical_membership.csv \
    --legacy-csv data/sp500_historical_membership.csv \
    --manifest data/sp500_membership_manifest.json \
    --retrieved-at $(date +%F)

# or reproducibly from a saved snapshot (audit trail):
curl -o /tmp/sp500_wiki.html "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
python scripts/rebuild_sp500_membership.py --html /tmp/sp500_wiki.html --out data/sp500_historical_membership.csv ...
```

Commit CSV **and** manifest together. Two runs on the same snapshot are
byte-identical. Current members carry an empty `end_date` (open interval); the
manifest pins source hash, coverage and known gaps (`unresolved_open_adds`).

> **License / Attribution (CC BY-SA 4.0):** the membership table is derived from
> the Wikipedia article ["List of S&P 500 companies"](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies)
> by Wikipedia contributors, whose text content is licensed under
> [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The derived
> CSV and its manifest are redistributed **share-alike under the same license**
> with this attribution; every generated manifest carries it in
> `source_license`, and the panel manifest forwards it in
> `membership_source_license`.

### 2. Build the offline full-universe panel

```bash
python scripts/build_full_universe_panel.py --start 2019-01-02 --end $(date +%F) \
    --out-dir data/full_universe_panel
```

Writes `full_universe_panel.csv` (only symbol-days inside recorded membership —
no look-ahead entrants) plus a deterministic `panel_manifest.json`. Requires the
PIT-capable table from step 1 (a removals-only table is refused, fail-closed).

### 3. Stale/Coverage gate (default ON — conservative)

No training/benchmark on a stale panel or outside membership coverage:

```bash
python scripts/build_full_universe_panel.py --check-gate data/full_universe_panel/panel_manifest.json --as-of $(date +%F)
```

Config (BORA parity `config.py` ↔ `config.oss.py`): `PANEL_STALE_GATE_ENABLED`
(default `True`), `PANEL_MAX_AGE_DAYS` (default `5`). The gate helper is
`core/panel_stale_gate.py`; the #2388 retrain pre-stage must call it before
consuming the panel.
