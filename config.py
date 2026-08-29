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

# ADR-GTM-1b: setdefault fallback for the baked-in beta public key so it ships to engine spawn
os.environ.setdefault(
    "AAA_ENTITLEMENT_PUBKEYS", "Rj4yopwrtnELzX309AV5vBAOuAqhlcL49IFsSDljPw0="
)

# --- Project paths (for subfolder layout) ---
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# --- Engine API (for multiple instances use ENGINE_PORT=8001 etc.) ---
ENGINE_PORT = int(os.getenv("ENGINE_PORT", "8001"))
AUTO_START_STRATEGY = os.getenv("AUTO_START_STRATEGY", "True").lower() == "true"
# Off-hours paper trading bypass (default False — production always respects market hours)
BYPASS_MARKET_HOURS = os.getenv("BYPASS_MARKET_HOURS", "False").lower() == "true"
# Market-closed report-only mode (owner 2026-07-26): market-closed → ONE full-universe panel
# refresh (reports stay complete 24/7) then idle; skip the continuous deep-eval + order machinery
# so the GPU rests. Default ON; MARKET_CLOSED_REPORT_ONLY=False restores legacy INC-6 behaviour.
MARKET_CLOSED_REPORT_ONLY = (
    os.getenv("MARKET_CLOSED_REPORT_ONLY", "True").lower() == "true"
)


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

# --- Alpaca Data Feed (ML-1 / LIVE-1 T1 #1424 alignment) ---
# SIP (consolidated NBBO) is REQUIRED by MiFID II Art. 27 for Fremdkapital (cloud/Enterprise).
# But Art. 27 binds investment firms executing CLIENT orders — the OSS desktop edition trades the
# operator's OWN capital, so Art. 27 is N/A and SIP must NOT be a default requirement.
# live_trading_guard.py already skips the SIP check for DEPLOYMENT_MODE=LOCAL; the config default
# must match: IEX (free Alpaca tier) for both paper AND live.
# Override: ALPACA_DATA_FEED=sip if the operator has an Algo Trader Plus ($99/mo) or higher plan.
ALPACA_DATA_FEED = os.getenv("ALPACA_DATA_FEED", "iex")

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

# Simulator Settings (Alpaca Sim-Day)
SIM_MODE = os.getenv("AAA_SIM_MODE", "False").lower() == "true"
SIM_DATE = os.getenv("AAA_SIM_DATE", "")
SIM_WINDOW = os.getenv("AAA_SIM_WINDOW", "09:30-10:30 ET")
SIM_CADENCE = os.getenv("AAA_SIM_CADENCE", "1m")
# ADR-SIM-01 (#2693): TRADING days to replay; 1 = byte-identical single-day harness.
# A one-day P&L is structurally blind to exit policy (see config.py for the full rationale).
SIM_DAYS = int(os.getenv("SIM_DAYS", "1") or "1")
SIM_PACING = os.getenv("AAA_SIM_PACING", "flat-out")
# #2548 C2: local offline replay corpus (dir of {SYMBOL}.parquet|.csv). Mirrors config.py (BORA).
SIM_CORPUS_DIR = os.getenv("AAA_SIM_CORPUS_DIR", "sim_corpus")

# --- Alpaca OAuth Settings (Multi-Tenant) ---
OAUTH_CLIENT_ID = _clean_env("OAUTH_CLIENT_ID")
OAUTH_CLIENT_SECRET = _clean_env("OAUTH_CLIENT_SECRET")
OAUTH_REDIRECT_URI = _clean_env(
    "OAUTH_REDIRECT_URI", "http://127.0.0.1:8081/auth/alpaca/callback"
)

# --- Cloud SQL Settings (User Metadata) ---
DATABASE_URL = _clean_env("DATABASE_URL")

# --- GCP / Vertex AI Configuration ---
# Dormant, unset-by-default cloud config (#2372): GCP_PROJECT_ID/GCP_REGION/VERTEX_ENDPOINT_ID
# are defined below and read only by the dormant Vertex path — inert unless VERTEX_ENDPOINT_ID is set.

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


# INC-1 F1a-UX (mirror of config.py): when a LIVE boot silently degrades to PAPER (shadow-boot
# pre-flight failed), the reason is recorded here so a health/status field can surface it to the
# operator instead of the failure being invisible. ``None`` = no degrade recorded. Best-effort at
# boot; never a source of truth for trading mode (PAPER_TRADING is), purely a visibility breadcrumb.
LIVE_ENABLE_FAILED_REASON = None

# LSR R2 (#2253, mirror of config.py): True (best-effort at boot) when a live boot degraded to PAPER
# for a TRANSIENT reason (Alpaca 429/5xx/timeout, Redis timeout, Ollama-warming #11) — the WORM
# `enable` is KEPT so the next clean boot re-arms and retries. A TERMINAL degrade (Alpaca 401/403)
# instead writes a compensating WORM `disable` (core/live_worm.revoke_live) and leaves this False.
# Surfaced in /health next to LIVE_ENABLE_FAILED_REASON; never a source of truth for trading mode.
DEGRADED_LIVE = False


def force_paper_trading(reason: str = "") -> None:
    """Fail-closed runtime downgrade to PAPER trading (#1918) — mirror of config.py.

    The flat OSS edition (renamed to config.py in the shipped desktop snapshot —
    oss_make_snapshot.sh / make-release.ps1) holds config as module globals, so reset
    PAPER_TRADING and the derived Alpaca account / base-URL / data-feed back to the PAPER slots
    (never the live keys). Used by the live-trading guard to degrade instead of crash when the
    live entitlement lapses after the operator armed live — keeps the engine up so open positions
    stay managed (kill-switch/HITL/exits) and /api/live/disable stays reachable. Idempotent.
    """
    global PAPER_TRADING, ALPACA_API_KEY, ALPACA_SECRET_KEY
    global ALPACA_BASE_URL, ALPACA_DATA_FEED, BASE_URL
    import logging

    _paper_url = "https://paper-api.alpaca.markets"
    PAPER_TRADING = True
    os.environ["PAPER_TRADING"] = "true"
    # Fail-closed: force the PAPER endpoint UNCONDITIONALLY (a live ALPACA_BASE_URL override must
    # NOT survive a downgrade). Keep os.environ in lock-step for consumers that read the raw env
    # (e.g. core/kill_switch.py) rather than the config module.
    os.environ["ALPACA_BASE_URL"] = _paper_url
    # Re-select the PAPER account (never the live keys).
    ALPACA_API_KEY = API_KEY
    ALPACA_SECRET_KEY = API_SECRET
    # BOTH base-URL globals must be reset: the engine's primary broker init derives its paper/live
    # mode from the LEGACY ``BASE_URL`` global — ``is_paper = "paper" in config.BASE_URL`` in
    # core/engine/api_routes.py::_init_trading_clients — NOT from PAPER_TRADING. Missing BASE_URL
    # here would leave the desktop broker pointed at the LIVE endpoint after a downgrade (the exact
    # #1918 failure). ``ALPACA_BASE_URL`` is the modern alias kept in sync.
    BASE_URL = _paper_url
    ALPACA_BASE_URL = _paper_url
    # Paper defaults to the free IEX feed unless explicitly overridden (SIP on paper is harmless).
    ALPACA_DATA_FEED = os.getenv("ALPACA_DATA_FEED", "iex")
    logging.critical(
        "CRITICAL: forced PAPER_TRADING downgrade%s — live order execution disabled; open "
        "positions remain managed (kill-switch/HITL/exits active)."
        % (f" ({reason})" if reason else "")
    )


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

# Issue 2935: Materialitaets-Riegel (Fix 2b).
# Verhindert den Einstieg in Splitter-Positionen, die anschliessend als Staub das Portfolio blockieren.
# 0.25 = 25% der Zielgewichtung (equity / max_positions). 0.0 = Dämpfung aus.
MATERIAL_ENTRY_MIN_PCT_OF_TARGET = float(
    os.getenv("MATERIAL_ENTRY_MIN_PCT_OF_TARGET", "0.0") or "0.0"
)

# --- Position Sizing Settings ---
MAX_POSITION_PERCENT = 0.25  # Max 25% of portfolio per single position
MIN_POSITION_PERCENT = 0.05  # Min 5% of portfolio per trade (for low conviction)
RISK_PER_TRADE_PERCENT = 0.02  # Risk 2% per trade (for stop-loss calculation)
# Order floors — Alpaca's real $1 notional minimum (fractional shares, $0 commission).
# ADR-R09/R11: the legacy flat $50 slippage buffer + $50 dust filter zeroed EVERY order on a funded
# small account ((cash-50)/slots < $50 → blocked:risk "size 0 (risk/cash)"). Alpaca supports fractional
# notional at $0 commission, so the sub-$50 rationale is void; $1 is the only hard floor (risk_manager.py
# also enforces it at :858-861). Mirrored in config.py (BORA parity).
# PRODUCT CONSTRAINT (rev. of #2714): small accounts trade fractional shares well
# below $50 legitimately - default stays at Alpaca's $1 technical minimum (mirrors
# config.py); churn protection comes from the frequency guards, not order size.
MIN_ORDER_VALUE_USD = float(os.getenv("MIN_ORDER_VALUE_USD", "1.0"))
# #2965 (flag-dark): per-cycle durable entry-time + strategy bridge. OFF =
# byte-identical. Mirrors config.py (BORA parity).
ENTRY_TIME_RECONCILE_PER_CYCLE = os.getenv(
    "ENTRY_TIME_RECONCILE_PER_CYCLE", "false"
).strip().lower() in ("1", "true", "yes")

# ADR-R12 (#2960): entry floor INSIDE the total-exposure clamp branch — headroom
# residues of $1-$5 must not become dust positions. Fires only when the clamp
# binds (never on a small account's normal order, the #2784 objection). Default
# 0.0 = OFF = byte-identical. Mirrors config.py (BORA parity).
EXPOSURE_CLAMP_MIN_NOTIONAL_USD = float(
    os.getenv("EXPOSURE_CLAMP_MIN_NOTIONAL_USD", "0.0")
)
RISK_CASH_BUFFER_USD = (
    1.0  # Cash slippage/rounding buffer — the 5% MAX_TOTAL_EXPOSURE is the real cushion
)
# #2789: RELATIVE dust floor for EXIT orders at the two submit paths (mirrors config.py,
# BORA parity). The absolute floor above is an ENTRY knob — raising it to $50 suppressed
# no exit dust (its only exit-side consumer, the trim lever, is default-OFF) and blocked
# small accounts (#2721 → reverted by #2784). Relative thresholds cannot block a small
# account; stops, unlabeled paths and FULL closes are exempt in core/dust_floor.py.
DUST_EXIT_FLOOR_ENABLED = (
    os.getenv("DUST_EXIT_FLOOR_ENABLED", "False").lower() == "true"
)
# Rule A (primary): partial exit below this share of the reduced position = sliver.
DUST_EXIT_MIN_POSITION_PCT = float(os.getenv("DUST_EXIT_MIN_POSITION_PCT", "0.10"))
# Rule B (backstop, only when position value is unknown) — MUST stay strictly weaker
# than rule A (~1% of equity), else it becomes the binding rule on large accounts.
DUST_EXIT_MIN_EQUITY_PCT = float(os.getenv("DUST_EXIT_MIN_EQUITY_PCT", "0.001"))
MAX_POSITIONS = 10  # Maximum number of simultaneous positions in portfolio
# O3 (cash-aware sizing): the cash-constraint divisor = the REMAINING free slots
# (max_positions − currently-held), not the fixed book cap. So a mostly-invested book with little
# free cash still funds a PROPER-sized position (freed cash ÷ few remaining slots) instead of a
# dust order (cash ÷ 10). The MAX rail (MAX_POSITION_PERCENT / ESMA order cap) still bounds the top.
# OFF → the fixed /max_positions divisor, byte-identical to before. Mode-neutral (paper == live).
# Mirrored in config.py (BORA parity).
CASH_AWARE_SLOTS_ENABLED = (
    os.getenv("CASH_AWARE_SLOTS_ENABLED", "True").lower() == "true"
)

