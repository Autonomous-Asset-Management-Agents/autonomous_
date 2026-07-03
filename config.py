# config.py (OSS)
import logging
import os
import threading
from typing import Union

# SEC-5: Load secrets from OS keychain BEFORE dotenv.
# Keychain values are injected into os.environ so that
# _clean_env() / os.getenv() calls work unchanged.
# Precedence: explicit env var > keychain > .env.oss
from core.keychain import load_secrets_from_keychain

load_secrets_from_keychain()

from dotenv import load_dotenv

# Load .env.oss and .env (local dev — overridden by keychain values above)
load_dotenv(".env.oss")
load_dotenv(".env", override=True)

# --- Project paths (for subfolder layout) ---
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# --- Engine API (for multiple instances use ENGINE_PORT=8001 etc.) ---
ENGINE_PORT = int(os.getenv("ENGINE_PORT", "8001"))
AUTO_START_STRATEGY = os.getenv("AUTO_START_STRATEGY", "True").lower() == "true"
# Off-hours paper trading bypass (default False — production always respects market hours)
BYPASS_MARKET_HOURS = os.getenv("BYPASS_MARKET_HOURS", "False").lower() == "true"


# --- API Keys ---
def _clean_env(key, default=None):
    # SEC M6 (INV-01): an empty/whitespace env var means 'unset' - return the default
    # (usually None), never "". Otherwise Optional[SecretStr]/config fields can't tell an
    # unset secret from an empty one (e.g. a blank ALPACA_API_KEY passing as truthy-ish).
    val = os.getenv(key)
    if val is None:
        return default
    val = val.strip()
    return val if val else default


API_KEY = _clean_env("ALPACA_API_KEY")
API_SECRET = _clean_env("ALPACA_SECRET_KEY")
ALPACA_LIVE_API_KEY = _clean_env("ALPACA_LIVE_API_KEY")
ALPACA_LIVE_SECRET_KEY = _clean_env("ALPACA_LIVE_SECRET_KEY")

# --- Alpaca Paper Trading Switch ---
# Fallback Switch for Beta-Test-Phase
PAPER_TRADING = os.getenv("PAPER_TRADING", "True").lower() == "true"

# --- Safety Gates (Enterprise API compatibility) ---
# INF-8: Required by core/engine/base.py (start_live_strategy), order_executor.py
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
SHADOW_MODE = os.getenv("SHADOW_MODE", "False").lower() == "true"
STAGING_ENV = os.getenv("STAGING_ENV", "False").lower() == "true"


def get_secret_str(value: Union["SecretStr", str, None]) -> str:  # noqa: F821
    """Extract a plain string from a Pydantic SecretStr or plain str value.

    Enterprise uses Pydantic SecretStr for API keys; OSS uses plain strings.
    This function provides a unified accessor so callers don't need to know
    which edition they are running on.
    """
    if value is None:
        return ""
    if hasattr(value, "get_secret_value"):
        return value.get_secret_value()
    if isinstance(value, str):
        return value
    raise TypeError(
        f"config.get_secret_str() expected SecretStr or str, got {type(value).__name__!r}."
    )


# Update BASE_URL based on PAPER_TRADING flag if not explicitly set
if (
    "ALPACA_BASE_URL" not in os.environ
    or os.getenv("ALPACA_BASE_URL") == "https://paper-api.alpaca.markets"
):
    BASE_URL = (
        "https://paper-api.alpaca.markets"
        if PAPER_TRADING
        else "https://api.alpaca.markets"
    )
else:
    BASE_URL = _clean_env("ALPACA_BASE_URL", "https://api.alpaca.markets")
POLYGON_API_KEY = _clean_env("POLYGON_API_KEY")
GEMINI_API_KEY = _clean_env("GEMINI_API_KEY")

# --- Enterprise compatibility aliases ---
# Enterprise config.py exposes these via Pydantic model + __getattr__.
# Scripts (analyze_bot.py, train_v4_lightgbm.py) and some core modules
# access config.ALPACA_API_KEY etc. directly. These aliases ensure OSS
# provides the same public interface without Pydantic.
is_desktop = os.getenv("DEPLOYMENT_MODE", "").upper() == "LOCAL"
if is_desktop and not PAPER_TRADING:
    ALPACA_API_KEY = ALPACA_LIVE_API_KEY
    ALPACA_SECRET_KEY = ALPACA_LIVE_SECRET_KEY
else:
    ALPACA_API_KEY = API_KEY
    ALPACA_SECRET_KEY = API_SECRET
