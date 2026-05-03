# config.py (OSS)
import os
import logging
from dotenv import load_dotenv

# Load .env (local dev)
load_dotenv()

# --- Project paths (for subfolder layout) ---
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# --- Engine API (for multiple instances use ENGINE_PORT=8001 etc.) ---
ENGINE_PORT = int(os.getenv("ENGINE_PORT", "8001"))
AUTO_START_STRATEGY = os.getenv("AUTO_START_STRATEGY", "True").lower() == "true"


# --- API Keys ---
def _clean_env(key, default=None):
    val = os.getenv(key, default)
    return val.strip() if val else val


API_KEY = _clean_env("ALPACA_API_KEY")
API_SECRET = _clean_env("ALPACA_SECRET_KEY")

# --- Alpaca Paper Trading Switch ---
# Fallback Switch for Beta-Test-Phase
PAPER_TRADING = os.getenv("PAPER_TRADING", "True").lower() == "true"

# Update BASE_URL based on PAPER_TRADING flag if not explicitly set
if PAPER_TRADING and "ALPACA_BASE_URL" not in os.environ:
    BASE_URL = "https://paper-api.alpaca.markets"
else:
    BASE_URL = _clean_env("ALPACA_BASE_URL", "https://api.alpaca.markets")
POLYGON_API_KEY = _clean_env("POLYGON_API_KEY")
GEMINI_API_KEY = _clean_env("GEMINI_API_KEY")

# --- Alpaca Data Feed (ML-1: SIP = consolidated NBBO for MiFID II best-execution) ---
# SIP (default) provides the National Best Bid/Offer from the consolidated tape.
# IEX shows only IEX prices (single exchange) — insufficient for best-execution evidence.
# Override: ALPACA_DATA_FEED=iex to revert (e.g. if Alpaca account not on paid SIP plan).
ALPACA_DATA_FEED = os.getenv("ALPACA_DATA_FEED", "iex" if PAPER_TRADING else "sip")

# --- Databento (Institutional Historical Data — Epic 2.7) ---
# Replaces yfinance for backtesting and ML model training.
# Set DATABENTO_API_KEY in GCP Secret Manager or .env for local dev.
# When not set, Polygon is used as fallback (charts/VIX) and Alpaca for stocks.
DATABENTO_API_KEY = _clean_env("DATABENTO_API_KEY")
DATABENTO_ENABLED = bool(DATABENTO_API_KEY)  # Auto-activated when key is present
# GCS bucket for persistent Databento cache (ML-1 Phase 6).
# Cloud Run containers lose local disk cache on restart — GCS cache persists forever.
# Set to empty string to disable GCS cache and use local disk pickle only.
DATABENTO_GCS_BUCKET = os.getenv("DATABENTO_GCS_BUCKET", "")

# --- Alpaca OAuth Settings (Multi-Tenant) ---
OAUTH_CLIENT_ID = _clean_env("OAUTH_CLIENT_ID")
OAUTH_CLIENT_SECRET = _clean_env("OAUTH_CLIENT_SECRET")
OAUTH_REDIRECT_URI = _clean_env(
    "OAUTH_REDIRECT_URI", "http://127.0.0.1:8081/auth/alpaca/callback"
)

# --- Cloud SQL Settings (User Metadata) ---
DATABASE_URL = _clean_env("DATABASE_URL")

# --- GCP / Vertex AI Configuration ---
GCP_PROJECT_ID = _clean_env("GCP_PROJECT_ID")
GCP_REGION = _clean_env("GCP_REGION", "us-central1")
# If set, the bot will try to use this Vertex Endpoint for inference instead of local PyTorch
VERTEX_ENDPOINT_ID = _clean_env("VERTEX_ENDPOINT_ID")

# --- Gemini Model ---
# Updated to use available model (gemini-1.5-flash is stable)
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")