# ADR-R10: Hebel-Verbot (default AN). Der Sizer lehnt jede Order ab, deren Wert den freien
# Cash übersteigt — und damit jede neue Position, solange der Cash negativ (= geliehen) ist.
# Warum default AN, obwohl es den Handelspfad berührt: cash < 0 bedeutet Exposure > 100 %,
# was MAX_TOTAL_EXPOSURE_PCT (0.95) bereits verbietet. Die Regel erzwingt also nur, was die
# Config ohnehin behauptet, und bindet ausschliesslich in Zustaenden, in denen jener Deckel
# schon versagt hat (er faellt aus, wenn kein Broker-Client vorliegt, risk_manager.py:517).
# Sie kann eine Order nur verkleinern, nie vergroessern. Auf false setzen erlaubt Margin.
RISK_FORBID_LEVERAGE = os.getenv("RISK_FORBID_LEVERAGE", "True").lower() == "true"

# --- Dynamic Position Sizing (Conviction-Based) ---
# Position size scales between MIN and MAX based on conviction score
# Conviction is calculated from: model confidence, RSI, ADX, MACD alignment
ENABLE_DYNAMIC_SIZING = True  # True = size based on conviction, False = fixed sizing

# Conviction EWMA + top-up dead-band (churn fix). The conviction that drives dynamic sizing re-rates
# every cycle from the raw round-table score, so a held name's target weight jitters → repeated micro
# top-ups (buy-high/sell-low churn). CONVICTION_EWMA smooths conviction per symbol:
#   smoothed = alpha·raw + (1-alpha)·prev  (first cycle → raw). Lower alpha = smoother/laggier.
# POSITION_TOPUP_DEAD_BAND_PCT: do NOT top up a held name already within this % of its (smoothed)
# conviction target weight — no re-buy for a marginal move. Default on; env rollback. Mirrored in config.py.
CONVICTION_EWMA_ENABLED = os.getenv("CONVICTION_EWMA_ENABLED", "True").lower() == "true"
CONVICTION_EWMA_ALPHA = float(os.getenv("CONVICTION_EWMA_ALPHA", "0.3") or "0.3")
POSITION_TOPUP_DEAD_BAND_PCT = float(
    os.getenv("POSITION_TOPUP_DEAD_BAND_PCT", "5.0") or "5.0"
)

# --- #1953 TRD-2: Vol-Targeting / Risk-Parity sizing (default ON) ---
# Master switch: ON (default) => calculate_position_size multiplies the plumbed HAR-RV
# forecast_vol into final_risk_scaler as clip(target/fv, lo, hi); a missing/invalid
# forecast is a fail-safe no-op (scaler 1.0). OFF => vol-blind sizing (env rollback).
# Default flipped ON ON OWNER RESPONSIBILITY (owner-waiver 2026-08-14, in lieu of the
# walk-forward OOS gate — proxy evidence accepted; #1953 is a Sharpe-neutral drawdown
# dial, MiFID II Art. 17). USER-SETTABLE via "Volatilitäts-Sizing" + VOL_TARGET_DAILY_VOL.
# Mirrored in config.py (BORA parity).
VOL_TARGETING_SIZING_ENABLED = (
    os.getenv("VOL_TARGETING_SIZING_ENABLED", "True").lower() == "true"
)
# ADR-R13: Vol-Targeting-Kalibrierung — target 1.5%/Tag, Scaler-Bounds [0.5, 1.5]
# Basis: MiFID II Art. 17 (Risk-Controls im algorithmischen Handel); Werte gespiegelt
# aus den gewachsenen vol_model.vol_size_scaler-Defaults (core/ml/vol_model.py).
# Begründung: 1.5%/Tag Ziel-Vol ≈ Median der Universe-Forecasts (HAR-RV, tägl. Stdev);
# die Bounds begrenzen den Risk-Parity-Eingriff auf ±50% der conviction-Größe, damit
# ein Ausreißer-Forecast nie eine Order dominiert. Finance-Judgement, nicht auf die
# aktuelle Universe-Verteilung gefittet — Backtest-Kalibrierung + jährlicher Review
# (Plan #1953 §12.6); bis dahin env-tunbar. Inert solange der Master-Schalter OFF ist.
VOL_TARGET_DAILY_VOL = float(os.getenv("VOL_TARGET_DAILY_VOL", "0.015"))
VOL_SIZE_SCALER_LO = float(os.getenv("VOL_SIZE_SCALER_LO", "0.5"))
VOL_SIZE_SCALER_HI = float(os.getenv("VOL_SIZE_SCALER_HI", "1.5"))

# #3095 (b): UpsideSkewAgent — 25Δ Risk-Reversal als direktionales Aufwärtssignal.
# Default OFF (dark) = byte-identisch. UPSIDE_SKEW_WEIGHT = Stimmgewicht.
UPSIDE_SKEW_AGENT_ENABLED = (
    os.getenv("UPSIDE_SKEW_AGENT_ENABLED", "false").lower() == "true"
)
UPSIDE_SKEW_WEIGHT = float(os.getenv("UPSIDE_SKEW_WEIGHT", "0.30"))
# #3094 (a): per-name IMPLIED vol -> #1953 forecast_vol sizer; VIXAware leaves the
# direction mean. Default OFF = byte-identical. IV_VRP_DEBIAS_FACTOR = median RV/IV
# (ADR-a01) removes the VRP level bias before the daily scaler; calibrated in sweep.
IMPLIED_VOL_FORECAST_ENABLED = (
    os.getenv("IMPLIED_VOL_FORECAST_ENABLED", "false").lower() == "true"
)
IV_VRP_DEBIAS_FACTOR = float(os.getenv("IV_VRP_DEBIAS_FACTOR", "0.80"))
# #3099 (H2): bounded calm-regime risk-on step in the ADR-R08 VIX ladder — more
# invested in a favorable regime, NEVER leverage. Default OFF = byte-identical.
# The >1.0 step is capped downstream by MAX_TOTAL_EXPOSURE_PCT + per-name caps +
# RISK_FORBID_LEVERAGE. BULL_EXPOSURE_CALM_SCALER (VIX<=18) calibrated in sweep.
BULL_EXPOSURE_ENABLED = os.getenv("BULL_EXPOSURE_ENABLED", "false").lower() == "true"
BULL_EXPOSURE_CALM_SCALER = float(os.getenv("BULL_EXPOSURE_CALM_SCALER", "1.15"))

# --- Portfolio Management Settings ---
# Smart rebalancing: Only adjust positions when drift exceeds threshold
REBALANCE_DRIFT_THRESHOLD_PCT = 3.0  # Only rebalance if position drifts >3% from target
REBALANCE_COOLDOWN_HOURS = 2.0  # Minimum hours between rebalances per symbol
# (MIN_HOLD_HOURS_PORTFOLIO removed #2372: dead — no reader, absent in Enterprise; the portfolio
# rebalance-sell gate reads MIN_HOLD_HOURS below.)
MAX_TRADES_PER_SYMBOL_PER_DAY = 5  # Max round-trips per symbol per day
CONSECUTIVE_SELL_BYPASS_THRESHOLD = (
    8  # Number of consecutive SELL signals to bypass hold period
)
# ADR-R12 (anti-churn Part C, #2176): min seconds between OPENING orders for the same
# symbol — blocks buy->sell->buy micro-churn that burns the daily-trade budget. BUY-side
# only (exits never gated); per-symbol; complements MAX_TRADES_PER_SYMBOL_PER_DAY.
MIN_ORDER_INTERVAL_SEC = 900
# ADR-R14 (#2696): displacement honours SMART_EXIT_MIN_HOLD_DAYS (and couples the
# 'held >5 days with minimal gain' heuristic to it). See config.py for the full rationale.
DISPLACEMENT_RESPECTS_MIN_HOLD = (
    os.getenv("DISPLACEMENT_RESPECTS_MIN_HOLD", "True").lower() == "true"
)
# #2940: Naht fuer die Haltedauer-Kurve im Positions-Score. Defaults reproduzieren
# die historischen Literale byte-identisch; nur ein Sweep-Knopf. Siehe config.py und
# core/portfolio_manager.py::holding_period_score fuer die Begruendung.
HOLDING_PERIOD_SCORE_FRESH = float(
    os.getenv("HOLDING_PERIOD_SCORE_FRESH", "30.0") or "30.0"
)
# #2940: 0.0 = historische Stufen (Maximum ab Tag 7). > 0 = lineare Reifung von
# HOLDING_PERIOD_SCORE_FRESH auf 90 ueber diese Zahl von Tagen.
HOLDING_PERIOD_SCORE_SPAN_DAYS = float(
    os.getenv("HOLDING_PERIOD_SCORE_SPAN_DAYS", "0.0") or "0.0"
)

# --- Global Settings ---
NEWS_POLLING_INTERVAL_SECONDS = (
    99999999 if os.getenv("AAA_SIM_MODE", "False").lower() == "true" else 60 * 5
)
STRATEGY_MONITOR_INTERVAL_SECONDS = 30 * 60  # Check for strategy switch every 30 mins
# #1832 — TIME-driven loop-stall threshold: flag the trading loop STALLED once its last
# completed cycle is older than this WHILE the market is open (catches a fully-dead loop the
# cycle-driven CycleWatchdog misses). Default 3x the monitor interval (90 min); env-overridable.
# #2142: consecutive active-ping failures before the LatencyWatchdog trips the
# global kill-switch (debounce a single transient Alpaca blip; passive real-order
# latency stays immediate).
# ADR-2142-01: LATENCY_TRIP_CONSECUTIVE = 3
# Basis: EU AI Act Art. 14 human-oversight + operational safety (kill-switch debounce).
# Rationale: trip only after 3 CONSECUTIVE threshold-breaching active pings (~<=30s at the
# 10s ping interval), so a single transient Alpaca blip cannot halt all trading, while a
# sustained outage still trips promptly. Passive real-order-latency trip stays immediate.
# 1 = today's trip-on-first-failure behaviour. Conservative default; annual review.
LATENCY_TRIP_CONSECUTIVE = int(os.getenv("LATENCY_TRIP_CONSECUTIVE", "3"))
LOOP_STALL_AFTER_SECONDS = int(os.getenv("LOOP_STALL_AFTER_SECONDS", "5400"))
# #1832 increment 2 — the independent stall-monitor thread checks loop liveness this often and
# emits a loop_stalled OTel span + Slack alert once per stall episode. Default-on (safety obs).
LOOP_STALL_CHECK_INTERVAL_SECONDS = int(
    os.getenv("LOOP_STALL_CHECK_INTERVAL_SECONDS", "300")
)
ENABLE_LOOP_STALL_MONITOR = (
    os.getenv("ENABLE_LOOP_STALL_MONITOR", "true").lower() == "true"
)
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

