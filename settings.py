# settings.py
import logging
import os
import threading
from typing import Any, List, Optional, Union

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.governance.iron_dome_policy import MAX_ORDER_VALUE_CEILING


def _clean_env(key, default=None):
    # SEC M6 (INV-01): an empty/whitespace env var means 'unset' - return the default
    # (usually None), never "". Otherwise Optional[SecretStr]/config fields can't tell an
    # unset secret from an empty one (e.g. a blank ALPACA_API_KEY passing as truthy-ish).
    val = os.getenv(key)
    if val is None:
        return default
    val = val.strip()
    return val if val else default


class RuntimeConfigState(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.oss", ".env"), extra="ignore", env_ignore_empty=True
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_and_parse_bools(cls, v, info):
        if not isinstance(v, str):
            return v

        is_bool = False
        if info.field_name in cls.model_fields:
            ann = cls.model_fields[info.field_name].annotation
            if ann is bool:
                is_bool = True
            else:
                from typing import Union, get_args, get_origin

                origin = get_origin(ann)
                if origin is Union:
                    is_bool = bool in get_args(ann)

        if is_bool:
            v = v.strip()
            if v.lower() in ("1", "true", "yes", "on"):
                return True
            if v.lower() in ("0", "false", "no", "off"):
                return False
        return v

    # Project paths
    PROJECT_ROOT: str = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR: str = os.path.join(PROJECT_ROOT, "data")
    # USER_DATA_DIR: per-user MUTABLE account-state (app.db, checkpoints.db, audit,
    # equity). The desktop sets AAA_USER_DATA_DIR so a fresh install starts clean
    # instead of inheriting the bundled dev state. Defaults to DATA_DIR when unset â†’
    # dev + Cloud Run are byte-identical (BORA). Read-only models/static stay in DATA_DIR.
    USER_DATA_DIR: str = os.getenv("AAA_USER_DATA_DIR") or os.path.join(
        PROJECT_ROOT, "data"
    )

    # Engine API
    ENGINE_PORT: int = int(os.getenv("ENGINE_PORT", "8001"))
    CONSENSUS_RETURN_HARNESS_PATH: str = os.getenv("CONSENSUS_RETURN_HARNESS_PATH", "")
    AUTO_START_STRATEGY: bool = (
        os.getenv("AUTO_START_STRATEGY", "True").lower() == "true"
    )

    # API Keys. ALPACA_API_KEY/SECRET are the PAPER account by default; ``_select_alpaca_account``
    # (below) swaps them to the SEPARATE live account when PAPER_TRADING is off (#1425) â€” paper
    # and live credentials are stored + selected independently, never overwriting each other.
    API_KEY: Optional[SecretStr] = None
    API_SECRET: Optional[SecretStr] = None
    ALPACA_API_KEY: Optional[SecretStr] = _clean_env("ALPACA_API_KEY")
    ALPACA_SECRET_KEY: Optional[SecretStr] = _clean_env("ALPACA_SECRET_KEY")
    ALPACA_LIVE_API_KEY: Optional[SecretStr] = _clean_env("ALPACA_LIVE_API_KEY")
    ALPACA_LIVE_SECRET_KEY: Optional[SecretStr] = _clean_env("ALPACA_LIVE_SECRET_KEY")

    @model_validator(mode="after")
    def _select_alpaca_account(self):
        # #1425: the DESKTOP/OSS edition stores paper + live Alpaca credentials in SEPARATE
        # keychain slots and lets the operator switch between them. When live (PAPER_TRADING off,
        # the WORM-verified gate T1) it uses the live slots â€” never the paper key, and NO fallback
        # to paper (fail-closed if the live keys are missing). BORA: the engine is byte-identical
        # in Cloud Run, where secrets come from GCP and there are no ALPACA_LIVE_* slots â€” so the
        # swap is gated to DEPLOYMENT_MODE=LOCAL and the cloud's single ALPACA_API_KEY is untouched.
        self.API_KEY = self.ALPACA_API_KEY
        self.API_SECRET = self.ALPACA_SECRET_KEY
        is_desktop = os.getenv("DEPLOYMENT_MODE", "").upper() == "LOCAL"
        if is_desktop and not self.PAPER_TRADING:
            self.ALPACA_API_KEY = self.ALPACA_LIVE_API_KEY
            self.ALPACA_SECRET_KEY = self.ALPACA_LIVE_SECRET_KEY
        # Keep ALPACA_BASE_URL in lock-step with the selected account (paper-api â†” live api),
        # unless an explicit ALPACA_BASE_URL override is set. Done per-instance so it stays correct
        # after a runtime rebuild (apply_hitl_policy_update) and under test â€” not only at import.
        if (
            "ALPACA_BASE_URL" not in os.environ
            or self.ALPACA_BASE_URL == "https://paper-api.alpaca.markets"
        ):
            self.ALPACA_BASE_URL = (
                "https://paper-api.alpaca.markets"
                if self.PAPER_TRADING
                else "https://api.alpaca.markets"
            )
        return self

    POLYGON_API_KEY: Optional[SecretStr] = _clean_env("POLYGON_API_KEY")
    # #3145: fundamentals producer source â€” 'sec' (free SEC EDGAR, public-domain,
    # bundleable; DEFAULT) | 'polygon' (legacy keyed). Reader/agents source-agnostic.
    FUNDAMENTALS_SOURCE: str = (
        (os.getenv("FUNDAMENTALS_SOURCE", "sec") or "sec").strip().lower()
    )
    # #3146: ValuationAgent PEG -> P/S / P/B (later P/FFO) fallback. Default OFF (dark).
    VALUATION_MULTIMETRIC_ENABLED: bool = (
        os.getenv("VALUATION_MULTIMETRIC_ENABLED", "False").lower() == "true"
    )
    GEMINI_API_KEY: Optional[SecretStr] = _clean_env("GEMINI_API_KEY")
    DATABENTO_API_KEY: Optional[SecretStr] = _clean_env("DATABENTO_API_KEY")

    PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "True").lower() == "true"
    ALPACA_BASE_URL: str = (
        "https://paper-api.alpaca.markets"
        if (
            os.getenv("PAPER_TRADING", "True").lower() == "true"
            and "ALPACA_BASE_URL" not in os.environ
        )
        else _clean_env("ALPACA_BASE_URL", "https://api.alpaca.markets")
    )
    ALPACA_DATA_FEED: str = os.getenv(
        "ALPACA_DATA_FEED",
        (
            "iex"
            if os.getenv("PAPER_TRADING", "True").lower() == "true"
            or os.getenv("DEPLOYMENT_MODE", "").upper() == "LOCAL"
            else "sip"
        ),
    )

    DATABENTO_ENABLED: bool = bool(os.getenv("DATABENTO_API_KEY"))
    DATABENTO_GCS_BUCKET: str = os.getenv("DATABENTO_GCS_BUCKET", "")

    # Simulator Settings (Alpaca Sim-Day)
    SIM_MODE: bool = os.getenv("AAA_SIM_MODE", "False").lower() == "true"
    SIM_DATE: str = os.getenv("AAA_SIM_DATE", "")
    SIM_WINDOW: str = os.getenv("AAA_SIM_WINDOW", "09:30-10:30 ET")
    SIM_CADENCE: str = os.getenv("AAA_SIM_CADENCE", "1m")
    # ADR-SIM-01 (#2693): number of TRADING days to replay. Default 1 â€” byte-identical to the
    # single-day harness every existing sim test and acceptance run depends on.
    # Why >1 matters: on one static-price day, selling at the mark and holding at the mark are
    # worth the same (equity = cash + sum(qty*price)), so a one-day P&L is STRUCTURALLY blind to
    # exit policy â€” measured Delta 0.00 between two genuinely different parameter sets (#2692).
    # The consequence of an exit lands on later days. Annual review.
    SIM_DAYS: int = int(os.getenv("SIM_DAYS", "1") or "1")
    SIM_PACING: str = os.getenv("AAA_SIM_PACING", "flat-out")
    # #2548 C2: local offline replay corpus (dir of {SYMBOL}.parquet|.csv). The sim reads bars
    # ONLY from here â€” never a live StockHistoricalDataClient â€” so a sim day is offline + reproducible.
    SIM_CORPUS_DIR: str = os.getenv("AAA_SIM_CORPUS_DIR", "sim_corpus")

    OAUTH_CLIENT_ID: Optional[str] = _clean_env("OAUTH_CLIENT_ID")
    OAUTH_CLIENT_SECRET: Optional[SecretStr] = _clean_env("OAUTH_CLIENT_SECRET")
    OAUTH_REDIRECT_URI: str = _clean_env(
        "OAUTH_REDIRECT_URI", "http://127.0.0.1:8081/auth/alpaca/callback"
    )

    DATABASE_URL: Optional[str] = _clean_env(
        "DATABASE_URL",
        # OSS-4: Default to SQLite for local-first desktop mode.
        # Enterprise deployments override via Secret Manager / .env.
        # Account-state DB lives under USER_DATA_DIR (per-user; AAA_USER_DATA_DIR or DATA_DIR).
        f"sqlite+aiosqlite:///{os.path.join(USER_DATA_DIR, 'aaagents.db')}",
    )

    # OSS-4: REDIS_URL is optional. Empty string â†’ LocalStateClient (in-memory).
    # Set to redis://... for Enterprise mode with Redis Memorystore.
    REDIS_URL: str = os.getenv("REDIS_URL", "").strip()

    @property
    def is_local_mode(self) -> bool:
        """True when running in local desktop mode (SQLite, no Redis).

        Detects the runtime edition based on DATABASE_URL:
        - SQLite URL or empty â†’ local desktop mode (OSS Phase 1)
        - PostgreSQL URL â†’ enterprise cloud mode (Phase 2)
        """
        db_url = self.DATABASE_URL or ""
        return db_url.startswith("sqlite") or not db_url

    GCP_PROJECT_ID: Optional[str] = _clean_env("GCP_PROJECT_ID")
    GCP_REGION: str = _clean_env("GCP_REGION", "us-central1")
    VERTEX_ENDPOINT_ID: Optional[str] = _clean_env("VERTEX_ENDPOINT_ID")

    GEMINI_MODEL_NAME: str = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")
    ENABLE_GEMINI_IN_SIMULATION: bool = False
    GEMINI_RATE_LIMIT_DELAY: float = 6.0
    GEMINI_MAX_RETRIES: int = 3
    USE_SPY_VOLATILITY_FALLBACK: bool = True
    GEMINI_DAILY_CALL_LIMIT: int = int(os.getenv("GEMINI_DAILY_CALL_LIMIT", "950"))

    ENABLE_FRACTIONAL_SHARES: bool = True
    MIN_POSITION_VALUE: float = 1.0

    # #3154 (UXC-1 S1): kanonische Env-Naehte â€” unset â‡’ byte-identische Defaults.
    MAX_POSITION_PERCENT: float = float(
        os.getenv("MAX_POSITION_PERCENT", "0.25") or "0.25"
    )
    # #3154: Obergrenze des Conviction-Sizer-Bands (5â€“30 %), gekappt durch
    # MAX_POSITION_PERCENT. Bisher NUR getattr-Fallback 0.30 im Sizer
    # (risk_manager.py) â€” jetzt kanonisch; Fallback dort MUSS diesem Default
    # gleichen (#2784-Regel).
    MAX_POSITION_PERCENT_SIZING: float = float(
        os.getenv("MAX_POSITION_PERCENT_SIZING", "0.30") or "0.30"
    )

    # ADR-R10: see config.oss.py. Forbids leverage in the sizer (order value <= free cash).
    # risk_manager.py reads it via `from config import RISK_FORBID_LEVERAGE`, which resolves
    # on BOTH editions â€” here through the PEP-562 module `__getattr__` (bottom of this file)
    # that proxies to `_config_state`. Verified by execution: the import returns True, and
    # `RISK_FORBID_LEVERAGE=false` flips it. Set false to allow margin.
    RISK_FORBID_LEVERAGE: bool = (
        os.getenv("RISK_FORBID_LEVERAGE", "True").lower() == "true"
    )
    MIN_POSITION_PERCENT: float = float(
        os.getenv("MIN_POSITION_PERCENT", "0.05") or "0.05"
    )  # #3154 Env-Naht
    RISK_PER_TRADE_PERCENT: float = 0.02
    # Order floors â€” Alpaca's real $1 notional minimum (fractional shares, $0 commission).
    # ADR-R09/R11 (see config.oss.py + risk_manager.py): the legacy flat $50 slippage buffer + $50 dust
    # filter zeroed EVERY order on a funded small account ((cash-50)/slots < $50 â†’ blocked:risk). Alpaca's
    # only hard floor is $1; both are set to it. Mirrored in config.oss.py (BORA parity). risk_manager.py
    # reads them via `from config import â€¦` (PEP-562 __getattr__ on this edition â†’ _config_state).
    # PRODUCT CONSTRAINT (rev. of #2714): many customers run SMALL accounts where
    # fractional-share orders well below $50 are legitimate position sizes - a flat
    # $50 floor would block their entire book. Default stays at Alpaca's $1 technical
    # minimum; churn protection for small orders comes from the frequency guards
    # (#2716/#2719/#2720), not from order size. Operators of large accounts may
    # raise this via env.
    MIN_ORDER_VALUE_USD: float = float(os.getenv("MIN_ORDER_VALUE_USD", "1.0"))
    # #2965 (flag-dark): per-cycle durable entry-time â€” reconcile cooldown-based
    # instead of once/session AND bridge strategies to pm._trade_history. OFF =
    # byte-identical (HWM same-minute churn fix; activation = owner decision).
    ENTRY_TIME_RECONCILE_PER_CYCLE: bool = os.getenv(
        "ENTRY_TIME_RECONCILE_PER_CYCLE", "false"
    ).strip().lower() in ("1", "true", "yes")

    # ADR-R12: Entry-Floor INSIDE the total-exposure clamp branch (#2960).
    # Basis: the cap CLAMPS an order to the remaining headroom â€” residues of
    # $1-$5 were bought as standalone dust positions (live 08/2026: DD 0.0207
    # shares â‰ˆ $2.90, EL 0.0146 â‰ˆ $1.27). The floor fires ONLY when the clamp
    # binds, so a small account's normal cash-/pct-bound order can never reach
    # it â€” exactly the #2784 objection to raising the flat MIN_ORDER_VALUE_USD
    # (which stays at Alpaca's $1 technical minimum). Default 0.0 = OFF =
    # today's behavior byte-identical; activation is an owner decision
    # (suggested: 5.0). Mirrored in config.oss.py (BORA parity). Annual review.
    EXPOSURE_CLAMP_MIN_NOTIONAL_USD: float = float(
        os.getenv("EXPOSURE_CLAMP_MIN_NOTIONAL_USD", "0.0")
    )
    RISK_CASH_BUFFER_USD: float = 1.0
    # #2789: RELATIVE dust floor for EXIT orders, at the two submit paths. The absolute
    # floor above cannot do this job â€” it is an ENTRY knob whose only exit-side consumer
    # (the trim lever) is default-OFF, which is why raising it to $50 suppressed no exit
    # dust and merely blocked small accounts (#2721 â†’ reverted by #2784). Exit dust is
    # structural: displacement remainders, rotation part-sells, trim slivers, partial
    # fills. Relative thresholds make the floor incapable of blocking a small account.
    # Protective stops, unlabeled paths and FULL closes are exempt inside
    # core/dust_floor.py â€” a floor must never trap a position.
    DUST_EXIT_FLOOR_ENABLED: bool = (
        os.getenv("DUST_EXIT_FLOOR_ENABLED", "False").lower() == "true"
    )
    # Rule A (primary): a partial exit below this share of the position being reduced is
    # a sliver, not a portfolio action. Scales with account size by construction.
    DUST_EXIT_MIN_POSITION_PCT: float = float(
        os.getenv("DUST_EXIT_MIN_POSITION_PCT", "0.10")
    )
    # Rule B (backstop): only reaches orders whose position value is unknown. MUST stay
    # strictly weaker than rule A â€” with 10 equal-weight slots a position is ~10% of
    # equity, so rule A binds at ~1% of equity and this at 0.1%, i.e. 10x weaker. A
    # stronger backstop would suppress legitimate de-concentration on large accounts:
    # the flat-floor mistake again, merely dressed as a relative one.
    DUST_EXIT_MIN_EQUITY_PCT: float = float(
        os.getenv("DUST_EXIT_MIN_EQUITY_PCT", "0.001")
    )
    MAX_POSITIONS: int = 10
    MATERIAL_ENTRY_MIN_PCT_OF_TARGET: float = float(
        os.getenv("MATERIAL_ENTRY_MIN_PCT_OF_TARGET", "0.0") or "0.0"
    )
    # O3 (cash-aware sizing): cash-constraint divisor = remaining free slots (max_positions - held),
    # not the fixed cap â†’ a mostly-invested book funds proper-sized positions instead of dust. OFF â†’
    # byte-identical to the fixed /max_positions divisor. Mode-neutral (paper == live). Mirrored in
    # config.oss.py (BORA parity).
    CASH_AWARE_SLOTS_ENABLED: bool = (
        os.getenv("CASH_AWARE_SLOTS_ENABLED", "True").lower() == "true"
    )
    # ADR-R11 (#3135): proportional slot-cash split. The equal (cash/slots) cash cap
    # flattens every concurrent BUY of a cycle to the SAME dollar amount, erasing the
    # per-name IV/conviction size differentiation produced upstream by #3094
    # (VIXAware-IV -> forecast_vol -> vol-targeting) and the conviction sizer. ON scales
    # each slot by the position's own target value; it can only SHRINK vs the equal split
    # (no-leverage axiom preserved). PROMOTED Default ON (#3135, owner decision 31.08.):
    # complements #3094 (already default ON) â€” the cash cap otherwise re-flattened its
    # differentiation (measured live, real-money loss 31.08.). Clamp-inventory verified:
    # differentiation survives to the executed order value. Sim sweep FOLLOWS (post-promote).
    # Revert: PROPORTIONAL_SLOT_CASH_ENABLED=false. Basis: MiFID II Art. 17. Annual review.
    PROPORTIONAL_SLOT_CASH_ENABLED: bool = (
        os.getenv("PROPORTIONAL_SLOT_CASH_ENABLED", "True").lower() == "true"
    )
    ENABLE_DYNAMIC_SIZING: bool = True

    # Conviction EWMA + top-up dead-band (churn fix; mirrors config.oss.py). The conviction that drives
    # dynamic sizing re-rates every cycle from the raw round-table score â†’ a held name's target weight
    # jitters â†’ repeated micro top-ups (buy-high/sell-low churn). CONVICTION_EWMA smooths conviction per
    # symbol: smoothed = alphaÂ·raw + (1-alpha)Â·prev (first cycle â†’ raw; lower alpha = smoother/laggier).
    # POSITION_TOPUP_DEAD_BAND_PCT: do NOT top up a held name already within this % of its (smoothed)
    # conviction target weight â€” no re-buy for a marginal move. Default on; env rollback.
    CONVICTION_EWMA_ENABLED: bool = (
        os.getenv("CONVICTION_EWMA_ENABLED", "True").lower() == "true"
    )
    CONVICTION_EWMA_ALPHA: float = float(
        os.getenv("CONVICTION_EWMA_ALPHA", "0.3") or "0.3"
    )
    POSITION_TOPUP_DEAD_BAND_PCT: float = float(
        os.getenv("POSITION_TOPUP_DEAD_BAND_PCT", "5.0") or "5.0"
    )

    # --- #1953 TRD-2: Vol-Targeting / Risk-Parity sizing (default ON) ---
    # Master switch: ON (default) => calculate_position_size multiplies the plumbed
    # HAR-RV forecast_vol into final_risk_scaler as clip(target/fv, lo, hi); a
    # missing/invalid forecast is a fail-safe no-op (scaler 1.0). OFF => vol-blind
    # sizing (env rollback). Default flipped ON ON OWNER RESPONSIBILITY (owner-waiver
    # 2026-08-14, in lieu of the walk-forward OOS gate â€” proxy evidence accepted:
    # #1953 is a Sharpe-neutral drawdown dial, MiFID II Art. 17). USER-SETTABLE via the
    # "VolatilitÃ¤ts-Sizing" control + the VOL_TARGET_DAILY_VOL dial. Mirrored in
    # config.oss.py (BORA parity).
    VOL_TARGETING_SIZING_ENABLED: bool = (
        os.getenv("VOL_TARGETING_SIZING_ENABLED", "True").lower() == "true"
    )
    # ADR-R13: Vol-Targeting-Kalibrierung â€” target 1.5%/Tag, Scaler-Bounds [0.5, 1.5]
    # Basis: MiFID II Art. 17 (Risk-Controls im algorithmischen Handel); Werte gespiegelt
    # aus den gewachsenen vol_model.vol_size_scaler-Defaults (core/ml/vol_model.py).
    # BegrÃ¼ndung: 1.5%/Tag Ziel-Vol â‰ˆ Median der Universe-Forecasts (HAR-RV, tÃ¤gl. Stdev);
    # die Bounds begrenzen den Risk-Parity-Eingriff auf Â±50% der conviction-GrÃ¶ÃŸe, damit
    # ein AusreiÃŸer-Forecast nie eine Order dominiert. Finance-Judgement, nicht auf die
    # aktuelle Universe-Verteilung gefittet â€” Backtest-Kalibrierung + jÃ¤hrlicher Review
    # (Plan #1953 Â§12.6); bis dahin env-tunbar. Inert solange der Master-Schalter OFF ist.
    VOL_TARGET_DAILY_VOL: float = float(os.getenv("VOL_TARGET_DAILY_VOL", "0.015"))
    VOL_SIZE_SCALER_LO: float = float(os.getenv("VOL_SIZE_SCALER_LO", "0.5"))
    VOL_SIZE_SCALER_HI: float = float(os.getenv("VOL_SIZE_SCALER_HI", "1.5"))

    # #3095 (b): UpsideSkewAgent â€” 25Î” Risk-Reversal (IV(Call)-IV(Put)) als
    # DIREKTIONALES AufwÃ¤rtssignal im Konsens. Default OFF (dark) = byte-identisch
    # (der Agent enthÃ¤lt sich / ist nicht aktiv). UPSIDE_SKEW_WEIGHT = Stimmgewicht.
    UPSIDE_SKEW_AGENT_ENABLED: bool = (
        os.getenv("UPSIDE_SKEW_AGENT_ENABLED", "false").lower() == "true"
    )
    UPSIDE_SKEW_WEIGHT: float = float(os.getenv("UPSIDE_SKEW_WEIGHT", "0.30"))

    # #3199: UpsideSkew as a SIZING-asymmetry tilt (distinct from the #3095
    # DIRECTIONAL agent above). The 25Î” RR skew PERCENTILE becomes a bounded
    # multiplicative factor on the vol-targeting scaler (risk_manager.skew_size_tilt)
    # â€” bullish upside skew sizes up, crash skew sizes down â€” mirroring #3094
    # (IV LEVEL -> size) around the IV SKEW dimension. Default OFF / CAP 0.0 =>
    # tilt 1.0 => byte-identical (sizer AND MiFID II audit). CAP bounds the tilt to
    # [1-cap, 1+cap]. Mirrored in config.oss.py (BORA parity). Promote only after a
    # measured paper/prod A/B (RR is options-only, offline-absent â€” plan #3199 Â§5).
    SKEW_SIZE_TILT_ENABLED: bool = (
        os.getenv("SKEW_SIZE_TILT_ENABLED", "false").lower() == "true"
    )
    SKEW_SIZE_TILT_CAP: float = float(os.getenv("SKEW_SIZE_TILT_CAP", "0.0"))

    # #3210: Coverage-Vorsichts-Sizing-Abschlag (Prudenz, KEIN Alpha). StÃ¤rke lambda
    # des abgeschwÃ¤chten âˆšcoverage-Abschlags: discount = 1 - lambda*(1 - sqrt(cov)).
    # Default 0.0 = aus (byte-identisch, dark-ship). Arm-Wert 0.25. ADR-R17.
    COVERAGE_SIZING_STRENGTH: float = float(
        os.getenv("COVERAGE_SIZING_STRENGTH", "0.0") or "0.0"
    )
    # #3284 (Epic #3086): Clean-weight sizing (Variant 2, PER-SYMBOL). One target-weight
    # authority instead of the 7-factor conviction stack. Conviction LEAVES the SIZE lever
    # (stays direction/admission) â€” Grinold-Kahn: signal once. Values:
    #   "off" => byte-identical to the conviction path (dark-ship default);
    #   "a"   => strict 1/N (equal-DOLLAR, DeMiguel baseline arm);
    #   "b"   => 1/N x vol-targeting x active switchable tilts (skew/coverage) â€” equal-RISK-ish.
    # final_risk_scaler is NOT applied in a/b (vix/confidence/size/reduction dropped as
    # double-counters); for "b" only the ONE risk input (vol) + the live tilts compose.
    # Cash couples to the resulting target via the existing _demand_fraction. Per-symbol by
    # design: the sizer is called one symbol at a time (order_executor), so cross-sectional
    # Sigma_j renorm/capped-simplex is not computable here â€” it is Phase 1b where the set is
    # in-process. Promote only after RTR-0 at the holding horizon + #3281 TC + human sign-off
    # (MiFID II RTS 6, EU-AI-Act Art. 14). Mirrored in config.oss.py (BORA parity).
    # PROMOTED to default "b" (#3284, Owner-Waiver 2026-09-09): 1/N Ã— VIXAware vol-targeting
    # is now the DEFAULT sizing procedure, replacing the 7-factor conviction stack ("off" is
    # kept as the switchable legacy fallback). Rationale: capital-preservation-first â€” the
    # vol-tilt gave the shallowest drawdown in the stress window of the #3284 sim campaign.
    # Real-money still requires the WORM live-consent gate; the audit "applied==logged" gap
    # (1/N base in the risk_size_scaler mirror, #3284 Point 3) is closed before that consent.
    # Mirror in config.oss.py (BORA parity). Promote-PR sequenced AFTER the #3288 sizer merge.
    CLEAN_WEIGHT_SIZING: str = (
        os.getenv("CLEAN_WEIGHT_SIZING", "b").strip().lower() or "b"
    )
    # #3094 (a): route the per-name IMPLIED vol into the #1953 forecast_vol sizer
    # AND drop VIXAware from the direction mean (consensus). The sizer
    # (risk_manager) is untouched.
    # PROMOTED to default ON: VIXAware's score is 1 - IV-percentile, i.e. a
    # DIRECTIONLESS volatility magnitude that was steering the DIRECTION vote at
    # 26.5% of the total directional weight (0.45 of 1.70) against a 0.65 buy
    # threshold. Counterfactual over 150,355 logged decisions of the 60-day
    # baseline run (score reconstruction exact on 150,305/150,305): removing it
    # moves 20.18% of all decisions across the threshold. The newly released buys
    # carry a median VIXAware score of 0.209 (volatile names), the dropped ones
    # 0.907 (calm names) â€” the direction lever was running an unintended
    # low-volatility selection. The volatility statement now acts on SIZE only,
    # where those same volatile names enter at 38% of a calm name's size.
    # Set IMPLIED_VOL_FORECAST_ENABLED=false to revert without a deployment.
    IMPLIED_VOL_FORECAST_ENABLED: bool = (
        os.getenv("IMPLIED_VOL_FORECAST_ENABLED", "true").lower() == "true"
    )
    # ADR-a01: removes the Variance-Risk-Premium LEVEL bias before the
    # daily-calibrated scaler (raw IV would systematically under-size).
    # CALIBRATED on corpus 7cf4c4b03bf0 (15,544 point-in-time symbol/day pairs,
    # 2026-05-08..2026-08-04): HAR-RV yields a mean vol scaler of 0.8472; the
    # IV factor reproducing that SAME exposure is 0.8513 -> 0.85. Chosen for
    # EXPOSURE NEUTRALITY so that (a) swaps the vol ESTIMATOR without also
    # moving the risk LEVEL (one lever per change; no relative drag vs SPY).
    # NOTE: the forecast-neutral factor (median realized/implied over 31,056
    # pairs, 2024-01..2026-07) is 0.932 â€” i.e. the empirical VRP is only ~7%,
    # not the ~20% the pre-calibration 0.80 placeholder assumed. Moving to
    # 0.932 would cut mean exposure by a further 7.3% and is a SEPARATE
    # risk-level decision, deliberately not bundled here.
    # Inert while IMPLIED_VOL_FORECAST_ENABLED is OFF.
    IV_VRP_DEBIAS_FACTOR: float = float(os.getenv("IV_VRP_DEBIAS_FACTOR", "0.85"))
    # #3619 (Epic #3086): how strongly the vol forecast (IV -> forecast_vol) changes the
    # position size. Registry-Setting 0..1: 0 = no effect (every name 1/N), 1 = the full
    # vol-targeting effect (today). Linear blend in risk_manager.vol_targeting_scaler:
    # scaler' = 1 + w * (scaler - 1). Never a direction effect; caps unchanged.
    # Auslieferung 1.0 => byte-identisch. Shown on the round-table card as the
    # VIXAware slider while IMPLIED_VOL_FORECAST_ENABLED is on (VIX_RISK_WEIGHT stays
    # the consensus weight of the IV-off path).
    VIX_SIZE_INFLUENCE: float = float(os.getenv("VIX_SIZE_INFLUENCE", "1.0") or "1.0")
    # #3099 (H2): bounded calm-regime risk-on step in the ADR-R08 VIX ladder â€” more
    # invested in a favorable regime, NEVER leverage. Default OFF = byte-identical
    # (pure down-only ladder). The >1.0 step is capped downstream by
    # MAX_TOTAL_EXPOSURE_PCT (0.95) + per-name caps + RISK_FORBID_LEVERAGE.
    BULL_EXPOSURE_ENABLED: bool = (
        os.getenv("BULL_EXPOSURE_ENABLED", "false").lower() == "true"
    )
    # Calm-regime (VIX<=18) scaler when the flag is ON. To be calibrated in the
    # pro-cyclical-stress sweep; inert while BULL_EXPOSURE_ENABLED is OFF.
    BULL_EXPOSURE_CALM_SCALER: float = float(
        os.getenv("BULL_EXPOSURE_CALM_SCALER", "1.15")
    )

    REBALANCE_DRIFT_THRESHOLD_PCT: float = 3.0
    REBALANCE_COOLDOWN_HOURS: float = 2.0
    MIN_HOLD_HOURS: float = 1.0
    MAX_TRADES_PER_SYMBOL_PER_DAY: int = 5
    CONSECUTIVE_SELL_BYPASS_THRESHOLD: int = 8
    # ADR-R12 (anti-churn Part C, #2176): minimum seconds between OPENING orders for
    # the SAME symbol. Blocks the micro-churn pathology (buy->sell->buy within a
    # minute) that spends the daily-trade budget without building the book. 900s (15m)
    # spaces intraday re-entries while letting genuine signal rotation through;
    # per-symbol, so unrelated names are unaffected. BUY-side only â€” exits are never
    # gated (exit invariant). Complements the daily cap MAX_TRADES_PER_SYMBOL_PER_DAY.
    MIN_ORDER_INTERVAL_SEC: int = 900
    # ADR-R14 (#2696): does the DISPLACEMENT path honour SMART_EXIT_MIN_HOLD_DAYS?
    # Displacement (portfolio_manager.debate_position_swap â€” sell the weakest holding to fund a
    # better candidate) read NO holding policy: a hard-coded 1-day argument, which the
    # `score_diff > 15` branch ignored outright. Measured on a 10-day replay it produced 13 of 24
    # SELLs, against 3 from the rotation path that DOES read the policy â€” which is why arming
    # SMART_EXIT_MIN_HOLD_DAYS did not stop the churn. True also couples the 'held >5 days with
    # minimal gain' heuristic to the same period. Gates ONLY opportunity-driven displacement;
    # stop-loss, intelligent_exit and the DrawdownGuard are separate paths. Annual review.
    DISPLACEMENT_RESPECTS_MIN_HOLD: bool = (
        os.getenv("DISPLACEMENT_RESPECTS_MIN_HOLD", "True").lower() == "true"
    )
    # #2940: Naht fuer die Haltedauer-Kurve im Positions-Score (Gewicht 20 %).
    # Defaults reproduzieren die historischen Literale 30/50/70/90 an den Grenzen 1/3/7 Tagen
    # BYTE-IDENTISCH â€” dies ist nur ein Sweep-Knopf, kein neues Verhalten. Begruendung und
    # Kurvenformen: core/portfolio_manager.py::holding_period_score. Der Sweep prueft drei
    # Kandidaten: Amtsinhaber (30/0) Â· "unbekannt ist nicht schlecht" (50/0) Â· Reifung ueber
    # die konfigurierte Haltefrist (50/20). Gelesen auf CALL-Zeit ueber get_config
    # (CODING_POLICY Â§2.10). Mirrored in config.oss.py (BORA-Paritaet).
    HOLDING_PERIOD_SCORE_FRESH: float = float(
        os.getenv("HOLDING_PERIOD_SCORE_FRESH", "30.0") or "30.0"
    )
    # #2940: 0.0 = historische Stufen (Maximum ab Tag 7). > 0 ersetzt die Stufen durch
    # eine lineare Reifung von HOLDING_PERIOD_SCORE_FRESH auf 90 ueber diese Zahl von Tagen â€”
    # damit laesst sich der Score an die tatsaechliche Haltefrist koppeln statt an 7 Tage.
    HOLDING_PERIOD_SCORE_SPAN_DAYS: float = float(
        os.getenv("HOLDING_PERIOD_SCORE_SPAN_DAYS", "0.0") or "0.0"
    )

    NEWS_POLLING_INTERVAL_SECONDS: int = (
        99999999 if os.getenv("AAA_SIM_MODE", "False").lower() == "true" else 300
    )
    STRATEGY_MONITOR_INTERVAL_SECONDS: int = 1800
    # #1832 â€” TIME-driven loop-stall threshold: flag the trading loop STALLED once its last
    # completed cycle is older than this WHILE the market is open (catches a fully-dead loop the
    # cycle-driven CycleWatchdog misses). Default 3x the monitor interval (90 min); env-overridable.
    # #2142: how many CONSECUTIVE active-ping failures the LatencyWatchdog needs
    # before it trips the global kill-switch. Debounces a single transient Alpaca
    # blip; the passive real-order-latency trip stays immediate.
    # ADR-2142-01: LATENCY_TRIP_CONSECUTIVE = 3
    # Basis: EU AI Act Art. 14 human-oversight + operational safety (kill-switch debounce).
    # Rationale: trip only after 3 CONSECUTIVE threshold-breaching active pings (~<=30s at the
    # 10s ping interval), so a single transient Alpaca blip cannot halt all trading, while a
    # sustained outage still trips promptly. Passive real-order-latency trip stays immediate.
    # 1 = today's trip-on-first-failure behaviour. Conservative default; annual review.
    LATENCY_TRIP_CONSECUTIVE: int = int(os.getenv("LATENCY_TRIP_CONSECUTIVE", "3"))
    LOOP_STALL_AFTER_SECONDS: int = int(os.getenv("LOOP_STALL_AFTER_SECONDS", "5400"))
    # #1832 increment 2 â€” the independent stall-monitor thread checks loop liveness this often and
    # emits a loop_stalled OTel span + Slack alert once per stall episode. Default-on (safety obs).
    LOOP_STALL_CHECK_INTERVAL_SECONDS: int = int(
        os.getenv("LOOP_STALL_CHECK_INTERVAL_SECONDS", "300")
    )
    ENABLE_LOOP_STALL_MONITOR: bool = (
        os.getenv("ENABLE_LOOP_STALL_MONITOR", "true").lower() == "true"
    )
    DEFAULT_SYMBOLS: List[str] = Field(
        default_factory=lambda: [
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
    )
    LEARNED_RULES_FILE: str = os.path.join(DATA_DIR, "ai_learned_rules.json")
    BENCHMARK_EQUITY_FILE: str = os.path.join(DATA_DIR, "benchmark_equity.json")
    BENCHMARK_COMPARISON_CSV: str = os.path.join(
        DATA_DIR, "benchmark_equity_comparison.csv"
    )

    ACTIVE_STRATEGY: str = os.getenv("ACTIVE_STRATEGY", "RLAgent")
    RL_MODEL_VERSION: str = os.getenv("RL_MODEL_VERSION", "rl_agent_v3_dsr")
    LSTM_MODEL_VERSION: str = os.getenv("LSTM_MODEL_VERSION", "v1")
    STRATEGY_SWITCH_WITHOUT_LIQUIDATION: bool = True
    SIMULATION_RL_VERSION: str = "rl_agent_v3_dsr"
    SIMULATION_USE_LIVE_STABILIZATION: bool = True
    SIMULATION_FALLBACK_BUY: bool = False

    SPECIALIST_HIGH_PRIO_INTERVAL_HOURS: float = 2.0
    SPECIALIST_FULL_CYCLE_HOURS: float = 12.0
    SPECIALIST_UNIVERSE_SIZE: int = 500

    TRAILING_STOP_PCT: float = 3.0
    # #3632: STOP_LOSS_PCT ist nur noch der Broker-Backstop (core/broker_stops.py, ADR-020,
    # Schalter BROKER_STOPS_ENABLED). Die Engine selbst verkauft ueber die Verlust-Stufen
    # des Intelligent Exit (LOSS_*_PCT). TAKE_PROFIT_PCT entfiel mit Smart Exit.
    STOP_LOSS_PCT: float = float(
        os.getenv("STOP_LOSS_PCT", "7.0") or "7.0"
    )  # #3154 Env-Naht
    MIN_HOLD_HOURS_BEFORE_TRAIL: float = 1.0
    MIN_PROFIT_FOR_TRAIL_PCT: float = 2.0

    # #1952 — Min-hold + hysteresis gate on the rank-drop rebalance (core/hold_policy.py).
    # The LSTM signal is a ~5-day-horizon signal; selling a name the moment it
    # drops out of the top-N churns the book daily and throws away the horizon
    # alpha (bake-off: daily-naive net Sharpe 0.758 vs ~5-day-hold ~1.56, +0.80).
    # SMART_EXIT_MIN_HOLD_DAYS: minimum days a name is committed before a plain
    #   rebalance-sell may fire. Rollback lever: set 0.0 to restore the legacy
    #   sell-on-drop behavior. Env-configurable (SMART_EXIT_MIN_HOLD_DAYS).
    # SMART_EXIT_EXIT_RANK_HYSTERESIS: early-exit override factor. A held name is
    #   sold BEFORE min-hold only if its rank has collapsed past
    #   top_n_size * this factor (sustained adverse info, not daily noise).
    # Risk exits (stop-loss / take-profit / trailing) are NEVER gated by these.
    # #2839: defaults = shipped desktop launcher profile (owner 2026-08-13, "desktop
    # is master") â€” previously 5.0/3.0 while every tested install ran 20/5 via the
    # Electron shell env injection. Pinned by tests/unit/test_desktop_master_defaults.py.
    SMART_EXIT_MIN_HOLD_DAYS: float = 20.0
    SMART_EXIT_EXIT_RANK_HYSTERESIS: float = 5.0

    # --- Autonomous SELL decisions: de-concentration TRIM + consensus-drop ROTATION ---
    # Two flag-gated per-cycle exit levers (the trading loop emits ~0 organic SELL because
    # the rank-drop signal is averaged away by the bullish conditioners). Each ARMS an
    # autonomous real-money SELL path. ROTATION ships OFF (owner directive 2026-09-08 â€” was ON
    # 2026-08-03, reverted after live churn; see below); TRIM stays OFF â€” each is an independent lever.
    #   ROTATION_EXIT_ENABLED: sell a held name that dropped out of the LSTM top-N past
    #     hysteresis / min-hold (reuses SMART_EXIT_MIN_HOLD_DAYS + SMART_EXIT_EXIT_RANK_HYSTERESIS).
    #   DECONCENTRATION_TRIM_ENABLED: partially reduce an overweight name toward target
    #     (a $-fraction of its market value â€” never a price division, never oversells).
    #   ROTATION_PANEL_MAX_AGE_DAYS: refuse a STALE LSTM panel (a populated but outdated
    #     snapshot would pin exits to an old top-K). Date-normalized (now.date() - snapshot_date).
    # Flags OFF â†’ byte-identical no-op (no panel/recommender call, no SELL). Both levers only
    # ever REDUCE exposure; the SELL still passes ComplianceGuardian + kill-switch + market-hours.
    # Mode-neutral (paper == live). Mirrored in config.oss.py (BORA parity).
    # ROTATION now defaults OFF (owner directive 2026-09-08): on LIVE real money it churned the
    # book â€” held names the round-table STILL rated BUY were force-sold at a loss to rotate into
    # new picks (CLX held 3d â†’ âˆ’4.7%, APO 1d â†’ âˆ’4.0%, CRL sold-then-rebought the next day), ~85%
    # turnover in 8 trading days, contradicting the capital-preservation mandate. The exits bypass
    # the consensus (the direction signal stayed BUY) and the gatekeeper â€” a separate opportunity
    # path. Root-cause fix (holding-gate + consensus-symmetric exits) is tracked separately; this
    # flip is the immediate, reversible mitigation. When re-enabled, rotation stays bounded by
    # ROTATION_MAX_EXITS_PER_SESSION (the per-SESSION ceiling is what actually bounds a whole-book
    # flush â€” rotation SELLs are exempt from the daily-trades cap and the loop runs ~390
    # cycles/session; the per-cycle cap alone does NOT prevent it â€” the 2026-07-28 flush). TRIM
    # already defaults OFF. Re-enable rotation with ROTATION_EXIT_ENABLED=true.
    # #2712 Inc 2: displacement sells AFTER the swap BUY confirms (False = legacy
    # sell-first with its recovery-roundtrip failure mode).
    DISPLACEMENT_BUY_FIRST: bool = (
        os.getenv("DISPLACEMENT_BUY_FIRST", "True").lower() == "true"
    )
    # #2711 (P0): min-hold is a HARD gate for rotation/rank exits (False = legacy OR).
    ROTATION_MIN_HOLD_HARD_GATE: bool = (
        os.getenv("ROTATION_MIN_HOLD_HARD_GATE", "True").lower() == "true"
    )
    # #3291 (dark, default OFF = byte-identical): make PositionScore.days_held measure the
    # CURRENT lot (stamped on a witnessed absentâ†’present entry, reset on full exit) instead
    # of ``min(_trade_history)``. The min() dates a name to the OLDEST buy in the 30-day
    # window, so a name re-entered within 30 days (sellâ†’rebuy) looked 20+ days old and the
    # rotation min-hold gate churned the fresh lot on live real money (2026-09-08, #3291).
    # OFF preserves the legacy min(_trade_history) age. Promote only after the #3291 sim A/B.
    ROTATION_MIN_HOLD_CURRENT_LOT: bool = (
        os.getenv("ROTATION_MIN_HOLD_CURRENT_LOT", "False").lower() == "true"
    )
    ROTATION_EXIT_ENABLED: bool = (
        os.getenv("ROTATION_EXIT_ENABLED", "False").lower() == "true"
    )
    DECONCENTRATION_TRIM_ENABLED: bool = (
        os.getenv("DECONCENTRATION_TRIM_ENABLED", "False").lower() == "true"
    )
    # #3291: master switch for opportunity DISPLACEMENT â€” the full-book swap that sells the
    # weakest holding to fund a new BUY (portfolio_manager.should_open_new_position Case 2 â†’
    # debate_position_swap). This is the last opportunity-driven forced sell that ROTATION_EXIT
    # OFF does NOT stop (it force-sold BUY-rated names on live real money, #3291). Default ON =
    # byte-identical. OFF â‡’ a full book waits for a slot to free via a consensus SELL or a risk
    # exit (capital-preservation posture). Sibling of ROTATION_EXIT_ENABLED; BORA parity.
    DISPLACEMENT_ENABLED: bool = (
        os.getenv("DISPLACEMENT_ENABLED", "True").lower() == "true"
    )
    # ADR-D01 (#3418, Owner-Entscheid Variante B): Sitzungsdeckel der Verdraengung = 2.
    # Basis: Keine regulatorische Pflicht â€” eine Churn-Grenze, uebernommen vom Geschwister
    #        ROTATION_MAX_EXITS_PER_SESSION (ebenfalls 2).
    # Begruendung: Der per-Zyklus-Deckel allein bindet KEINEN Buchdurchlauf.
    #        Verdraengungs-Verkaeufe sind risikomindernde SELLs und damit von der
    #        Tages-Handelsgrenze ausgenommen; der Zyklus laeuft rund 390-mal je Sitzung.
    #        Eine anhaltende Rangverschiebung leert das Buch dann Name fuer Name ueber den
    #        Tag â€” das ist der Flush vom 28.07., und dieselbe Lehre, die die Rotation ihren
    #        Sitzungsdeckel gekostet hat.
    #        Der Wert folgt dem der Rotation, weil beide Pfade dieselbe Absicht verfolgen;
    #        zwei verschiedene Deckel fuer dieselbe Frage waeren ein Zustand, in dem einer
    #        von beiden falsch ist.
    #        <= 0 bedeutet UNBEGRENZT und stellt das heutige Verhalten byte-identisch her
    #        (Rueckweg ohne Revert). Schutz-Exits, Stop-Loss und DrawdownGuard sind NICHT
    #        betroffen â€” der Deckel bindet ausschliesslich gelegenheitsgetriebene
    #        Verdraengung. Jaehrliche Pruefung.
    DISPLACEMENT_MAX_PER_SESSION: int = int(
        os.getenv("DISPLACEMENT_MAX_PER_SESSION", "2")
    )
    # Conviction-weighting fix (churn root cause): the dynamic sizer intentionally targets
    # 5â€“25% per name by conviction (risk_manager.calculate_position_size), but the trim
    # de-concentrated every name back to the flat 1/max_positions (=10%) weight â€” two
    # contradictory target-weight authorities â†’ buy-high/sell-low churn (the machine bought a
    # name up to ~20%, trimmed it to 10%, re-bought it, â€¦ burning the round-trip spread).
    # RESPECTS_CONVICTION=True â†’ de-concentrate ONLY genuine over-concentration past
    # MAX_POSITION_PERCENT (the hard sizing/gatekeeper cap = 25%), leaving the sizer's band
    # intact (let conviction winners run; only price-drift over the cap is trimmed). Never
    # emits INCREASE. False â†’ legacy flat-10% target (rollback). Mirrored in config.oss.py.
    DECONCENTRATION_TRIM_RESPECTS_CONVICTION: bool = (
        os.getenv("DECONCENTRATION_TRIM_RESPECTS_CONVICTION", "True").lower() == "true"
    )
    # ADR-EXIT-02 (#2681): trailing-tier profile of core/intelligent_exit.py
    # (`_active_trail_tiers`, resolved at CALL time â€” the module constants are frozen at
    # import). "legacy" (DEFAULT) = today's table, byte-identical. "midterm" widens the
    # trails and raises the min-hold hours (6-15% / 72-120h instead of 1.5-8% / 1-12h).
    # Basis: live churn 2026-08-05 â€” the legacy (2.0, 1.5, 1.0) tier sold HPE 30 minutes
    # after a top-up on a 1.6% pullback and PCG on 2.2%, both re-bought higher; at
    # single-stock daily volatility a 1.5% trail from +2% profit after 1h is noise, not
    # profit protection, and it contradicts the proven "selection + mid-term hold"
    # pattern (#1957/#2401). The LOSS side (LOSS_TIER_* / HARD_STOP_LOSS_PCT) is
    # deliberately NOT part of the profile â€” losers are cut unchanged. An unknown value
    # falls back to "legacy" with a WARNING. Mirrored in config.oss.py (BORA parity).
    # #2839: default legacyâ†’midterm = shipped desktop launcher profile (desktop is master).
    EXIT_TRAIL_PROFILE: str = os.getenv("EXIT_TRAIL_PROFILE", "midterm")
    ROTATION_PANEL_MAX_AGE_DAYS: int = int(
        os.getenv("ROTATION_PANEL_MAX_AGE_DAYS", "3")
    )
    # Rotation per-cycle cap (mirrors config.oss.py) â€” bound the whole-book flush so a
    # single ranking shift cannot fully liquidate in one cycle. Default 1 (owner directive
    # 2026-08-03, tightened from 2 when ROTATION_EXIT_ENABLED flipped ON): at most ONE full
    # exit per cycle â€” the most conservative staged drain. A value <=0 FLOORS to 1 in the
    # loop (NOT "unlimited" â€” that was a footgun); disable rotation via ROTATION_EXIT_ENABLED.
    ROTATION_MAX_EXITS_PER_CYCLE: int = int(
        os.getenv("ROTATION_MAX_EXITS_PER_CYCLE", "1")
    )
    # Rotation per-SESSION ceiling (2026-08-03, owner directive; mirrors config.oss.py) â€” the
    # per-cycle cap alone does NOT prevent a whole-book flush (rotation exits are risk-reducing
    # SELLs, exempt from the daily-trades cap, and the loop runs ~390 cycles/session). Caps
    # CUMULATIVE rotation exits per TRADING DAY (loop resets on date rollover); protective
    # stop-losses stay UNCAPPED. <=0 = unlimited (byte-identical rollback).
    # #2714: the TRIM lever gets a session cap too (rotation already had one).
    # #2839: rotation session cap 3â†’2 = shipped desktop launcher profile (desktop is master).
    TRIM_MAX_EXITS_PER_SESSION: int = int(os.getenv("TRIM_MAX_EXITS_PER_SESSION", "3"))
    ROTATION_MAX_EXITS_PER_SESSION: int = int(
        os.getenv("ROTATION_MAX_EXITS_PER_SESSION", "2")
    )

    # #3632 (eine Exit-Autoritaet): die Verlust-Stufen des Intelligent Exit sind
    # Registry-Settings (WORM-Apply, Desktop-Durchreichung, Console). Auslieferung
    # byte-identisch zu den alten Modul-Konstanten LOSS_TIER_1/2/3 (-2 / -4 / -6 %).
    # Ordnung (Registry-Klemme (d)): LOSS_WATCH > LOSS_CUT > LOSS_ESCALATION > HARD_STOP.
    #   WATCH      Basisdruck 40 - Beobachtung, verkauft nicht allein.
    #   CUT        Basisdruck 70 - verkauft, sobald Druck x Zeitfaktor >= 90, d.h. ab
    #              24 h Haltedauer (Zeit-Stufen 4/24/72 h bleiben fest, Owner 24.09.2026).
    #   ESCALATION Basisdruck 90 - verkauft sofort.
    #   HARD_STOP  fest, env-frei (Leitplanke A6); jede Stufe wird darueber geklemmt.
    LOSS_WATCH_PCT: float = float(os.getenv("LOSS_WATCH_PCT", "-2.0") or "-2.0")
    LOSS_CUT_PCT: float = float(os.getenv("LOSS_CUT_PCT", "-4.0") or "-4.0")
    LOSS_ESCALATION_PCT: float = float(
        os.getenv("LOSS_ESCALATION_PCT", "-6.0") or "-6.0"
    )
    HARD_STOP_LOSS_PCT: float = -8.0
    MOMENTUM_SELL_THRESHOLD: float = -0.3
    # #3662: Panik-Schutz des Intelligent Exit — Schalter (Registry, Console > Stops); im Fenster verkauft nur der Hard Stop
    PANIC_PROTECTION_ENABLED: bool = (
        os.getenv("PANIC_PROTECTION_ENABLED", "True").lower() == "true"
    )
    # #3662: Panik-Fenster in Stunden nach dem Kauf (0,5–4,0; an / 2,0 h = Stand vor #3662)
    PANIC_PROTECTION_HOURS: float = float(
        os.getenv("PANIC_PROTECTION_HOURS", "2.0") or "2.0"
    )
    # desktop-exit: feed the per-cycle risk-exit a REAL remembered high-water-mark (peak since held)
    # so intelligent_exit's trailing stop can fire when a held winner rolls over. OFF â†’ hwm defaults to
    # current price â†’ drawdown 0 â†’ trailing stop can never fire (held winners never trimmed, cash stuck).
    # Default ON (operator directive; the trailing stop only ever PROTECTS gains â€” safe direction).
    # Mirrored in config.oss.py (BORA parity).
    POSITION_EXIT_HWM_TRAILING_ENABLED: bool = (
        os.getenv("POSITION_EXIT_HWM_TRAILING_ENABLED", "True").lower() == "true"
    )
    # #2557: persist the trailing-stop high-water-mark map across the daily desktop restart. Today
    # _position_high_water_marks (trading_loop.py) is in-memory only â†’ lost on reboot â†’ the trail
    # measures drawdown only from boot and the pre-loop position_stop path can't fire a tier-trail
    # (a missing HWM defaults to max(entry,current) â†’ drawdownâ‰ˆ0). Guarded ALSO by
    # POSITION_EXIT_HWM_TRAILING_ENABLED. Default OFF (opt-in; a prove-it/OOS gate re-enables it).
    # Fail-safe: OFF or a store miss/error â†’ prev={} â†’ byte-identical (a missing peak only makes the
    # trail STRICTER, never a false exit). Mirrored in config.oss.py (BORA parity).
    POSITION_EXIT_HWM_PERSIST_ENABLED: bool = (
        os.getenv("POSITION_EXIT_HWM_PERSIST_ENABLED", "False").lower() == "true"
    )
    # #3604 (replaces #2554's STOPOUT_REENTRY_COOLDOWN_MIN): TRADING DAYS a FULLY exited
    #   name is locked out of BUY re-entry - any exit reason (stop, trailing, rotation,
    #   SELL vote, manual). Measured 2026-09-23 on the installed app: re-buys 4-24 h after
    #   the exit lost -1,769 $ (n=14); the 4-hour minute brake no longer covered them.
    #   1 = not before the next trading day. 0 = off. Persisted across restarts. Shipped
    #   at 1 (owner decision 2026-09-23, plan #3605/#3611). Operator-tunable in the
    #   registry (0-10), WORM-audited.
    REENTRY_LOCKOUT_DAYS: int = int(os.getenv("REENTRY_LOCKOUT_DAYS", "1") or "1")
    # #3655: Handelstage, die der von einem Stop-LOSS-Verkauf befreite Platz fuer NEUE
    # Titel gesperrt bleibt (der verkaufte Titel darf nach seiner Sperre zurueck).
    # Gemessen: Sofort-Ersatz -1.890 $ (74 Lose), Rueckkehrer +1.446 $ (9 Lose).
    # 0 = aus (byte-identisch). Auslieferung 1. Spiegelbild von REENTRY_LOCKOUT_DAYS.
    STOP_EXIT_SLOT_HOLD_DAYS: int = int(
        os.getenv("STOP_EXIT_SLOT_HOLD_DAYS", "1") or "1"
    )
    # #2555: feed the per-cycle risk-exit the CURRENT holding's entry-time (locked at first
    # observation, reset when the position goes flat, seeded from the #1994 fill-reconcile)
    # instead of min(_trade_history) â€” which returns the oldest trade in the 30-day window
    # and goes stale across a sold-then-rebought symbol (inflated hours_held â†’ panic-bypass
    # + tightened loss-cut on a fresh position). OFF â†’ min() fallback (byte-identical).
    # Default ON (correctness; fail-safe fallback). Mirrored in config.oss.py (BORA parity).
    POSITION_EXIT_ENTRY_TIME_RECONCILE_ENABLED: bool = (
        os.getenv("POSITION_EXIT_ENTRY_TIME_RECONCILE_ENABLED", "True").lower()
        == "true"
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
    CONVICTION_SIGNED_ENABLED: bool = (
        os.getenv("CONVICTION_SIGNED_ENABLED", "False").lower() == "true"
    )

    # ADR-COST-01: simulation execution costs. Read ONLY by core/simulation.py â€” the
    # backtest. No live order path reads these, so a change here alters no real trade. It
    # alters which WORLD we measure, and every Sharpe we have ever quoted comes from it.
    #
    # COMMISSION: $0 â€” Alpaca charges no commission on US equities. The previous $0.50/side
    # was not a conservative assumption but a factually wrong one, on every simulated fill.
    # (Sell-side regulatory fees â€” SEC ~$27.80/$1M sold, TAF ~$0.000166/share â€” are real
    # but ~0.3bps at our size; folded into slippage rather than modelled separately.)
    #
    # SLIPPAGE: 2bps/side. A JUDGEMENT, so here is its basis: we trade S&P 500 large caps
    # in $1-10k clips, where the quoted spread is ~1-2bps and our size sits far below
    # top-of-book. 2bps/side is the half-spread plus the regulatory dust above. The previous
    # 10bps/side models an illiquid name or institutional size â€” ~5x too expensive for the
    # market we actually trade.
    #
    # âš  THE ERROR HAD A DIRECTION. Overstated costs FLATTER every argument for reducing
    # turnover, because they inflate the savings. Correcting them reportedly lifts
    # top-quintile net Sharpe 0.758 -> ~1.35 with ZERO strategy change. That is ACCOUNTING,
    # NOT IMPROVEMENT, and must never be reported as a win â€” nor is the corrected figure a
    # measurement: model_card.json cites corrected_benchmark_results.json, which exists in
    # no ref. The honest statement is "not currently decidable".
    SLIPPAGE_PERCENT: float = (
        0.0002  # 2bps/side â€” half-spread, large caps, retail clips
    )
    COMMISSION_PER_TRADE: float = 0.0  # Alpaca charges no commission on US equities

    MAX_TOTAL_EXPOSURE_PCT: float = float(
        os.getenv("MAX_TOTAL_EXPOSURE_PCT", "0.95") or "0.95"
    )  # #3154 Env-Naht
    KELLY_FRACTION_CAP: Optional[float] = None

    # BUG-AI-S01 (#1232): configurable broker-equity fallback. When the live
    # equity fetch fails / the broker is unavailable, sizing uses this value
    # (with a WARNING) instead of a hardcoded fictional number. Default = the
    # Alpaca paper default; set it to your real account size for live trading.
    DEFAULT_EQUITY: float = float(os.getenv("DEFAULT_EQUITY", "100000.0"))

    ENABLE_COMPLIANCE_GUARDIAN: bool = True
    # ADR-C01: Max Order Value = 10,000 USD/EUR â€” internal risk policy v1.0, NOT a
    # regulatory duty (the former regulatory anchor was withdrawn, #3219). Read by
    # ComplianceGuardian.__init__ (core/compliance.py); the ratified ceiling is
    # MAX_ORDER_VALUE_CEILING (core/governance/iron_dome_policy.py).
    # GAP5 fix: was "50000.0", silently overriding the documented ADR cap.
    COMPLIANCE_MAX_ORDER_VALUE: float = float(
        os.getenv("COMPLIANCE_MAX_ORDER_VALUE", "10000.0")
    )
    COMPLIANCE_MAX_DAILY_TRADES: int = int(
        os.getenv("COMPLIANCE_MAX_DAILY_TRADES", "10")
    )

    # --- Intraday Execution Timing & Buy Pacing (mirrors config.oss.py) ---
    # ADR-C05: Intraday Timing Guard â€” no BUY in the opening-noise window (wide spreads /
    #   thin book right after the open). Basis: internal risk policy v1.0; 30 m covers the
    #   post-open volatility spike. INTRADAY_TIMING_ENABLED=False = today's behaviour.
    INTRADAY_TIMING_ENABLED: bool = (
        os.getenv("INTRADAY_TIMING_ENABLED", "True").lower() == "true"
    )
    NO_BUY_OPENING_MINUTES: int = int(os.getenv("NO_BUY_OPENING_MINUTES", "30"))
    # ADR-C06: Buy Pacing Guard â€” spread BUYs (cooldown + â‰¤N/hour) so a burst can't exhaust
    #   the 10-trade daily cap in one cycle (the 2026-07-28 whole-book-flush class of event).
    GLOBAL_BUY_COOLDOWN_MINUTES: int = int(
        os.getenv("GLOBAL_BUY_COOLDOWN_MINUTES", "30")
    )
    MAX_BUYS_PER_HOUR: int = int(os.getenv("MAX_BUYS_PER_HOUR", "2"))
    # #3349 (Epic #2963): Earnings-Proximity-Guard â€” BUY-only entry gate around a
    #   symbol's earnings report (EDGAR 8-K Item 2.02). Blocks NEW buys POST_DAYS
    #   after (exact) / PRE_DAYS before (cadence estimate) a report. Default OFF/dark
    #   â‡’ byte-identical. Fail-open on cold cache; fail-closed on a fetch error.
    EARNINGS_GUARD_ENABLED: bool = (
        os.getenv("EARNINGS_GUARD_ENABLED", "False").lower() == "true"
    )
    EARNINGS_GUARD_POST_DAYS: int = int(os.getenv("EARNINGS_GUARD_POST_DAYS", "2"))
    EARNINGS_GUARD_PRE_DAYS: int = int(os.getenv("EARNINGS_GUARD_PRE_DAYS", "0"))
    # #3361 (Epic #2963): regime-beyond-VIX sizing throttle. A credit-led risk-off
    #   composite (HYG/TLT/USO + sector correlation + SPY drawdown, Alpaca daily bars)
    #   scales NEW buys by REGIME_THROTTLE_SIZE_FACTOR while today's reading sits at/above
    #   the REGIME_RISKOFF_PERCENTILE of its own history. Never sells, never blocks.
    #   Default OFF/dark => byte-identical; every data gap fails open (factor 1.0).
    # ADR-REG-04: 75th percentile / factor 0.5 = the marking-test threshold (09/2026:
    #   macro cut-days mean 69 vs threshold 66) and a half-size entry; internal risk
    #   policy, operator-tunable within the registry bounds, WORM-audited. Annual review.
    REGIME_THROTTLE_ENABLED: bool = (
        os.getenv("REGIME_THROTTLE_ENABLED", "False").lower() == "true"
    )
    REGIME_RISKOFF_PERCENTILE: int = int(os.getenv("REGIME_RISKOFF_PERCENTILE", "75"))
    REGIME_THROTTLE_SIZE_FACTOR: float = float(
        os.getenv("REGIME_THROTTLE_SIZE_FACTOR", "0.5")
    )
    COMPLIANCE_HFT_MAX_ORDERS_PER_SEC_SYMBOL: int = int(
        os.getenv("COMPLIANCE_HFT_MAX_ORDERS_PER_SEC_SYMBOL", "2")
    )
    COMPLIANCE_HFT_MAX_ORDERS_PER_SEC_AGGREGATE: int = int(
        os.getenv("COMPLIANCE_HFT_MAX_ORDERS_PER_SEC_AGGREGATE", "10")
    )

    # --- HITL Autonomy Policy (PR-0a, GAP2) ---
    # ADR-C14: EU AI Act Art. 14 â€” a real-money run must have a configured human-oversight
    # policy. These six values define it; all default to the safe all-manual / dormant
    # state (HITL off, both limits 0). Enforced at boot by _enforce_hitl_boot_gate() below.
    HITL_ENABLED: bool = os.getenv("HITL_ENABLED", "False").lower() == "true"
    HITL_MAX_VALUE_PER_TRADE: float = float(
        os.getenv("HITL_MAX_VALUE_PER_TRADE", "0.0")
    )
    HITL_MAX_VALUE_PER_DAY: float = float(os.getenv("HITL_MAX_VALUE_PER_DAY", "0.0"))
    HITL_AUTONOMOUS_UNLIMITED: bool = (
        os.getenv("HITL_AUTONOMOUS_UNLIMITED", "False").lower() == "true"
    )
    HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: bool = (
        os.getenv("HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS", "False").lower() == "true"
    )
    HITL_EXPIRY_SECONDS: int = int(os.getenv("HITL_EXPIRY_SECONDS", "900"))

    # --- EOD sector-constraint assessment (#2652, Epic #2655 Weg 1) â€” dormant by default.
    # When enabled, the market-close transition derives a RELATIVE sector-cap proposal, signs
    # a local Ed25519 approval token (TTL below) and delivers it via the daily_report channels.
    EOD_CONSTRAINT_ASSESSMENT_ENABLED: bool = (
        os.getenv("EOD_CONSTRAINT_ASSESSMENT_ENABLED", "False").lower() == "true"
    )
    EOD_CONSTRAINT_TOKEN_TTL_HOURS: float = float(
        os.getenv("EOD_CONSTRAINT_TOKEN_TTL_HOURS", "12")
    )

    USE_LIMIT_ORDERS: bool = os.getenv("USE_LIMIT_ORDERS", "False").lower() == "true"
    # #2558 (exit-selective, ships dark): give EXITS (SELLs) a marketable-limit path
    # (crosses the spread â†’ less slippage than a plain market order) WITHOUT touching
    # the buy side. Independent of the GLOBAL USE_LIMIT_ORDERS (which also affects BUYs).
    # An exit still always fills: a rejected/erroring limit exit falls back to market.
    USE_LIMIT_EXITS: bool = os.getenv("USE_LIMIT_EXITS", "False").lower() == "true"
    LIMIT_ORDER_SPREAD_BUFFER_PCT: float = float(
        os.getenv("LIMIT_ORDER_SPREAD_BUFFER_PCT", "0.001")
    )

    SLACK_WEBHOOK_URL: Optional[str] = os.getenv("SLACK_WEBHOOK_URL")
    ENABLE_SLACK_ALERTS: bool = (
        os.getenv("ENABLE_SLACK_ALERTS", "False").lower() == "true"
    )
    ENABLE_HEARTBEAT: bool = os.getenv("ENABLE_HEARTBEAT", "False").lower() == "true"
    HEARTBEAT_INTERVAL_HOURS: int = int(os.getenv("HEARTBEAT_INTERVAL_HOURS", "6"))
    # #1806 (GTM-1): periodic heartbeat is machine-only by default (no equity/PnL
    # to Slack under GTM / multi-instance operation). Set True to opt back in.
    HEARTBEAT_INCLUDE_EQUITY: bool = (
        os.getenv("HEARTBEAT_INCLUDE_EQUITY", "False").lower() == "true"
    )

    BYPASS_MARKET_HOURS: bool = (
        os.getenv("BYPASS_MARKET_HOURS", "False").lower() == "true"
    )
    # Market-closed report-only mode (owner 2026-07-26): when the market is closed, run exactly
    # ONE full-universe LSTM rank/panel refresh (reports stay complete 24/7, rendered on-demand)
    # then idle until near open â€” the continuous round-table deep-eval + order machinery is
    # skipped, so the GPU rests. Reverts INC-6's continuous market-closed analysis. Default ON;
    # env `MARKET_CLOSED_REPORT_ONLY=False` restores the legacy INC-6 behaviour byte-identically.
    MARKET_CLOSED_REPORT_ONLY: bool = (
        os.getenv("MARKET_CLOSED_REPORT_ONLY", "True").lower() == "true"
    )

    # --- #3382: Broker-seitige Stops (Epic #3366, ARC-E1) ---
    # ADR-S01: Vorbedingung war der laufende Abgleich (#3389) — sonst droht der
    #          Doppelverkauf: der Broker fuellt den Stop, die Engine erfaehrt es nicht und
    #          verkauft noch einmal. Der Idempotenz-Schluessel (#3387) faengt die identische
    #          Wiederholung, nicht den zweiten Verkauf aus anderer Quelle.
    # #3632:   Vorbedingung erfuellt (RECONCILIATION_ENABLED / _STREAM_ENABLED default True,
    #          #3389 geschlossen). Auslieferung AN (Owner-Freigabe 24.09.2026): der
    #          Broker-Stop ist der Deckel fuer den Fehlerfall des Intelligent Exit, seit
    #          Smart Exit entfernt ist. Registry-Setting (WORM-Apply, Desktop, Console).
    BROKER_STOPS_ENABLED: bool = (
        os.getenv("BROKER_STOPS_ENABLED", "True").lower() == "true"
    )

    # --- #3381: Zeitbudget der Entscheidung (Epic #3366, ARC-E1) ---
    # ADR-T01: Altersgrenze des Entscheidungspreises = 900 s.
    # Basis: Der offene Zyklus taktet mit 60 s (trading_loop.py `asyncio.sleep(... else 60)`);
    #        ein Live-Trade ist dabei Sekunden alt. 900 s sind das 15-Fache der Taktung â€” die
    #        Grenze kann im Normalbetrieb nicht greifen. Sie greift bei einem illiquiden Symbol,
    #        dessen letzter Trade Stunden zurueckliegt, und beim daily_bar-Fallback (~17 h).
    #        Sie schuetzt vor einem ALTEN, nicht vor einem FALSCHEN Kurs â€” Plausibilitaet ist
    #        nicht Teil von #3381. 0 schaltet die Pruefung ab (Rollback ohne Revert).
    MAX_QUOTE_AGE_SECONDS: float = float(os.getenv("MAX_QUOTE_AGE_SECONDS", "900"))
    # ADR-T02: Staffelung Agent < Symbol < Zyklus (MiFID II Art. 17).
    # Der Agent behaelt seine bisherigen 60,0 s (round_table/runner.py); die Symbol-Grenze
    # steigt von 45,0 s auf 120,0 s, weil sie bisher UNTER der Agenten-Grenze lag â€” das Symbol
    # brach ab, waehrend seine Agenten noch arbeiteten, und hinterliess einen Teilzyklus.
    # Geordnet wird nach aussen, nie nach innen: es faellt keine Arbeit weg, die heute laeuft.
    # Die Zyklusgrenze ist neu und bewusst eine Notgrenze (30 min), keine Betriebsgroesse.
    AGENT_VOTE_TIMEOUT_SECONDS: float = float(
        os.getenv("AGENT_VOTE_TIMEOUT_SECONDS", "60")
    )
    SYMBOL_EVAL_TIMEOUT_SECONDS: float = float(
        os.getenv("SYMBOL_EVAL_TIMEOUT_SECONDS", "120")
    )
    CYCLE_TIMEOUT_SECONDS: float = float(os.getenv("CYCLE_TIMEOUT_SECONDS", "1800"))

    # --- #3389: Abgleich mit der Broker-Wahrheit (Epic #3367, ARC-E2) ---
    # ADR-R01: Der Abgleich laeuft mit; er korrigiert NIE von selbst (EU AI Act Art. 14 â€”
    #          eine Bestandsaenderung ist eine Kapitalentscheidung).
    RECONCILIATION_ENABLED: bool = (
        os.getenv("RECONCILIATION_ENABLED", "True").lower() == "true"
    )
    # ADR-R02: Sperrwirkung GETRENNT schaltbar und standardmaessig AUS. Wie oft eine
    #          gemeldete Abweichung gutartig ist, ist nicht gemessen â€” der Dienst hatte
    #          bis #3389 keinen Produktionsaufrufer. Eine Sperre mit unbekannter
    #          Fehlalarmquote legt den Handel still. Erst beobachten (mindestens eine
    #          volle Handelswoche gegen Paper), dann scharfschalten (Plan #3389 Â§8).
    # #3588: Ereignisstrom des Abgleichs (TradingStream). DEFAULT AN seit der Messung am
    #        Paper-Konto vom 23.09.2026 (Owner-Entscheid; Bericht:
    #        docs/3588-ereignisstrom/results/MESSUNG_2026-09-23.md). Gemessen: ein Fill ist
    #        nach 0,7-2,5 s bekannt statt erst mit dem naechsten periodischen Lauf (bis
    #        30 s). Grenzen, ebenfalls gemessen: Was waehrend einer Trennung gefuellt wird,
    #        liefert der Strom NICHT nach — das findet der periodische Lauf und meldet es
    #        als missing_fill. Er bleibt deshalb die tragende Stufe. Ohne Zugangsdaten wird
    #        kein Strom abonniert (sichtbar protokolliert).
    RECONCILIATION_STREAM_ENABLED: bool = (
        os.getenv("RECONCILIATION_STREAM_ENABLED", "True").lower() == "true"
    )
    # #3589 (Owner-Entscheid 25.09.2026): Die Sperre unterscheidet jetzt GEHEILTE von
    #        OFFENEN Befunden. Ein nachgetragener Fill ist eine Abweichung, die derselbe
    #        Lauf schon beseitigt hat — er wird gemeldet und sperrt NICHT. Sonst haette
    #        ein einziger Verbindungsabriss den Handel angehalten, bis ein Mensch eine
    #        Sperre aufhebt, deren Ursache nicht mehr besteht (gemessen am 23.09.2026 am
    #        Paper-Konto: zwei solche Befunde aus einem Abriss).
    #        Der Default bleibt vorerst False: Die Fehlalarmquote im NORMALBETRIEB ist
    #        noch nicht gemessen. Naechster Schritt ist eine volle Handelswoche gegen
    #        Paper mit gezaehlten offenen Befunden, dann die Scharfschaltung.
    RECONCILIATION_BLOCK_ON_BREAK: bool = (
        os.getenv("RECONCILIATION_BLOCK_ON_BREAK", "False").lower() == "true"
    )
    RECONCILIATION_INTERVAL_SECONDS: float = float(
        os.getenv("RECONCILIATION_INTERVAL_SECONDS", "30")
    )
    # --- #3449: Outbox â€” ein Order-Intent wird festgeschrieben, bevor gesendet wird ---
    # ADR-O01: Default AN, in beiden Editionen gleich (Owner-Entscheid 18.09.2026). Ein
    #          Default AUS waere ein weiteres Modul ohne Wirkung. Das Flag ist die Notbremse:
    #          abschalten ohne Release. Nach einem Neustart wird beim Broker abgeglichen,
    #          nie blind nachgesendet (ADR-O02, Plan docs/3449-outbox-verdrahtung).
    ORDER_OUTBOX_ENABLED: bool = (
        os.getenv("ORDER_OUTBOX_ENABLED", "True").lower() == "true"
    )
    # --- #3453: Engine-Sperre â€” genau ein Schreiber je Konto und Modus ---
    # ADR-L01: Default AN, in beiden Editionen gleich (Plan docs/3453-sperre-verdrahtung,
    #          Â§7.3; keine editionsabhaengige Voreinstellung). Vor jeder Order prueft die
    #          Absendestelle die Berechtigung; ohne sie wird zurueckgehalten, auch ein
    #          Schutz-Exit. Das Flag ist die Notbremse: abschalten ohne Release.
    ENGINE_LEASE_ENABLED: bool = (
        os.getenv("ENGINE_LEASE_ENABLED", "True").lower() == "true"
    )

    # --- #1016: Frist fuer eine haengende Order (Epic #3367, ARC-E2) ---
    # ADR-H01: Nach dieser Frist wird eine nicht gefuellte Order beim Broker storniert
    #          (fail-soft: ein fehlgeschlagenes Storno bricht den Zyklus nicht). Bis #1016
    #          stand die Zahl als nackte `120` im Order-Pfad — nicht abschaltbar, nicht
    #          verkuerzbar, ohne Begruendung. Sie entscheidet, wie lange Kapital in einer
    #          nicht gefuellten Order gebunden bleibt und wie lange ein Schutz-Exit auf
    #          seinen Fill wartet, bevor der Markt-Nachschuss aus #2558 greift.
    #          Auslieferungswert 120 s = der bisherige Wirkwert; 0 schaltet das Warten ab
    #          (die Order bleibt dann bis zum Tagesende liegen, TIF ist DAY).
    #          Jahresreview zusammen mit den uebrigen Fristen.
    ORDER_FILL_TIMEOUT_SECONDS: float = float(
        os.getenv("ORDER_FILL_TIMEOUT_SECONDS", "120")
    )
    # Takt der Statusabfrage. Kuerzer heisst schneller erkannt und mehr Broker-Abfragen;
    # Alpaca drosselt ab 200 Anfragen je Minute und Konto (HTTP 429).
    ORDER_FILL_POLL_SECONDS: float = float(os.getenv("ORDER_FILL_POLL_SECONDS", "2"))

    # --- #3627: Namen, die es nur als getattr-Rueckfallwert gab (Epic #3369, ARC-E4) ---
    # Jeder Name hier wurde im Code als ``getattr(config, "NAME", Default)`` gelesen, war
    # aber nirgends deklariert. Damit war der Rueckfallwert die EINZIGE Quelle: nicht
    # ueber die Umgebung setzbar, in keinem erzeugten Register, und ein Tippfehler im
    # Namen faellt nie auf. Die Defaults sind exakt die bisherigen Rueckfallwerte — der
    # Wirkwert aendert sich durch die Deklaration nicht, er wird nur sichtbar und setzbar.
    # ADR-K01: ``USE_CASH_ONLY`` steht auf dem Kapitalpfad: True bemisst gegen das
    #          abgerechnete Guthaben (``cash``), False gegen die Kaufkraft
    #          (``buying_power``) und damit gegen Kredit. Default True = heutiger
    #          Wirkwert an allen drei Lesestellen (base.py, lstm_strategy.py,
    #          rl_execution.py). Jahresreview zusammen mit den uebrigen Deckeln.
    USE_CASH_ONLY: bool = os.getenv("USE_CASH_ONLY", "True").lower() == "true"
    THOUGHT_RATE_LIMIT_SECONDS: float = float(
        os.getenv("THOUGHT_RATE_LIMIT_SECONDS", "120")
    )
    # Die drei Trainingsschalter sind in scripts/README_TRAINING.md als setzbar
    # beschrieben — sie waren es nie. Die Defaults folgen dem Code, nicht dem Text.
    TRAINING_YEARS: int = int(os.getenv("TRAINING_YEARS", "8"))
    TRAINING_TARGET_ACCURACY_LSTM: float = float(
        os.getenv("TRAINING_TARGET_ACCURACY_LSTM", "0.85")
    )
    TRAINING_RL_TIMESTEPS: int = int(os.getenv("TRAINING_RL_TIMESTEPS", "500000"))

    SHADOW_MODE_HOURS: float = float(os.getenv("SHADOW_MODE_HOURS", "24"))
    INTELLIGENT_EXIT_ENABLED: bool = (
        os.getenv("INTELLIGENT_EXIT_ENABLED", "True").lower() == "true"
    )
    ROUND_TABLE_USE_ML_MODELS: bool = (
        os.getenv("ROUND_TABLE_USE_ML_MODELS", "False").lower() == "true"
    )

    # INF-8: Environment and Safety Gates
    STAGING_ENV: bool = os.getenv("STAGING_ENV", "False").lower() == "true"
    SHADOW_MODE: bool = os.getenv("SHADOW_MODE", "False").lower() == "true"

    # Fusion (dormant, default OFF): Shadow-TFT-Vote. When ON, the LangGraph state
    # carries the per-symbol TFT scalars and the Round Table records what a TFT-only
    # vote WOULD say vs the real consensus (recorded, NOT counted â€” no order impact,
    # no LLM cost). Measure-before-activate. See implementation_plan
    # 2026-06-09-tft-state-shadow-vote.
    SHADOW_TFT_VOTE_ENABLED: bool = (
        os.getenv("SHADOW_TFT_VOTE_ENABLED", "False").lower() == "true"
    )
    SHADOW_TFT_VOTE_CHAIN_PATH: str = os.getenv(
        "SHADOW_TFT_VOTE_CHAIN_PATH",
        os.path.join(USER_DATA_DIR, "shadow_tft_votes.jsonl"),
    )
    # SpecialistAlpha shadow vote (#76 measurement, activation runbook addition 3): records what
    # the specialist WOULD vote vs the real consensus â€” recorded, NOT counted; default OFF (zero
    # order impact). Usable in Sim-Day (offline, as-of) + live paper (forward shadow). Mirrored in
    # config.oss.py (BORA).
    SHADOW_SPECIALIST_VOTE_ENABLED: bool = (
        os.getenv("SHADOW_SPECIALIST_VOTE_ENABLED", "False").lower() == "true"
    )
    SHADOW_SPECIALIST_VOTE_CHAIN_PATH: str = os.getenv(
        "SHADOW_SPECIALIST_VOTE_CHAIN_PATH",
        os.path.join(USER_DATA_DIR, "shadow_specialist_votes.jsonl"),
    )

    # Fusion (dormant, default OFF): two-flag TFT specialist wiring.
    # ML_PREDICTION_ENABLED runs _fetch_ml_prediction â†’ populates SpecialistReport.ml_*
    # (what the dormant Shadow-TFT-Vote reads). ML_SENTIMENT_BLEND_ENABLED separately gates
    # the convergence blend that changes sentiment_score â€” so the TFT signal is MEASURED
    # before it changes any decision (validate-before-activate). See implementation_plan
    # 2026-06-09-model-registry.
    ML_PREDICTION_ENABLED: bool = (
        os.getenv("ML_PREDICTION_ENABLED", "False").lower() == "true"
    )
    ML_SENTIMENT_BLEND_ENABLED: bool = (
        os.getenv("ML_SENTIMENT_BLEND_ENABLED", "False").lower() == "true"
    )
    # Specialist report parity (RPAR Epic #1262, Task T1 #1265 - dormant, default OFF).
    # When ON, the per-symbol deep-research synthesis uses the V2 prompt+parser
    # (core/specialist/{prompt,parser}.py): the prompt additionally asks for
    # COMPANY/BULL/BEAR/THESIS prose and the parser fills company_summary/bull_case/
    # bear_case/investment_thesis. The flag switches BOTH prompt-build sites AND the
    # parser atomically - a V2 prompt is never run through the V1 parser. OFF reproduces
    # today's 6-tuple path byte-for-byte (prompt string, parsed tuple, score/recommendation/
    # reasons unchanged - NEWS-8). This is display-only prose; it changes no decision.
    # Mirrored in config.oss.py (dual-edition parity).
    SPECIALIST_PROMPT_V2: bool = (
        os.getenv("SPECIALIST_PROMPT_V2", "True").lower() == "true"
    )
    # RPAR T2 (#1264, dormant, default OFF): gates the deterministic, LLM-free
    # specialist card fields (pros/cons/summary/headlines) in _build_report. OFF ->
    # the fields keep their V0 defaults -> the serialized DTO is byte-identical (the
    # Epic-#1262 "all RPAR flags OFF -> byte-identical" guarantee, decoupled from the
    # registry-activation gate #1284). Display-only; never changes the score. Mirrored
    # in config.oss.py (dual-edition parity).
    SPECIALIST_CARDS_ENABLED: bool = (
        os.getenv("SPECIALIST_CARDS_ENABLED", "True").lower() == "true"
    )

    # Rich-synthesis card (Direction A, dormant, default OFF). Two coupled flags:
    #   SPECIALIST_GROUNDING_ENABLED â€” runs core.specialist.grounding on the V2
    #     bull/bear/thesis prose: every displayed sentence is mechanically bound to
    #     a real headline of THIS symbol; ungrounded/fabricated sentences are
    #     dropped (the #2202 "Cerebras" catcher). Also switches the V2 prompt into
    #     [H#]-citation mode. Requires SPECIALIST_PROMPT_V2 to have any prose to act
    #     on. Display-only; changes no decision.
    #   SPECIALIST_SYNTHESIS_CARD_ENABLED â€” emits the card's grounding citations,
    #     dropped-claim ledger and the non-directional HAR-RV volatility band in the
    #     specialist DTO. HARD-COUPLED to GROUNDING (see _enforce_card_grounding):
    #     the card can NEVER surface ungrounded prose. OFF -> the DTO key-set is
    #     byte-identical to today. Mirrored in config.oss.py (dual-edition parity).
    SPECIALIST_GROUNDING_ENABLED: bool = (
        os.getenv("SPECIALIST_GROUNDING_ENABLED", "True").lower() == "true"
    )
    SPECIALIST_SYNTHESIS_CARD_ENABLED: bool = (
        os.getenv("SPECIALIST_SYNTHESIS_CARD_ENABLED", "True").lower() == "true"
    )

    @field_validator("COMPLIANCE_MAX_ORDER_VALUE")
    @classmethod
    def _clamp_max_order_value(cls, v: float) -> float:
        if v > MAX_ORDER_VALUE_CEILING:
            import logging

            logging.getLogger("TradingConfig").warning(
                f"COMPLIANCE_MAX_ORDER_VALUE ({v}) exceeds ratified ceiling ({MAX_ORDER_VALUE_CEILING}). Clamping."
            )
            return MAX_ORDER_VALUE_CEILING
        return v

    @model_validator(mode="after")
    def _enforce_card_grounding(self):
        # HARD COUPLING (Direction A): the synthesis card must never render
        # ungrounded LLM prose. If the card is enabled without grounding, force it
        # OFF (fail-safe) and log at WARNING â€” the card is display-only, so
        # disabling it can never affect a decision, whereas showing ungrounded
        # prose is exactly the #2202 fabrication risk this design exists to block.
        if (
            self.SPECIALIST_SYNTHESIS_CARD_ENABLED
            and not self.SPECIALIST_GROUNDING_ENABLED
        ):
            import logging

            logging.getLogger(__name__).warning(
                "SPECIALIST_SYNTHESIS_CARD_ENABLED requires "
                "SPECIALIST_GROUNDING_ENABLED - forcing the card OFF (fail-safe)."
            )
            self.SPECIALIST_SYNTHESIS_CARD_ENABLED = False
        return self

    # --- GTM-1 (#1840) Stripe tier-upgrade checkout (Brick 1, cloud-only) -----------
    # One integration, two products: PRO (GTM-2 Private) and PROFESSIONAL (GTM-3) are the
    # only Stripe-purchasable tiers. BASIC is free and INSTITUTIONAL is B2B invoicing, so
    # neither has a price id. The Stripe SECRET KEY is loaded from Secret Manager at
    # request time (never from config). Mirrored in config.oss.py (dual-edition parity).
    # VORSCHLAG â€” the real price ids arrive with #1805; these default empty placeholders.
    STRIPE_PRICE_ID_PRO: str = os.getenv("STRIPE_PRICE_ID_PRO", "")  # VORSCHLAG (#1805)
    STRIPE_PRICE_ID_PROFESSIONAL: str = os.getenv(
        "STRIPE_PRICE_ID_PROFESSIONAL", ""
    )  # VORSCHLAG (#1805)
    # Where Stripe redirects after the hosted checkout. Placeholders for Brick 1; real
    # token-delivery URLs are Brick 3 (#1840 delivery). Env-overridable.
    ENTITLEMENT_CHECKOUT_SUCCESS_URL: str = os.getenv(
        "ENTITLEMENT_CHECKOUT_SUCCESS_URL", "https://aaagents.de/checkout/success"
    )
    ENTITLEMENT_CHECKOUT_CANCEL_URL: str = os.getenv(
        "ENTITLEMENT_CHECKOUT_CANCEL_URL", "https://aaagents.de/checkout/cancel"
    )

    # --- GTM-2 (#1809) paywall master switch + Lemon Squeezy (Merchant-of-Record) ----
    # PAYWALL_ENABLED gates the PAID Private/Senior path. Default OFF = the free offline
    # beta claim (ADR-GTM-1b) stays in effect â€” current shipping behaviour is unchanged.
    # When True: beta:claim is refused, the console shows the paid checkout, and activation
    # is by pasting a signed offline Ed25519 annual token. The offline verification and the
    # four runtime gates are always active regardless of this flag; it only chooses HOW a
    # Junior desktop is offered the upgrade (free claim vs. paid checkout). Single toggle by
    # design (flag-minimalism). Mirrored in config.oss.py (dual-edition parity).
    PAYWALL_ENABLED: bool = os.getenv("PAYWALL_ENABLED", "False").lower() == "true"
    # Lemon Squeezy (MoR) â€” the non-secret checkout identifiers. The API key and webhook
    # secret are NOT here: like the Stripe secret key they load from Secret Manager at
    # request time. Empty placeholders until Ops provisions the store before the flag flips.
    LEMONSQUEEZY_STORE_ID: str = os.getenv("LEMONSQUEEZY_STORE_ID", "")
    LEMONSQUEEZY_VARIANT_ID_PRO: str = os.getenv("LEMONSQUEEZY_VARIANT_ID_PRO", "")
    # The full Lemon Squeezy HOSTED checkout ("buy link") the desktop opens for "Get Senior".
    # A plain public URL (no secret) that ops pastes from the LS dashboard at provisioning;
    # empty until then â†’ the console falls back to the key-activation panel. Kept as the exact
    # URL (not assembled) to avoid guessing LS's store-slug/variant URL format (zero-guessing).
    LEMONSQUEEZY_CHECKOUT_URL: str = os.getenv("LEMONSQUEEZY_CHECKOUT_URL", "")
    # Minted license lifetime in days. Annual (owner decision 2026: "erstmal einmal pro Jahr").
    # The signed token carries the authoritative expiry; the cloud issuer mints for this long.
    LEMONSQUEEZY_LICENSE_VALID_DAYS: int = int(
        os.getenv("LEMONSQUEEZY_LICENSE_VALID_DAYS", "365")
    )
    # The price string shown on the paid Senior card. Owner-confirmed introductory price
    # 9,99 €/year (2026). Kept as ONE config source (not hardcoded in the UI) so it can be
    # changed without a code edit and MUST be kept in sync with the actual Lemon Squeezy
    # variant price â€” the LS checkout remains the authoritative amount charged.
    LEMONSQUEEZY_PRICE_DISPLAY: str = os.getenv("LEMONSQUEEZY_PRICE_DISPLAY", "9,99 €")

    # ===================================================================
    # RPAR Epic #1262 / Task T5 (#1269) - ML<->LLM convergence-blend weights.
    # DECISION-MATH (P1): these eight constants drive _blend_ml_sentiment
    # (core/stock_specialist.py), which overrides sentiment_score ->
    # recommendation/escalate on the autonomous order path. Surfaced here (not
    # hidden in getattr defaults) so they are auditable + tunable for the P1 24h
    # human review (CODING_POLICY Â§5.5). BASIS (all eight, per ADR below): P3-B
    # convergence math, Bundle #L2555-2618; the values == the historical getattr
    # defaults, so the merge is byte-identical even at flag-ON (math unchanged),
    # and are reconciled against a real Bundle snapshot via the #76 shadow harness.
    # Mirrored in config.oss.py. ACTIVATION: flip ML_SENTIMENT_BLEND_ENABLED ON
    # ONLY after green #76 shadow validation (hard Papa blocker) + walk-forward sign-off.
    # ===================================================================
    # ADR-T5-01: SPECIALIST_ML_SATURATION_PCT = 2.0
    # Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
    # Rationale: clamps base_return_pct to +/-2% before mapping onto [0,100];
    #   wider saturates outliers, narrower over-reacts to small returns. Must be > 0.
    SPECIALIST_ML_SATURATION_PCT: float = float(
        os.getenv("SPECIALIST_ML_SATURATION_PCT", "2.0")
    )
    # ADR-T5-02: SPECIALIST_ML_LLM_AGREEMENT_HIGH = 0.75
    # Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
    # Rationale: agreement = 1 - |ml-llm|/100; >= 0.75 => "converged" branch.
    SPECIALIST_ML_LLM_AGREEMENT_HIGH: float = float(
        os.getenv("SPECIALIST_ML_LLM_AGREEMENT_HIGH", "0.75")
    )
    # ADR-T5-03: SPECIALIST_ML_LLM_AGREEMENT_MID = 0.50
    # Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
    # Rationale: agreement >= 0.50 (but < HIGH) => "partial" branch; below => diverged.
    SPECIALIST_ML_LLM_AGREEMENT_MID: float = float(
        os.getenv("SPECIALIST_ML_LLM_AGREEMENT_MID", "0.50")
    )
    # ADR-T5-04: SPECIALIST_BLEND_CONVERGED_ML_W = 0.55
    # Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
    # Rationale: converged-branch ML weight; pairs with LLM_W (ADR-T5-05) to sum 1.0.
    SPECIALIST_BLEND_CONVERGED_ML_W: float = float(
        os.getenv("SPECIALIST_BLEND_CONVERGED_ML_W", "0.55")
    )
    # ADR-T5-05: SPECIALIST_BLEND_CONVERGED_LLM_W = 0.45
    # Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
    # Rationale: converged-branch LLM weight; pairs with ML_W (ADR-T5-04) to sum 1.0.
    SPECIALIST_BLEND_CONVERGED_LLM_W: float = float(
        os.getenv("SPECIALIST_BLEND_CONVERGED_LLM_W", "0.45")
    )
    # ADR-T5-06: SPECIALIST_BLEND_PARTIAL_ML_W = 0.40
    # Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
    # Rationale: partial-branch ML weight (lower trust than converged); sums 1.0 with LLM_W.
    SPECIALIST_BLEND_PARTIAL_ML_W: float = float(
        os.getenv("SPECIALIST_BLEND_PARTIAL_ML_W", "0.40")
    )
    # ADR-T5-07: SPECIALIST_BLEND_PARTIAL_LLM_W = 0.60
    # Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
    # Rationale: partial-branch LLM weight; pairs with ML_W (ADR-T5-06) to sum 1.0.
    SPECIALIST_BLEND_PARTIAL_LLM_W: float = float(
        os.getenv("SPECIALIST_BLEND_PARTIAL_LLM_W", "0.60")
    )
    # ADR-T5-08: SPECIALIST_BLEND_DIVERGED_SHRINK = 0.30
    # Basis: RPAR T5 / P3-B convergence math (Bundle #L2555-2618).
    # Rationale: diverged branch shrinks LLM toward neutral 50: 50 + (llm-50)*0.30.
    SPECIALIST_BLEND_DIVERGED_SHRINK: float = float(
        os.getenv("SPECIALIST_BLEND_DIVERGED_SHRINK", "0.30")
    )
    # Per-symbol TFT checkpoint root (Section 2.10: core reads this via get_config(),
    # NEVER os.getenv). Empty â†’ model_registry falls back to module-relative
    # core/ml/models/. On Cloud Run the read-only GCS-FUSE mount sets it to
    # /gcs/models/tft so the ~1.3 GB tree is read on demand instead of copied into the
    # 2 Gi in-memory FS. Mirrored in config.oss.py (dual-edition parity, #1159 lesson).
    TFT_MODELS_ROOT: str = os.getenv("TFT_MODELS_ROOT", "")
    # ADR-MLA-11 (#1885): TFT_SERVING_FIX default ON.
    # Basis: ML-Voll-Audit 08.07. (MLA-11), fix validated against the trainer's own
    # scoring (train_tft_per_symbol._score_fold: decoder-step-0 read, Ã—100 to percent).
    # Rationale: when ON, per-symbol TFT post-processing (a) reads decoder-step-0 â€” the
    # step the gate's walkforward_ic actually certified â€” instead of the 5-step horizon
    # mean, and (b) scales the decimal log-return output Ã—100 to percent (the trainer's
    # scoring unit). With the old OFF default, an ACTIVATED TFT served horizon-averaged
    # decimal log-returns that downstream reads as percent â†’ direction almost always
    # "neutral" inside the Â±0.3 dead-band with fake ~0.99 confidence. Serving must match
    # the unit the gate validated, so trainer-scoring parity is the only correct default.
    # Still gated by ML_PREDICTION_ENABLED (default OFF) before anything reaches a
    # decision; OSS ships TFT dormant â†’ no ship-behavior change there.
    # Rollback: env TFT_SERVING_FIX=0 restores the historical mean+no-scale read
    # byte-for-byte. Remove the flag after bake time (dead-flag hygiene, #1885 scope).
    # Mirrored in config.oss.py.
    TFT_SERVING_FIX: bool = os.getenv("TFT_SERVING_FIX", "True").lower() == "true"
    # TFT quality-gate honest metric (M3b, dormant, default OFF â€” validate-before-activate).
    # When ON, the per-symbol gate (core/ml/quality_gate.py) judges a model by the honest
    # ~506-obs decoder-step-0 OOS IC (metadata `walkforward_ic_oos506`) instead of the noisy
    # 250-obs `walkforward_ic`. The 250-obs metric's SE (~0.063) sits inside the 0.0/0.05
    # floors â†’ best-of-3-seed selection leaks ~21% winner's-curse models with NEGATIVE honest
    # OOS IC into the served set. OFF = byte-identical (reads walkforward_ic). Flip ON at
    # activation (TFT_SERVING_FIX is already default-ON since #1885). Mirrored in config.oss.py.
    TFT_QUALITY_GATE_HONEST_IC: bool = (
        os.getenv("TFT_QUALITY_GATE_HONEST_IC", "False").lower() == "true"
    )
    # ADR-ML-GATE-02 (MLR-3, #1903): TFT_NET_SHARPE_FLOOR = 0.0
    # Basis: feedback_sharpe_over_ic â€” net-of-cost Sharpe is the price of a model,
    # IC is not. The offline Layer-4 cost model (scripts/apply_cost_model.py,
    # Almgren-Chriss spread+commission+impact) stamps `net_sharpe` into the
    # per-symbol gate metadata; Layer 2 (scripts/apply_fdr_layer.py,
    # Benjamini-Hochberg) stamps `fdr_passed`.
    # Rationale: when those fields are PRESENT, the serving gate
    # (core/ml/quality_gate.py) additionally requires fdr_passed == True AND
    # net_sharpe > this floor. Default 0.0 = "must at least not lose money after
    # costs" â€” deliberately below the offline Layer-4 promotion bar (1.0, beat
    # SPY) so the default only removes actively money-losing models and cannot
    # starve the local served set. Cloud raises it via env once the served set
    # under a higher bar is walk-forward validated (validate-before-activate).
    # Fields ABSENT (legacy metadata) â†’ gate behaviour unchanged (fail-safe).
    # Mirrored in config.oss.py. Annual review.
    TFT_NET_SHARPE_FLOOR: float = float(os.getenv("TFT_NET_SHARPE_FLOOR", "0.0"))
    # ADR-ML-GATE-03 (MLR-9, #1909): Deflated Sharpe gate (Bailey/Lopez de Prado,
    # "The Deflated Sharpe Ratio", 2014).
    # Basis: BH-FDR (ADR-ML-GATE-02) corrects multiple testing across the
    # universe; the deflated Sharpe additionally corrects SELECTION of the best
    # of n_trials under non-normality â€” a model can pass FDR and still have a
    # deflated Sharpe <= 0 (backtest overfitting). Orthogonal error sources â†’
    # additive AND-gate (fdr_passed AND net_sharpe > floor AND deflated_sharpe
    # > TFT_DSR_FLOOR).
    # Rationale: when ON, core/ml/quality_gate.py additionally requires the
    # Layer-5 `deflated_sharpe` (annualized SR - E[max SR] margin stamped by
    # scripts/apply_deflated_sharpe_layer.py; n_trials from the walk-forward
    # manifest, never hard-coded) to clear TFT_DSR_FLOOR. Default OFF
    # (validate-before-activate): activate only after the Layer-5 backfill has
    # run and the DSR distribution over the served set is reviewed â€” a strict
    # floor with no stamped fields would only WARN (fail-safe), but a strict
    # floor over a weak universe can empty the served set. Field absent â†’
    # passthrough + WARNING (legacy metadata, mirrors ADR-ML-GATE-02).
    # Rollback: unset env (no redeploy) â†’ byte-identical MLR-3 gate.
    # Mirrored in config.oss.py. Annual review.
    TFT_DEFLATED_SHARPE_GATE_ENABLED: bool = (
        os.getenv("TFT_DEFLATED_SHARPE_GATE_ENABLED", "False").lower() == "true"
    )
    # ADR-ML-GATE-03: TFT_DSR_FLOOR = 0.0 â€” the annualized deflation margin must
    # be POSITIVE (exclusive floor): the net-of-cost Sharpe must at least beat
    # the expected maximum Sharpe of n_trials zero-skill trials. Tune per the
    # Inc-2 universe distribution before activation. Mirrored in config.oss.py.
    TFT_DSR_FLOOR: float = float(os.getenv("TFT_DSR_FLOOR", "0.0"))
    # ADR-ML-GATE-03: IC demotion (feedback_sharpe_over_ic â€” net-of-cost
    # deflated Sharpe is the price of a model, IC is diagnosis). When ON the
    # quality gate's `walkforward_ic <= IC_MIN` reject is skipped and only
    # WARNED; the field stays in metadata for report/DecisionContext consumers
    # (api_routes reads it display-only). Default OFF â†’ historical IC reject
    # byte-identical. Separately switchable from the DSR gate. Mirrored in
    # config.oss.py.
    TFT_IC_DIAGNOSTIC_ONLY: bool = (
        os.getenv("TFT_IC_DIAGNOSTIC_ONLY", "False").lower() == "true"
    )

    # Manifest override flags (CWE-502). Default True.
    AAA_REQUIRE_MANIFEST: bool = (
        os.getenv("AAA_REQUIRE_MANIFEST", "True").lower() == "true"
    )
    TFT_REQUIRE_MANIFEST: bool = (
        os.getenv("TFT_REQUIRE_MANIFEST", "True").lower() == "true"
    )

    # RPAR-T4 (#1268): route the Specialist synthesis through the ADR-014 LLM provider
    # seam for Bundle-output-parity (dormant, default OFF - validate-before-activate).
    # OFF reproduces the hand-rolled core/stock_specialist._call_gemini_sync path
    # byte-for-byte (own genai.Client, temperature 0.3, daily-budget gate + increment
    # unchanged). When ON, the Gemini branch of _gemini_synthesize routes via
    # provider.generate_content_async(prompt, max_output_tokens=800) - the same unified
    # seam the bundle uses (and the already-seam-routed Ollama branch). This flag flip
    # is TRADING-RELEVANT: a different LLM path -> potentially different synthesis text ->
    # potentially different parsed sentiment_score in the decision path, so it is a
    # SEPARATE conscious gate (independent of the registry) with a Golden-Fixture/HITL
    # reconcile against a real Bundle snapshot before any flip. Mirrored in config.oss.py.
    LLM_OUTPUT_PARITY: bool = os.getenv("LLM_OUTPUT_PARITY", "False").lower() == "true"

    # RPAR T3 (#1267, dormant, default OFF): Google-News-source parity. When ON, the
    # Stock Specialist also fetches Google-News-RSS headlines (_fetch_google_news) and
    # merges them with the Polygon headlines (core.specialist.news.merge_headlines,
    # Google-first, case-insensitive dedup, cap 10). OFF reproduces today's Polygon-only
    # recent_headlines byte-for-byte (no RSS fetch, no merge reached). Changes only the
    # NEWS *inputs* to the LLM synthesis, not the deterministic _build_report scoring math
    # (NEWS-8 invariant). Mirrored in config.oss.py.
    SPECIALIST_NEWS_V2: bool = False

    # RPAR T6a (#1268, dormant, default OFF): the data-integrity guard. When ON, the
    # stock specialist runs core.data_integrity.assess(gathered) and sets the two
    # DISPLAY-ONLY report fields (data_quality / degraded); a hard data failure skips
    # the (non-decision) LLM synthesis and reuses the existing V0-default synthesis. It
    # NEVER changes sentiment_score / recommendation / reasons. RQ-1 B5 (#1525): default-ON
    # now that B1/B2/B3 made the inputs entity-correct, recent + count-unbiased, so the
    # presence-based data_quality / degraded are trustworthy. Decision-neutral (display +
    # skip_llm only). Both editions flip together -> dual-edition parity holds. Mirrored in
    # config.oss.py.
    DATA_INTEGRITY_GUARD_ENABLED: bool = (
        os.getenv("DATA_INTEGRITY_GUARD_ENABLED", "True").lower() == "true"
    )

    # RQ-1 A3 (#1519): gate the count-driven score behaviours â€” the +4 insider-cluster /
    # +5 activist bonuses AND the >=82 score auto-escalation â€” behind one flag, default OFF,
    # until B3 (#1523) makes sentiment directional. Stops the uniform ~94/100 count-driven
    # BUY. Mirrored in config.oss.py.
    SPECIALIST_COUNT_BONUS_ENABLED: bool = (
        os.getenv("SPECIALIST_COUNT_BONUS_ENABLED", "False").lower() == "true"
    )

    # RQ-1 B3b (#1536): fetch + parse each Form 4 document for the real insider buy/sell
    # DIRECTION (the efts index has no transaction code). Default OFF -> no extra SEC
    # requests + the fetcher row keys stay unchanged; ON -> rows gain a "direction" field the
    # prompt surfaces so sells don't read as bullish. Mirrored in config.oss.py.
    SPECIALIST_FORM4_DIRECTION_ENABLED: bool = (
        os.getenv("SPECIALIST_FORM4_DIRECTION_ENABLED", "False").lower() == "true"
    )

    # RQ-1 B2 (#1522): surface per-source filing freshness in the report DTO. Additive +
    # flag-gated (default OFF -> byte-identical DTO / BORA parity, like the report-quality
    # badge). When ON: per-source "as of" date + data_stale. Mirrored in config.oss.py.
    SPECIALIST_FRESHNESS_ENABLED: bool = (
        os.getenv("SPECIALIST_FRESHNESS_ENABLED", "False").lower() == "true"
    )
    SPECIALIST_FRESHNESS_SLA_DAYS: int = int(
        os.getenv("SPECIALIST_FRESHNESS_SLA_DAYS", "30")
    )

    # Fusion (GAP9): the ComplianceGatekeeper ("Iron Dome") portfolio context. ARMED by
    # default (True, since #1962): the trading loop builds one portfolio snapshot per cycle
    # (core/engine/portfolio_context.build_portfolio_context) so the concentration / PDT /
    # daily-limit checks fire on real data. Set to False to disable â†’ NO snapshot, the runner
    # sees an empty context, and the gatekeeper approves exactly as today (BYTE-IDENTICAL).
    # See implementation_plan 2026-06-11 plan_C_gap9_portfolio_context.
    GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED: bool = (
        os.getenv("GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED", "True").lower() == "true"
    )
    # Strict posture â€” only meaningful once the feature above is ON. When the snapshot
    # could not be built (broker error / equity<=0), fail CLOSED: a BUY score yields NO
    # signal instead of proceeding contextless. SELL/HOLD are never blocked. Default False
    # = fail OPEN (warn + proceed = today's behaviour).
    GATEKEEPER_REQUIRE_CONTEXT: bool = (
        os.getenv("GATEKEEPER_REQUIRE_CONTEXT", "False").lower() == "true"
    )
    # #2037: opt-in gate for the PDT cross-day SELL exemption. Default OFF (paper-observe
    # first). When ON, build_portfolio_context queries the broker for TODAY's fills and the
    # gatekeeper exempts a genuine cross-day SELL (no fill today) from the PDT hard-block.
    # A risk-relaxing FINRA carve-out on the already-armed context path â†’ deliberately opt-in.
    GATEKEEPER_PDT_CROSSDAY_EXEMPTION: bool = (
        os.getenv("GATEKEEPER_PDT_CROSSDAY_EXEMPTION", "False").lower() == "true"
    )

    # #2153 Part A: per-symbol anti-churn entry cap. Default ON (operator D1) â€” the churn
    # (AAPL 7Ã—/day) is the reported production defect, so the guard ships active. When ON,
    # build_portfolio_context counts today's per-symbol fills and the gatekeeper vetoes a
    # BUY at/over MAX_TRADES_PER_SYMBOL_PER_DAY. Fail-safe + SELL-exempt. Byte-identical
    # default to config.oss.py (both True).
    GATEKEEPER_SYMBOL_CHURN_LIMIT: bool = (
        os.getenv("GATEKEEPER_SYMBOL_CHURN_LIMIT", "True").lower() == "true"
    )

    # RTR-6 (#2410): Strict-ML-Gate RL coupling. True: BOTH core ML votes (LSTM AND
    # RL, each weight > 0) must be present or the runner vetoes with "Missing core ML
    # votes". Set to False and a valid LSTM
    # vote alone satisfies the gate: the RL abstain is logged once per session at
    # WARNING (Â§5.6) and the audit reasoning notes "RL not required (flag)". A cycle
    # where BOTH abstain stays approved=False â€” the no-ML protection never lifts.
    # Rationale (R1/RTR-0): RL's root is registry.get_active().evaluate_for_symbol â€”
    # without an active registry strategy RL always abstains, so re-activating the
    # LSTM (#2388) is inert while this is True. DEFAULT FALSE in both editions
    # (ADR-018, owner 2026-07-26): the RL requirement is waived; the LSTM requirement stays
    # (runner.py _strict_ml_gate_blocks vetoes a missing LSTM vote regardless) (#3261).
    GATEKEEPER_STRICT_ML_REQUIRES_RL: bool = (
        os.getenv("GATEKEEPER_STRICT_ML_REQUIRES_RL", "False").lower() == "true"
    )

    # ADR-PDT-01: apply FINRA (compliance.py#L)'s pattern-day-trader rule AS WRITTEN, instead of the
    # deliberately-stricter approximation the gatekeeper enforces today.
    #
    # The rule (FINRA Rule 4210 / Reg T): the PDT designation attaches on MORE THAN 3 day
    # trades in 5 business days, AND ONLY for a margin account whose equity is BELOW
    # $25,000. At or above $25k it does not bind at all.
    #
    # Today's gate blocks at >= 3 (one too strict) and never reads equity â€” so a funded
    # $100k paper account is refused EVERY order, including risk-reducing SELLs, by a rule
    # that does not apply to it. That over-block is the most likely reason a configured
    # bot places zero trades.
    #
    # This is opt-in because it RELAXES a compliance gate on the live decision path. The
    # original fail-safe intent ("never permit a FINRA (compliance.py#L) under-block") is preserved inside
    # the branch: unknown/absent/unparseable equity still BLOCKS. We stop blocking only
    # where the rule provably cannot apply. Default OFF â‡’ today's behaviour is unchanged.
    # ADR-C04-EXIT: exempt risk-reducing exits from the SIDE-AGNOSTIC daily-trades cap.
    #
    # record_trade() takes no side, so once COMPLIANCE_MAX_DAILY_TRADES is spent NO SELL
    # lands either â€” including a stop-loss â€” until reset_daily_limit() at the NY rollover.
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
    # predicate is fail-CLOSED â€” a missing held_qty stays capped.
    # DEFAULT ON (#2178, Regel-Konsistenz-Audit R1): the daily-trades cap was trapping
    # risk-reducing SELL/stop-loss exits once the ~10/day budget was spent â€” a bot that
    # cannot sell cannot cut a loss. Set to False to restore the pre-#2166 behaviour.
    COMPLIANCE_ALLOW_RISK_REDUCING_EXITS: bool = (
        os.getenv("COMPLIANCE_ALLOW_RISK_REDUCING_EXITS", "True").lower() == "true"
    )

    # ACTIVATED (R2, exit-invariant): default ON. The FINRA-accurate (compliance.py#L) PDT gate (>3 day
    # trades AND only below the $25k equity floor) is the rule as written; the old
    # (>=3, equity-blind) gate over-blocked funded accounts â€” including risk-reducing
    # SELLs â€” by a rule that does not apply to them ("the bot places zero trades"). The
    # logic was built + tested in #2166; fail-safe preserved (unknown/absent equity still
    # BLOCKS â€” never a FINRA (compliance.py#L) under-block). Env-overridable (`=false` restores the old gate).
    GATEKEEPER_PDT_FINRA_ACCURATE: bool = (
        os.getenv("GATEKEEPER_PDT_FINRA_ACCURATE", "True").lower() == "true"
    )

    # #1951 / plan #2205 â€” DrawdownGuard: hard-veto â†’ conditioner (+severe backstop).
    # When ON: in the 7â€“25% band the DrawdownGuard no longer hard-vetoes a BUY; instead
    # its severity DAMPENS the BUY conviction/position-size, and the DAMPED conviction
    # gates the action (below SIGNAL_BUY_THRESHOLD â‡’ HOLD, audited â€” #1951). A genuinely
    # severe drawdown (> DRAWDOWN_SEVERE_VETO_THRESHOLD) is still hard-blocked.
    # ADR-DDG-02 (#1951): default OFF â€” the audited 0.07 hard veto is the shipped
    # posture. Live forensics (13.â€“22.07.2026) proved the default-ON conditioner was
    # cosmetic (708 floor-BUYs at ~0.1x damped conviction) and broke the plan's
    # byte-identical-at-off guarantee. Re-activation only after a net-of-cost OOS
    # proof (2024-2026, v1/v3 harness) and staged paper observation (MiFID II Art.17;
    # annual review). Set env DRAWDOWN_GUARD_CONDITIONER_ENABLED=true to opt in.
    DRAWDOWN_GUARD_CONDITIONER_ENABLED: bool = (
        os.getenv("DRAWDOWN_GUARD_CONDITIONER_ENABLED", "False").lower() == "true"
    )
    # ADR-DDG-01 â€” max fraction the drawdown severity may cut a BUY's conviction (0..1).
    # 0.8 â†’ a name at â‰¥20% drawdown (severity 1.0) keeps only ~20% of its conviction/size.
    # MiFID II Art.17 sizing control; annual review. Inert while the conditioner is OFF.
    DRAWDOWN_CONVICTION_DAMP_MAX: float = float(
        os.getenv("DRAWDOWN_CONVICTION_DAMP_MAX", "0.8") or "0.8"
    )
    # ADR-DDG-01 â€” drawdown fraction above which the guard still HARD-blocks a BUY
    # (correction-vs-bear-market line; 0.25 = past a bear-market entry). Annual review.
    DRAWDOWN_SEVERE_VETO_THRESHOLD: float = float(
        os.getenv("DRAWDOWN_SEVERE_VETO_THRESHOLD", "0.25") or "0.25"
    )
    # Master gate for the DrawdownGuard HARD-VETO of new BUYs (both the 30d-window and
    # the 1-bar-fallback paths). Default TRUE â‡’ shipped behaviour, byte-identical.
    # Set FALSE to REMOVE the veto: the guard still votes its soft score, but never
    # hard-blocks a BUY. Measured rationale (16y S&P, correctly-timed, Alpaca cost;
    # docs/rtr/drawdown-guard-removal/implementation_plan.md): the per-name ">7% below
    # 30d high" veto neither reduces portfolio max-drawdown (the always-on VIX scaler
    # already halves it) nor lifts Sharpe, and it *costs* return by vetoing the
    # mean-reverting pullback names that then out-perform (VIX+veto Sharpe 0.97 â†’
    # VIX-only 1.08). Flipping to FALSE is a P1 trading-path change (MiFID II Art. 17)
    # â€” activation only after a net-of-cost OOS proof on the real Round-Table book.
    DRAWDOWN_GUARD_VETO_ENABLED: bool = (
        os.getenv("DRAWDOWN_GUARD_VETO_ENABLED", "True").lower() == "true"
    )

    # TRD-8 T6 (#3004) - MomentumAgent: monotone squashing instead of a hard clamp.
    # `clamp(0.5 + momentum_12_1 / 0.60)` hits the ceiling once 12-1M momentum passes
    # +30 %. Measured over 26,744 logged votes: 13,033 (48.7 %) carry exactly 1.00;
    # reconstructed raw values there run to a median of +51.8 % and a maximum of
    # +2,879 %. A value identical across half the universe is not a vote inside a
    # weighted mean (consensus.py:295-306) - it is a constant +0.1285 lift in front of
    # a fixed 0.65 buy threshold (runner.py:903-915), and it lifts 17.0 % of logged
    # buys across on its own. ON substitutes 0.5 + 0.5*tanh(m / MOMENTUM_SCORE_SCALE):
    # strictly monotone, no ties, same [0,1] range and same 0.5 neutral point.
    # DEFAULT ON since #3035 (owner decision 2026-08-25). The pre-registered criterion of
    # the stage-2 sim run (docs/3004-momentum-normierung/implementation_plan.md) was MISSED;
    # the activation is a deliberate setting, not a passed gate (#3261).
    #
    # Parsed tolerantly ("1"/"0" as well as "true"/"false") because research/sweep.py
    # validates spec values as NUMBERS: the widespread `.lower() == "true"` idiom would
    # read a swept "1" as False and the sweep would measure the same state twice.
    MOMENTUM_SCORE_SMOOTH_ENABLED: bool = os.getenv(
        "MOMENTUM_SCORE_SMOOTH_ENABLED", "true"
    ).strip().lower() in ("1", "true", "yes")

    # #3038 TRD-10 â€” VIXAwareRiskAgent votes on the symbol's own expected swing
    # (implied volatility, ranked against YESTERDAY's cross-section) instead of the
    # market-wide VIX. Dormant, default OFF â‡’ byte-identical vote path.
    #
    # Why: measured over 29,395 live decisions the agent's score spans 0.754..0.773,
    # is identical for every symbol on 17 of 23 days, and is bit-identical to
    # RegimeDetectionAgent in 26,755 of 26,755 cases (agents.py:891 vs :577 â€” same
    # sigmoid, same input). The consensus compares symbols of one day against each
    # other; a quantity that is equal for all of them separates nobody.
    #
    # Evidence for the new input: 71,559 symbol-days, IV vs maximum drawdown
    # r = -0.330, negative in ten of ten quarters (research/iv_study.py). The
    # criterion is DRAWDOWN, not return â€” the return correlation flips sign between
    # periods (VISION_AND_GOALS Â§3: capital preservation before return).
    #
    # Parsed tolerantly ("1"/"0" as well as "true"/"false") because research/sweep.py
    # validates spec values as NUMBERS: the widespread `.lower() == "true"` idiom would
    # read a swept "1" as False and stage 2 would measure the same state twice.
    # ACTIVATED 2026-08-26 (stage 3, owner decision). The stage-2 sweep
    # (sweep-46defed1d318, 60 trading days) ran the OLD rule into the portfolio
    # stop loss on day 43 of 60 (-7.1 %, 85 approved buys dropped on
    # trading_halted); the new rule never touched it, at half the maximum
    # drawdown. CORRECTED 2026-08-27: those figures came from a cell marked
    # `timed_out` (43 of 60 days). The repeat with the halt fix (#3067) gives
    # +4.71 % -> +7.66 % return and -7.11 % -> -5.95 % drawdown, both cells
    # complete -- and the stop-loss trip does NOT reproduce. Both differences
    # sit at the sim's noise floor (2.5 pp, measured). Direction unchanged,
    # evidence weaker. Three of four registered criteria met;
    # the saturation criterion is MISSED (4.50 % at the rims against <= 2 %),
    # and two attempted explanations for that were measured and refuted. This is
    # an owner decision on a missed gate, not a passed one â€” same posture as
    # #3004. Rollback: `=0` restores the market-wide VIX sigmoid, test-enforced.
    VIXAWARE_IMPLIED_VOL_ENABLED: bool = os.getenv(
        "VIXAWARE_IMPLIED_VOL_ENABLED", "true"
    ).strip().lower() in ("1", "true", "yes")

    # #1955 TRD-4 â€” Meta-Labeling (LÃ³pez de Prado): secondary Trade/No-Trade
    # model over the primary consensus BUYs (dormant, default OFF). When ON,
    # `_apply_meta_label` (runner.py) drops an approved BUY to an audited
    # no_trade when p(net-of-cost profitable) < META_LABEL_MIN_PROBA. OFF â‡’
    # no-op, no model load, byte-identical order path. Activation is gated on
    # the net-of-cost OOS harness proof (prove-it-or-kill-it, plan #1955 Â§11).
    META_LABEL_FILTER_ENABLED: bool = (
        os.getenv("META_LABEL_FILTER_ENABLED", "False").lower() == "true"
    )
    # ADR-ML-META-01 (#1955): META_LABEL_MIN_PROBA = 0.55
    # Basis: AFML Ch. 3.6 meta-labeling â€” the secondary model gates on
    # p(profitable NET of spread+fee+slippage), not accuracy. Rationale:
    # conservative start just above indifference (0.50) so only clearly
    # edge-less BUYs are dropped (regime-shift guard, plan Â§12.5); decides
    # real capital exposure (MiFID II Art. 17) â†’ annual review, re-tuned ONLY
    # against the net-of-cost OOS benchmark (2024â€“2026), never ad hoc.
    META_LABEL_MIN_PROBA: float = float(
        os.getenv("META_LABEL_MIN_PROBA", "0.55") or "0.55"
    )
    # Pickle artifact written by scripts/train_meta_label.py (schema_version 1,
    # feature whitelist pinned). Missing/corrupt â‡’ unblocked in observation mode;
    # fails CLOSED when META_LABEL_REQUIRE_MODEL is True.
    META_LABEL_MODEL_PATH: str = os.getenv(
        "META_LABEL_MODEL_PATH", "data/meta_label_model.pkl"
    )
    META_LABEL_REQUIRE_MODEL: bool = (
        os.getenv("META_LABEL_REQUIRE_MODEL", "False").lower() == "true"
    )

    # #1968 (RT-BUG-2) â€” MomentumAgent one-bar fallback â†’ ABSTAIN (dormant, default OFF).
    # With <40 daily closes the 12-1M momentum path is unavailable and the one-bar
    # fallback (close-open)/open is direction-identical to RegimeDetection's close/open
    # ratio â€” a silent regime ECHO that also modulates its own weight via the
    # bearish-regime halving in consensus.py (self-referential coupling). ON â†’ the
    # fallback ABSTAINS (score 0.5, weight 0.0, excluded from consensus â€” the
    # VIXAware/SpecialistAlpha abstention pattern) instead of echoing; the real 12-1M
    # primary path is untouched. OFF (default) â‡’ byte-identical one-bar fallback.
    # Mirrored in config.oss.py (BORA dual-edition parity).
    MOMENTUM_FALLBACK_ABSTAIN_ENABLED: bool = (
        os.getenv("MOMENTUM_FALLBACK_ABSTAIN_ENABLED", "True").lower() == "true"
    )

    # #1949 (RTR-2, Epic #1958; DEFAULT ON since #2516 / ADR-018, #3261): RegimeDetectionAgent O/C-proxy â†’
    # real MarketRegimeModel conditioner. ON â†’ (a) trading_loop threads the per-cycle
    # VIX value + regime label (monitor_loop â†’ current_market_data) into the DECLARED
    # SymbolEvalState channels "vix"/"regime" (LangGraph drops undeclared keys â€”
    # verified against langgraph==1.0.10), (b) RegimeDetectionAgent scores the
    # continuous VIX sigmoid instead of the one-bar close/open proxy (removes the
    # Momentum double count), (c) consensus.py scales ALL directional voters
    # (Momentum/LSTM/RLConfidence/SpecialistAlpha) gradually by regime severity
    # instead of the binary Momentum-only Ã—0.5. OFF â‡’ O/C proxy + Ã—0.5 block +
    # None-valued channels. DEFAULT ON since #2516: ADR-018 (owner 2026-07-26) waived the
    # RTR-0 (#1947) net-of-cost proof this activation was originally gated on (#3261).
    # Mirrored in config.oss.py (BORA dual-edition parity).
    REGIME_CONDITIONER_ENABLED: bool = (
        os.getenv("REGIME_CONDITIONER_ENABLED", "True").lower() == "true"
    )
    # ADR-RGC-01 (#1949) â€” max fraction the regime conditioner may cut a directional
    # voter's consensus weight in FULL risk-off (severity 1.0 â‡’ factor 1-damp).
    # 0.5 mirrors today's hardcoded Momentum Ã—0.5 as the worst-case bound, now
    # applied gradually. Judgement value (MiFID II Art. 17 pre-trade control);
    # recalibrate via RTR-0 backtest, annual review. Inert while the flag is OFF.
    REGIME_RISKOFF_DAMP_MAX: float = float(
        os.getenv("REGIME_RISKOFF_DAMP_MAX", "0.5") or "0.5"
    )
    # ADR-RGC-02 (#1949) â€” VIX sigmoid midpoint (score 0.5) for the conditioner.
    # 25.0 = MarketRegimeModel's "normal" threshold (core/market_regime.py
    # vix_thresholds) and the same midpoint VIXAwareRiskAgent uses â€” one consistent
    # risk-appetite scale across the round table. Judgement value (MiFID II
    # Art. 17); annual review. Inert while the flag is OFF.
    REGIME_VIX_MIDPOINT: float = float(
        os.getenv("REGIME_VIX_MIDPOINT", "25.0") or "25.0"
    )

    # RPAR-#1284 / G1b (Aktivierungs-Gate, dormant, default OFF - Desktop-Launcher-only).
    # When ON, start_live_strategy (after the live_universe is populated) constructs +
    # starts the StockSpecialistRegistry and wires it into the Round Table, so
    # SpecialistAlphaAgent receives real reports. OFF (default) -> the registry stays the
    # None set by _init_specialist_registry -> BYTE-IDENTICAL to today's main (the
    # /specialist-reports unavailable-DTO and the monitor_loop None-gate are unchanged).
    # The flip was a P1 decision-path change (a new weighted consensus voter), human-
    # gated at the time; #2839 default Falseâ†’True = shipped desktop launcher profile
    # (owner 2026-08-13, "desktop is master" â€” every tested install has been running
    # it ON via the shell env injection). Mirrored in config.oss.py (dual-edition parity).
    SPECIALIST_REGISTRY_ENABLED: bool = (
        os.getenv("SPECIALIST_REGISTRY_ENABLED", "True").lower() == "true"
    )

    # Dynamic specialist-report coverage (Epic #1998 RPT-GOLD, sub #2630). ON -> the report
    # registry's HIGH-PRIORITY set becomes: held positions U top-N by round-table consensus_score
    # U base watchlist, instead of the static _CARD_REGISTRY_STOCKS. Dark by default -> byte-
    # identical to today when OFF. Mirrored in config.oss.py (dual-edition parity).
    # #2839: default Falseâ†’True = shipped desktop launcher profile (desktop is master).
    SPECIALIST_COVERAGE_DYNAMIC: bool = (
        os.getenv("SPECIALIST_COVERAGE_DYNAMIC", "True").lower() == "true"
    )
    SPECIALIST_TOP_N_CONVICTION: int = int(
        os.getenv("SPECIALIST_TOP_N_CONVICTION", "20") or "20"
    )
    SPECIALIST_COVER_POSITIONS: bool = (
        os.getenv("SPECIALIST_COVER_POSITIONS", "True").lower() == "true"
    )

    # #1346: config-gated SpecialistAlphaAgent vote weight (0.0 = dormant / excluded
    # from consensus). The finance-core (round_table/agents.py) reads this via
    # get_config() instead of os.environ (CODING_POLICY Â§2.10). Mirrored in config.oss.py.
    # #2839: default 0.0â†’0.40 = shipped desktop launcher profile (desktop is master;
    # pairs with SPECIALIST_REGISTRY_ENABLED=True â€” the weight is meaningless without it).
    SPECIALIST_ALPHA_WEIGHT: float = float(
        os.getenv("SPECIALIST_ALPHA_WEIGHT", "0.40") or "0.40"
    )

    # RTR-0 (#1947) / Epic #1958: config-gated RLConfidenceAgent vote weight. Default
    # 0.40 == the historical hardcoded weight (byte-identical to today). The RL vote is
    # currently direction-blind â€” its score is 0.5 + (|pred|/2)*0.5 on a BUY action, so a
    # strongly-BEARISH prediction (large |pred|) still yields a max-conviction BUY. Set
    # RL_CONFIDENCE_WEIGHT=0.0 to MUTE it as an interim guard until the sign-fix + RTR-0
    # attribution land (prove-it-or-kill-it). read via get_config() (CODING_POLICY Â§2.10).
    # Mirrored in config.oss.py (BORA dual-edition parity).
    RL_CONFIDENCE_WEIGHT: float = float(
        os.getenv("RL_CONFIDENCE_WEIGHT", "0.0") or "0.0"
    )

    # #2815 (TRD-8 T1): the remaining consensus knobs, config-gated like the two above so
    # the parameter sweep can vary them per RUN (fresh process â€” agents.py resolves ONCE
    # at import, see the #1346 monkeypatch-race comment). Defaults == the historical
    # hardcoded literals, byte-identical when unset (pinned by
    # tests/unit/test_consensus_weight_seam.py). Read via get_config() in the finance-core
    # (CODING_POLICY Â§2.10). Mirrored in config.oss.py (BORA). Weights are clamped to
    # [0, 1] at the read site; thresholds are validated as 0 < sell < buy < 1 with a
    # WARNING fallback. Garbage in a weight env FAILS CLOSED at boot â€” the same loud
    # pydantic/parse behaviour as every numeric field here (deliberate platform posture);
    # the sweep orchestrator (#2816) validates spec values BEFORE spawning a run.
    DRAWDOWN_GUARD_WEIGHT: float = float(
        os.getenv("DRAWDOWN_GUARD_WEIGHT", "0.60") or "0.60"
    )
    REGIME_DETECTION_WEIGHT: float = float(
        os.getenv("REGIME_DETECTION_WEIGHT", "0.50") or "0.50"
    )
    MOMENTUM_AGENT_WEIGHT: float = float(
        os.getenv("MOMENTUM_AGENT_WEIGHT", "0.45") or "0.45"
    )
    VIX_RISK_WEIGHT: float = float(os.getenv("VIX_RISK_WEIGHT", "0.45") or "0.45")
    LSTM_SIGNAL_WEIGHT: float = float(os.getenv("LSTM_SIGNAL_WEIGHT", "0.40") or "0.40")

    # #3145(3)/#3146: consensus weights for the FundamentalsAgent / ValuationAgent.
    # ACTIVATED at 0.35 as the shipped default (owner decision 2026-09-03): the walk-forward
    # deviations are usable without score centering, whose effect was measured negligible.
    # Overridable via env. Both editions carry the same default (BORA parity, config.oss.py).
    FUNDAMENTALS_AGENT_WEIGHT: float = float(
        os.getenv("FUNDAMENTALS_AGENT_WEIGHT", "0.35") or "0.35"
    )
    VALUATION_AGENT_WEIGHT: float = float(
        os.getenv("VALUATION_AGENT_WEIGHT", "0.35") or "0.35"
    )

    # #3154 (UXC-1 S1): letzte fehlende Gewichts-Naht (Review B3) â€” unset â‡’ 0.35.
    NEWS_SENTIMENT_WEIGHT: float = float(
        os.getenv("NEWS_SENTIMENT_WEIGHT", "0.35") or "0.35"
    )
    # #3250: TA-Feature-Voter (TrendAgent / VolumeConfirmationAgent) â€” Konsens-
    # Stimmgewichte. DARK-Default 0.0 â‡’ VoteResult(weight=0.0) â‡’ aus dem Konsens
    # ausgeschlossen (byte-identisch), bis ein Owner armt. BORA-parallel zu
    # config.oss.py. Beide Voter lesen nur die Last-Row-Skalare aus state["features"].
    TREND_AGENT_WEIGHT: float = float(os.getenv("TREND_AGENT_WEIGHT", "0.0") or "0.0")
    VOLUME_CONFIRM_AGENT_WEIGHT: float = float(
        os.getenv("VOLUME_CONFIRM_AGENT_WEIGHT", "0.0") or "0.0"
    )
    # #3275: QualityAgent (Composite-Quality-Richtungsstimme) â€” Konsens-Stimmgewicht.
    # DORMANTES 0.30 (min 0.10, wie UpsideSkew): dark Ã¼ber QUALITY_AGENT_ENABLED=false
    # (Enthaltung â‡’ byte-identisch), aber attribuierbar (>0), sobald die RTR-0-
    # Attribution das Flag armt. BORA-parallel zu config.oss.py. Liest denselben
    # PIT-Fundamentals-Feed wie der FundamentalsAgent (kein neuer Datenbezug).
    QUALITY_AGENT_WEIGHT: float = float(
        os.getenv("QUALITY_AGENT_WEIGHT", "0.30") or "0.30"
    )
    # #3154 (UXC-1 S1): per-Agent-Enable-Flags der direktionalen Voter, default
    # TRUE (unset â‡’ byte-identisch: jeder stimmt wie heute). Flag false â‡’ der
    # Agent ABSTAINED (weight 0.0 direkt, "EXCLUDED â€” deactivated by
    # configuration") â€” nie Weightâ†’0 (min_weight-Klemme base_agent.py) und nie
    # Vote-Drop (Record-Sichtbarkeit). Lesefehler â‡’ FAIL-CLOSED (Abstain +
    # WARNING, agents._agent_enabled) â€” bewusste Abweichung vom Fail-open-
    # Hausmuster, Archon-Audit #3154. UpsideSkew hat sein Flag bereits (dark).
    MOMENTUM_AGENT_ENABLED: bool = (
        os.getenv("MOMENTUM_AGENT_ENABLED", "true").lower() == "true"
    )
    LSTM_SIGNAL_AGENT_ENABLED: bool = (
        os.getenv("LSTM_SIGNAL_AGENT_ENABLED", "true").lower() == "true"
    )
    SPECIALIST_ALPHA_AGENT_ENABLED: bool = (
        os.getenv("SPECIALIST_ALPHA_AGENT_ENABLED", "true").lower() == "true"
    )
    NEWS_SENTIMENT_AGENT_ENABLED: bool = (
        os.getenv("NEWS_SENTIMENT_AGENT_ENABLED", "true").lower() == "true"
    )
    VIX_RISK_AGENT_ENABLED: bool = (
        os.getenv("VIX_RISK_AGENT_ENABLED", "true").lower() == "true"
    )
    # Rev. 3 (#3154, Owner 02.09.2026): DrawdownGuard: Pre-trade-Veto-Agent komplett aus (Abstain, nie vetoed)
    DRAWDOWN_GUARD_AGENT_ENABLED: bool = (
        os.getenv("DRAWDOWN_GUARD_AGENT_ENABLED", "true").lower() == "true"
    )
    # Rev. 3 (#3154, Owner 02.09.2026): RegimeDetection: Regime-Overlay aus (Abstain => keine Konsens-Konditionierung)
    REGIME_DETECTION_AGENT_ENABLED: bool = (
        os.getenv("REGIME_DETECTION_AGENT_ENABLED", "true").lower() == "true"
    )
    # Rev. 3 (#3154, Owner 02.09.2026): Fundamentals-Stimme aus (Abstain)
    FUNDAMENTALS_AGENT_ENABLED: bool = (
        os.getenv("FUNDAMENTALS_AGENT_ENABLED", "true").lower() == "true"
    )
    # Rev. 3 (#3154, Owner 02.09.2026): Valuation-Stimme aus (Abstain)
    VALUATION_AGENT_ENABLED: bool = (
        os.getenv("VALUATION_AGENT_ENABLED", "true").lower() == "true"
    )
    # Rev. 3 (#3154, Owner 02.09.2026): RLConfidence-Stimme aus (Abstain)
    RL_CONFIDENCE_AGENT_ENABLED: bool = (
        os.getenv("RL_CONFIDENCE_AGENT_ENABLED", "true").lower() == "true"
    )
    # #3250: TA-Feature-Voter â€” Master-Gates, Default FALSE (dark, byte-identisch;
    # der Agent enthÃ¤lt sich, bis ein Owner armt). BORA-parallel zu config.oss.py.
    TREND_AGENT_ENABLED: bool = (
        os.getenv("TREND_AGENT_ENABLED", "false").lower() == "true"
    )
    VOLUME_CONFIRM_AGENT_ENABLED: bool = (
        os.getenv("VOLUME_CONFIRM_AGENT_ENABLED", "false").lower() == "true"
    )
    # #3275: QualityAgent â€” Master-Gate, Default FALSE (dark, byte-identisch; der Agent
    # enthÃ¤lt sich, bis ein Owner armt). BORA-parallel zu config.oss.py.
    QUALITY_AGENT_ENABLED: bool = (
        os.getenv("QUALITY_AGENT_ENABLED", "false").lower() == "true"
    )
    # #2947: behebt zwei Rechenfehler in der Kandidaten-Bewertung (score_opportunity) â€”
    # (a) die vier Komponentengewichte summieren auf 0,90, waehrend der Positions-Score auf 1,00
    # summiert, und beide wurden gegen die Schwelle 15 voneinander abgezogen; zusammen mit der
    # UNBEDINGT vergebenen Pauschale von 15 Punkten fuer "RL sagt BUY" (rl_action=1 ist an beiden
    # Live-Aufrufstellen ein Literal) kuerzte sich die Marge vollstaendig weg. (b) die
    # Confidence-Komponente rechnete mit dem Faktor fuer einen Eingang in +/-5, bekam aber die
    # LSTM-Vorhersage in +/-1. True = behoben (Gewichte normiert, Bonus ueber
    # RL_CONFIDENCE_WEIGHT skaliert, Faktor am Eingang). False = alte Arithmetik byte-identisch,
    # dokumentierter Rueckfall. Mirrored in config.oss.py (BORA-Paritaet).
    OPPORTUNITY_SCORE_NORMALIZED: bool = (
        os.getenv("OPPORTUNITY_SCORE_NORMALIZED", "True").lower() == "true"
    )

    SIGNAL_BUY_THRESHOLD: float = float(
        os.getenv("SIGNAL_BUY_THRESHOLD", "0.65") or "0.65"
    )
    SIGNAL_SELL_THRESHOLD: float = float(
        os.getenv("SIGNAL_SELL_THRESHOLD", "0.35") or "0.35"
    )
    # #3180 Consensus Retention Gate (plan #3189, dark). A held name's OPINION exit (rotation /
    # book-overflow / take-profit / momentum-fade / model-signal) is SUPPRESSED while the live
    # round-table blend-consensus for that symbol is > this threshold. Hard risk stops
    # (loss>=90, trailing>=85) are NEVER gated (ExitAnalysis.tier='risk'). Default 0.0 = OFF
    # (gate condition `threshold > 0` false â‡’ no veto â‡’ byte-identical). A single float drives
    # on/off so the A/B sweep can vary it numerically. Calibration via the walk-forward A/B.
    CONSENSUS_RETENTION_THRESHOLD: float = float(
        os.getenv("CONSENSUS_RETENTION_THRESHOLD", "0.0") or "0.0"
    )

    # RTR-1 (#1948, Epic #1958, dormant, default OFF): NewsSentimentAgent NLP path.
    # ON -> the trading loop fills SymbolEvalState.news_headlines from the FREE
    # Google-News-RSS point-in-time producer (core/nlp/headlines.py, only
    # published_utc <= current_time) and NewsSentimentAgent scores REAL headlines
    # with a local NLP model (core/nlp/news_sentiment.py: FinBERT from the offline
    # cache, else the vendored VADER lexicon) instead of the headline-less LLM
    # symbol guess. OFF -> no news fetch, no NLP import, the LLM-/abstain-path
    # runs byte-identical. ADR-NS-07 (2026-08-04): default flipped ON by explicit
    # OWNER activation â€” a deliberate waiver of the RTR-0 gate (#1947: prove
    # marginal net-of-cost Sharpe + low rank-correlation vs the LSTM vote first).
    # NewsSentiment was NOT inside the 2026-07-26 Round-Table-v2 RTR-free waiver
    # (LSTM+Momentum+Konditionierer), so this is a separate, documented override.
    # LIVE BACKEND = vendored VADER lexicon: `transformers` is not a dependency
    # and the FinBERT weights are not bundled, so the FinBERT path degrades
    # WARNING-visibly to VADER (both free, deterministic, point-in-time; Â§5.6).
    # Fails CLOSED â€” no point-in-time headline / no backend -> weight 0 (EXCLUDED
    # from the consensus, never approves). Mirrored in config.oss.py (BORA parity).
    NEWS_SENTIMENT_NLP_ENABLED: bool = (
        os.getenv("NEWS_SENTIMENT_NLP_ENABLED", "True").lower() == "true"
    )
    # ADR-NS-01 (#1948): cap of point-in-time headlines fed to the NLP scorer per
    # symbol/cycle. 8 balances signal coverage against desktop-CPU latency
    # (FinBERT forward â‰ˆ 100ms/headline) and audit-log size. Inert while the
    # flag above is OFF. Annual review.
    NEWS_SENTIMENT_MAX_HEADLINES: int = int(
        os.getenv("NEWS_SENTIMENT_MAX_HEADLINES", "8") or "8"
    )
    # ADR-NS-02 (#1948): NLP backend selector. "finbert" = ProsusAI/finbert via
    # transformers, LOCAL offline cache only (no runtime download; dep/assets
    # missing -> transparent WARNING + vendored-VADER-lexicon fallback);
    # "finbert-tone" = yiyanghkust/finbert-tone; "vader" = force the stage-1
    # lexicon path. Inert while the flag above is OFF. Annual review.
    NEWS_SENTIMENT_MODEL: str = (
        os.getenv("NEWS_SENTIMENT_MODEL", "finbert") or "finbert"
    )

    # #2389 (Epic RTR #1958, dormant, default OFF): real TA-feature snapshot for the
    # MiFID II Art. 25 decision record. ON -> the orchestration graph's
    # _compute_features_node fetches the 30d OHLCV window (shared with
    # DrawdownGuardAgent via the data-provider cycle cache), computes
    # core/round_table/features.py::compute_technical_features and attaches the
    # last-row SCALARS as state["features"]; the Round Table runner maps them into
    # the DecisionContext (real rsi_14/atr_14d/macd/bb_pct/volume_ratio instead of
    # the neutral defaults). OFF (default) -> the node is a no-op (no fetch, no
    # compute) and the decision snapshot keeps today's defaults â€” byte-identical.
    # Mirrored in config.oss.py (dual-edition parity).
    # #2839: default Falseâ†’True = shipped desktop launcher profile (desktop is master).
    DESKTOP_FEATURE_SNAPSHOT_ENABLED: bool = (
        os.getenv("DESKTOP_FEATURE_SNAPSHOT_ENABLED", "True").lower() == "true"
    )

    # Insight-Quality ratchet (RPAR T6b #1271, dormant, default OFF). When ON, the
    # specialist's synthesis is graded and (via an injected LLM judge) may be
    # rewritten or abstained - the ratchet can return the prior report instead of a
    # weaker fresh one. That CAN change sentiment_score/recommendation/escalate, which
    # SpecialistAlphaAgent.vote reads on the order path -> P1-by-rule, gated exactly
    # like ML_SENTIMENT_BLEND_ENABLED. OFF (default) = the flat _build_report path,
    # signal_quality='llm_only' -> DTO + decision byte-identical to today. PR-1 lands
    # the core/specialist/insight_quality package + this flag DORMANT (nothing in
    # research() reads it yet); research()-wiring + _fetch_earnings_transcript = PR-2.
    # Mirrored in config.oss.py (dual-edition parity, #1159 lesson).
    INSIGHT_QUALITY_ENABLED: bool = (
        os.getenv("INSIGHT_QUALITY_ENABLED", "False").lower() == "true"
    )

    # RPAR-1 (#1262) Abschluss / #1490: deterministic, bundle-free report-quality badge. Default
    # OFF -> _serialize_specialist_report emits NO report_quality key (exact key-set contract +
    # engine/DTO byte-identity hold). Mirrored in config.oss.py (dual-edition parity).
    REPORT_QUALITY_BADGE_ENABLED: bool = (
        os.getenv("REPORT_QUALITY_BADGE_ENABLED", "False").lower() == "true"
    )

    # RPT-6 (Epic #1998; DEFAULT ON since #2516 / ADR-018, #3261): wires the auditable v2 report
    # pipeline (core/report: fact_set -> render -> narrate(get_llm_provider) ->
    # audit) into StockSpecialistAgent.research(). ON -> research() ADDITIVELY
    # attaches an auditable Markdown report to SpecialistReport.report_markdown /
    # report_audit_* (report-only). OFF -> research() skips the attach
    # entirely -> the decision path (score/recommendation/reasons/escalate) and
    # the serialized DTO are BYTE-IDENTICAL to today. Never touches the order
    # path (the SpecialistAlpha score votes regardless: SPECIALIST_ALPHA_WEIGHT
    # defaults to 0.40 since #2839; #3261). Mirrored in config.oss.py (dual-
    # edition parity) â€” both editions share the default (ON).
    REPORT_GENERATOR_V2_ENABLED: bool = (
        os.getenv("REPORT_GENERATOR_V2_ENABLED", "True").lower() == "true"
    )

    # RPT-8b (A5, dormant, default OFF): when ON *and* SPECIALIST_REGISTRY_ENABLED,
    # the specialist registry is constructed over the FULL S&P 500 universe
    # (get_sp500_symbols()) instead of the ENVIRONMENT-gated trading `live_universe`,
    # and its refresh generates DETERMINISTIC-FIRST reports (narrate=False, no Gemini
    # synthesis) for every symbol. Report-only and DECOUPLED from trading: the
    # trading `live_universe`/scanner path is untouched (grep-verifiable), so all
    # ~500 auditable reports can populate for the reader without changing what the
    # bot trades. OFF (default) -> registry keeps today's live_universe scope and
    # research() path. Mirrored in config.oss.py (dual-edition parity).
    REPORT_REGISTRY_FULL_UNIVERSE_ENABLED: bool = (
        os.getenv("REPORT_REGISTRY_FULL_UNIVERSE_ENABLED", "False").lower() == "true"
    )

    # REPORT-ONLY DECOUPLING (DEFAULT ON since #2529, #3261). When ON, the StockSpecialistRegistry
    # is booted at engine __init__ in report_only mode over the FULL S&P 500, INDEPENDENT of
    # self.api / start_live_strategy, so /specialist-reports cards appear even when Alpaca is
    # NOT connected (self.api is None) â€” a research REPORT needs no broker link. NEVER feeds
    # trading: report_only reports carry neutral decision fields. (The weight is NOT the
    # guard: SPECIALIST_ALPHA_WEIGHT defaults to 0.40 since #2839, so a non-neutral specialist
    # report does vote; #3261.) OFF -> registry stays the None
    # set by _init_specialist_registry -> byte-identical. Mirrored in config.oss.py (dual-
    # edition parity, #1159 lesson); the same default (ON) in BOTH editions so the guarantee holds on
    # the OSS/desktop build too (whose SPECIALIST_REGISTRY_ENABLED defaults True).
    REPORT_REGISTRY_STANDALONE_ENABLED: bool = (
        os.getenv("REPORT_REGISTRY_STANDALONE_ENABLED", "True").lower() == "true"
    )

    # REPORT_CACHE_PERSIST (2026-07-23) â€” mirrored in config.oss.py. ON -> the
    # specialist registry persists each fresh report to
    # <AAA_USER_DATA_DIR>/report_cache and reloads fresh-enough ones at boot, so
    # an app restart shows the SAME cards instead of regenerating every report
    # with drifted news inputs. Report-only; default ON in BOTH editions so the
    # Reports page survives an engine restart out of the box (a restart used to wipe
    # every card because this defaulted OFF). Stale (>high-prio age) reports still
    # regenerate on schedule; set REPORT_CACHE_PERSIST_ENABLED=False to opt out.
    REPORT_CACHE_PERSIST_ENABLED: bool = (
        os.getenv("REPORT_CACHE_PERSIST_ENABLED", "True").lower() == "true"
    )

    # Phase B (ACTIVE, default ON since the desktop launcher profile #2839; this comment said "dormant, default OFF" until 24.09.2026) â€” âš  TRADING-BEHAVIOUR flag. When ON, the engine
    # FEEDS the full ~500-symbol universe to the Round Table instead of the scanner's
    # top-10 funnel, and lets the existing 20-slot displacement arbitrate what is
    # actually held (ADR-FU02).
    #
    # âš  WHAT THIS IS NOT, STATED PLAINLY: the ON path judges each symbol on its own
    # and competes the survivors for slots. It does NOT rank the universe and buy the
    # top decile. An earlier version of this comment claimed it did â€” it never did,
    # and describing an intention in the present tense is how a design note becomes a
    # false statement about the code. The cross-sectional construction the rationale
    # below argues for is NOT implemented here; the only cross-sectional element that
    # exists is LSTMSignalAgent's rank vote (LSTM_VOTE_USES_CROSS_SECTIONAL_RANK),
    # which is one voice among many, not portfolio construction. Closing that gap is
    # the open work this flag must not be flipped without.
    #
    # This is a P1 decision-path change: it changes WHICH symbols are bought.
    #
    # It MUST stay OFF until walk-forward-validated + human sign-off (MiFID II RTS 6,
    # CODING_POLICY Â§5.x). Rationale for the cross-sectional design: our own
    # walk-forward shows per-symbol independent bets do NOT survive multiple-testing
    # (0/488 pass FDR) while the cross-sectional rank is a real (if weak) edge â€” so
    # breadth must arrive as a RANKING + portfolio construction, never as ~500
    # isolated BUY/HOLD calls. OFF (default) -> universe selection, scanner funnel and
    # position selection are byte-identical to today. Mirrored in config.oss.py.
    FULL_UNIVERSE_TRADING_ENABLED: bool = (
        os.getenv("FULL_UNIVERSE_TRADING_ENABLED", "True").lower() == "true"
    )

    # ADR-FU01: Full-universe evaluation fan-out cap = 20 concurrent symbols.
    # Basis: today's hard 200-symbol truncation (core/engine/trading_loop.py) is
    #   incidentally the ONLY bound on the per-symbol `asyncio.gather`. Evaluating
    #   ~500 symbols unbounded opens ~500 concurrent graph invocations x 9 round-table
    #   agents (~4,500 in-flight LLM calls) against ONE local Ollama instance â€”
    #   connection-pool exhaustion, i.e. an outage, not a cost.
    # Rationale: mirrors the proven per-loop semaphore the market scanner already
    #   uses (core/market_scanner.py SCAN_CONCURRENCY=20). Read ONLY inside the
    #   FULL_UNIVERSE_TRADING_ENABLED branch, so the default path is untouched.
    #   Tune with the walk-forward, not by feel. Mirrored in config.oss.py.
    FULL_UNIVERSE_EVAL_CONCURRENCY: int = int(
        os.getenv("FULL_UNIVERSE_EVAL_CONCURRENCY", "20") or "20"
    )

    # round-table-v2 rank-cache funnel: cap the round-table deep-eval set to [held positions âˆª the
    # top-K LSTM-ranked symbols from the cached panel]. 0 = DISABLED â†’ full universe, byte-identical.
    # Set 20 to activate (~25x less GPU on the frequent cycles; the full LSTM ranking still runs on its
    # own cadence to keep the panel warm). K should exceed MAX_POSITIONS for displacement headroom.
    # NOTE: the panel is written only when REPORT_GENERATOR_V2_ENABLED is on; with it off this flag is
    # a safe no-op (empty panel â†’ full universe).
    # #2680 "Patient AI": deep-eval cap = held âˆª top-K LSTM. Default 30 matches the value the desktop
    # already injects at runtime (main.cjs), so config and the shipped app never disagree.
    ROUND_TABLE_TOP_K_EVAL: int = int(os.getenv("ROUND_TABLE_TOP_K_EVAL", "30") or "30")

    # Max age (calendar days) of the LSTM panel snapshot for the funnel to trust it. Older â‡’ the rank
    # producer likely stopped â‡’ fall back to the FULL universe (never trade a shrunken set on a stale
    # ranking). Default 2 covers a normal weekend gap.
    ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS: int = int(
        os.getenv("ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS", "2") or "2"
    )

    # ADR-RANK-01 (#2680 "Patient AI", ARMED by default â€” owner decision 2026-08-05) â€” rank
    # the LSTM cross-section on a
    # TREND instead of a point-in-time photo. ON, every consumer of the ranking (the
    # LSTMSignalAgent BUY vote, the rotation SELL trigger, the deep-eval funnel and the
    # report card â€” all via lstm_panel_store.active_cross_section) reads the per-symbol
    # MEDIAN over the last WINDOW_DAYS snapshot dates instead of today's scores.
    # Basis: measured 2026-08-05 â€” IVZ was bought while point-in-time top-10 (14:00 UTC)
    # and rotated out at point-in-time rank 242/501 (17:09 UTC): ~230 places in three
    # hours, one spike both buying and selling the name. A 20-day min-hold posture cannot
    # sit on a signal that moves that fast.
    # WINDOW_DAYS counts the last N snapshot DATES the panel recorded (trading days), not
    # calendar days â€” 10 â‰ˆ two trading weeks, matching the ~5-day LSTM horizon with slack.
    # MIN_COVERAGE: a symbol seen on fewer than this many of those dates is NOT ranked â€”
    # without it a freshly covered name is "smoothed" over one high sample and tops the
    # book (the spike, one layer down). The floor scales down on a cold start (fewer dates
    # than the window exist), so a fresh engine smooths over what it has instead of
    # blocking.
    # âš  DEFAULT ON, AND IT CHANGES LIVE BEHAVIOUR ON BOTH EDITIONS. This is NOT a dark
    # ship: with the flag armed, WHICH names the board buys and WHEN rotation exits them
    # is decided on the trend table, not on today's scores. The owner armed it on
    # 2026-08-05 in direct response to the measured IVZ churn â€” the point-in-time basis is
    # what the incident was, so shipping it OFF would ship the known-broken default. It is
    # a Round-Table INPUT change; the RTR-0 attribution gate (#1947) is not currently
    # executable (#2402), so this activation rests on the owner directive, not on gate
    # evidence â€” stated here rather than implied.
    # Direction of the change: strictly LESS reactive. The smoothed table cannot invent a
    # rank; it can only refuse to follow a one-day move. Its worst case is a later entry
    # or a later rotation, never a trade the point-in-time basis would not also have made.
    # Rollback: LSTM_RANK_SMOOTHING_ENABLED=false â‡’ cross_section_at everywhere =
    # exactly the pre-#2680 behaviour. Annual review.
    LSTM_RANK_SMOOTHING_ENABLED: bool = (
        os.getenv("LSTM_RANK_SMOOTHING_ENABLED", "True").lower() == "true"
    )
    LSTM_RANK_SMOOTHING_WINDOW_DAYS: int = int(
        os.getenv("LSTM_RANK_SMOOTHING_WINDOW_DAYS", "10") or "10"
    )
    LSTM_RANK_SMOOTHING_MIN_COVERAGE: int = int(
        os.getenv("LSTM_RANK_SMOOTHING_MIN_COVERAGE", "5") or "5"
    )

    # ADR-RANK-02 (#2683, ARMED by default) â€” persist the LSTM rank panel so the trend
    # smoothing above survives a restart. The panel store is an in-memory singleton whose
    # only writer records ONE snapshot per calendar day; the operations engine restarts at
    # least daily (evidence: git_sha + first-decision per run, 03.â€“05.08.2026), so after
    # every start the median window holds ONE date â€” and a median over one sample IS the
    # point-in-time value the smoothing was written to replace. Without persistence #2680 is
    # therefore a facade in practice, which is why this ships ON rather than dark: OFF would
    # ship the known-ineffective state. ON, ``record_cycle`` additionally writes to
    # ``lstm_panel_snapshots`` best-effort (a DB failure costs telemetry, never the trading
    # cycle) and the store hydrates the last SNAPSHOT_HISTORY_LEN days on first access.
    # NEVER hydrates under SIM_MODE â€” a sim builds its own deterministic panel from the
    # corpus (core/sim/runner._seed_lstm_panel) and must not inherit the live one.
    # Rollback: LSTM_PANEL_PERSISTENCE_ENABLED=false â‡’ memory-only, exactly as before.
    LSTM_PANEL_PERSISTENCE_ENABLED: bool = (
        os.getenv("LSTM_PANEL_PERSISTENCE_ENABLED", "True").lower() == "true"
    )

    # #2672 (TRD, dark default OFF): join the per-cycle broker position snapshot into every
    # symbol's round-table eval state (in_position / position_qty / position_avg_price /
    # unrealized_pnl / position_context_confirmed as declared LangGraph channels). Fixes the
    # board's position-blindness (decisions persisted in_position=0 for held symbols â€” field
    # evidence 31.07.â€“04.08.: 4,202/4,209 decisions blind). OFF (default) â†’ the producer emits
    # None for all five channels = today's behaviour, byte-identical (house pattern #1949).
    # The underlying snapshot fetch itself is UNCONDITIONAL (Archon audit on #2672, Critical 1:
    # never couple a compliance-critical data feed to an unrelated tuning flag).
    # ARMED by default (owner decision 2026-08-05). Verified RECORD-ONLY before arming:
    # no agent reads `in_position` from the state (grep over core/round_table/agents.py)
    # and NO execution path branches on `DecisionContext.in_position` â€” the single
    # reader is runner._position_fields_from_state, which maps it into the audit record.
    # Arming therefore changes WHAT IS WRITTEN (decisions / mifid_decision_log finally
    # state the true position state instead of a hardcoded 0) and changes NO trading
    # decision. It is the prerequisite for board-driven exits, which remain a separate,
    # RTR-0-gated step. Field evidence for why it matters: 4,202/4,209 decisions were
    # position-blind 31.07.â€“04.08. while the account held ~146k$.
    ROUND_TABLE_POSITION_CONTEXT_ENABLED: bool = (
        os.getenv("ROUND_TABLE_POSITION_CONTEXT_ENABLED", "True").lower() == "true"
    )

    # round-table-v2 increment 2: re-rank the FULL universe (the panel producer's LSTM pass) at most
    # once per this many minutes, instead of every ~60s cycle. 0 = every cycle (byte-identical). The
    # LSTM works on daily bars, so a periodic refresh loses no signal. Intended activation value: 120
    # (every 2 hours). Keep it well below ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS so the funnel never
    # reads a self-inflicted stale panel.
    ROUND_TABLE_RANK_REFRESH_MINUTES: int = int(
        os.getenv("ROUND_TABLE_RANK_REFRESH_MINUTES", "120") or "120"
    )

    # Snapshot-fetch resilience (mirrors config.oss.py) â€” the 2nd panel-starvation path.
    # Chunk the per-cycle universe snapshot fetch so one failed/timed-out request can no
    # longer zero the WHOLE panel (-> empty ranking -> all-HOLD). <=0 (or >= universe
    # size) => one request (byte-identical rollback).
    SNAPSHOT_FETCH_CHUNK_SIZE: int = int(
        os.getenv("SNAPSHOT_FETCH_CHUNK_SIZE", "100") or "100"
    )

    # #2499 (event-loop-stall Fix A): off-load the per-symbol create_live_features pandas_ta transform
    #   to a ProcessPoolExecutor so its GIL hold no longer starves the uvicorn event loop.
    #   DEFAULT ON (promoted, 0.3.6 regression fix): #2516 flipped FULL_UNIVERSE_TRADING_ENABLED default-on,
    #   so ~500 symbols are featurized every cycle; left inline the pandas GIL hold starved /health +
    #   /portfolio-summary (8â€“97s) â†’ delayed first-load paper data + timed-out live-switch reconcile.
    #   Fail-safe: any pool problem degrades to the byte-identical inline path (see models/feature_pool.py).
    #   Override FEATURE_POOL_ENABLED=false to roll back without a rebuild. FEATURE_POOL_WORKERS = worker
    #   processes (spawn on Windows via pythonw; picklable create_live_features).
    FEATURE_POOL_ENABLED: bool = (
        os.getenv("FEATURE_POOL_ENABLED", "True").lower() == "true"
    )
    FEATURE_POOL_WORKERS: int = int(os.getenv("FEATURE_POOL_WORKERS", "2") or "2")
    # ADR-FU02: full-universe slot count. Basis: with ~500 names judged instead of ~10,
    #   far more clear the opportunity bar than there is capital for â€” the slot count IS
    #   the portfolio's scarcity, and a better chance must DISPLACE the weakest holding
    #   rather than the book growing without bound.
    #
    # âš  THE NUMBER. #2119 set this to 50; anti-churn Part C (#2176, plan-approved) moves it
    #   to 20 on the operator's call â€” 1/20 = 5% per equal-weight cash slot, which matches
    #   MIN_POSITION_PERCENT (0.05) exactly, so the cash clamp never binds below the
    #   conviction floor (that is why 20, not the BA-proposed 25). Returns to the original
    #   ADR-FU02 value. Supersedes #2119's 50 â€” one knob, one value, this is the current one.
    #
    # âš  AND THE PREMISE THIS COMMENT USED TO STATE WAS WRONG. It said "the existing
    #   debate_position_swap already arbitrates the displacement". It does â€” on the
    #   TENANT path. #2119 establishes that anti-churn and the full-portfolio swap are
    #   NOT armed on the desktop/global fallback, which is the path the app actually
    #   takes (RLAgent -> RoundTable -> global fallback). I found the function and never
    #   checked who reaches it. So raising this number does not, by itself, buy the
    #   displacement it assumes: #2119 is what arms it, and this flag must not be
    #   flipped before that lands.
    #
    # Read ONLY inside the FULL_UNIVERSE_TRADING_ENABLED branch â€” MAX_POSITIONS (=10)
    # itself is NOT touched, so the default path keeps its book size exactly. Tune with
    # the walk-forward. Mirrored in config.oss.py.
    FULL_UNIVERSE_MAX_POSITIONS: int = int(
        os.getenv("FULL_UNIVERSE_MAX_POSITIONS", "10") or "10"
    )

    # RPT/Phase B (dormant, default OFF) â€” produce the LSTM cross-section panel
    # INDEPENDENTLY of ACTIVE_STRATEGY.
    #
    # THE PROBLEM IT SOLVES, and it is the keystone of the whole report: the panel's
    # only writer is LSTMDynamicStrategy.update_lstm_rankings, and ACTIVE_STRATEGY
    # defaults to RLAgent â€” which never writes one. So the report's OPERATIVE number,
    # the cross-sectional rank the recommendation leads with, renders "Platz n/a von
    # n/a" for EVERY symbol in the shipped build. Not an edge case: the default.
    #
    # ON -> a standby LSTMDynamicStrategy is registered (set_active=False) purely as a
    # RANKER and its update_lstm_rankings runs each cycle, so the panel exists whatever
    # strategy trades. It is never asked to trade: run_for_symbol is not called on it,
    # and its _bought_this_window / high_water_marks / _entry_time stay empty by
    # construction.
    #
    # DORMANCY is unusually strong here: the new branch is an `elif` hanging off the
    # existing `hasattr(strategy, "update_lstm_rankings")` in trading_loop, so it can
    # only fire where that hasattr is FALSE â€” i.e. in exactly the states where zero
    # code runs today. There is no old path to preserve, because none existed. OFF also
    # means the producer is never CONSTRUCTED, so no second torch model is loaded.
    #
    # âš  The panel is telemetry until LSTM_VOTE_USES_CROSS_SECTIONAL_RANK is also on;
    # that COMPOSITION is what needs the walk-forward, not this flag alone.
    # Mirrored in config.oss.py.
    LSTM_RANK_PANEL_STRATEGY_INDEPENDENT: bool = (
        os.getenv("LSTM_RANK_PANEL_STRATEGY_INDEPENDENT", "True").lower() == "true"
    )

    # LSTM-panel-starvation fix (docs/lstm-panel-starvation/implementation_plan.md) â€”
    # warm the local daily-bar cache ONCE per trading day (engine start + NY-date
    # rollover) so update_lstm_rankings' per-symbol get_data reads are cache hits
    # instead of ~500 live per-cycle fetches that rate-limit the free IEX feed and
    # empty the panel (-> all-HOLD). DEFAULT ON (the fix). OFF = the exact rollback:
    # per-symbol fetch every cycle, byte-identical. Fail-safe by construction â€” a
    # warm-up error falls back to today's per-symbol fetch, so ON can only REDUCE
    # live requests, never break a cycle. Mirrored in config.oss.py.
    LSTM_BAR_STORE_WARMUP_ENABLED: bool = (
        os.getenv("LSTM_BAR_STORE_WARMUP_ENABLED", "True").lower() == "true"
    )

    # ADR-SURV-02 (#1907 MLR-7 Inc 2/3) â€” Panel-Stale/Coverage-Gate for the offline
    # full-universe panel (scripts/build_full_universe_panel.py), the data foundation
    # of the cross-sectional retrains (#2388).
    # Basis: ESMA backtesting guidelines / MiFID II â€” survivorship/PIT integrity of
    # training data. A retrain on a stale panel, or on a window the membership table
    # does not cover, bakes survivorship/look-ahead bias into a trading model before
    # the first metric is computed. Annual review (Â§5.5).
    # DEFAULT-SAFE = ON (conservative, BLOCKING): core/panel_stale_gate.py refuses
    # training when the panel's data (manifest coverage_end) is older than
    # PANEL_MAX_AGE_DAYS or has_point_in_time_membership(as_of) denies the window.
    # Nothing on the live decision path reads these keys â€” consumers are the offline
    # builder's --check-gate and the #2388 retrain pre-stage. OFF -> the check
    # short-circuits to "allowed" with zero further behaviour (BORA byte-identity).
    # PANEL_MAX_AGE_DAYS=5: one trading week â€” a panel refreshed weekly stays usable,
    # anything older must be rebuilt first. Mirrored in config.oss.py.
    PANEL_STALE_GATE_ENABLED: bool = (
        os.getenv("PANEL_STALE_GATE_ENABLED", "True").lower() == "true"
    )
    PANEL_MAX_AGE_DAYS: int = int(os.getenv("PANEL_MAX_AGE_DAYS", "5") or "5")

    # Phase B (DEFAULT ON since #2516 / ADR-018, #3261) â€” âš  TRADING-BEHAVIOUR flag, and the most
    # SUBSTANTIVE one: it changes what LSTMSignalAgent (an ACTIVE weight-0.4 voter)
    # judges on.
    #
    # Today it votes on the RAW per-symbol prediction (0.5 + 0.5*tanh(pred/scale)).
    # That is precisely the claim our own walk-forward refutes: per-symbol LSTM
    # predictions do NOT survive multiple testing (0/488 pass FDR). The signal that
    # DID hold is the CROSS-SECTIONAL RANK (IC 0.067, t 20.4) â€” already computed and
    # already read by the auditable report via the LSTM panel store.
    #
    # ON -> the agent votes on the stock's rank against the whole universe instead of
    # its isolated prediction, i.e. on the validated signal. OFF -> the raw tanh path.
    # DEFAULT ON since #2516 / ADR-018 (owner 2026-07-26, one-time RTR-0 waiver) (#3261). The original
    # condition â€” a walk-forward measuring the swap before activation â€” was not run; the
    # activation rests on ADR-018, not on that measurement. Mirrored in config.oss.py.
    #
    # âš  PREREQUISITE â€” this flag ALONE does nothing, and that is not obvious:
    # the standing comes from the LSTM panel, and the panel's only writer lives in
    # LSTMDynamicStrategy.update_lstm_rankings, itself gated on
    # REPORT_GENERATOR_V2_ENABLED. So the vote needs BOTH (a) ACTIVE_STRATEGY =
    # LSTMDynamic â€” the default is RLAgent, which never writes a panel â€” AND (b)
    # REPORT_GENERATOR_V2_ENABLED. Miss either and the module abstains on every
    # symbol forever: safe, honest, and completely inert. Flipping this flag under
    # RLAgent and expecting an effect would be measuring nothing and calling it a
    # result. Whoever runs that walk-forward must set the writer up first.
    LSTM_VOTE_USES_CROSS_SECTIONAL_RANK: bool = (
        os.getenv("LSTM_VOTE_USES_CROSS_SECTIONAL_RANK", "True").lower() == "true"
    )

    # RTR / distinct-ML-sources (mirrors #2426; DEFAULT ON since #2516 / ADR-018, #3261) â€”
    # the Round Table's
    # two ML voices must be able to read DISTINCT registered models. TODAY both
    # LSTMSignalAgent and RLConfidenceAgent resolve their source via registry.get_active();
    # ACTIVE_STRATEGY defaults to "RLAgent", so BOTH read the SAME object (the RL trader) â€”
    # the "collapse" documented in core/agent_registry.py::get(): two votes, 0.80 combined
    # weight, one source, the LSTM voice wearing RL's number (observed live: lstm_prediction=0).
    #
    # ON -> LSTMSignalAgent resolves its OWN registered model via registry.get("LSTMDynamic")
    # (the standby ranker, monitor_loop.py:77) and RLConfidenceAgent resolves the active
    # trader by name (ACTIVE_STRATEGY, default "RLAgent") â€” two names, two sources, so the
    # gremium can finally weigh LSTM against RL. A named source that is not registered ->
    # the agent ABSTAINS (weight 0), never a fake vote. OFF -> the get_active() path.
    # âš  TRADING-BEHAVIOUR flag: switching a source changes the vote -> changes trading. The
    # original condition was a WALK-FORWARD before LIVE arming; the default-ON activation
    # rests on #2516 / ADR-018 (owner 2026-07-26, one-time RTR-0 waiver) instead (#3261). Armed only via the desktop _childEnv. Mirrored in
    # config.oss.py (BORA parity).
    ROUND_TABLE_DISTINCT_ML_SOURCES: bool = (
        os.getenv("ROUND_TABLE_DISTINCT_ML_SOURCES", "True").lower() == "true"
    )

    # RTR-5/B1 (#2401, dark, default OFF) â€” Book-Constructor mode: the Round Table
    # as a SELECTOR/book constructor (Top-N target book + minimal rebalance diff)
    # instead of a per-symbol timing machine. Evidence (#2401 Â§1): the only proven
    # +55% came from selection + holding (6 concentrated picks, then 4 months of
    # zero trades); the timing cadence produced 8017 signals / 11 micro-fills in
    # 10 days with zero equity contribution. OFF (default) -> the module
    # (core/round_table/book_constructor.py) is a no-op and NOTHING on the trading
    # path changes (byte-identical). Wiring/activation is B3 (Paper, only after
    # RTR-0-Inc-2 proof + Owner-Go). Mirrored in config.oss.py (BORA parity).
    BOOK_CONSTRUCTOR_ENABLED: bool = (
        os.getenv("BOOK_CONSTRUCTOR_ENABLED", "False").lower() == "true"
    )
    # ADR-RTR5-01: Rebalance cadence = 168h (weekly).
    # Basis: MiFID II Art. 17 (orderly trading; anti-churn) + #2401 forensics.
    # Rationale: the comparative edge is cross-sectional RANKING (which names),
    # not intraday timing (when today). The proven +55% run held 6 picks for
    # months; the ~30-min per-symbol cadence churned (8017 signals/10d, 11
    # micro-fills, zero equity contribution). Weekly re-ranking preserves the
    # ~5-day LSTM signal horizon (#1952 min-hold bake-off: ~5-day-hold net
    # Sharpe ~1.56 vs daily-naive 0.758) while still rotating genuinely
    # collapsed names. Inert while BOOK_CONSTRUCTOR_ENABLED is OFF. Tunable for
    # the B3 walk-forward (e.g. regime-change triggered rebalance); annual review.
    BOOK_CONSTRUCTOR_REBALANCE_INTERVAL_HOURS: float = float(
        os.getenv("BOOK_CONSTRUCTOR_REBALANCE_INTERVAL_HOURS", "168") or "168"
    )

    # #3632: EXIT_POLICY_TRAILING_ENABLED / TRAILING_FROM_PEAK_PCT (und TAKE_PROFIT_PCT)
    # entfielen mit dem Rueckbau von Smart Exit; Gewinnsicherung laeuft ueber die
    # Trailing-Stufen des Intelligent Exit (EXIT_TRAIL_PROFILE). Alte WORM-Eintraege
    # bleiben lesbar; die Registry kennt die Keys nicht mehr.

    # #2886 Book-Cap-Invariante (ships dark, default OFF = byte-identisch). Vorfall
    # 14.08.: Nach einem Broker-Konto-Wechsel zÃ¤hlte der Slot-Check des Entscheidungs-
    # pfads 0/10 belegte Slots (leerer interner PM = "alles frei", fail-open) und
    # kaufte bei vollem Buch weiter â€” 12 Positionen statt 10. ON aktiviert drei
    # Bausteine: (1) Neukauf-Gate nur auf broker-verifizierter Positionszahl,
    # fail-closed wenn der Broker-Stand nicht bestÃ¤tigt werden kann; (2) Konto-
    # IdentitÃ¤ts-Tracking im PortfolioManager (Account-Wechsel â‡’ Reset + Rehydration);
    # (3) book_overflow-RÃ¼ckbau: Bestand > Obergrenze â‡’ BUY-Stopp + geordneter Abbau
    # Ã¼ber den bestehenden Rotations-Exit-Pfad (min-hold bindend, Session-Cap gilt,
    # Stop-Losses unberÃ¼hrt). Read via get_config() (CODING_POLICY Â§2.10).
    # Mirrored in config.oss.py (BORA dual-edition parity).
    # Default True per Owner-Entscheid 16.08. (Aktivierung im selben PR wie das
    # Feature); Rollback-Hebel: env False = byte-identisches Legacy-Verhalten.
    BOOK_CAP_ENFORCEMENT_ENABLED: bool = (
        os.getenv("BOOK_CAP_ENFORCEMENT_ENABLED", "True").lower() == "true"
    )

    # #2113 Decision-Outcome-Capture (Epic #1913 MLR, owner-only, NO egress) â€” all
    # default OFF (ships dark). Master flag: gates the durable, per-decision_id
    # `decision_outcomes` row (SQLite/Postgres, local only) AND the newly wired
    # execution-outcome codes (blocked:kill_switch / hitl_held). OFF (default) â‡’
    # no row is written, no new outcome code is emitted, the decision/execution
    # path is byte-identical to today. Finance-core reads this via get_config()
    # (CODING_POLICY Â§2.10). Mirrored in config.oss.py (dual-edition parity).
    # #2839: default Falseâ†’True = shipped desktop launcher profile (desktop is master;
    # capture stays local-only, NO egress â€” the flag never sends anything anywhere).
    DECISION_CAPTURE_ENABLED: bool = (
        os.getenv("DECISION_CAPTURE_ENABLED", "True").lower() == "true"
    )
    # Label-attribution job (Increment 2 activation â€” flag ships dormant here;
    # nothing reads it in Increment 1).
    DECISION_CAPTURE_ATTRIBUTION_ENABLED: bool = (
        os.getenv("DECISION_CAPTURE_ATTRIBUTION_ENABLED", "False").lower() == "true"
    )
    # Forward-return label horizon in trading days for the Inc-2 attribution job.
    DECISION_CAPTURE_FORWARD_RETURN_DAYS: int = int(
        os.getenv("DECISION_CAPTURE_FORWARD_RETURN_DAYS", "5") or "5"
    )

    # Security (I-2 #943 â€” Rogue Agent Hardening):
    # ENVIRONMENT is used by core/round_table/registry.py to enforce a hard
    # production guard that blocks plugin loading regardless of ALLOW_UNTRUSTED_PLUGINS.
    # Set to 'production' on Cloud Run via:
    #   gcloud run services update aaa-backend --set-env-vars ENVIRONMENT=production
    # Default: 'development' (OSS local â€” plugin guard inactive, safe for OSS dev).
    # Valid values: 'development' | 'staging' | 'production'
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")


os.makedirs(RuntimeConfigState.model_fields["DATA_DIR"].default, exist_ok=True)
os.makedirs(RuntimeConfigState.model_fields["USER_DATA_DIR"].default, exist_ok=True)


def _enforce_hitl_boot_gate(
    paper_trading: bool, hitl_enabled: bool, autonomous_unlimited: bool
) -> None:
    """EU AI Act Art. 14 (PR-0a): a real-money run MUST have a configured HITL policy.

    Raises at import if live trading is configured without HITL; logs a loud CRITICAL if
    live trading runs in Mode C (HITL on but no autonomous limits). Extracted as a function
    so it is unit-testable and mirrored verbatim in config.oss.py (M1 â€” the flat OSS edition
    defines all six HITL_* values before calling this).
    """
    if not paper_trading and not hitl_enabled:
        if os.getenv("DEPLOYMENT_MODE", "").upper() == "LOCAL":
            _config_state.PAPER_TRADING = True
            return
        raise RuntimeError(
            "CRITICAL COMPLIANCE: live trading (PAPER_TRADING=False) requires "
            "HITL_ENABLED=True (EU AI Act Art. 14 â€” human oversight of capital decisions)."
        )
    if not paper_trading and hitl_enabled and autonomous_unlimited:
        logging.critical(
            "COMPLIANCE: HITL_AUTONOMOUS_UNLIMITED on LIVE trading â€” EU AI Act Art. 14 "
            "Mode C (no autonomous limits). Ensure this is deliberate."
        )


# Singleton Instance
_config_state = RuntimeConfigState()
# Serialises every read-modify-write of the module-global _config_state (apply_remote_config +
# apply_hitl_policy_update). Without it two concurrent writers â€” e.g. a POST /api/hitl/policy
# racing a remote-config push, both running while the trading loop reads get_config() â€” can
# interleave model_dumpâ†’updateâ†’reassign and silently drop one update, leaving the Art-14 audit
# trail disagreeing with the surviving state (dev-env Â§2.8: new global mutators need a lock).
_config_lock = threading.Lock()
_enforce_hitl_boot_gate(
    _config_state.PAPER_TRADING,
    _config_state.HITL_ENABLED,
    _config_state.HITL_AUTONOMOUS_UNLIMITED,
)


# --- INF-13 Desktop Telemetry (P2 #1456) â€” all default OFF/safe -------------
# Mirror of config.oss.py (dual-edition parity). The desktop edition is where
# this telemetry runs; the cloud (K_SERVICE) path is unaffected. Egress is
# activation-gated (#1457). Module-level constants â€” read via getattr(config, â€¦).
# Consent â€” opt-in, default OFF (Â§25 TDDDG / Art.6 DSGVO, #1368 Gate â‘£):
TELEMETRY_CRASH_CONSENT = (
    os.getenv("TELEMETRY_CRASH_CONSENT", "False").lower() == "true"
)
TELEMETRY_USAGE_CONSENT = (
    os.getenv("TELEMETRY_USAGE_CONSENT", "False").lower() == "true"
)
# Egress master switch (client side of the activation gate) â€” default OFF:
TELEMETRY_EGRESS_ENABLED = (
    os.getenv("TELEMETRY_EGRESS_ENABLED", "False").lower() == "true"
)
# Local store retention (Art.5(1)(e)); consumed by core/telemetry_local.prune_store:
TELEMETRY_RETENTION_DAYS = float(os.getenv("TELEMETRY_RETENTION_DAYS", "7"))
TELEMETRY_RETENTION_MB = float(os.getenv("TELEMETRY_RETENTION_MB", "50"))
# OBS-2 (#2635): drop successful /health polls + OPTIONS CORS-preflights from the local
# telemetry store (~35 % of the export, near-zero signal). ERROR spans + trading spans are
# ALWAYS kept. Default ON. Mirrored in config.oss.py (BORA parity).
TELEMETRY_DROP_HEALTH_SPANS = (
    os.getenv("TELEMETRY_DROP_HEALTH_SPANS", "True").lower() == "true"
)
# INF-13 P2 (#2069) â€” execution/compliance block visibility in the diagnose-export.
# Emits a scrubbed OTel span per block so telemetry.jsonl (â†’ export) carries the reason.
# Observation-only, no capital-path effect; spans stay in the LOCAL store (egress is
# separately gated by TELEMETRY_EGRESS_ENABLED=False â€” TDDDG opt-in unchanged).
# #2839: default Falseâ†’True = shipped desktop launcher profile (desktop is master).
EXECUTION_BLOCK_TELEMETRY_ENABLED = (
    os.getenv("EXECUTION_BLOCK_TELEMETRY_ENABLED", "True").lower() == "true"
)


def get_config() -> RuntimeConfigState:
    return _config_state


def apply_remote_config(db_config: dict):
    global _config_state
    updates = {}

    if "gemini_model" in db_config:
        updates["GEMINI_MODEL_NAME"] = db_config["gemini_model"]
        if _config_state.GEMINI_MODEL_NAME != db_config["gemini_model"]:
            logging.info(
                f"Dynamic Config: GEMINI_MODEL_NAME updated to {db_config['gemini_model']}"
            )

    if "alpaca_paper" in db_config:
        updates["PAPER_TRADING"] = bool(db_config["alpaca_paper"])
        if _config_state.PAPER_TRADING != updates["PAPER_TRADING"]:
            logging.info(
                f"Dynamic Config: PAPER_TRADING updated to {updates['PAPER_TRADING']}"
            )

    # EU AI Act Art. 14 (PR-0a, E1): the boot gate fires only once at import. A remote
    # config push must NOT silently flip the engine to live trading without a configured
    # HITL policy â€” refuse the update fail-closed.
    would_be_live = not updates.get("PAPER_TRADING", _config_state.PAPER_TRADING)

    if would_be_live and not _config_state.HITL_ENABLED:
        logging.error(
            "CRITICAL COMPLIANCE: refused remote-config flip to live trading "
            "(PAPER_TRADING=False) without HITL_ENABLED=True (EU AI Act Art. 14)."
        )
        return

    if updates:
        # Create a new instance under the lock (atomic read-modify-write).
        with _config_lock:
            new_state_dict = _config_state.model_dump()
            new_state_dict.update(updates)
            _config_state = RuntimeConfigState(**new_state_dict)


# INC-1 F1a-UX: when a LIVE boot silently degrades to PAPER (shadow-boot pre-flight failed),
# the reason is recorded here so a health/status field can surface it to the operator instead
# of the failure being invisible. ``None`` = no degrade recorded. Set best-effort at boot; never
# a source of truth for trading mode (PAPER_TRADING is), purely an operator-visibility breadcrumb.
LIVE_ENABLE_FAILED_REASON: Optional[str] = None

# LSR R2 (#2253): True (best-effort at boot) when a live boot degraded to PAPER for a TRANSIENT
# reason (Alpaca 429/5xx/timeout, Redis timeout, Ollama-warming #11) â€” the WORM `enable` is KEPT so
# the next clean boot re-arms and retries. A TERMINAL degrade (Alpaca 401/403) instead writes a
# compensating WORM `disable` (core/live_worm.revoke_live) and leaves this False. Surfaced in
# /health next to LIVE_ENABLE_FAILED_REASON; never a source of truth for trading mode (PAPER_TRADING
# is), purely an operator-visibility breadcrumb.
DEGRADED_LIVE: bool = False


def force_paper_trading(reason: str = "") -> None:
    """Fail-closed runtime downgrade to PAPER trading (#1918).

    Used by the live-trading guard (core/engine/live_trading_guard.py) when a live boot was
    requested (PAPER_TRADING=False) but the resolved entitlement forbids live â€” e.g. the signed
    license token lapsed AFTER the operator armed live on the WORM chain. Degrading here (instead
    of the old hard RuntimeError) keeps the engine process alive so open real-money positions stay
    MANAGED (kill-switch/HITL/exits) and /api/live/disable stays reachable â€” no boot-loop.

    Rebuilds ``_config_state`` FRESH from the environment with ``PAPER_TRADING=true`` so
    ``_select_alpaca_account`` re-selects the PAPER Alpaca account / base URL / data feed and
    never the live keys. A ``model_dump()``-based update (as in ``apply_remote_config``) would
    carry the ALREADY-swapped live key forward â€” the validator only swaps paper->live, never the
    reverse â€” so we re-read the environment instead. Idempotent; a no-op flavour when already
    paper. Mirrored in config.oss.py (dual-edition parity; the OSS snapshot renames config.oss.py
    -> config.py, so the desktop â€” the only place this lapse occurs â€” relies on the mirror).
    """
    global _config_state
    with _config_lock:
        os.environ["PAPER_TRADING"] = "true"
        # Force the PAPER endpoint so a stale live ALPACA_BASE_URL override cannot survive the
        # rebuild: _select_alpaca_account keeps an explicit non-paper ALPACA_BASE_URL, and the
        # engine's primary broker derives paper/live from config.BASE_URL (-> ALPACA_BASE_URL via
        # __getattr__) in core/engine/api_routes.py::_init_trading_clients â€” not from PAPER_TRADING.
        os.environ["ALPACA_BASE_URL"] = "https://paper-api.alpaca.markets"
        _config_state = RuntimeConfigState()
    logging.critical(
        "CRITICAL: forced PAPER_TRADING downgrade%s â€” live order execution disabled; open "
        "positions remain managed (kill-switch/HITL/exits active).",
        f" ({reason})" if reason else "",
    )


# The five runtime-adjustable HITL policy limits (PR-0a-ii-6). HITL_ENABLED is deliberately
# absent â€” enabling HITL is the env+redeploy step (C2), never a runtime/API mutation.
_HITL_ADJUSTABLE = {
    "HITL_MAX_VALUE_PER_TRADE",
    "HITL_MAX_VALUE_PER_DAY",
    "HITL_AUTONOMOUS_UNLIMITED",
    "HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS",
    "HITL_EXPIRY_SECONDS",
}


def apply_hitl_policy_update(limits: dict) -> None:
    """Mutate the running HITL policy limits at runtime (PR-0a-ii-6 POST /api/hitl/policy).

    Rebuilds ``_config_state`` thread-safely (same pattern as ``apply_remote_config``), so
    ``get_config()`` reflects the new limits on the gate's next decision. Only the five
    adjustable values are honoured; ``HITL_ENABLED`` is never settable here (env-only, C2) â€” a
    stray key is ignored, defence-in-depth behind the ``extra="forbid"`` POST DTO.
    """
    global _config_state
    updates = {k: v for k, v in limits.items() if k in _HITL_ADJUSTABLE}
    if not updates:
        return
    # Atomic read-modify-write â€” never interleave with apply_remote_config or a concurrent
    # policy POST (else last-writer-wins drops an update; dev-env Â§2.8).
    with _config_lock:
        new_state_dict = _config_state.model_dump()
        new_state_dict.update(updates)
        _config_state = RuntimeConfigState(**new_state_dict)


def __getattr__(name: str) -> Any:
    try:
        return getattr(_config_state, name)
    except AttributeError:
        # Some variables like API_KEY are aliases in the old config. Let's provide them if needed.
        if name == "BASE_URL":
            return _config_state.ALPACA_BASE_URL
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_secret_str(value: Union["SecretStr", str, None]) -> str:
    """Extract a plain string from a Pydantic SecretStr or plain str value.

    This is the single, authoritative extraction point for all config secrets
    before they are passed to third-party clients (Alpaca SDK, Polygon HTTP,
    Gemini). Never scatter hasattr(v, 'get_secret_value') checks across the
    codebase â€” always call this function instead.

    Args:
        value: A ``pydantic.SecretStr`` instance, a plain ``str``, or ``None``.

    Returns:
        The underlying string value, or an empty string if ``value`` is None.

    Raises:
        TypeError: If ``value`` is neither a SecretStr, str, nor None.
    """
    if value is None:
        return ""
    if hasattr(value, "get_secret_value"):
        return value.get_secret_value()
    if isinstance(value, str):
        return value
    raise TypeError(
        f"config.get_secret_str() expected SecretStr or str, got {type(value).__name__!r}. "
        "Check that the config field is typed as Optional[SecretStr] or str."
    )


logging.getLogger("alpaca").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("h5py").setLevel(logging.WARNING)

from core.structured_logging import setup_logging  # noqa: E402

setup_logging()

GEMINI_AVAILABLE = False
if _config_state.GEMINI_API_KEY:
    try:
        from google import genai

        _test_client = genai.Client(
            api_key=(
                _config_state.GEMINI_API_KEY.get_secret_value()
                if _config_state.GEMINI_API_KEY
                else None
            )
        )

        logging.info(
            "Gemini AI successfully configured using google.genai (recommended)."
        )
        GEMINI_AVAILABLE = True
    except ImportError:
        logging.warning("Gemini library (google-genai) not installed.")
    except Exception:
        logging.exception("Failed to configure Gemini AI: ")
        GEMINI_AVAILABLE = False
else:
    logging.warning("Gemini API Key not found in .env file.")

if not GEMINI_AVAILABLE:
    logging.warning("Gemini AI features will be disabled/limited.")