def apply_remote_config(db_config: dict):
    """
    Applies remote configurations dynamically fetched from Cloud SQL system_config.
    Modifies the global variables so all modules using them see the updated values.
    """
    global GEMINI_MODEL_NAME, PAPER_TRADING
    import logging

    if "gemini_model" in db_config:
        old_val = GEMINI_MODEL_NAME
        GEMINI_MODEL_NAME = db_config["gemini_model"]
        if old_val != GEMINI_MODEL_NAME:
            logging.info(
                f"Dynamic Config: GEMINI_MODEL_NAME updated to {GEMINI_MODEL_NAME}"
            )

    if "alpaca_paper" in db_config:
        old_val = PAPER_TRADING
        PAPER_TRADING = bool(db_config["alpaca_paper"])
        if old_val != PAPER_TRADING:
            logging.info(f"Dynamic Config: PAPER_TRADING updated to {PAPER_TRADING}")


# Available models as of 2026:
# - "gemini-2.0-flash" (fast, recommended)
# - "gemini-2.5-flash" (newer, faster)
# - "gemini-2.5-pro" (most capable)

# When True, simulation uses the same Gemini-based scanner as live (realistic news-driven backtest). Rate limits apply.
ENABLE_GEMINI_IN_SIMULATION = False  # Set to True to match live behavior in backtests
GEMINI_RATE_LIMIT_DELAY = 6.0  # Minimum seconds between API calls
GEMINI_MAX_RETRIES = 3
USE_SPY_VOLATILITY_FALLBACK = True  # Use SPY volatility when VIX fails
# Hard daily Gemini call limit — free-tier guard (1M tokens/day free on Gemini 2.5 Flash).
# 950 calls × ~950 tokens/call ≈ 900K tokens/day → safely under the 1M free limit.
# On limit hit: specialist returns neutral signal (no crash). Resets at midnight UTC.
# Override: GEMINI_DAILY_CALL_LIMIT=1200 to allow more calls (may incur cost).
GEMINI_DAILY_CALL_LIMIT = int(os.getenv("GEMINI_DAILY_CALL_LIMIT", "950"))

# --- Fractional Shares Settings ---
ENABLE_FRACTIONAL_SHARES = (
    True  # Enable fractional share trading (Alpaca supports this)
)
MIN_POSITION_VALUE = 1.0  # Minimum $1 position (Alpaca fractional minimum)

# --- Position Sizing Settings ---
MAX_POSITION_PERCENT = 0.25  # Max 25% of portfolio per single position
MIN_POSITION_PERCENT = 0.05  # Min 5% of portfolio per trade (for low conviction)
RISK_PER_TRADE_PERCENT = 0.02  # Risk 2% per trade (for stop-loss calculation)
MAX_POSITIONS = 10  # Maximum number of simultaneous positions in portfolio

# --- Dynamic Position Sizing (Conviction-Based) ---
# Position size scales between MIN and MAX based on conviction score
# Conviction is calculated from: model confidence, RSI, ADX, MACD alignment
ENABLE_DYNAMIC_SIZING = True  # True = size based on conviction, False = fixed sizing

# --- Portfolio Management Settings ---
# Smart rebalancing: Only adjust positions when drift exceeds threshold
REBALANCE_DRIFT_THRESHOLD_PCT = 3.0  # Only rebalance if position drifts >3% from target
REBALANCE_COOLDOWN_HOURS = 2.0  # Minimum hours between rebalances per symbol
MIN_HOLD_HOURS_PORTFOLIO = (
    1.0  # Min hours before portfolio rebalancing considers selling
)
MAX_TRADES_PER_SYMBOL_PER_DAY = 5  # Max round-trips per symbol per day
CONSECUTIVE_SELL_BYPASS_THRESHOLD = (
    8  # Number of consecutive SELL signals to bypass hold period
)

# --- Global Settings ---
NEWS_POLLING_INTERVAL_SECONDS = 60 * 5  # Poll news every 5 minutes
STRATEGY_MONITOR_INTERVAL_SECONDS = 30 * 60  # Check for strategy switch every 30 mins
DEFAULT_SYMBOLS = [
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "TSLA",
]
LEARNED_RULES_FILE = os.path.join(DATA_DIR, "ai_learned_rules.json")
BENCHMARK_EQUITY_FILE = os.path.join(DATA_DIR, "benchmark_equity.json")
BENCHMARK_COMPARISON_CSV = os.path.join(DATA_DIR, "benchmark_equity_comparison.csv")