# Min-hold + hysteresis gate on smart_exit rule #1 (rebalance-sell).
# #2839: defaults = shipped desktop launcher profile (desktop is master).
SMART_EXIT_MIN_HOLD_DAYS = float(os.getenv("SMART_EXIT_MIN_HOLD_DAYS", "20.0"))
SMART_EXIT_EXIT_RANK_HYSTERESIS = float(
    os.getenv("SMART_EXIT_EXIT_RANK_HYSTERESIS", "5.0")
)

# --- Autonomous SELL decisions: de-concentration TRIM + consensus-drop ROTATION ---
# Two flag-gated per-cycle exit levers (the trading loop emits ~0 organic SELL because
# the rank-drop signal is averaged away by the bullish conditioners). Each ARMS an
# autonomous real-money SELL path and ships OFF — an independent rollback lever.
# #2712 Inc 2: displacement sells AFTER the swap BUY confirms (False = legacy).
DISPLACEMENT_BUY_FIRST = os.getenv("DISPLACEMENT_BUY_FIRST", "True").lower() == "true"
# #2711 (P0): min-hold is a HARD gate for rotation/rank exits (False = legacy OR).
ROTATION_MIN_HOLD_HARD_GATE = (
    os.getenv("ROTATION_MIN_HOLD_HARD_GATE", "True").lower() == "true"
)
#   ROTATION_EXIT_ENABLED: sell a held name that dropped out of the LSTM top-N past
#     hysteresis / min-hold (reuses SMART_EXIT_MIN_HOLD_DAYS + SMART_EXIT_EXIT_RANK_HYSTERESIS).
#   DECONCENTRATION_TRIM_ENABLED: partially reduce an overweight name toward target
#     (a $-fraction of its market value — never a price division, never oversells).
#   ROTATION_PANEL_MAX_AGE_DAYS: refuse a STALE LSTM panel (a populated but outdated
#     snapshot would pin exits to an old top-K). Date-normalized (now.date() - snapshot_date).
# Flags OFF → byte-identical no-op (no panel/recommender call, no SELL). Both levers only
# ever REDUCE exposure; the SELL still passes ComplianceGuardian + kill-switch + market-hours.
# Mode-neutral (paper == live). Mirrored in config.py (BORA parity).
# ROTATION now defaults ON (owner directive 2026-08-03): a held name that drops out of the
# LSTM top-N is exited, bounded by BOTH ROTATION_MAX_EXITS_PER_CYCLE=1 (≤1 exit/cycle) AND
# #2714: the TRIM lever gets a session cap too (rotation already had one).
TRIM_MAX_EXITS_PER_SESSION = int(os.getenv("TRIM_MAX_EXITS_PER_SESSION", "3"))
# ROTATION_MAX_EXITS_PER_SESSION=3 (≤3 exits/trading-day). The per-cycle cap ALONE does NOT
# prevent a whole-book flush — rotation exits are risk-reducing SELLs, exempt from the
# daily-trades cap, and the loop runs ~390 cycles/session, so a sustained ranking shift would
# otherwise drain the book one name per cycle over the day (the 2026-07-28 terminal state,
# only slower). The per-SESSION ceiling is what actually bounds it. TRIM stays OFF (it was the
# 'flatten every name to 10%' half of that incident). Set ROTATION_EXIT_ENABLED=false to opt out.
ROTATION_EXIT_ENABLED = os.getenv("ROTATION_EXIT_ENABLED", "True").lower() == "true"
DECONCENTRATION_TRIM_ENABLED = (
    os.getenv("DECONCENTRATION_TRIM_ENABLED", "False").lower() == "true"
)
# Conviction-weighting fix (churn root cause; mirrors config.py): the dynamic sizer targets 5–25%
# per name by conviction, but the trim de-concentrated every name back to flat 1/max_positions (10%)
# → two contradictory target-weight authorities → buy-high/sell-low churn. RESPECTS_CONVICTION=True →
# de-concentrate ONLY genuine over-concentration past MAX_POSITION_PERCENT (=25%), leaving the sizer's
# band intact (let winners run; only price-drift over the cap trims). Never INCREASE. False → legacy 10%.
DECONCENTRATION_TRIM_RESPECTS_CONVICTION = (
    os.getenv("DECONCENTRATION_TRIM_RESPECTS_CONVICTION", "True").lower() == "true"
)
# ADR-EXIT-02 (#2681, mirrors config.py) — trailing-tier profile of
# core/intelligent_exit.py, resolved at CALL time. "legacy" (DEFAULT) = today's table,
# byte-identical; "midterm" = wider trails + longer min-holds (mid-term hold pattern).
# Unknown value -> "legacy" + WARNING. Loss side (LOSS_TIER_*) untouched by the profile.
# #2839: default legacy→midterm = shipped desktop launcher profile (desktop is master).
EXIT_TRAIL_PROFILE = os.getenv("EXIT_TRAIL_PROFILE", "midterm")
ROTATION_PANEL_MAX_AGE_DAYS = int(os.getenv("ROTATION_PANEL_MAX_AGE_DAYS", "3"))
# Rotation per-cycle cap (2026-07-28 incident): bound how many FULL rotation exits the
# hook dispatches in ONE cycle, so a single ranking shift cannot flush the whole book at
# once (it drains gradually over cycles). Default 1 (owner directive 2026-08-03, tightened
# from 2 when ROTATION_EXIT_ENABLED flipped ON): at most ONE full exit per cycle — the most
# conservative staged drain. A value <=0 FLOORS to 1 in the loop (NOT "unlimited" — that was
# a footgun; disable rotation via ROTATION_EXIT_ENABLED). SELL-side sibling of #2546's
# INTRADAY_TIMING buy-pacing; mirrored in config.py.
ROTATION_MAX_EXITS_PER_CYCLE = int(os.getenv("ROTATION_MAX_EXITS_PER_CYCLE", "1"))
# Rotation per-SESSION ceiling (2026-08-03, owner directive): the per-cycle cap alone does
# NOT prevent a whole-book flush — rotation exits are risk-reducing SELLs, EXEMPT from the
# daily-trades cap (COMPLIANCE_ALLOW_RISK_REDUCING_EXITS default True), and the trading loop
# runs ~390 cycles/session, so a sustained ranking shift would drain the book one name per
# cycle over the day (the 2026-07-28 terminal state, only slower). This caps CUMULATIVE
# rotation exits per TRADING DAY (the loop resets the counter on date rollover); protective
# stop-losses stay UNCAPPED. Default 3; <=0 = unlimited (byte-identical rollback). Mirrored
# in config.py (BORA parity).
# #2839: rotation session cap 3→2 = shipped desktop launcher profile (desktop is master).
ROTATION_MAX_EXITS_PER_SESSION = int(os.getenv("ROTATION_MAX_EXITS_PER_SESSION", "2"))

# --- Intelligent Exit System (Epic 2.4) — 5-Tier Kapitalschutz ---
# Loss Management: Verlierer schnell schneiden
LOSS_TIER_1_PCT = -2.0  # Nach -2%: Beobachtungsmodus
LOSS_TIER_2_PCT = -4.0  # Nach -4%: Erhöhte Wachsamkeit
LOSS_TIER_3_PCT = -6.0  # Nach -6%: Aggressiver Exit
HARD_STOP_LOSS_PCT = -8.0  # Absoluter Hard Stop (kein Override möglich)
# Winner Management: Gewinner laufen lassen
MOMENTUM_SELL_THRESHOLD = -0.3  # LSTM unter -0.3 = Momentum endet
# General
MIN_HOLD_HOURS = 1.0  # 1h min hold (Anti-Churn) — BORA parity with Enterprise config.py:171 (#2372); was 0.5, accidental drift from the two-constant collapse
PANIC_PROTECTION_HOURS = 2.0  # Erste 2h: kein Verkauf (außer Hard Stop)
# desktop-exit: feed the per-cycle risk-exit a REAL remembered high-water-mark (peak since held) so the
# trailing stop can fire when a held winner rolls over. OFF → hwm defaults to current → drawdown 0 →
# trailing stop can never fire (held winners never trimmed, cash stuck). Default ON (operator directive;
# the trailing stop only ever PROTECTS gains — safe direction). Mirrored in config.py (BORA parity).
POSITION_EXIT_HWM_TRAILING_ENABLED = (
    os.getenv("POSITION_EXIT_HWM_TRAILING_ENABLED", "True").lower() == "true"
)
# #2557: persist the trailing-stop high-water-mark map across the daily desktop restart (today it is
# in-memory only → lost on reboot → trail measures drawdown only from boot; the pre-loop
# position_stop path can't fire a tier-trail with a missing HWM). Guarded ALSO by
# POSITION_EXIT_HWM_TRAILING_ENABLED. Default OFF (opt-in). Fail-safe: OFF or a store miss/error →
# prev={} → byte-identical (a missing peak only makes the trail STRICTER, never a false exit).
# Mirrored in config.py (BORA parity) — including how the value is PARSED. config.py is a
# pydantic BaseSettings (RuntimeConfigState, config.py:34): pydantic reads the env var itself
# and accepts "1"/"0"/"yes"/"on" as well as "true"/"false". This module has no such layer, so
# a bare `.lower() == "true"` made the SAME variable mean the OPPOSITE thing here — measured
# 2026-08-25: `=1` yielded True in config.py and False in config.oss.py. An operator setting
# `POSITION_EXIT_HWM_PERSIST_ENABLED=1` on the desktop silently got OFF. Parsed tolerantly now,
# so both editions agree. Default and shipped behaviour unchanged (OFF).
POSITION_EXIT_HWM_PERSIST_ENABLED = os.getenv(
    "POSITION_EXIT_HWM_PERSIST_ENABLED", "false"
).strip().lower() in ("1", "true", "yes")
# #2554: minutes a just-stopped-out / harvested name is locked OUT of BUY re-entry (the
# churn-carousel brake the BUY-side cooldowns never provide). 0 = disabled (byte-identical).
# Default 0 — enable after OOS validation. Mirrored in config.py (BORA parity).
STOPOUT_REENTRY_COOLDOWN_MIN = float(
    os.getenv("STOPOUT_REENTRY_COOLDOWN_MIN", "240") or "240"
)
# #2555: feed the per-cycle risk-exit the CURRENT holding's entry-time instead of
# min(_trade_history) (stale across a sold-then-rebought symbol → inflated hours_held →
# panic-bypass + tightened loss-cut on a fresh position). OFF → min() fallback (byte-
# identical). Default ON (correctness; fail-safe). Mirrored in config.py (BORA parity).
POSITION_EXIT_ENTRY_TIME_RECONCILE_ENABLED = (
    os.getenv("POSITION_EXIT_ENTRY_TIME_RECONCILE_ENABLED", "True").lower() == "true"
)