ALPACA_BASE_URL = BASE_URL

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
# Removed for OSS release (F10 Leakage fix)

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
        # EU AI Act Art. 14 (PR-0a, E1): refuse a runtime flip to live trading without a
        # configured HITL policy (mirrors config.py). HITL_ENABLED is a module global,
        # resolved at call time.
        would_be_live = not bool(db_config["alpaca_paper"])
        if would_be_live and not HITL_ENABLED:
            logging.error(
                "CRITICAL COMPLIANCE: refused remote-config flip to live trading "
                "(PAPER_TRADING=False) without HITL_ENABLED=True (EU AI Act Art. 14)."
            )
            return
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

# BUG-AI-S01 (#1232): configurable broker-equity fallback (was a hardcoded
# 100000.0 sprinkled in the engine loops). Default = the Alpaca paper default;
# set DEFAULT_EQUITY to your real account size for live trading.
DEFAULT_EQUITY = float(os.getenv("DEFAULT_EQUITY", "100000.0"))

# --- Compliance Guardian Settings ---
ENABLE_COMPLIANCE_GUARDIAN = True  # Master switch for pre-trade compliance checks
# ADR-C01: Max Order Value = 10,000 EUR (ESMA/MiFID II Art. 57) — must match the
# ComplianceGuardian class default (GAP5 fix: was "50000.0").
COMPLIANCE_MAX_ORDER_VALUE = float(
    os.getenv("COMPLIANCE_MAX_ORDER_VALUE", "10000.0")
)  # EUR/USD, max single order value
COMPLIANCE_MAX_DAILY_TRADES = int(
    os.getenv("COMPLIANCE_MAX_DAILY_TRADES", "10")
)  # Max trades per day across all symbols

# --- HITL Autonomy Policy (PR-0a, GAP2) ---
# ADR-C14: EU AI Act Art. 14 — mirrors config.py. All six values default to the safe
# all-manual / dormant state. M1: ALL defined BEFORE the boot gate so the flat OSS edition
# cannot NameError on HITL_ENABLED / HITL_AUTONOMOUS_UNLIMITED.
HITL_ENABLED = os.getenv("HITL_ENABLED", "False").lower() == "true"
HITL_MAX_VALUE_PER_TRADE = float(os.getenv("HITL_MAX_VALUE_PER_TRADE", "0.0"))
HITL_MAX_VALUE_PER_DAY = float(os.getenv("HITL_MAX_VALUE_PER_DAY", "0.0"))
HITL_AUTONOMOUS_UNLIMITED = (
    os.getenv("HITL_AUTONOMOUS_UNLIMITED", "False").lower() == "true"
)
HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS = (
    os.getenv("HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS", "False").lower() == "true"
)
HITL_EXPIRY_SECONDS = int(os.getenv("HITL_EXPIRY_SECONDS", "900"))


def _enforce_hitl_boot_gate(paper_trading, hitl_enabled, autonomous_unlimited):
    """EU AI Act Art. 14 (PR-0a) — mirror of config.py._enforce_hitl_boot_gate."""
    if not paper_trading and not hitl_enabled:
        raise RuntimeError(
            "CRITICAL COMPLIANCE: live trading (PAPER_TRADING=False) requires "
            "HITL_ENABLED=True (EU AI Act Art. 14 — human oversight of capital decisions)."
        )
    if not paper_trading and hitl_enabled and autonomous_unlimited:
        logging.critical(
            "COMPLIANCE: HITL_AUTONOMOUS_UNLIMITED on LIVE trading — EU AI Act Art. 14 "
            "Mode C (no autonomous limits). Ensure this is deliberate."
        )


_enforce_hitl_boot_gate(PAPER_TRADING, HITL_ENABLED, HITL_AUTONOMOUS_UNLIMITED)

# --- Runtime-adjustable HITL policy mutator (POST /api/hitl/policy, #1463) ---
# config.py (Enterprise) ships apply_hitl_policy_update; the OSS edition was
# missing it entirely, so the desktop handler raised an uncaught AttributeError
# -> bare HTTP 500. Parity fix: expose the same mutator. The OSS config IS the
# module globals (get_config() rebuilds a SimpleNamespace from globals() on every
# call), so we update the globals in place — get_config() then reflects the new
# limits on the gate's next decision. HITL_ENABLED is never settable here
# (env-only); unknown keys are ignored. Thread-safe.
_HITL_ADJUSTABLE = {
    "HITL_MAX_VALUE_PER_TRADE",
    "HITL_MAX_VALUE_PER_DAY",
    "HITL_AUTONOMOUS_UNLIMITED",
    "HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS",
    "HITL_EXPIRY_SECONDS",
}
_hitl_policy_lock = threading.Lock()


