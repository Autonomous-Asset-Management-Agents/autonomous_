# torch_model.py
import gc
import json
import logging
import os
from datetime import datetime, timedelta

import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset

from core.utils import ta

# Vertex AI Logging imports
try:
    from core.vertex_experiment import (
        end_vertex_experiment,
        init_vertex_experiment,
        log_vertex_metrics,
        log_vertex_params,
    )
except ImportError:

    def init_vertex_experiment(*args, **kwargs):
        pass

    def log_vertex_params(*args, **kwargs):
        pass

    def log_vertex_metrics(*args, **kwargs):
        pass

    def end_vertex_experiment(*args, **kwargs):
        pass


# Import config and data provider
import config
from core.data_provider import HistoricalDataProvider

# --- Constants (paths under project data dir) ---
DATA_DIR = getattr(
    config, "DATA_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
MODEL_FILE_NAME = os.path.join(DATA_DIR, "lstm_model.pth")
SCALER_X_FILE_NAME = os.path.join(DATA_DIR, "scaler_x.pkl")
SCALER_Y_FILE_NAME = os.path.join(DATA_DIR, "scaler_y.pkl")
MODEL_METADATA_FILE = os.path.join(DATA_DIR, "model_metadata.json")
GEMINI_AVAILABLE = config.GEMINI_AVAILABLE


class FeatureGenerationError(RuntimeError):
    """Raised when create_live_features() cannot produce a valid feature set.

    ADR-SEC-03: Callers MUST catch this and mark the agent vote as
    'abstention' (score=0.5, weight=0) rather than injecting a dummy
    score of 0.0 into the ConsensusEngine, which would silently bias
    the Round Table towards SELL.
    """

    pass


def get_lstm_paths(version=None):
    """Return (model_path, scaler_x_path, scaler_y_path, metadata_path) for the given LSTM version.
    version: 'v1' (default/original) or 'v2' (new training). If None, uses config.LSTM_MODEL_VERSION.
    """
    v = version or getattr(config, "LSTM_MODEL_VERSION", "v1")
    if v == "v2":
        return (
            os.path.join(DATA_DIR, "lstm_model_v2.pth"),
            os.path.join(DATA_DIR, "scaler_x_v2.pkl"),
            os.path.join(DATA_DIR, "scaler_y_v2.pkl"),
            os.path.join(DATA_DIR, "model_metadata_v2.json"),
        )
    return (
        MODEL_FILE_NAME,
        SCALER_X_FILE_NAME,
        SCALER_Y_FILE_NAME,
        MODEL_METADATA_FILE,
    )


def resolve_sequence_length(metadata, fallback=60, context="LSTM"):
    """#1878: the serve-side sequence length MUST come from the model's own metadata.

    The shipped v1 model was trained/validated with sequence_length=20
    (data/model_metadata.json) while the serve code hardcoded 60 — every live
    prediction ran in an unvalidated window configuration. Trust the metadata;
    fall back (with a WARNING, never DEBUG — CODING_POLICY §5.6) only when the
    field is missing or invalid.

    Args:
        metadata: parsed model_metadata dict (may be None).
        fallback: window to use when metadata carries no valid value.
        context: log prefix identifying the calling strategy.
    """
    seq = (metadata or {}).get("sequence_length")
    if isinstance(seq, int) and not isinstance(seq, bool) and seq > 0:
        return seq
    logging.warning(
        "%s: model metadata has no valid 'sequence_length' (got %r) — falling back "
        "to %d; serving may run in an unvalidated window configuration (#1878).",
        context,
        seq,
        fallback,
    )
    return fallback


# --- Feature Engineering ---

# #1878 review (Finding 1): minimum raw-history rows before create_live_features()
# output carries real signal. Why 50: sma_50 (below) needs 50 closes — the slowest
# indicator in this pipeline — and MACD(12, 26, 9) needs 26 + 9 = 35; with fewer
# rows the fillna() placeholder defaults dominate the feature matrix. Serve paths
# MUST gate history length on max(sequence_length, FEATURE_WARMUP_ROWS): a small
# metadata window (v1 ships sequence_length=20) must never allow inference — and
# therefore BUYs — on placeholder features.
FEATURE_WARMUP_ROWS = 50


def z_to_return(scaler_y, z, context="LSTM"):
    """#1878 Fix 2 — map a StandardScaler z-score to a real 5-day return via
    ``return = z * scaler_y.scale_ + scaler_y.mean_``. Strictly monotone
    (scale_ > 0): converting BOTH the prediction AND every comparison threshold
    keeps each buy/sell decision byte-identical (behavior-preserving) while the
    reported value + thresholds become real returns.

    BORA: pure numpy affine — identical on Desktop (SQLite) and Enterprise
    (Postgres/Redis/Cloud Run). Degrades to identity + WARNING (never crashes)
    when scaler_y is missing (older bundle)."""
    if scaler_y is None:
        logging.warning(
            "%s: scaler_y missing — z_to_return degrades to identity "
            "(prediction stays a z-score); reload the model bundle.",
            context,
        )
        return float(z)
    return float(z * scaler_y.scale_[0] + scaler_y.mean_[0])


def create_live_features(df: pd.DataFrame):
    """
    ROBUST Feature Engineering for better prediction accuracy (80%+ target).
    Now includes advanced technical indicators with comprehensive error handling.
    """
    try:
        df = df.copy()

        # --- FIX: Fill missing Sentiment with 0 (Neutral) instead of crashing ---
        if "market_news_sentiment" not in df.columns:
            df["market_news_sentiment"] = 0.0
        df["market_news_sentiment"] = df["market_news_sentiment"].fillna(0.0)

        # --- FIX: Fill VIX with 20 (Average) if missing ---
        if "vix" not in df.columns:
            df["vix"] = 20.0
        df["vix"] = df["vix"].fillna(20.0)

        # Ensure no zero/NaN values in critical columns
        cols_to_check = ["open", "high", "low", "close", "volume"]
        for col in cols_to_check:
            if col not in df.columns:
                return None
            df[col] = (
                df[col].ffill().fillna(0.001)
            )  # Use 0.001 instead of 0 to avoid division issues

        # === 1. VOLATILITY & RETURNS (Enhanced) ===
        df["log_ret"] = (
            np.log(df["close"] / df["close"].shift(1))
            .fillna(0)
            .replace([np.inf, -np.inf], 0)
        )
        df["volatility_20"] = df["log_ret"].rolling(window=20).std().fillna(0)
        df["volatility_5"] = df["log_ret"].rolling(window=5).std().fillna(0)
        df["returns_cumsum_5"] = df["log_ret"].rolling(5).sum().fillna(0)
        df["returns_std_5"] = df["log_ret"].rolling(5).std().fillna(0)

        # === 2. MOMENTUM INDICATORS (Enhanced) ===
        rsi14 = ta.rsi(df["close"], length=14)
        df["rsi_14"] = rsi14.fillna(50) if rsi14 is not None else 50.0

        rsi7 = ta.rsi(df["close"], length=7)
        df["rsi_7"] = rsi7.fillna(50) if rsi7 is not None else 50.0

        df["rsi_overbought"] = (df["rsi_14"] > 70).astype(int)
        df["rsi_oversold"] = (df["rsi_14"] < 30).astype(int)

        macd = ta.macd(df["close"])
        if macd is not None and "MACD_12_26_9" in macd.columns:
            df["macd"] = macd["MACD_12_26_9"].fillna(0)
            df["macd_signal"] = macd["MACDs_12_26_9"].fillna(0)
            df["macd_hist"] = (df["macd"] - df["macd_signal"]).fillna(0)
        else:
            df["macd"] = 0.0
            df["macd_signal"] = 0.0
            df["macd_hist"] = 0.0

        # === 3. TREND INDICATORS (Enhanced) ===
        adx = ta.adx(df["high"], df["low"], df["close"], length=14)
        if adx is not None and "ADX_14" in adx.columns:
            df["adx_14"] = adx["ADX_14"].fillna(20)
        else:
            df["adx_14"] = 20.0

        # Moving averages for trend identification
        sma20 = ta.sma(df["close"], length=20)
        df["sma_20"] = (
            sma20.fillna(df["close"].mean())
            if sma20 is not None
            else df["close"].mean()
        )

        sma50 = ta.sma(df["close"], length=50)
        df["sma_50"] = (
            sma50.fillna(df["close"].mean())
            if sma50 is not None
            else df["close"].mean()
        )

        # Avoid division by zero
        df["price_vs_sma20"] = (
            ((df["close"] - df["sma_20"]) / df["sma_20"].clip(lower=0.001))
            .fillna(0)
            .replace([np.inf, -np.inf], 0)
        )
        df["sma_slope_20"] = df["sma_20"].diff(5).fillna(0)

        # === 4. MEAN REVERSION SIGNALS (New) ===
        bbands = ta.bbands(df["close"], length=20, std=2)
        if bbands is not None:
            bb_cols = [col for col in bbands.columns if "BB" in col]
            if len(bb_cols) >= 3:
                df["bb_upper"] = bbands[bb_cols[0]].fillna(df["close"].max())
                df["bb_lower"] = bbands[bb_cols[2]].fillna(df["close"].min())
            else:
                df["bb_upper"] = df["close"] * 1.02
                df["bb_lower"] = df["close"] * 0.98
        else:
            df["bb_upper"] = df["close"] * 1.02
            df["bb_lower"] = df["close"] * 0.98

        # Safe division for bb_position
        bb_range = (df["bb_upper"] - df["bb_lower"]).clip(lower=0.001)
        df["bb_position"] = (
            ((df["close"] - df["bb_lower"]) / bb_range).fillna(0.5).clip(0, 1)
        )
        df["bb_width"] = (
            (bb_range / df["close"].clip(lower=0.001))
            .fillna(0)
            .replace([np.inf, -np.inf], 0)
        )

        # === 5. VOLUME ANALYSIS (New) ===
        df["volume_sma_20"] = (
            df["volume"].rolling(20).mean().fillna(df["volume"].mean())
        )
        df["volume_ratio"] = (
            (df["volume"] / df["volume_sma_20"].clip(lower=0.001))
            .fillna(1)
            .replace([np.inf, -np.inf], 1)
        )
        df["volume_momentum"] = (
            df["volume"].pct_change(5).fillna(0).replace([np.inf, -np.inf], 0)
        )

        # === 6. PRICE ACTION FEATURES (New) ===
        df["high_low_ratio"] = (
            ((df["high"] - df["low"]) / df["close"].clip(lower=0.001))
            .fillna(0)
            .replace([np.inf, -np.inf], 0)
        )
        hl_range = (df["high"] - df["low"]).clip(lower=0.001)
        df["close_position"] = (
            ((df["close"] - df["low"]) / hl_range).fillna(0.5).clip(0, 1)
        )
        df["gap"] = (
            (
                (df["open"] - df["close"].shift(1))
                / df["close"].shift(1).clip(lower=0.001)
            )
            .fillna(0)
            .replace([np.inf, -np.inf], 0)
        )

        # === 7. MARKET CONTEXT (New) ===
        df["vix_change"] = (
            df["vix"].pct_change().fillna(0).replace([np.inf, -np.inf], 0)
        )
        df["vix_trend_up"] = (
            (df["vix"] > df["vix"].rolling(5).mean()).astype(int).fillna(0)
        )
        df["sentiment_momentum"] = df["market_news_sentiment"].diff().fillna(0)

        # === 8. INTERACTION FEATURES (New) ===
        df["rsi_vix_interaction"] = ((df["rsi_14"] / 100) * (df["vix"] / 30)).fillna(0)
        df["momentum_vol_interaction"] = (df["macd_hist"] * df["volatility_20"]).fillna(
            0
        )

        # === 9. TARGET VARIABLE ===
        df["target_5d_raw"] = (
            (df["close"].shift(-5) / df["close"].clip(lower=0.001) - 1)
            .fillna(0)
            .replace([np.inf, -np.inf], 0)
        )
        df["target_5d"] = df["target_5d_raw"].apply(
            lambda x: 1 if x > 0.01 else (-1 if x < -0.01 else 0)
        )

        # Final NaN/Inf cleanup
        for col in df.columns:
            df[col] = df[col].replace([np.inf, -np.inf], 0).fillna(0)

        return df

    except Exception as e:
        # ADR-SEC-03: Do not silently return None and inject 0.0 into ConsensusEngine.
        # Raise FeatureGenerationError so callers can explicitly mark the agent
        # vote as abstention (score=0.5, weight=0) rather than a SELL signal.
        import traceback

        logging.warning(
            "Feature generation failed for symbol: %s\nTraceback:\n%s",
            e,
            traceback.format_exc(),
        )
        raise FeatureGenerationError(str(e)) from e


# --- PyTorch LSTM Model with Attention (Enhanced for 80%+ accuracy) ---
class AttentionLayer(nn.Module):
    """Attention mechanism to focus on important time steps"""

    def __init__(self, hidden_dim):
        super(AttentionLayer, self).__init__()
        self.attention = nn.Linear(hidden_dim, 1)

    def forward(self, lstm_output):
        # lstm_output shape: (batch_size, seq_len, hidden_dim)
        attention_weights = torch.softmax(self.attention(lstm_output), dim=1)
        # Weighted sum of outputs
        context = torch.sum(lstm_output * attention_weights, dim=1)
        return context, attention_weights


class LSTMModel(nn.Module):
    def __init__(self, input_dim, hidden_dim, num_layers, output_dim, dropout=0.3):
        super(LSTMModel, self).__init__()
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers

        # Enhanced LSTM with higher dropout for better generalization
        self.lstm = nn.LSTM(
            input_dim,
            hidden_dim,
            num_layers,
            batch_first=True,
            dropout=dropout,
            bidirectional=True,  # Bidirectional for context
        )

        # Attention layer
        self.attention = AttentionLayer(hidden_dim * 2)  # *2 for bidirectional

        # Dense layers with batch normalization
        self.bn1 = nn.BatchNorm1d(hidden_dim * 2)
        self.fc1 = nn.Linear(hidden_dim * 2, hidden_dim)
        self.dropout1 = nn.Dropout(dropout)
        self.relu = nn.ReLU()

        self.fc2 = nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        # LSTM forward pass
        lstm_out, _ = self.lstm(x)  # (batch, seq_len, hidden_dim*2)

        # Attention mechanism
        context, _ = self.attention(lstm_out)  # (batch, hidden_dim*2)

        # Dense layers
        out = self.bn1(context)
        out = self.fc1(out)
        out = self.relu(out)
        out = self.dropout1(out)
        out = self.fc2(out)

        return out


# --- Ensemble Model (Combines multiple LSTM instances for better prediction) ---
class EnsembleLSTMModel(nn.Module):
    def __init__(
        self, input_dim, hidden_dim, num_layers, output_dim, num_models=3, dropout=0.3
    ):
        super(EnsembleLSTMModel, self).__init__()
        self.num_models = num_models

        # Create multiple models with slightly different initializations
        self.models = nn.ModuleList(
            [
                LSTMModel(input_dim, hidden_dim, num_layers, output_dim, dropout)
                for _ in range(num_models)
            ]
        )

    def forward(self, x):
        outputs = []
        for model in self.models:
            outputs.append(model(x))

        # Average predictions from all models
        ensemble_output = torch.stack(outputs, dim=0).mean(dim=0)
        return ensemble_output


# --- Dataset Class ---
class StockDataset(Dataset):
    def __init__(self, X, y):
        # Keep as numpy arrays to save RAM (converts to Tensor only when needed)
        self.X = X.astype(np.float32)
        self.y = y.astype(np.float32)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        # Convert to tensor on-the-fly
        # Note: self.X[idx] is a 2D array (seq_len, features)
        #       self.y[idx] is a scalar, so we need to wrap it in an array first
        return torch.from_numpy(self.X[idx]), torch.tensor(
            self.y[idx], dtype=torch.float32
        )


# --- Data Preparation ---
def prepare_data(data_provider, news_processor, training_days=365 * 7):
    """
    Fetches data, merges it robustly, calculates features, and creates sequences.
    """
    logging.info("--- Starting Model Training ---")
    logging.info(f"Using {int(training_days / 365)} year rolling training window.")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=training_days + 200)  # Buffer for indicators

    # 1. Load Global Data (VIX & News)
    logging.info("Loading Global Data (VIX & News)...")

    # VIX
    vix_df = data_provider.get_data("^VIX", end_date, days=training_days + 200)
    if vix_df is not None and not vix_df.empty:
        vix_df = vix_df[["close"]].rename(columns={"close": "vix"})
    else:
        logging.warning("VIX data missing. Using default 20.0.")
        idx = pd.date_range(start=start_date, end=end_date)
        vix_df = pd.DataFrame(index=idx, data={"vix": 20.0})

    # News Sentiment
    logging.info("Fetching historical news sentiment...")
    news_cache_file = "sentiment_cache.csv"
    news_df = pd.DataFrame()

    if os.path.exists(news_cache_file):
        logging.info(f"Loading news from {news_cache_file}...")
        news_df = pd.read_csv(news_cache_file, index_col=0, parse_dates=True)
    else:
        logging.info(
            "No news cache found. (Skipping fetch to avoid cycle, rely on defaults or run prepare_rl_data.py)..."
        )

    if news_df.empty:
        logging.warning("News data empty. Using neutral 0.0.")

    # 2. Combine Global Context
    # Create a master time index
    full_idx = pd.date_range(start=start_date, end=end_date, freq="D", tz="UTC")
    context_df = pd.DataFrame(index=full_idx)

    # Robust Merge Helper
    def robust_merge_series(target_df, source_df, col_name, default_val):
        if source_df is None or source_df.empty:
            target_df[col_name] = default_val
            return target_df

        # Timezone Alignment
        if source_df.index.tz is None:
            source_df.index = source_df.index.tz_localize("UTC")
        else:
            source_df.index = source_df.index.tz_convert("UTC")

        # Reindex to master timeline and forward fill
        s = source_df[col_name].reindex(target_df.index)
        s = s.ffill().fillna(default_val)
        target_df[col_name] = s
        return target_df

    context_df = robust_merge_series(context_df, vix_df, "vix", 20.0)
    context_df = robust_merge_series(context_df, news_df, "market_news_sentiment", 0.0)

    # 3. Process Symbols
    logging.info("Fetching training symbol list...")
    try:
        symbols = data_provider.get_sp500_symbols()
    except Exception:
        symbols = data_provider.get_available_symbols()[:100]

    logging.info(f"Training on {len(symbols)} symbols.")

    sequences_X = []
    sequences_y = []

    # Hyperparameters
    SEQ_LENGTH = 60
    # ENHANCED: Add all the new features we created
    feature_cols = [
        "log_ret",
        "volatility_20",
        "volatility_5",
        "returns_cumsum_5",
        "returns_std_5",
        "rsi_14",
        "rsi_7",
        "rsi_overbought",
        "rsi_oversold",
        "macd",
        "macd_signal",
        "macd_hist",
        "adx_14",
        "sma_20",
        "sma_50",
        "price_vs_sma20",
        "sma_slope_20",
        "bb_upper",
        "bb_lower",
        "bb_position",
        "bb_width",
        "volume_sma_20",
        "volume_ratio",
        "volume_momentum",
        "high_low_ratio",
        "close_position",
        "gap",
        "vix",
        "vix_change",
        "vix_trend_up",
        "market_news_sentiment",
        "sentiment_momentum",
        "rsi_vix_interaction",
        "momentum_vol_interaction",
    ]

    processed_count = 0
    logging.info(f"Generating sequences for {len(symbols)} symbols...")

    for i, symbol in enumerate(symbols):
        if i > 0 and i % 50 == 0:
            logging.info(f"Sequencing progress: {i}/{len(symbols)}...")

        try:
            # Fetch Stock Data
            df = data_provider.get_data(symbol, end_date, days=training_days + 200)

            if df is None or df.empty or len(df) < SEQ_LENGTH + 50:
                continue

            # Timezone Alignment
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            else:
                df.index = df.index.tz_convert("UTC")

            # Robust Merge
            df = df.join(context_df, how="left")
            df["vix"] = df["vix"].ffill().fillna(20.0)
            df["market_news_sentiment"] = (
                df["market_news_sentiment"].ffill().fillna(0.0)
            )

            # Generate Indicators
            df = create_live_features(df)
            if df is None:
                continue

            # Drop NaNs
            df.dropna(subset=feature_cols + ["target_5d"], inplace=True)

            if len(df) < SEQ_LENGTH:
                continue

            # Create Sequences
            data_x = df[feature_cols].values
            data_y = df["target_5d"].values

            for t in range(len(df) - SEQ_LENGTH):
                seq_x = data_x[t : t + SEQ_LENGTH]
                target = data_y[t + SEQ_LENGTH - 1]

                sequences_X.append(seq_x)
                sequences_y.append(target)

            processed_count += 1

        except Exception:
            continue

    logging.info(f"Successfully processed {processed_count} symbols.")

    if not sequences_X:
        logging.error("No training data could be generated. Exiting.")
        return None, None, None

    X = np.array(sequences_X)
    y = np.array(sequences_y)

    logging.info(f"Data Shape: X={X.shape}, y={y.shape}")

    return X, y, feature_cols