# ADR-CONV-01: make conviction mean "evidence FOR the action we are taking".
#
# rl_signal.py:114 scores model confidence as min(abs(model_pred) / 1.2, 1.0). Two defects
# in one line: abs() discards the SIGN, and the min() SATURATES at |pred| >= 1.2, so every
# real prediction (portfolio_manager.py:337 documents the range as "-5 to +5") pins that
# term to a constant. Measured on the real function: pred=-5.764 scores 0.950 while
# pred=+5.764 scores 0.800 -- a maximally BEARISH forecast outscores the mirror bullish one,
# on a BUY. Incoherent by inspection; no harness needed to call it wrong.
#
# When ON, evidence against the action contributes nothing toward it: agreement gates the
# score MULTIPLICATIVELY, so a bearish BUY lands at exactly 0.0. Multiplicative, not signed,
# for a measured reason -- conviction's whole effect on order size is ONE step at
# conv ~= 0.067; above it COMPLIANCE_MAX_ORDER_VALUE / the stop-loss constraint / the cash
# slot always bind first. Merely SIGNING the term (0.950 -> 0.550) leaves both values above
# the step and changes ZERO submitted orders. Only reaching 0.0 crosses it.
#
# SAFETY: the gate can only ever SHRINK a position (verified over 200k random draws against
# the legacy function: 0 violations). That is what makes it enable-able without a harness.
#
# This does NOT fix the 120/120-BUY behaviour. That is the reward function
# (trading_environment.py:533-536: holding cash pays -0.00001, holding a position pays
# daily_return, so cash is strictly dominated and "always invested" is CORRECT for that
# reward). Tracked separately. Default OFF -- the RLAgent path is unchanged on merge.
CONVICTION_SIGNED_ENABLED = (
    os.getenv("CONVICTION_SIGNED_ENABLED", "False").lower() == "true"
)

# --- Simulation execution costs (ADR-COST-01) ---
# Read ONLY by core/simulation.py — the backtest. No live order path reads these, so a
# change here alters no real trade. It alters which WORLD we measure, and every Sharpe we
# have ever quoted comes from that world.
#
# COMMISSION: $0. Alpaca charges no commission on US equities. The previous $0.50/side was
# not a conservative assumption but a factually wrong one, applied to every simulated fill.
# (Sell-side regulatory fees — SEC ~$27.80/$1M sold, TAF ~$0.000166/share — are real but
# ~0.3bps at our size; folded into slippage rather than modelled separately.)
#
# SLIPPAGE: 2bps/side. This is a JUDGEMENT, not a fact, so here is its basis: we trade S&P
# 500 large caps in $1-10k clips, where the quoted spread is ~1-2bps and our size is far
# below the top-of-book. 2bps/side is the half-spread plus the regulatory dust above. The
# previous 10bps/side models an illiquid name or institutional size — a different market
# than ours, ~5x too expensive.
#
# ⚠ THE ERROR HAD A DIRECTION. Overstated costs FLATTER any argument for reducing
# turnover, because they inflate the savings. Correcting them reportedly lifts top-quintile
# net Sharpe 0.758 -> ~1.35 with ZERO strategy change. That is ACCOUNTING, NOT IMPROVEMENT,
# and must never be reported as a win. The corrected figure is not a measurement either:
# model_card.json cites corrected_benchmark_results.json, which exists in no ref.
SLIPPAGE_PERCENT = 0.0002  # 2bps/side — half-spread, S&P 500 large caps, retail clips
COMMISSION_PER_TRADE = 0.0  # $ per side — Alpaca charges no commission on US equities

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

# --- Intraday Execution Timing & Buy Pacing (mirrors config.py, BORA parity) ---
# ADR-C05: Intraday Timing Guard — no BUY in the opening-noise window (wide spreads).
INTRADAY_TIMING_ENABLED = os.getenv("INTRADAY_TIMING_ENABLED", "True").lower() == "true"
NO_BUY_OPENING_MINUTES = int(os.getenv("NO_BUY_OPENING_MINUTES", "30"))
# ADR-C06: Buy Pacing Guard — spread BUYs (cooldown + <=N/hour) so a burst can't exhaust
# the 10-trade daily cap in one cycle. SELL-side sibling of ROTATION_MAX_EXITS_PER_CYCLE.
GLOBAL_BUY_COOLDOWN_MINUTES = int(os.getenv("GLOBAL_BUY_COOLDOWN_MINUTES", "30"))
MAX_BUYS_PER_HOUR = int(os.getenv("MAX_BUYS_PER_HOUR", "2"))
COMPLIANCE_HFT_MAX_ORDERS_PER_SEC_SYMBOL = int(
    os.getenv("COMPLIANCE_HFT_MAX_ORDERS_PER_SEC_SYMBOL", "2")
)
COMPLIANCE_HFT_MAX_ORDERS_PER_SEC_AGGREGATE = int(
    os.getenv("COMPLIANCE_HFT_MAX_ORDERS_PER_SEC_AGGREGATE", "10")
)

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

# --- EOD sector-constraint assessment (#2652, Epic #2655 Weg 1) — dormant by default.
# Mirrors config.py (BORA): market-close derives a RELATIVE sector-cap proposal, signs a
# local Ed25519 approval token (TTL below) and delivers it via the daily_report channels.
EOD_CONSTRAINT_ASSESSMENT_ENABLED = (
    os.getenv("EOD_CONSTRAINT_ASSESSMENT_ENABLED", "False").lower() == "true"
)
EOD_CONSTRAINT_TOKEN_TTL_HOURS = float(
    os.getenv("EOD_CONSTRAINT_TOKEN_TTL_HOURS", "12")
)


def _enforce_hitl_boot_gate(paper_trading, hitl_enabled, autonomous_unlimited):
    """EU AI Act Art. 14 (PR-0a) — mirror of config.py._enforce_hitl_boot_gate."""
    if not paper_trading and not hitl_enabled:
        # HOTFIX (live-boot-failsafe): on the DESKTOP (DEPLOYMENT_MODE=LOCAL) a raise here KILLS the
        # engine process before uvicorn binds → the shell restart-loops → the app bricks and the
        # operator can't reach /api/live/disable. Refuse live by DEGRADING to PAPER — compliant (no
        # unsupervised live capital decisions) AND the engine boots managed (kill-switch/HITL/exits).
        # Cloud (non-LOCAL) stays fail-closed HARD: a container restart-loop is a visible ops signal,
        # not a stranded user. Mirrors the #1918 live-guard philosophy.
        if os.environ.get("DEPLOYMENT_MODE", "").upper() == "LOCAL":
            logging.critical(
                "CRITICAL COMPLIANCE: live armed (PAPER_TRADING=False) without HITL_ENABLED=True "
                "(EU AI Act Art. 14) — REFUSING live, forcing PAPER so the desktop boots managed "
                "instead of crash-looping. Enable HITL to arm live."
            )
            force_paper_trading(
                reason="live armed without HITL_ENABLED (EU AI Act Art. 14) — desktop fail-safe"
            )
            return
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
# #2558 (exit-selective, ships dark): marketable-limit path for EXITS (SELLs) only,
# independent of the GLOBAL USE_LIMIT_ORDERS. BORA parity with config.py.
USE_LIMIT_EXITS = os.getenv("USE_LIMIT_EXITS", "False").lower() == "true"
LIMIT_ORDER_SPREAD_BUFFER_PCT = float(
    os.getenv("LIMIT_ORDER_SPREAD_BUFFER_PCT", "0.001")
)

# --- Slack Alerts ---
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
ENABLE_SLACK_ALERTS = os.getenv("ENABLE_SLACK_ALERTS", "False").lower() == "true"
ENABLE_HEARTBEAT = os.getenv("ENABLE_HEARTBEAT", "False").lower() == "true"
HEARTBEAT_INTERVAL_HOURS = int(os.getenv("HEARTBEAT_INTERVAL_HOURS", "6"))
# #1806 (GTM-1): periodic heartbeat is machine-only by default (no equity/PnL
# to Slack under GTM / multi-instance operation). Set True to opt back in.
HEARTBEAT_INCLUDE_EQUITY = (
    os.getenv("HEARTBEAT_INCLUDE_EQUITY", "False").lower() == "true"
)

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
# Desktop default ON (0.2.14): populates the card's ml_* fields (report-only measure;
# the decision-path blend stays behind ML_SENTIMENT_BLEND_ENABLED, default OFF).
ML_PREDICTION_ENABLED = os.getenv("ML_PREDICTION_ENABLED", "True").lower() == "true"
ML_SENTIMENT_BLEND_ENABLED = (
    os.getenv("ML_SENTIMENT_BLEND_ENABLED", "False").lower() == "true"
)
# Specialist report parity (RPAR Epic #1262, Task T1 #1265) - mirrors config.py, default
# OFF. When ON the synthesis uses the V2 prompt+parser (COMPANY/BULL/BEAR/THESIS prose);
# OFF reproduces today's 6-tuple path byte-for-byte (NEWS-8: no score/recommendation delta).
# Desktop default ON (0.2.14): the grounded card REQUIRES V2 prose — with grounding ON
# (desktop default) and PROMPT_V2 OFF, #2249 FIX 1 blanks every displayed case and a
# fresh install shows empty cards forever (live: first customer install 2026-07-23).
SPECIALIST_PROMPT_V2 = os.getenv("SPECIALIST_PROMPT_V2", "True").lower() == "true"
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
# SpecialistAlpha shadow vote (#76 measurement, activation runbook addition 3): recorded, NOT
# counted; default OFF (zero order impact). Mirrored in config.py (BORA).
SHADOW_SPECIALIST_VOTE_ENABLED = (
    os.getenv("SHADOW_SPECIALIST_VOTE_ENABLED", "False").lower() == "true"
)
# RPAR T2 (#1264) - deterministic, LLM-free specialist card fields (pros/cons/
# summary/headlines). OSS/desktop edition defaults ON (display-only, no cost);
# the cloud config.py keeps it OFF (dormant).
SPECIALIST_CARDS_ENABLED = (
    os.getenv("SPECIALIST_CARDS_ENABLED", "True").lower() == "true"
)
# Rich-synthesis card (Direction A) — mirrors config.py, default OFF (dormant).
# GROUNDING runs core.specialist.grounding on the V2 prose (headline-binding gate,
# the #2202 catcher) and switches the V2 prompt into [H#]-citation mode; CARD emits
# the card's citations/dropped-ledger/vol-band in the DTO. HARD-COUPLED: the card is
# forced OFF when grounding is off (fail-safe) so it can never surface ungrounded prose.
SPECIALIST_GROUNDING_ENABLED = (
    os.getenv("SPECIALIST_GROUNDING_ENABLED", "True").lower() == "true"
)
SPECIALIST_SYNTHESIS_CARD_ENABLED = (
    os.getenv("SPECIALIST_SYNTHESIS_CARD_ENABLED", "True").lower() == "true"
)
if SPECIALIST_SYNTHESIS_CARD_ENABLED and not SPECIALIST_GROUNDING_ENABLED:
    logging.getLogger(__name__).warning(
        "SPECIALIST_SYNTHESIS_CARD_ENABLED requires SPECIALIST_GROUNDING_ENABLED "
        "- forcing the card OFF (fail-safe)."
    )
    SPECIALIST_SYNTHESIS_CARD_ENABLED = False