def apply_hitl_policy_update(limits: dict) -> None:
    """Mutate the running HITL policy limits at runtime (POST /api/hitl/policy).

    OSS-edition mirror of ``config.py.apply_hitl_policy_update``: updates the
    module-global limits in place so ``get_config()`` reflects them on the gate's
    next decision. Only the five adjustable values are honoured; ``HITL_ENABLED``
    is never settable here (env-only). A stray/unknown key is ignored."""
    updates = {k: v for k, v in limits.items() if k in _HITL_ADJUSTABLE}
    if not updates:
        return
    with _hitl_policy_lock:
        globals().update(updates)


# --- Limit Order Settings ---
USE_LIMIT_ORDERS = os.getenv("USE_LIMIT_ORDERS", "False").lower() == "true"
LIMIT_ORDER_SPREAD_BUFFER_PCT = float(
    os.getenv("LIMIT_ORDER_SPREAD_BUFFER_PCT", "0.001")
)

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

# --- Compatibility for get_config() (Epic 4.x / PR 1159) ---
import sys
import types

# ML & Feature Flags
ML_PREDICTION_ENABLED = os.getenv("ML_PREDICTION_ENABLED", "False").lower() == "true"
ML_SENTIMENT_BLEND_ENABLED = (
    os.getenv("ML_SENTIMENT_BLEND_ENABLED", "False").lower() == "true"
)
# Specialist report parity (RPAR Epic #1262, Task T1 #1265) - mirrors config.py, default
# OFF. When ON the synthesis uses the V2 prompt+parser (COMPANY/BULL/BEAR/THESIS prose);
# OFF reproduces today's 6-tuple path byte-for-byte (NEWS-8: no score/recommendation delta).
SPECIALIST_PROMPT_V2 = os.getenv("SPECIALIST_PROMPT_V2", "False").lower() == "true"
# ===================================================================
# RPAR Epic #1262 / Task T5 (#1269) - ML<->LLM convergence-blend weights.
# Mirrors config.py (dual-edition parity). DECISION-MATH (P1) for
# _blend_ml_sentiment; surfaced for the P1 audit instead of hiding in getattr
# defaults. BASIS (all eight, per ADR below): P3-B convergence math, Bundle
# #L2555-2618; values == the historical getattr defaults -> byte-identical merge,
# reconciled via the #76 shadow harness. Flip ML_SENTIMENT_BLEND_ENABLED ON ONLY
# after green #76 shadow validation + walk-forward sign-off.
# ===================================================================
# ADR-T5-01: SPECIALIST_ML_SATURATION_PCT = 2.0
# Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
# Rationale: clamps base_return_pct to +/-2% before mapping onto [0,100]. Must be > 0.
SPECIALIST_ML_SATURATION_PCT = float(os.getenv("SPECIALIST_ML_SATURATION_PCT", "2.0"))
# ADR-T5-02: SPECIALIST_ML_LLM_AGREEMENT_HIGH = 0.75
# Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
# Rationale: agreement >= 0.75 => "converged" branch.
SPECIALIST_ML_LLM_AGREEMENT_HIGH = float(
    os.getenv("SPECIALIST_ML_LLM_AGREEMENT_HIGH", "0.75")
)
# ADR-T5-03: SPECIALIST_ML_LLM_AGREEMENT_MID = 0.50
# Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
# Rationale: agreement >= 0.50 (but < HIGH) => "partial" branch; below => diverged.
SPECIALIST_ML_LLM_AGREEMENT_MID = float(
    os.getenv("SPECIALIST_ML_LLM_AGREEMENT_MID", "0.50")
)
# ADR-T5-04: SPECIALIST_BLEND_CONVERGED_ML_W = 0.55
# Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
# Rationale: converged-branch ML weight; sums 1.0 with LLM_W (ADR-T5-05).
SPECIALIST_BLEND_CONVERGED_ML_W = float(
    os.getenv("SPECIALIST_BLEND_CONVERGED_ML_W", "0.55")
)
# ADR-T5-05: SPECIALIST_BLEND_CONVERGED_LLM_W = 0.45
# Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
# Rationale: converged-branch LLM weight; sums 1.0 with ML_W (ADR-T5-04).
SPECIALIST_BLEND_CONVERGED_LLM_W = float(
    os.getenv("SPECIALIST_BLEND_CONVERGED_LLM_W", "0.45")
)
# ADR-T5-06: SPECIALIST_BLEND_PARTIAL_ML_W = 0.40
# Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
# Rationale: partial-branch ML weight (lower trust than converged); sums 1.0 with LLM_W.
SPECIALIST_BLEND_PARTIAL_ML_W = float(
    os.getenv("SPECIALIST_BLEND_PARTIAL_ML_W", "0.40")
)
# ADR-T5-07: SPECIALIST_BLEND_PARTIAL_LLM_W = 0.60
# Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
# Rationale: partial-branch LLM weight; sums 1.0 with ML_W (ADR-T5-06).
SPECIALIST_BLEND_PARTIAL_LLM_W = float(
    os.getenv("SPECIALIST_BLEND_PARTIAL_LLM_W", "0.60")
)
# ADR-T5-08: SPECIALIST_BLEND_DIVERGED_SHRINK = 0.30
# Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
# Rationale: diverged branch shrinks LLM toward neutral 50: 50 + (llm-50)*0.30.
SPECIALIST_BLEND_DIVERGED_SHRINK = float(
    os.getenv("SPECIALIST_BLEND_DIVERGED_SHRINK", "0.30")
)
SHADOW_TFT_VOTE_ENABLED = (
    os.getenv("SHADOW_TFT_VOTE_ENABLED", "False").lower() == "true"
)
# RPAR T2 (#1264) - deterministic, LLM-free specialist card fields (pros/cons/
# summary/headlines). OSS/desktop edition defaults ON (display-only, no cost);
# the cloud config.py keeps it OFF (dormant).
SPECIALIST_CARDS_ENABLED = (
    os.getenv("SPECIALIST_CARDS_ENABLED", "True").lower() == "true"
)
# Per-symbol TFT checkpoint root — mirrors config.py (Section 2.10: core reads it via
# get_config(), never os.getenv). Empty → model_registry falls back to core/ml/models/.
TFT_MODELS_ROOT = os.getenv("TFT_MODELS_ROOT", "")
# TFT serving correctness (M1 ×100 + M3a step-0) — mirrors config.py, default OFF
# (validate-before-activate; flag flip is trading-relevant).
TFT_SERVING_FIX = os.getenv("TFT_SERVING_FIX", "False").lower() == "true"
# TFT quality-gate honest metric (M3b) — mirrors config.py, default OFF. When ON the gate
# judges models by the honest OOS IC (walkforward_ic_oos506) not the noisy walkforward_ic.
TFT_QUALITY_GATE_HONEST_IC = (
    os.getenv("TFT_QUALITY_GATE_HONEST_IC", "False").lower() == "true"
)
# RPAR T6a (#1268) data-integrity guard - mirrors config.py. RQ-1 B5 (#1525): default-ON now
# that B1/B2/B3 made the inputs entity-correct/recent/count-unbiased. Decision-neutral: sets
# only the display data_quality/degraded fields + skip_llm on near-empty data.
DATA_INTEGRITY_GUARD_ENABLED = (
    os.getenv("DATA_INTEGRITY_GUARD_ENABLED", "True").lower() == "true"
)
# RQ-1 A3 (#1519): gate the +4/+5 count bonuses and the >=82 score auto-escalation behind
# one flag, default OFF, until B3 (#1523) makes sentiment directional. Mirrors config.py.
SPECIALIST_COUNT_BONUS_ENABLED = (
    os.getenv("SPECIALIST_COUNT_BONUS_ENABLED", "False").lower() == "true"
)
# RQ-1 B3b (#1536): fetch + parse each Form 4 document for the real insider buy/sell direction
# (the efts index has no transaction code). Default OFF -> no extra SEC requests. Mirrors config.py.
SPECIALIST_FORM4_DIRECTION_ENABLED = (
    os.getenv("SPECIALIST_FORM4_DIRECTION_ENABLED", "False").lower() == "true"
)
# RQ-1 B2 (#1522): per-source filing freshness in the report DTO; additive + flag-gated
# (default OFF -> byte-identical DTO / BORA parity). Mirrors config.py.
SPECIALIST_FRESHNESS_ENABLED = (
    os.getenv("SPECIALIST_FRESHNESS_ENABLED", "False").lower() == "true"
)
SPECIALIST_FRESHNESS_SLA_DAYS = int(os.getenv("SPECIALIST_FRESHNESS_SLA_DAYS", "30"))
# RPAR-T4 (#1268): route Specialist synthesis through the ADR-014 LLM seam for
# Bundle-output-parity - mirrors config.py, default OFF (validate-before-activate;
# flip is trading-relevant). OFF reproduces _call_gemini_sync byte-for-byte.
LLM_OUTPUT_PARITY = os.getenv("LLM_OUTPUT_PARITY", "False").lower() == "true"