# --- Strategy Mode: switch between original RLAgent and LSTM Dynamic ---
# "RLAgent" = original LSTM+RL with signal stabilization, portfolio debate, churn prevention
# "LSTMDynamic" = LSTM-only buys (first time recommended), confidence-based sizing, smart exit
ACTIVE_STRATEGY = os.getenv("ACTIVE_STRATEGY", "RLAgent")
# Model Versions
RL_MODEL_VERSION = os.getenv("RL_MODEL_VERSION", "rl_agent_v3_dsr")
LSTM_MODEL_VERSION = os.getenv("LSTM_MODEL_VERSION", "v1")
# When switching between RLAgent and LSTMDynamic, keep positions (no liquidation)
STRATEGY_SWITCH_WITHOUT_LIQUIDATION = True

# --- Simulation model versions (used when running backtests / benchmarks) ---
# Simulations always use the newest LSTM (lstm_model.pth) and the RL v3 DSR model below.
SIMULATION_RL_VERSION = "rl_agent_v3_dsr"  # RL model used in simulations (v3 DSR)

# --- Simulation parity with live (backtest matches live behavior) ---
# When True, RLStrategy uses the same signal stabilization in simulation as in live (fewer, more conservative trades).
SIMULATION_USE_LIVE_STABILIZATION = (
    True  # Default True so backtest and live behave the same
)
# When False, no fallback buys when strategy places no orders; backtest shows "no trades" so you fix models/data.
SIMULATION_FALLBACK_BUY = False  # Default False to avoid inflating backtest returns

# --- Stock Specialist System (Epic 3.3) — 99% API-Kostensenkung ---
SPECIALIST_HIGH_PRIO_INTERVAL_HOURS = 2.0  # High-priority symbols refresh every 2h
SPECIALIST_FULL_CYCLE_HOURS = 12.0  # Full universe cycle target (12h)
SPECIALIST_UNIVERSE_SIZE = 500  # Full S&P 500 universe

# --- Smart exit (LSTMDynamic / rule-based exits); override in config for tuning ---
TRAILING_STOP_PCT = (
    3.0  # Sell if price falls this % from high-water mark (e.g. 2.0, 3.0, 4.0)
)
STOP_LOSS_PCT = 7.0  # Hard stop: sell if down this % from entry (e.g. 5.0, 7.0, 10.0)
TAKE_PROFIT_PCT = 25.0  # Take profit at this % gain (e.g. 15.0, 25.0, 35.0)
MIN_HOLD_HOURS_BEFORE_TRAIL = 1.0  # Don't apply trailing stop in first N hours
MIN_PROFIT_FOR_TRAIL_PCT = 2.0  # Start trailing only after this % profit

# --- Intelligent Exit System (Epic 2.4) — 5-Tier Kapitalschutz ---
# Loss Management: Verlierer schnell schneiden
LOSS_TIER_1_PCT = -2.0  # Nach -2%: Beobachtungsmodus
LOSS_TIER_2_PCT = -4.0  # Nach -4%: Erhöhte Wachsamkeit
LOSS_TIER_3_PCT = -6.0  # Nach -6%: Aggressiver Exit
HARD_STOP_LOSS_PCT = -8.0  # Absoluter Hard Stop (kein Override möglich)
# Winner Management: Gewinner laufen lassen
MOMENTUM_SELL_THRESHOLD = -0.3  # LSTM unter -0.3 = Momentum endet
# General
MIN_HOLD_HOURS = 0.5  # Mindestens 30min halten (Anti-Churn)
PANIC_PROTECTION_HOURS = 2.0  # Erste 2h: kein Verkauf (außer Hard Stop)

# --- Simulation execution costs (realistic backtests) ---
# Slippage applied to sim fills: buy at open*(1+SLIPPAGE_PERCENT), sell at open*(1-SLIPPAGE_PERCENT)
SLIPPAGE_PERCENT = (
    0.001  # 0.1% default; use 0.0005 for optimistic, 0.002 for conservative
)
COMMISSION_PER_TRADE = 0.50  # $ per side (e.g. 0 for free broker, 0.50 for realistic)