# --- Training Loop ---
def train_model():
    # --- FIX: IMPORT HERE TO AVOID CIRCULAR DEPENDENCY ---
    from ai_components import NewsProcessor

    # -----------------------------------------------------
    # 1. Prepare Data
    dp = HistoricalDataProvider()
    np_processor = NewsProcessor()

    # Initialize Vertex AI Logging
    project_id = getattr(config, "GCP_PROJECT_ID", "aama-project")
    location = getattr(config, "GCP_REGION", "europe-west3")
    init_vertex_experiment(
        project_id=project_id,
        location=location,
        experiment_name="lstm-model-training",
        run_prefix="lstm-run",
    )

    # Use 7 years as per your request
    X, y, feature_cols = prepare_data(dp, np_processor, training_days=365 * 7)

    if X is None:
        logging.error("Data preparation failed. Exiting training.")
        return

    # 2. Scale Data
    logging.info("Scaling data...")
    from sklearn.preprocessing import StandardScaler

    N, S, F = X.shape
    # Reshape for scaling
    X_flat = X.reshape(N * S, F)

    scaler_x = StandardScaler()
    X_scaled_flat = scaler_x.fit_transform(X_flat)

    # Free up original X memory immediately
    del X
    del X_flat
    gc.collect()

    # Reshape back
    X_scaled = X_scaled_flat.reshape(N, S, F)
    del X_scaled_flat
    gc.collect()

    scaler_y = StandardScaler()
    y_scaled = scaler_y.fit_transform(y.reshape(-1, 1))

    # Free up original y
    del y
    gc.collect()

    joblib.dump(scaler_x, SCALER_X_FILE_NAME)
    joblib.dump(scaler_y, SCALER_Y_FILE_NAME)

    metadata = {
        "features_list": feature_cols,
        "sequence_length": S,
        "model_params": {
            "input_dim": F,
            "hidden_dim": 64,
            "num_layers": 2,
            "output_dim": 1,
        },
    }
    with open(MODEL_METADATA_FILE, "w") as f:
        json.dump(metadata, f)

    # Log hyperparameters to Vertex AI
    log_vertex_params(
        {
            "features_count": F,
            "sequence_length": S,
            "hidden_dim": 128,
            "num_layers": 3,
            "num_models": 3,
            "dropout": 0.4,
            "batch_size": 64,
            "learning_rate": 0.001,
            "epochs": 50,
            "training_days": 365 * 7,
        }
    )

    # 3. Train with Ensemble Model
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logging.info(f"Training ENSEMBLE model on {device}...")

    # Use the optimized Dataset class
    dataset = StockDataset(X_scaled, y_scaled)

    # Split Train/Val with stratification for better distribution
    train_size = int(0.8 * len(dataset))
    val_size = len(dataset) - train_size
    train_dataset, val_dataset = torch.utils.data.random_split(
        dataset, [train_size, val_size]
    )

    # OPTIMIZED: Larger batch size with gradient accumulation
    BATCH_SIZE = 64
    ACCUMULATION_STEPS = 2  # Effective batch size = 128

    train_loader = DataLoader(
        train_dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=0
    )
    val_loader = DataLoader(
        val_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=0
    )

    # Use Ensemble with 3 LSTM models voting
    model = EnsembleLSTMModel(
        input_dim=F,
        hidden_dim=128,  # Increased from 64
        num_layers=3,  # Increased from 2
        output_dim=1,
        num_models=3,  # Ensemble of 3 models
        dropout=0.4,  # Increased for better generalization
    ).to(device)

    # OPTIMIZED: Focal Loss for imbalanced classification
    criterion = nn.MSELoss()  # Keep MSE for regression

    # OPTIMIZED: Learning rate schedule with warmup
    optimizer = optim.AdamW(model.parameters(), lr=0.001, weight_decay=1e-5)
    scheduler = optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer, T_0=5, T_mult=2
    )

    epochs = 50  # More epochs for better convergence
    patience = 10  # Early stopping
    best_val_loss = float("inf")
    patience_counter = 0

    for epoch in range(epochs):
        model.train()
        train_loss = 0

        optimizer.zero_grad()
        for batch_idx, (batch_X, batch_y) in enumerate(train_loader):
            batch_X, batch_y = batch_X.to(device), batch_y.to(device)

            outputs = model(batch_X)
            loss = criterion(outputs, batch_y)

            # Gradient accumulation for effective larger batch sizes
            loss.backward()

            if (batch_idx + 1) % ACCUMULATION_STEPS == 0:
                optimizer.step()
                optimizer.zero_grad()

            train_loss += loss.item()

        # Handle leftover gradients
        if len(train_loader) % ACCUMULATION_STEPS != 0:
            optimizer.step()
            optimizer.zero_grad()

        model.eval()
        val_loss = 0
        val_predictions = []
        val_actuals = []

        with torch.no_grad():
            for batch_X, batch_y in val_loader:
                batch_X, batch_y = batch_X.to(device), batch_y.to(device)
                outputs = model(batch_X)
                loss = criterion(outputs, batch_y)
                val_loss += loss.item()

                val_predictions.extend(outputs.cpu().numpy().flatten())
                val_actuals.extend(batch_y.cpu().numpy().flatten())

        scheduler.step()

        avg_train_loss = train_loss / len(train_loader)
        avg_val_loss = val_loss / len(val_loader)

        # Calculate accuracy (for direction prediction)
        predictions_up = np.array(val_predictions) > 0
        actuals_up = np.array(val_actuals) > 0
        accuracy = np.mean(predictions_up == actuals_up)

        logging.info(
            f"Epoch {epoch + 1:3d}/{epochs} | Train Loss: {avg_train_loss:.5f} | Val Loss: {avg_val_loss:.5f} | Accuracy: {accuracy:.2%}"
        )

        # Log metrics to Vertex AI
        log_vertex_metrics(
            {
                "train_loss": avg_train_loss,
                "val_loss": avg_val_loss,
                "val_accuracy": float(accuracy),
            },
            step=epoch + 1,
        )

        # Early stopping
        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            patience_counter = 0
            torch.save(model.state_dict(), MODEL_FILE_NAME)  # nosec B614
            logging.info(f"  ✓ New best model saved (Val Loss: {best_val_loss:.5f})")
        else:
            patience_counter += 1
            if patience_counter >= patience:
                logging.info(f"Early stopping triggered after {epoch + 1} epochs")
                break

    logging.info(f"Training completed. Best model saved to {MODEL_FILE_NAME}")
    logging.info(f"Final Val Loss: {best_val_loss:.5f}")

    # End Vertex AI run
    end_vertex_experiment()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
    )
    train_model()