# RPAR T3 (#1267) - Google-News-source parity, mirrors config.py, default OFF. When ON the
# Stock Specialist merges Google-News-RSS headlines with the Polygon headlines (Google-first,
# case-insensitive dedup, cap 10). OFF = byte-identical Polygon-only recent_headlines.
SPECIALIST_NEWS_V2 = os.getenv("SPECIALIST_NEWS_V2", "False").lower() == "true"

# Portfolio Context & Gatekeeper (GAP9)
GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED = (
    os.getenv("GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED", "False").lower() == "true"
)
GATEKEEPER_REQUIRE_CONTEXT = (
    os.getenv("GATEKEEPER_REQUIRE_CONTEXT", "False").lower() == "true"
)

# StockSpecialistRegistry re-enable (RPAR-#1284 / G1b). ON -> start_live_strategy
# constructs+starts the registry and wires SpecialistAlphaAgent; OFF -> registry stays
# None. This is a P1 decision-path change. The OSS/desktop edition (paper trading,
# single-tenant, local AI = no per-call cost) defaults ON so the Specialist cards have
# data and the Round Table gets the specialist signal. The CLOUD config.py keeps it OFF
# and human-gated (24h review + #76 shadow harness) before any real-money flip.
SPECIALIST_REGISTRY_ENABLED = (
    os.getenv("SPECIALIST_REGISTRY_ENABLED", "True").lower() == "true"
)