# --- Risk: optional total exposure cap and Kelly-style cap ---
# Max fraction of equity that can be in open positions (sum of position values). None = no cap.
MAX_TOTAL_EXPOSURE_PCT = 0.95  # e.g. 0.95 = 95% max in positions, 5% cash buffer
# Kelly fraction cap: scale position size by this (e.g. 0.25 = quarter-Kelly). None = no Kelly cap.
KELLY_FRACTION_CAP = None  # Set to 0.25 or 0.5 to limit size in hot streaks

# --- Compliance Guardian Settings ---
ENABLE_COMPLIANCE_GUARDIAN = True  # Master switch for pre-trade compliance checks
COMPLIANCE_MAX_ORDER_VALUE = float(
    os.getenv("COMPLIANCE_MAX_ORDER_VALUE", "50000.0")
)  # EUR/USD, max single order value
COMPLIANCE_MAX_DAILY_TRADES = int(
    os.getenv("COMPLIANCE_MAX_DAILY_TRADES", "10")
)  # Max trades per day across all symbols

# --- Slack Alerts ---
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
ENABLE_SLACK_ALERTS = os.getenv("ENABLE_SLACK_ALERTS", "False").lower() == "true"
ENABLE_HEARTBEAT = os.getenv("ENABLE_HEARTBEAT", "False").lower() == "true"
HEARTBEAT_INTERVAL_HOURS = int(os.getenv("HEARTBEAT_INTERVAL_HOURS", "6"))

# NOTE: AUTO_START_STRATEGY is defined once above (line ~16).
# Removed duplicate definition that was present in original config.py.

# --- Hot-Swap / Shadow Mode (Epic 2.3-Pre / PR-C) ---
# Dauer in Stunden, die eine neue Strategy im Shadow-Mode (Paper-Trade) validiert wird
# bevor sie live geschaltet werden kann. Konfigurierbar per ENV.
SHADOW_MODE_HOURS = float(os.getenv("SHADOW_MODE_HOURS", "24"))

# --- Intelligent Exit System (Epic 2.4) ---
INTELLIGENT_EXIT_ENABLED = (
    os.getenv("INTELLIGENT_EXIT_ENABLED", "True").lower() == "true"
)

# --- ML Feature Flags (Epic 4.x — LightGBM Round Table layer) ---
# When False, Round Table agents use heuristic logic only (safe default).
# Set to True once LightGBM models reach AUC >= 0.75 and are deployed to GCS.
ROUND_TABLE_USE_ML_MODELS = (
    os.getenv("ROUND_TABLE_USE_ML_MODELS", "False").lower() == "true"
)

# --- Logging Configuration ---
# Set levels for noisy libraries
logging.getLogger("alpaca").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("h5py").setLevel(logging.WARNING)

# Configure root logger with GcpJsonFormatter (Epic 7.4 EU AI Act Compliant Audit Logging)
from core.structured_logging import setup_logging


def init_logging():
    """Explizite Initialisierung des Loggers (niemals implizit via import aufrufen)."""
    setup_logging()


# --- Check Gemini Availability ---
GEMINI_AVAILABLE = False
if GEMINI_API_KEY:
    try:
        from google import genai

        # New SDK uses Client() with api_key parameter
        _test_client = genai.Client(api_key=GEMINI_API_KEY)
        del _test_client
        logging.info(
            "Gemini AI successfully configured using google.genai (recommended)."
        )
        GEMINI_AVAILABLE = True
    except ImportError:
        logging.warning("Gemini library (google-genai) not installed.")
    except Exception as e:
        logging.error(f"Failed to configure Gemini AI: {e}")
        GEMINI_AVAILABLE = False
else:
    logging.warning("Gemini API Key not found in .env file.")

# Log final Gemini status
if not GEMINI_AVAILABLE:
    logging.warning("Gemini AI features will be disabled/limited.")