# Per-symbol TFT checkpoint root — mirrors config.py (Section 2.10: core reads it via
# get_config(), never os.getenv). Empty → model_registry falls back to core/ml/models/.
TFT_MODELS_ROOT = os.getenv("TFT_MODELS_ROOT", "")
# ADR-MLA-11 (#1885): TFT serving correctness (M1 ×100 + M3a step-0) — mirrors config.py,
# default ON: serving must match the unit the trainer's scoring validated
# (train_tft_per_symbol._score_fold), else an activated TFT reads neutral/fake-confidence.
# TFT stays dormant in OSS (ML_PREDICTION_ENABLED default OFF) → no ship-behavior change.
# Rollback: env TFT_SERVING_FIX=0 restores the historical read byte-for-byte.
TFT_SERVING_FIX = os.getenv("TFT_SERVING_FIX", "True").lower() == "true"
# TFT quality-gate honest metric (M3b) — mirrors config.py, default OFF. When ON the gate
# judges models by the honest OOS IC (walkforward_ic_oos506) not the noisy walkforward_ic.
TFT_QUALITY_GATE_HONEST_IC = (
    os.getenv("TFT_QUALITY_GATE_HONEST_IC", "False").lower() == "true"
)
# ADR-ML-GATE-02 (MLR-3, #1903) — mirrors config.py. Net-of-cost Sharpe floor for the
# serving gate: when the offline layers stamped fdr_passed/net_sharpe into the model
# metadata, the gate requires fdr_passed == True AND net_sharpe > this floor.
# Default 0.0 (no money-losers after costs); fields absent → gate unchanged.
TFT_NET_SHARPE_FLOOR = float(os.getenv("TFT_NET_SHARPE_FLOOR", "0.0"))
# ADR-ML-GATE-03 (MLR-9, #1909) — mirrors config.py. Deflated Sharpe gate
# (Bailey/Lopez de Prado): when ON the serving gate additionally requires the
# Layer-5 `deflated_sharpe` (annualized SR - E[max SR] margin, stamped by
# scripts/apply_deflated_sharpe_layer.py) to clear TFT_DSR_FLOOR. Orthogonal to
# FDR (multiple testing) and net_sharpe (costs) — all three must hold. Default
# OFF (validate-before-activate); field absent → passthrough + WARNING.
TFT_DEFLATED_SHARPE_GATE_ENABLED = (
    os.getenv("TFT_DEFLATED_SHARPE_GATE_ENABLED", "False").lower() == "true"
)
# ADR-ML-GATE-03 — mirrors config.py. Exclusive floor for the annualized
# deflation margin: must beat the expected max Sharpe of n_trials null trials.
TFT_DSR_FLOOR = float(os.getenv("TFT_DSR_FLOOR", "0.0"))
# ADR-ML-GATE-03 — mirrors config.py. IC demotion (feedback_sharpe_over_ic):
# when ON the walkforward_ic <= IC_MIN reject only WARNS (diagnostic), the
# orthogonal fdr/net_sharpe/deflated_sharpe gates decide. Default OFF.
TFT_IC_DIAGNOSTIC_ONLY = os.getenv("TFT_IC_DIAGNOSTIC_ONLY", "False").lower() == "true"

# Manifest override flags (CWE-502). Default True.
AAA_REQUIRE_MANIFEST = os.getenv("AAA_REQUIRE_MANIFEST", "True").lower() == "true"
TFT_REQUIRE_MANIFEST = os.getenv("TFT_REQUIRE_MANIFEST", "True").lower() == "true"

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
# Desktop default ON (0.2.14): the card's headline corpus (grounding [H#] pool).
SPECIALIST_NEWS_V2 = os.getenv("SPECIALIST_NEWS_V2", "True").lower() == "true"

# Portfolio Context & Gatekeeper (GAP9)
GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED = (
    os.getenv("GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED", "True").lower() == "true"
)
GATEKEEPER_REQUIRE_CONTEXT = (
    os.getenv("GATEKEEPER_REQUIRE_CONTEXT", "False").lower() == "true"
)
# #2037: PDT cross-day SELL exemption — opt-in, default OFF (paper-observe first).
# Byte-identical default to config.py (both False). See implementation_plan v3 (#2050).
# ADR-C04-EXIT: exempt risk-reducing exits from the SIDE-AGNOSTIC daily-trades cap.
#
# record_trade() takes no side, so once COMPLIANCE_MAX_DAILY_TRADES is spent NO SELL
# lands either — including a stop-loss — until reset_daily_limit() at the NY rollover.
# There is no broker-side stop to fall back on (order_executor submits only
# Limit/Market orders), so every exit is bot-issued and must pass that gate. A bot
# that cannot sell cannot cut a loss.
#
# gatekeeper.py already ratified this principle one layer up (#2031: "eine
# risikoreduzierende SELL darf NIE durch das weiche Tageslimit blockiert werden").
# This restores the same invariant in ComplianceGuardian, reusing the #2065 predicate.
#
# It relaxes a self-imposed risk limit on the live decision path. The cap keeps
# binding on everything that ADDS risk (buys, over-sells, naked shorts), and the
# predicate is fail-CLOSED — a missing held_qty stays capped.
# DEFAULT ON (#2178, Regel-Konsistenz-Audit R1) — BORA-parity with config.py. The
# daily-trades cap was trapping risk-reducing SELL/stop-loss exits once the ~10/day
# budget was spent. Set to False to restore the pre-#2166 behaviour.
COMPLIANCE_ALLOW_RISK_REDUCING_EXITS = (
    os.getenv("COMPLIANCE_ALLOW_RISK_REDUCING_EXITS", "True").lower() == "true"
)

# ADR-PDT-01: apply FINRA's pattern-day-trader rule as written (>3 day trades in 5
# business days AND equity below $25,000), instead of today's stricter approximation
# (>=3, equity never read) which refuses EVERY order — including risk-reducing SELLs —
# on a funded account the rule does not cover. ACTIVATED (R2, exit-invariant): default ON
# so a funded account is no longer over-blocked ("the bot places zero trades"). The
# fail-safe survives inside the branch: unknown/absent equity still BLOCKS (never a FINRA
# under-block). BORA-parity with config.py. Env-overridable (`=false` restores the old gate).
GATEKEEPER_PDT_FINRA_ACCURATE = (
    os.getenv("GATEKEEPER_PDT_FINRA_ACCURATE", "True").lower() == "true"
)

# #1951 / plan #2205 — DrawdownGuard hard-veto → conditioner (+severe backstop).
# BORA-parity with config.py. When ON, the 7–25% band dampens BUY conviction/size
# instead of hard-vetoing, and the DAMPED conviction gates the action (below
# SIGNAL_BUY_THRESHOLD ⇒ HOLD — #1951); > DRAWDOWN_SEVERE_VETO_THRESHOLD still
# hard-blocks. ADR-DDG-02 (#1951): default OFF — the audited 0.07 hard veto is the
# shipped posture (live forensics: default-ON was cosmetic, 708 floor-BUYs).
# Re-activation only after net-of-cost OOS proof + staged paper observation
# (MiFID II Art.17; annual review). Set DRAWDOWN_GUARD_CONDITIONER_ENABLED=true
# to opt in.
DRAWDOWN_GUARD_CONDITIONER_ENABLED = (
    os.getenv("DRAWDOWN_GUARD_CONDITIONER_ENABLED", "False").lower() == "true"
)
# ADR-DDG-01 — max fraction the drawdown severity may cut a BUY's conviction (0..1).
DRAWDOWN_CONVICTION_DAMP_MAX = float(
    os.getenv("DRAWDOWN_CONVICTION_DAMP_MAX", "0.8") or "0.8"
)
# ADR-DDG-01 — drawdown fraction above which the guard still HARD-blocks a BUY.
DRAWDOWN_SEVERE_VETO_THRESHOLD = float(
    os.getenv("DRAWDOWN_SEVERE_VETO_THRESHOLD", "0.25") or "0.25"
)
# Master gate for the DrawdownGuard HARD-VETO of new BUYs (BORA-parity with config.py:
# same env name, same default). Default TRUE ⇒ shipped behaviour, byte-identical. FALSE
# removes the veto (guard still votes its soft score, never hard-blocks). Measured: the
# per-name veto neither cuts portfolio drawdown (VIX scaler already does) nor lifts
# Sharpe and costs return. Flipping to FALSE is a P1 change (MiFID II Art. 17), gated on
# a net-of-cost OOS proof on the real book. See docs/rtr/drawdown-guard-removal/.
DRAWDOWN_GUARD_VETO_ENABLED = (
    os.getenv("DRAWDOWN_GUARD_VETO_ENABLED", "True").lower() == "true"
)

# TRD-8 T6 (#3004) — MomentumAgent: monotone squashing instead of a hard clamp.
# BORA-parity with config.py: same env name, same default (OFF ⇒ byte-identical).
# `clamp(0.5 + momentum_12_1 / 0.60)` saturates at +30 % 12-1M momentum; measured over
# 26,744 logged votes, 48.7 % carry exactly 1.00, which acts as a constant +0.1285 lift
# in front of the fixed 0.65 buy threshold rather than as a graded vote. ON substitutes
# 0.5 + 0.5*tanh(m / MOMENTUM_SCORE_SCALE) — strictly monotone, no ties, same range.
# Parsed tolerantly ("1"/"0" too) because research/sweep.py validates spec values as
# numbers; `.lower() == "true"` would read a swept "1" as False.
MOMENTUM_SCORE_SMOOTH_ENABLED = os.getenv(
    "MOMENTUM_SCORE_SMOOTH_ENABLED", "true"
).strip().lower() in ("1", "true", "yes")

# #3038 TRD-10 — VIXAwareRiskAgent votes on the symbol's own implied volatility,
# ranked against yesterday's cross-section, instead of the market-wide VIX.
# BORA-parity with config.py (same env name, same default — dormant, OFF ⇒
# byte-identical vote path). Measured: today the score spans 0.754..0.773 and is
# bit-identical to RegimeDetectionAgent in 26,755 of 26,755 cases.
# Parsed tolerantly ("1"/"0" too) because research/sweep.py validates spec values as
# numbers; `.lower() == "true"` would read a swept "1" as False.
# ACTIVATED 2026-08-26 (stage 3, owner decision) — BORA-parity with config.py.
# Stage-2 sweep: the old rule hit the portfolio stop loss on day 43 of 60, the
# new one never did, at half the drawdown. Saturation criterion MISSED
# (4.50 % vs <= 2 %). Rollback: `=0`.
VIXAWARE_IMPLIED_VOL_ENABLED = os.getenv(
    "VIXAWARE_IMPLIED_VOL_ENABLED", "true"
).strip().lower() in ("1", "true", "yes")

# #1955 TRD-4 — Meta-Labeling (López de Prado) Trade/No-Trade filter over the
# primary consensus BUYs. BORA-parity with config.py (same env names, same
# defaults — dormant, default OFF ⇒ byte-identical order path, no model load).
# Activation only after the net-of-cost OOS harness proof (plan #1955 §11).
META_LABEL_FILTER_ENABLED = (
    os.getenv("META_LABEL_FILTER_ENABLED", "False").lower() == "true"
)
# ADR-ML-META-01 (#1955) — mirrors config.py: p(net-of-cost profitable) floor
# for taking a BUY; conservative start above indifference (0.50). Decides real
# capital exposure (MiFID II Art. 17) → annual review, re-tuned only against
# the net-of-cost OOS benchmark. Inert while the filter is OFF.
META_LABEL_MIN_PROBA = float(os.getenv("META_LABEL_MIN_PROBA", "0.55") or "0.55")
# Pickle artifact from scripts/train_meta_label.py; missing ⇒ unblocked in observation mode; fails CLOSED when META_LABEL_REQUIRE_MODEL is True.
META_LABEL_MODEL_PATH = os.getenv("META_LABEL_MODEL_PATH", "data/meta_label_model.pkl")
META_LABEL_REQUIRE_MODEL = (
    os.getenv("META_LABEL_REQUIRE_MODEL", "False").lower() == "true"
)