# #1346: config-gated SpecialistAlphaAgent vote weight (default 0.0 = dormant). The
# finance-core reads it via get_config() (CODING_POLICY §2.10), not os.environ.
SPECIALIST_ALPHA_WEIGHT = float(os.getenv("SPECIALIST_ALPHA_WEIGHT", "0.0") or "0.0")

# Insight-Quality ratchet (RPAR T6b #1271) - mirrors config.py, default OFF
# (dormant). When ON, the specialist synthesis is graded and may be rewritten /
# abstained, which can change sentiment_score on the order path -> gated like
# ML_SENTIMENT_BLEND_ENABLED. PR-1 lands the package + flag dormant; research()
# wiring is PR-2 (nothing reads this flag on the engine path yet).
INSIGHT_QUALITY_ENABLED = (
    os.getenv("INSIGHT_QUALITY_ENABLED", "False").lower() == "true"
)

# RPAR-1 (#1262) Abschluss / #1490: deterministic, bundle-free report-quality badge (default OFF →
# the serializer emits no report_quality key → byte-identical). Mirrors config.py.
REPORT_QUALITY_BADGE_ENABLED = (
    os.getenv("REPORT_QUALITY_BADGE_ENABLED", "False").lower() == "true"
)

USER_DATA_DIR = os.getenv("AAA_USER_DATA_DIR") or os.path.join(PROJECT_ROOT, "data")
REDIS_URL = os.getenv("REDIS_URL", "").strip()
GCP_PROJECT_ID = _clean_env("GCP_PROJECT_ID")
GCP_REGION = _clean_env("GCP_REGION", "us-central1")
VERTEX_ENDPOINT_ID = _clean_env("VERTEX_ENDPOINT_ID")
SHADOW_TFT_VOTE_CHAIN_PATH = os.getenv(
    "SHADOW_TFT_VOTE_CHAIN_PATH", os.path.join(USER_DATA_DIR, "shadow_tft_votes.jsonl")
)


# --- INF-13 Desktop Telemetry (P2 #1456) — all default OFF/safe -------------
# Local crash/stability capture (P1 #1373) is always-on and consent-free
# (nothing leaves the device). These flags gate EGRESS and local-store
# retention only. Egress additionally requires the Terraform-side
# enable_telemetry_backend (#1457, activation-gated). Mirrored to config.py
# (cloud) for dual-edition parity.
# Consent — opt-in, default OFF (§25 TDDDG / Art.6 DSGVO, #1368 Gate ④):
TELEMETRY_CRASH_CONSENT = (
    os.getenv("TELEMETRY_CRASH_CONSENT", "False").lower() == "true"
)
TELEMETRY_USAGE_CONSENT = (
    os.getenv("TELEMETRY_USAGE_CONSENT", "False").lower() == "true"
)
# Egress master switch (client side of the activation gate) — default OFF:
TELEMETRY_EGRESS_ENABLED = (
    os.getenv("TELEMETRY_EGRESS_ENABLED", "False").lower() == "true"
)
# Local store retention (Art.5(1)(e)); consumed by core/telemetry_local.prune_store:
TELEMETRY_RETENTION_DAYS = float(os.getenv("TELEMETRY_RETENTION_DAYS", "7"))
TELEMETRY_RETENTION_MB = float(os.getenv("TELEMETRY_RETENTION_MB", "50"))


def get_config():
    return types.SimpleNamespace(**globals())