# #1968 (RT-BUG-2) — MomentumAgent one-bar fallback → ABSTAIN (dormant, default OFF).
# BORA-parity with config.py (same env name, same default). When ON and <40 daily
# closes make the 12-1M momentum path unavailable, MomentumAgent ABSTAINS (score 0.5,
# weight 0.0, excluded from consensus) instead of echoing RegimeDetection's close/open
# direction with its one-bar (close-open)/open fallback. OFF (default) ⇒ byte-identical.
MOMENTUM_FALLBACK_ABSTAIN_ENABLED = (
    os.getenv("MOMENTUM_FALLBACK_ABSTAIN_ENABLED", "True").lower() == "true"
)

# #1949 (RTR-2, dormant, default OFF) — RegimeDetectionAgent O/C-proxy → real
# MarketRegimeModel conditioner. BORA-parity with config.py (same env names, same
# defaults). ON → VIX/regime threaded into declared SymbolEvalState channels, the
# agent scores the continuous VIX sigmoid instead of the one-bar close/open proxy,
# and consensus.py scales ALL directional voters gradually by regime severity
# instead of the binary Momentum-only ×0.5. OFF (default) ⇒ byte-identical.
# Activation gated on the RTR-0 (#1947) net-of-cost proof.
REGIME_CONDITIONER_ENABLED = (
    os.getenv("REGIME_CONDITIONER_ENABLED", "True").lower() == "true"
)
# ADR-RGC-01 (#1949) — max weight cut for directional voters in FULL risk-off;
# 0.5 mirrors the old hardcoded Momentum ×0.5 bound, applied gradually. Annual review.
REGIME_RISKOFF_DAMP_MAX = float(os.getenv("REGIME_RISKOFF_DAMP_MAX", "0.5") or "0.5")
# ADR-RGC-02 (#1949) — VIX sigmoid midpoint; 25.0 = MarketRegimeModel "normal"
# threshold, same midpoint as VIXAwareRiskAgent. Annual review.
REGIME_VIX_MIDPOINT = float(os.getenv("REGIME_VIX_MIDPOINT", "25.0") or "25.0")

GATEKEEPER_PDT_CROSSDAY_EXEMPTION = (
    os.getenv("GATEKEEPER_PDT_CROSSDAY_EXEMPTION", "False").lower() == "true"
)
# #2153 Part A: per-symbol anti-churn entry cap — default ON (operator D1). Byte-identical
# default to config.py (both True). See docs/anti-churn/implementation_plan.md §4 (BORA).
GATEKEEPER_SYMBOL_CHURN_LIMIT = (
    os.getenv("GATEKEEPER_SYMBOL_CHURN_LIMIT", "True").lower() == "true"
)

# RTR-6 (#2410): Strict-ML-Gate RL coupling — BORA-parity with config.py (same env
# name, same default True = byte-identical gate: LSTM AND RL both required, each
# weight > 0). Set to False and a valid LSTM vote alone satisfies the gate; the RL
# abstain is a once-per-session WARNING (§5.6) and the audit reasoning notes
# "RL not required (flag)". BOTH ML votes abstaining still vetoes (protection stays).
GATEKEEPER_STRICT_ML_REQUIRES_RL = (
    os.getenv("GATEKEEPER_STRICT_ML_REQUIRES_RL", "False").lower() == "true"
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

# Dynamic specialist-report coverage (Epic #1998 RPT-GOLD, sub #2630). ON -> the report
# registry's HIGH-PRIORITY set becomes: held positions U top-N by round-table consensus_score
# U base watchlist, instead of the static _CARD_REGISTRY_STOCKS. Dark by default -> byte-
# identical to today when OFF. Mirrored in config.py (dual-edition parity). Exposed via
# get_config() = SimpleNamespace(**globals()).
SPECIALIST_COVERAGE_DYNAMIC = (
    os.getenv("SPECIALIST_COVERAGE_DYNAMIC", "True").lower() == "true"
)
SPECIALIST_TOP_N_CONVICTION = int(
    os.getenv("SPECIALIST_TOP_N_CONVICTION", "20") or "20"
)
SPECIALIST_COVER_POSITIONS = (
    os.getenv("SPECIALIST_COVER_POSITIONS", "True").lower() == "true"
)

# #1346: config-gated SpecialistAlphaAgent vote weight (default 0.0 = dormant). The
# finance-core reads it via get_config() (CODING_POLICY §2.10), not os.environ.
# #2839: 0.0→0.40 = shipped desktop launcher profile (desktop is master).
SPECIALIST_ALPHA_WEIGHT = float(os.getenv("SPECIALIST_ALPHA_WEIGHT", "0.40") or "0.40")

# RTR-0 (#1947) / Epic #1958: config-gated RLConfidenceAgent vote weight. Default 0.40
# == the historical hardcoded weight (byte-identical). The RL vote is direction-blind
# (score 0.5 + (|pred|/2)*0.5 on a BUY action → max-conviction BUY even on a strongly
# BEARISH prediction); set RL_CONFIDENCE_WEIGHT=0.0 to MUTE it as an interim guard until
# the sign-fix + RTR-0 attribution land. Read via get_config() (CODING_POLICY §2.10).
# Mirrors config.py (BORA dual-edition parity: same env name + default in BOTH editions).
RL_CONFIDENCE_WEIGHT = float(os.getenv("RL_CONFIDENCE_WEIGHT", "0.0") or "0.0")


# #2815 (TRD-8 T1): remaining consensus knobs, config-gated so the parameter sweep can
# vary them per RUN. Defaults == the historical hardcoded literals (byte-identical when
# unset). Mirrors config.py (BORA dual-edition parity: same env names, same defaults).
# Weights clamp to [0, 1] at the read site; thresholds validate 0 < sell < buy < 1 there.
DRAWDOWN_GUARD_WEIGHT = float(os.getenv("DRAWDOWN_GUARD_WEIGHT", "0.60") or "0.60")
REGIME_DETECTION_WEIGHT = float(os.getenv("REGIME_DETECTION_WEIGHT", "0.50") or "0.50")
MOMENTUM_AGENT_WEIGHT = float(os.getenv("MOMENTUM_AGENT_WEIGHT", "0.45") or "0.45")
VIX_RISK_WEIGHT = float(os.getenv("VIX_RISK_WEIGHT", "0.45") or "0.45")
LSTM_SIGNAL_WEIGHT = float(os.getenv("LSTM_SIGNAL_WEIGHT", "0.40") or "0.40")
# #2947: behebt zwei Rechenfehler in score_opportunity — Gewichtssumme 0,90 plus unbedingte
# 15-Punkte-Pauschale hoben die Verdraengungsmarge auf, und die Confidence-Komponente rechnete
# mit dem Faktor fuer einen Eingang in +/-5 statt +/-1. False = alte Arithmetik byte-identisch.
# Siehe config.py fuer die vollstaendige Begruendung.
OPPORTUNITY_SCORE_NORMALIZED = (
    os.getenv("OPPORTUNITY_SCORE_NORMALIZED", "True").lower() == "true"
)

SIGNAL_BUY_THRESHOLD = float(os.getenv("SIGNAL_BUY_THRESHOLD", "0.65") or "0.65")
SIGNAL_SELL_THRESHOLD = float(os.getenv("SIGNAL_SELL_THRESHOLD", "0.35") or "0.35")

# RTR-1 (#1948, Epic #1958, dormant, default OFF) — mirrors config.py (BORA
# dual-edition parity: same env names, same defaults in BOTH editions). ON ->
# the trading loop fills SymbolEvalState.news_headlines from the FREE
# Google-News-RSS point-in-time producer and NewsSentimentAgent scores REAL
# headlines with a local NLP model (FinBERT offline cache, else the vendored
# VADER lexicon) instead of the headline-less LLM symbol guess. OFF ->
# byte-identical LLM-/abstain-path. ADR-NS-07 (2026-08-04): default flipped ON by
# explicit OWNER activation — a deliberate waiver of the RTR-0 gate (#1947), and
# OUTSIDE the 2026-07-26 Round-Table-v2 RTR-free waiver. LIVE BACKEND = vendored
# VADER lexicon (`transformers` not a dep + FinBERT weights not bundled -> FinBERT
# degrades WARNING-visibly to VADER; both free, deterministic). Fails CLOSED (no
# headline/backend -> weight 0, EXCLUDED). Mirrors config.py (BORA parity).
NEWS_SENTIMENT_NLP_ENABLED = (
    os.getenv("NEWS_SENTIMENT_NLP_ENABLED", "True").lower() == "true"
)
# ADR-NS-01 (#1948): headline cap per symbol/cycle for the NLP scorer (coverage
# vs desktop-CPU latency). Inert while the flag above is OFF. Annual review.
NEWS_SENTIMENT_MAX_HEADLINES = int(
    os.getenv("NEWS_SENTIMENT_MAX_HEADLINES", "8") or "8"
)
# ADR-NS-02 (#1948): NLP backend selector ("finbert" | "finbert-tone" | "vader");
# FinBERT loads from the LOCAL offline cache only (no runtime download), missing
# dep/assets degrade WARNING-visibly to the vendored VADER lexicon. Inert while
# the flag above is OFF. Annual review.
NEWS_SENTIMENT_MODEL = os.getenv("NEWS_SENTIMENT_MODEL", "finbert") or "finbert"

# #2389 (Epic RTR #1958, dormant, default OFF) — mirrors config.py. ON -> the
# orchestration graph's _compute_features_node computes the real TA-feature
# snapshot (30d OHLCV window shared with DrawdownGuardAgent) and the Round Table
# runner maps the scalars into the MiFID decision record. OFF (default) -> the
# node is a no-op and the snapshot keeps today's neutral defaults — byte-identical.
# Kept default OFF in BOTH editions (dual-edition parity).
DESKTOP_FEATURE_SNAPSHOT_ENABLED = (
    os.getenv("DESKTOP_FEATURE_SNAPSHOT_ENABLED", "True").lower() == "true"
)

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

# RPT-6 (Epic #1998, dormant, default OFF) — mirrors config.py. ON -> the
# specialist ADDITIVELY attaches an auditable v2 report; OFF (default) -> the
# decision path + DTO are byte-identical. Report-only; SPECIALIST_ALPHA_WEIGHT
# stays 0. Kept default OFF in BOTH editions so the byte-identical guarantee
# holds for the OSS/desktop build too (dual-edition parity).
REPORT_GENERATOR_V2_ENABLED = (
    os.getenv("REPORT_GENERATOR_V2_ENABLED", "True").lower() == "true"
)

# RPT-8b (A5, dormant, default OFF) — mirrors config.py. With this + the registry
# enabled, the specialist registry covers the FULL S&P 500 (decoupled from the
# ENVIRONMENT-gated trading universe) and refreshes DETERMINISTIC-FIRST reports
# (no Gemini), so all ~500 auditable reports populate for the reader without
# changing what the bot trades. OFF (default) in BOTH editions (dual-edition parity).
REPORT_REGISTRY_FULL_UNIVERSE_ENABLED = (
    os.getenv("REPORT_REGISTRY_FULL_UNIVERSE_ENABLED", "False").lower() == "true"
)

# REPORT-ONLY DECOUPLING (dormant, default OFF) — mirrors config.py. ON -> the registry is
# booted at engine __init__ in report_only mode over the full S&P 500, independent of self.api,
# so /specialist-reports cards appear without an Alpaca connection. Cannot influence
# trading at defaults: SpecialistAlpha votes are weight-0-clamped (SPECIALIST_ALPHA_WEIGHT=0,
# consensus filters weight-0). OFF (default) in BOTH
# editions (dual-edition parity).
REPORT_REGISTRY_STANDALONE_ENABLED = (
    os.getenv("REPORT_REGISTRY_STANDALONE_ENABLED", "True").lower() == "true"
)

# REPORT_CACHE_PERSIST (2026-07-23) — mirrors config.py. ON -> the specialist
# registry persists each fresh report to <AAA_USER_DATA_DIR>/report_cache and
# reloads fresh-enough ones at boot, so an app restart shows the SAME cards
# instead of regenerating every report with drifted news inputs. Report-only;
# default ON so the Reports page survives an engine restart out of the box (a
# restart used to wipe every card because this defaulted OFF). Stale reports still
# regenerate on schedule; set REPORT_CACHE_PERSIST_ENABLED=False to opt out.
REPORT_CACHE_PERSIST_ENABLED = (
    os.getenv("REPORT_CACHE_PERSIST_ENABLED", "True").lower() == "true"
)


# Phase B (dormant, default OFF) — ⚠ TRADING-BEHAVIOUR flag, mirrors config.py.
# When ON the engine FEEDS the full ~500-symbol universe to the Round Table instead
# of the scanner top-10 funnel, and the existing 20-slot displacement arbitrates what
# is held (ADR-FU02).
# ⚠ It does NOT rank the universe and buy the top decile — each symbol is judged on
# its own and the survivors compete for slots. An earlier version of this comment
# claimed the cross-sectional construction as fact; it was never implemented, and the
# gap is precisely why this flag must not be flipped yet.
# P1 decision-path change: MUST stay OFF until walk-forward-validated + human
# sign-off. Per-symbol independent bets do NOT survive FDR (0/488) — breadth should
# only ever arrive as a ranking + portfolio construction, which is the open work.
# OFF (default) = today's universe selection / scanner funnel / position selection,
# byte-identical.
FULL_UNIVERSE_TRADING_ENABLED = (
    os.getenv("FULL_UNIVERSE_TRADING_ENABLED", "True").lower() == "true"
)

# ADR-FU01 (mirrors config.py): full-universe evaluation fan-out cap.
# Today's 200-symbol truncation is the ONLY bound on the per-symbol asyncio.gather;
# evaluating ~500 unbounded would open ~4,500 concurrent LLM calls against ONE local
# Ollama and exhaust the pool (an outage, not a cost). 20 mirrors the scanner's proven
# per-loop semaphore (market_scanner SCAN_CONCURRENCY). Read ONLY inside the
# FULL_UNIVERSE_TRADING_ENABLED branch — the default path is untouched.
FULL_UNIVERSE_EVAL_CONCURRENCY = int(
    os.getenv("FULL_UNIVERSE_EVAL_CONCURRENCY", "20") or "20"
)

# round-table-v2 rank-cache funnel (mirrors config.py): cap the round-table deep-eval set to
# [held positions ∪ top-K LSTM-ranked]. 0 = DISABLED → full universe, byte-identical. Set 20 to activate.
# Panel is written only when REPORT_GENERATOR_V2_ENABLED is on; with it off this flag is a safe no-op.
# #2680 "Patient AI": deep-eval cap = held ∪ top-K LSTM. Default 30 matches the value the desktop
# already injects at runtime (main.cjs); config and the shipped app must never disagree (BORA parity).
ROUND_TABLE_TOP_K_EVAL = int(os.getenv("ROUND_TABLE_TOP_K_EVAL", "30") or "30")

# Max age (calendar days) of the LSTM panel snapshot for the funnel to trust it; older ⇒ full universe.
ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS = int(
    os.getenv("ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS", "2") or "2"
)

# ADR-RANK-01 (#2680 "Patient AI", ARMED by default — mirrors config.py): rank the LSTM
# cross-section on a TREND (per-symbol MEDIAN over the last WINDOW_DAYS snapshot dates)
# instead of today's point-in-time scores. ALL consumers read it through
# lstm_panel_store.active_cross_section (BUY vote, rotation SELL trigger, deep-eval
# funnel, report card). MIN_COVERAGE excludes symbols seen on too few of those dates so a
# one-day wonder cannot top the book; the floor scales down on a cold start.
# ⚠ DEFAULT ON = a real live behaviour change on both editions, NOT a dark ship (owner
# decision 2026-08-05, after the measured IVZ churn: bought point-in-time top-10 at 14:00
# UTC, rotated out at rank 242/501 at 17:09 UTC). Direction is strictly less reactive —
# the smoothed table can only refuse to follow a one-day move, never invent a rank.
# Rollback: LSTM_RANK_SMOOTHING_ENABLED=false ⇒ exactly the pre-#2680 behaviour.
LSTM_RANK_SMOOTHING_ENABLED = (
    os.getenv("LSTM_RANK_SMOOTHING_ENABLED", "True").lower() == "true"
)
LSTM_RANK_SMOOTHING_WINDOW_DAYS = int(
    os.getenv("LSTM_RANK_SMOOTHING_WINDOW_DAYS", "10") or "10"
)
LSTM_RANK_SMOOTHING_MIN_COVERAGE = int(
    os.getenv("LSTM_RANK_SMOOTHING_MIN_COVERAGE", "5") or "5"
)

# ADR-RANK-02 (#2683, ARMED by default — mirrors config.py): persist the LSTM rank panel to
# `lstm_panel_snapshots` so the trend smoothing survives a restart. The store is in-memory
# and records one snapshot per calendar day; with a daily-restarting engine the median window
# would hold ONE date, i.e. the point-in-time value the smoothing replaces — #2680 would be a
# facade. Writes are best-effort (a DB failure costs telemetry, never the cycle); hydration
# happens on first store access and is SKIPPED under SIM_MODE (a sim seeds its own panel).
# Rollback: LSTM_PANEL_PERSISTENCE_ENABLED=false ⇒ memory-only.
LSTM_PANEL_PERSISTENCE_ENABLED = (
    os.getenv("LSTM_PANEL_PERSISTENCE_ENABLED", "True").lower() == "true"
)

# #2672 (TRD, dark default OFF — mirrors config.py): join the per-cycle broker position
# snapshot into every symbol's round-table eval state (five declared LangGraph channels).
# OFF (default) → producer emits None for all five = today's behaviour, byte-identical.
# ARMED by default (owner decision 2026-08-05; mirrors config.py). Verified RECORD-ONLY:
# no agent reads `in_position` from the state and no execution path branches on
# DecisionContext.in_position — the single reader maps it into the audit record. Arming
# fixes WHAT IS WRITTEN (position-blind decision rows) and changes no trading decision.
ROUND_TABLE_POSITION_CONTEXT_ENABLED = (
    os.getenv("ROUND_TABLE_POSITION_CONTEXT_ENABLED", "True").lower() == "true"
)

# round-table-v2 increment 2 (mirrors config.py): re-rank the full universe at most once per this many
# minutes (0 = every cycle, byte-identical). Intended activation value: 120 (every 2 hours).
ROUND_TABLE_RANK_REFRESH_MINUTES = int(
    os.getenv("ROUND_TABLE_RANK_REFRESH_MINUTES", "120") or "120"
)

# Snapshot-fetch resilience (mirrors config.py) — the 2nd panel-starvation path.
# The per-cycle universe snapshot fetch was ONE StockSnapshotRequest for ~500 symbols;
# a single timeout / RemoteDisconnect set snapshots={} → update_lstm_rankings skipped
# EVERY symbol → empty panel → all-HOLD. Chunk the fetch so a failing chunk drops only
# ITS symbols while the rest still populate. <=0 (or >= universe size) => one request
# (byte-identical rollback).
SNAPSHOT_FETCH_CHUNK_SIZE = int(os.getenv("SNAPSHOT_FETCH_CHUNK_SIZE", "100") or "100")

# #2499 (mirrors config.py): off-load the per-symbol create_live_features pandas_ta transform to a
# ProcessPoolExecutor so its GIL hold no longer starves the uvicorn event loop.
# DEFAULT ON (promoted, 0.3.6 regression fix): #2516 flipped FULL_UNIVERSE_TRADING_ENABLED default-on, so
# ~500 symbols are featurized every cycle; left inline the pandas GIL hold starved /health +
# /portfolio-summary (8–97s) → delayed first-load paper data + timed-out live-switch reconcile. Fail-safe:
# any pool problem degrades to the byte-identical inline path. Override FEATURE_POOL_ENABLED=false to roll back.
FEATURE_POOL_ENABLED = os.getenv("FEATURE_POOL_ENABLED", "True").lower() == "true"
FEATURE_POOL_WORKERS = int(os.getenv("FEATURE_POOL_WORKERS", "2") or "2")
# ADR-FU02 (mirrors config.py): full-universe slot count. With ~500 names judged, more
# clear the bar than there is capital for — the slot count IS the scarcity, and a better
# chance must DISPLACE the weakest holding.
# ⚠ THE NUMBER. History: #2119 set 50; Part C (#2176) moved it to 20 (1/20 = 5% ==
#   MIN_POSITION_PERCENT). Now the operator's call: 10 — hold the best 10 of the ~500 judged
#   names, displacing the weakest into the same slot. At 10 the equal-weight cash slot is
#   1/10 = 10% (2× MIN_POSITION_PERCENT=0.05, so the clamp stays slack) and well under the
#   25%/symbol concentration limit. This makes the full-universe book EQUAL to MAX_POSITIONS=10
#   (same book size, wider scan). Config-tunable (env), BORA-parity with config.py.
# ⚠ Part C ARMS the displacement swap on the desktop/global fallback path (the path the app
#   takes) — mirroring the tenant block — so a better chance displaces the weakest holding.
# Read ONLY inside the FULL_UNIVERSE_TRADING_ENABLED branch; MAX_POSITIONS (=10) untouched.
FULL_UNIVERSE_MAX_POSITIONS = int(
    os.getenv("FULL_UNIVERSE_MAX_POSITIONS", "10") or "10"
)

# RPT/Phase B (dormant, default OFF; mirrors config.py) — produce the LSTM cross-section
# panel INDEPENDENTLY of ACTIVE_STRATEGY.
# The keystone: the panel's only writer is LSTMDynamic.update_lstm_rankings, and
# ACTIVE_STRATEGY defaults to RLAgent, which never writes one — so the report's operative
# number renders "Platz n/a von n/a" for EVERY symbol in the shipped build. ON registers a
# standby LSTMDynamic (set_active=False) as a RANKER only; it is never asked to trade.
# Dormancy: the branch is an `elif` off the existing hasattr(strategy,
# "update_lstm_rankings") — it can only fire where zero code runs today. OFF = the producer
# is never constructed (no second torch model).
LSTM_RANK_PANEL_STRATEGY_INDEPENDENT = (
    os.getenv("LSTM_RANK_PANEL_STRATEGY_INDEPENDENT", "True").lower() == "true"
)

# LSTM-panel-starvation fix (mirrors config.py) — warm the local daily-bar cache
# ONCE per trading day (engine start + NY-date rollover) so update_lstm_rankings'
# per-symbol get_data reads are cache hits instead of ~500 live per-cycle fetches
# that rate-limit the free IEX feed and empty the panel (-> all-HOLD). DEFAULT ON
# (the fix); OFF = exact rollback (per-symbol fetch every cycle, byte-identical).
# Fail-safe: a warm-up error falls back to today's per-symbol fetch.
LSTM_BAR_STORE_WARMUP_ENABLED = (
    os.getenv("LSTM_BAR_STORE_WARMUP_ENABLED", "True").lower() == "true"
)

# ADR-SURV-02 (#1907 MLR-7 Inc 2/3; mirrors config.py) — Panel-Stale/Coverage-Gate
# for the offline full-universe panel, the data foundation of the cross-sectional
# retrains (#2388). Basis: ESMA backtesting guidelines / MiFID II — survivorship/PIT
# integrity of training data; annual review (§5.5).
# DEFAULT-SAFE = ON (conservative, BLOCKING): core/panel_stale_gate.py refuses
# training when the panel's data (manifest coverage_end) is older than
# PANEL_MAX_AGE_DAYS (5 = one trading week) or has_point_in_time_membership(as_of)
# denies the window. Nothing on the live decision path reads these keys; OFF ->
# the check short-circuits to "allowed" with zero further behaviour (BORA
# byte-identity).
PANEL_STALE_GATE_ENABLED = (
    os.getenv("PANEL_STALE_GATE_ENABLED", "True").lower() == "true"
)
PANEL_MAX_AGE_DAYS = int(os.getenv("PANEL_MAX_AGE_DAYS", "5") or "5")

# Phase B (dormant, default OFF; mirrors config.py) — ⚠ TRADING-BEHAVIOUR flag, and
# the most SUBSTANTIVE one: it changes what LSTMSignalAgent (an ACTIVE weight-0.4
# voter) judges on. Today it votes on the RAW per-symbol prediction — precisely the
# claim our own walk-forward refutes (0/488 survive FDR). ON -> it votes on the
# CROSS-SECTIONAL RANK (IC 0.067, t 20.4), the signal that did hold, which the report
# already carries. Stays OFF until a walk-forward measures the swap.
LSTM_VOTE_USES_CROSS_SECTIONAL_RANK = (
    os.getenv("LSTM_VOTE_USES_CROSS_SECTIONAL_RANK", "True").lower() == "true"
)

# RTR / distinct-ML-sources (dark, default OFF; mirrors config.py + #2426) — the Round
# Table's two ML voices must read DISTINCT registered models. Today LSTMSignalAgent and
# RLConfidenceAgent both resolve get_active(); ACTIVE_STRATEGY defaults to "RLAgent", so
# both read the SAME object (the RL trader) — the collapse in core/agent_registry.py::get().
# ON -> LSTMSignalAgent resolves registry.get("LSTMDynamic") (the standby ranker) and
# RLConfidenceAgent resolves the active trader by name (ACTIVE_STRATEGY); a missing named
# source -> the agent ABSTAINS (weight 0). OFF (default) -> the get_active() path,
# byte-identical. ⚠ TRADING-BEHAVIOUR flag: needs a walk-forward before LIVE arming; armed
# only via the desktop _childEnv.
ROUND_TABLE_DISTINCT_ML_SOURCES = (
    os.getenv("ROUND_TABLE_DISTINCT_ML_SOURCES", "True").lower() == "true"
)

# RTR-5/B1 (#2401, dark, default OFF; mirrors config.py) — Book-Constructor mode:
# the Round Table as a SELECTOR/book constructor (Top-N target book + minimal
# rebalance diff) instead of a per-symbol timing machine. OFF (default) -> the
# module is a no-op and nothing on the trading path changes (byte-identical).
# Wiring/activation is B3 (Paper, only after RTR-0-Inc-2 proof + Owner-Go).
BOOK_CONSTRUCTOR_ENABLED = (
    os.getenv("BOOK_CONSTRUCTOR_ENABLED", "False").lower() == "true"
)
# ADR-RTR5-01 (mirrors config.py): Rebalance cadence = 168h (weekly).
# Basis: MiFID II Art. 17 (orderly trading; anti-churn) + #2401 forensics — the
# comparative edge is cross-sectional ranking, not intraday timing; weekly
# re-ranking preserves the ~5-day LSTM horizon (#1952 bake-off). Inert while
# BOOK_CONSTRUCTOR_ENABLED is OFF; tunable for the B3 walk-forward; annual review.
BOOK_CONSTRUCTOR_REBALANCE_INTERVAL_HOURS = float(
    os.getenv("BOOK_CONSTRUCTOR_REBALANCE_INTERVAL_HOURS", "168") or "168"
)

# RTR-5/B2 (#2401, dark, default OFF; mirrors config.py) — trailing-from-peak
# exit POLICY inside the existing should_sell_smart contract (core/smart_exit.py).
# OFF (default) -> should_sell_smart is byte-identical (pinned regression
# battery). PROPOSAL only (HITL-konform): risk/compliance/HITL gates and
# PortfolioManager.can_sell_position stay binding — no new autonomous SELL path.
EXIT_POLICY_TRAILING_ENABLED = (
    os.getenv("EXIT_POLICY_TRAILING_ENABLED", "True").lower() == "true"
)
# ADR-RTR5-02 (§5.5, mirrors config.py): TRAILING_FROM_PEAK_PCT = 10.0
# Basis: MiFID II Art. 17 + EU AI Act Art. 14 (proposal feeds the human-oversight
# gates). Rationale: 10% from the position HWM lets a multi-week winner breathe
# (ordinary pullbacks of the proven +55% run exceeded the legacy 3% trail) while
# proposing an exit before the bulk of an extended gain is round-tripped. Fires
# only in profit and only past the trail min-hold (min-hold wins, churn-safe).
# Inert while EXIT_POLICY_TRAILING_ENABLED is OFF; annual review.
TRAILING_FROM_PEAK_PCT = float(os.getenv("TRAILING_FROM_PEAK_PCT", "10.0") or "10.0")

USER_DATA_DIR = os.getenv("AAA_USER_DATA_DIR") or os.path.join(PROJECT_ROOT, "data")
REDIS_URL = os.getenv("REDIS_URL", "").strip()
GCP_PROJECT_ID = _clean_env("GCP_PROJECT_ID")
GCP_REGION = _clean_env("GCP_REGION", "us-central1")
VERTEX_ENDPOINT_ID = _clean_env("VERTEX_ENDPOINT_ID")
SHADOW_TFT_VOTE_CHAIN_PATH = os.getenv(
    "SHADOW_TFT_VOTE_CHAIN_PATH", os.path.join(USER_DATA_DIR, "shadow_tft_votes.jsonl")
)
SHADOW_SPECIALIST_VOTE_CHAIN_PATH = os.getenv(
    "SHADOW_SPECIALIST_VOTE_CHAIN_PATH",
    os.path.join(USER_DATA_DIR, "shadow_specialist_votes.jsonl"),
)

# --- GTM-1 (#1840) Stripe tier-upgrade checkout (Brick 1, cloud-only) -----------
# Mirror of config.py (dual-edition parity). Checkout is cloud-only (K_SERVICE), so the
# desktop/OSS edition never exercises these — they exist purely so config reads resolve
# identically across editions. PRO/PROFESSIONAL are the only purchasable tiers.
# VORSCHLAG — the real price ids arrive with #1805; these default empty placeholders.
STRIPE_PRICE_ID_PRO = os.getenv("STRIPE_PRICE_ID_PRO", "")  # VORSCHLAG (#1805)
STRIPE_PRICE_ID_PROFESSIONAL = os.getenv(
    "STRIPE_PRICE_ID_PROFESSIONAL", ""
)  # VORSCHLAG (#1805)
ENTITLEMENT_CHECKOUT_SUCCESS_URL = os.getenv(
    "ENTITLEMENT_CHECKOUT_SUCCESS_URL", "https://aaagents.de/checkout/success"
)
ENTITLEMENT_CHECKOUT_CANCEL_URL = os.getenv(
    "ENTITLEMENT_CHECKOUT_CANCEL_URL", "https://aaagents.de/checkout/cancel"
)

# --- GTM-2 (#1809) paywall master switch + Lemon Squeezy (Merchant-of-Record) ----
# BORA parity with config.py: single build/env toggle for the paid Private/Senior path,
# default OFF = the free offline beta. Non-secret Lemon Squeezy checkout identifiers only;
# the API key + webhook secret load from Secret Manager at request time (like Stripe).
PAYWALL_ENABLED = os.getenv("PAYWALL_ENABLED", "False").lower() == "true"
LEMONSQUEEZY_STORE_ID = os.getenv("LEMONSQUEEZY_STORE_ID", "")
LEMONSQUEEZY_VARIANT_ID_PRO = os.getenv("LEMONSQUEEZY_VARIANT_ID_PRO", "")
LEMONSQUEEZY_CHECKOUT_URL = os.getenv("LEMONSQUEEZY_CHECKOUT_URL", "")
LEMONSQUEEZY_LICENSE_VALID_DAYS = int(
    os.getenv("LEMONSQUEEZY_LICENSE_VALID_DAYS", "365")
)
LEMONSQUEEZY_PRICE_DISPLAY = os.getenv("LEMONSQUEEZY_PRICE_DISPLAY", "9,99 €")


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
# OBS-2 (#2635): drop successful /health polls + OPTIONS CORS-preflights from the local
# telemetry store (~35 % of the export, near-zero signal). ERROR + trading spans always kept.
# Default ON. Mirrored in config.py (BORA parity).
TELEMETRY_DROP_HEALTH_SPANS = (
    os.getenv("TELEMETRY_DROP_HEALTH_SPANS", "True").lower() == "true"
)
# INF-13 P2 (#2069) — execution/compliance block visibility in the diagnose-export.
# Emits a scrubbed OTel span per block so telemetry.jsonl (→ export) carries the reason.
# Default OFF for byte-identity; observation-only, no capital-path effect.
EXECUTION_BLOCK_TELEMETRY_ENABLED = (
    os.getenv("EXECUTION_BLOCK_TELEMETRY_ENABLED", "True").lower() == "true"
)


# #2886 Book-Cap-Invariante (ships dark, default OFF = byte-identisch; mirrors
# config.py, BORA dual-edition parity). ON = broker-verifiziertes Neukauf-Gate
# (fail-closed), Konto-Identitäts-Tracking im PM, book_overflow-Rückbau über den
# Rotations-Exit-Pfad. Siehe config.py für die vollständige Vorfalls-Begründung.
# Default True per Owner-Entscheid 16.08. (Parität mit config.py).
BOOK_CAP_ENFORCEMENT_ENABLED = (
    os.getenv("BOOK_CAP_ENFORCEMENT_ENABLED", "True").lower() == "true"
)

# #2113 Decision-Outcome-Capture (Epic #1913 MLR, owner-only, NO egress) — all
# default OFF (ships dark), mirrors config.py (dual-edition parity, identical
# env names + defaults). Master flag: gates the durable, per-decision_id
# `decision_outcomes` row (local SQLite) AND the newly wired execution-outcome
# codes (blocked:kill_switch / hitl_held). OFF (default) ⇒ no row, no new code,
# decision/execution path byte-identical to today.
DECISION_CAPTURE_ENABLED = (
    os.getenv("DECISION_CAPTURE_ENABLED", "True").lower() == "true"
)
# Label-attribution job (Increment 2 activation — dormant in Increment 1).
DECISION_CAPTURE_ATTRIBUTION_ENABLED = (
    os.getenv("DECISION_CAPTURE_ATTRIBUTION_ENABLED", "False").lower() == "true"
)
# Forward-return label horizon in trading days for the Inc-2 attribution job.
DECISION_CAPTURE_FORWARD_RETURN_DAYS = int(
    os.getenv("DECISION_CAPTURE_FORWARD_RETURN_DAYS", "5") or "5"
)


def get_config():
    return types.SimpleNamespace(**globals())
