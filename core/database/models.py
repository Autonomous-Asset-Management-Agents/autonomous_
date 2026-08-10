# core/database/models.py
# SQLAlchemy ORM models for GCP Cloud SQL PostgreSQL
# Schema managed via Alembic (alembic/versions/)
#
# Tables:
#   0001 - decisions, trades, ai_thoughts, risk_events, mifid_decision_log, portfolio_snapshots
#   0002 - WORM trigger + app_user permissions
#   0003 - round_table_sessions (per-agent votes + HOLD reasoning for ML training)

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

# BORA compatible JSON type for cross-dialect compilation
JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Decision(Base):
    """MiFID II Art. 25 — every trading decision recorded with full reasoning trace."""

    __tablename__ = "decisions"

    decision_id = Column(String, primary_key=True)
    symbol = Column(String, nullable=False, index=True)
    decision_time = Column(DateTime(timezone=True), index=True)
    model_version_id = Column(String)
    action = Column(String, nullable=False)  # BUY / SELL / HOLD
    action_executed = Column(Boolean)
    lstm_prediction = Column(Float)
    rl_raw_action = Column(Float)
    rl_stabilized_action = Column(Float)
    conviction_score = Column(Float)
    current_price = Column(Float)
    vix_level = Column(Float)
    market_regime = Column(String)
    rsi_14 = Column(Float)
    macd = Column(Float)
    macd_signal = Column(Float)
    adx_14 = Column(Float)
    bb_pct = Column(Float)
    volume_ratio = Column(Float)
    volatility_20d = Column(Float)
    atr_14d = Column(Float)
    in_position = Column(Boolean)
    position_qty = Column(Float)
    position_avg_price = Column(Float)
    unrealized_pnl = Column(Float)
    unrealized_pnl_pct = Column(Float)
    risk_approved = Column(Boolean)
    risk_reason = Column(Text)
    risk_size_scaler = Column(Float)
    risk_sl_multiplier = Column(Float)
    portfolio_approved = Column(Boolean)
    portfolio_reason = Column(Text)
    portfolio_slot_used = Column(Float)
    portfolio_max_slots = Column(Float)
    symbol_to_close = Column(String)
    intelligence_approved = Column(Boolean)
    intelligence_reason = Column(Text)
    reasoning_summary = Column(Text)
    reasoning_trace = Column(Text)
    trade_id = Column(String)
    execution_price = Column(Float)
    execution_qty = Column(Float)
    inference_latency_ms = Column(Float)
    is_simulation = Column(Boolean)
    triggered_by_stop = Column(Boolean)
    stop_type = Column(String)


class Trade(Base):
    """MiFID II Art. 16 — executed trades reference their authorising decision."""

    __tablename__ = "trades"

    trade_id = Column(String, primary_key=True)
    decision_id = Column(String, ForeignKey("decisions.decision_id"), index=True)
    symbol = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)  # buy / sell
    qty = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    total_value = Column(Float)
    executed_at = Column(DateTime(timezone=True), index=True)
    entry_price = Column(Float)
    position_pnl = Column(Float)
    position_pnl_pct = Column(Float)
    hold_duration_hours = Column(Float)
    order_type = Column(String)
    time_in_force = Column(String)
    order_status = Column(String)
    account_id = Column(String)
    strategy_name = Column(String)
    is_simulation = Column(Boolean)


class AIThought(Base):
    """EU AI Act — AI reasoning traces stored for transparency audit."""

    __tablename__ = "ai_thoughts"

    id = Column(String, primary_key=True)
    thought_time = Column(DateTime(timezone=True), index=True)
    symbol = Column(String, index=True)
    thought_type = Column(String)
    message = Column(Text)
    context_json = Column(JSON_TYPE)
    is_simulation = Column(Boolean)


class RiskEvent(Base):
    """Risk Manager events: kill-switch triggers, drawdown breaches, etc."""

    __tablename__ = "risk_events"

    id = Column(String, primary_key=True)
    event_time = Column(DateTime(timezone=True), index=True)
    event_type = Column(String, index=True)
    severity = Column(String)
    message = Column(Text)
    trigger_value = Column(Float)
    threshold_value = Column(Float)
    equity_at_event = Column(Float)
    details_json = Column(JSON_TYPE)
    is_simulation = Column(Boolean)


class MifidDecisionLog(Base):
    """MiFID II append-only audit trail — WORM-protected via DB trigger (migration 0002)."""

    __tablename__ = "mifid_decision_log"

    id = Column(String, primary_key=True)
    event_time = Column(DateTime(timezone=True), index=True)
    event_type = Column(String, index=True)
    severity = Column(String)
    message = Column(Text)
    user_id = Column(String)
    trigger_value = Column(Float)
    threshold_value = Column(Float)
    equity_at_event = Column(Float)
    details_json = Column(JSON_TYPE)
    is_simulation = Column(Boolean)


class IronDomePolicyAudit(Base):
    """ADR-SEC-06 (#1597): append-only queryable mirror of admin Iron Dome policy changes.

    The primary tamper-evident trail is the EU AI Act Art-14 hash chain (``log_policy_event``);
    this table is the **queryable mirror** — one row per change for ops / audit queries.
    """

    __tablename__ = "iron_dome_policy_audit"

    id = Column(String, primary_key=True)
    event_time = Column(DateTime(timezone=True), index=True)
    actor = Column(String, index=True)
    old_policy = Column(JSON_TYPE)
    new_policy = Column(JSON_TYPE)
    reason = Column(Text, nullable=True)


class PendingPolicyChange(Base):
    """ADR-SEC-06 (#1598): a loosening request awaiting a second admin approval (four-eyes).

    Persisted so a pending change + its approvals survive a restart and carry an
    initiator-vs-approver audit trail (MiFID II segregation of duties).
    """

    __tablename__ = "pending_policy_change"

    id = Column(String, primary_key=True)
    initiator = Column(String, index=True)
    requested_policy = Column(JSON_TYPE)
    approvals = Column(JSON_TYPE)  # list of distinct approver ids
    created_at = Column(DateTime(timezone=True))
    cooloff_until = Column(DateTime(timezone=True))
    applied = Column(Boolean, default=False)


class PortfolioSnapshot(Base):
    """Periodic snapshots of total equity, cash, and open positions."""

    __tablename__ = "portfolio_snapshots"

    id = Column(String, primary_key=True)
    timestamp = Column(DateTime(timezone=True), index=True)
    total_equity = Column(Float)
    cash = Column(Float)
    positions_json = Column(JSON_TYPE)
    strategy_name = Column(String)
    is_simulation = Column(Boolean)
    # PR-3: paper-vs-live discriminator. is_simulation is BACKTEST-only (paper runs on a REAL
    # Alpaca paper account -> is_simulation=False for BOTH paper and live), so segregating the
    # paper equity history from the live curve needs its own flag. Nullable: legacy rows written
    # before this column predate any live run -> treated as PAPER by the readers (fail-safe).
    paper_trading = Column(Boolean, nullable=True)
    is_sim_day = Column(Boolean, nullable=True, default=False)


class RoundTableSession(Base):
    """Per-symbol RoundTable evaluation — every decision including HOLD/NONE.

    Primary source for ML training and accountability:
    - WHY did the bot buy / sell / hold / not buy?
    - Which agent had which opinion (score, weight, reasoning)?
    - votes_json: [{agent_name, score, weight, reasoning, vetoed}, ...]

    Written by SenateProtocol._log_to_database() via CloudLogger.log_senate_session().
    One row per symbol per trading cycle.
    """

    __tablename__ = "round_table_sessions"

    session_id = Column(String, primary_key=True)
    session_time = Column(DateTime(timezone=True), nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    consensus_score = Column(Float, nullable=False, index=True)
    # BUY / SELL / HOLD / NONE  (NONE = score in 0.35–0.65 or gatekeeper vetoed)
    signal_action = Column(String, index=True)
    is_sim_day = Column(Boolean, nullable=True, default=False)
    gatekeeper_approved = Column(Boolean, nullable=False)
    # Plain-text reason why the bot did NOT buy — for human review and ML labelling
    gatekeeper_reason = Column(Text)
    vote_count = Column(Integer)
    # Full per-agent detail: [{agent_name, score, weight, reasoning, vetoed}, ...]
    votes_json = Column(JSON_TYPE)
    is_simulation = Column(Boolean, nullable=False, server_default="false")


class DecisionOutcome(Base):
    """#2113 (Epic #1913 MLR G5/G6): one durable capture row per Round-Table
    decision — Decision-Kern + Eval-Inputs + Execution-Outcome + (nachlaufende)
    Labels, joinable per ``decision_id``.

    Deliberately SEPARATE from the immutable MiFID II Art. 25 ``decisions``
    record (plan §3 Option A): labels are appended HERE hours later, so the
    append-only audit record is never mutated (WORM posture preserved). Written
    by ``CloudLogger.log_decision_outcome()`` behind ``DECISION_CAPTURE_ENABLED``
    (default OFF — owner-only, local DB, no egress). Desktop: bootstrapped via
    ``create_all()``; Enterprise Postgres: Alembic 0012.
    """

    __tablename__ = "decision_outcomes"

    decision_id = Column(String, ForeignKey("decisions.decision_id"), primary_key=True)
    session_id = Column(String, index=True)
    symbol = Column(String, nullable=False, index=True)
    decision_time = Column(DateTime(timezone=True), index=True)
    # Round-Table detail (runner.py serialized_votes — R-A umgangen, plan §5.3)
    consensus_score = Column(Float)
    signal_action = Column(String)  # BUY / SELL / HOLD / NONE
    gatekeeper_approved = Column(Boolean)
    gatekeeper_reason = Column(Text)
    votes_json = Column(JSON_TYPE)
    # Eval-Inputs (copied from the DecisionContext — #2389 fills them with real
    # TA scalars; until then these are today's defaults — fail-open, no compute)
    voted_price = Column(Float)
    cycle_open = Column(Float)
    cycle_high = Column(Float)
    cycle_low = Column(Float)
    cycle_close = Column(Float)
    vix_level = Column(Float)
    market_regime = Column(String)
    # Execution outcome (durable, per decision_id — replaces the in-memory
    # display store join for the capture record)
    execution_outcome = Column(String)  # executed / blocked:* / hitl_held / ...
    execution_reason = Column(Text)
    # Labels (nightly / on-demand attribution job, Increment 2 — nullable until filled)
    pnl = Column(Float)
    pnl_pct = Column(Float)
    hold_duration_hours = Column(Float)
    exit_reason = Column(String)
    forward_return_5d = Column(Float)
    # Lineage (4 columns — reproducibility: which code/config/features decided)
    model_version_id = Column(String)
    git_sha = Column(String)
    config_sha = Column(String)
    feature_list_hash = Column(String)
    is_simulation = Column(Boolean, nullable=False, server_default="false")


class LstmPanelSnapshot(Base):
    """#2683 — the durable half of the LSTM rank panel (``core/report/lstm_panel_store``).

    WHY THIS TABLE EXISTS: the panel store is an in-memory singleton and its only writer
    records ONE snapshot per calendar day. Since #2680 the ranking basis of every consumer
    (BUY vote, rotation SELL trigger, deep-eval funnel, report card) is the per-symbol
    MEDIAN over the last N snapshot DATES — so a restart, which empties the store, reduces
    that median to a single sample, i.e. back to the point-in-time value the smoothing was
    written to replace. The operations engine restarts at least daily, so without this the
    smoothing is inert exactly when it matters.

    One row per (day, symbol). ``snapshot_date`` is a plain calendar DATE (naive), matching
    the store's own normalisation — never a timestamp, so a same-day re-record is an upsert
    on the natural key rather than a second row.

    Retention is bounded by the writer (the store prunes to ``SNAPSHOT_HISTORY_LEN`` days),
    so the table stays at roughly ``20 × universe`` rows — a few thousand, not a growth risk.
    """

    __tablename__ = "lstm_panel_snapshots"

    snapshot_date = Column(Date, primary_key=True, index=True)
    symbol = Column(String, primary_key=True, index=True)
    score = Column(Float, nullable=False)


class SystemConfig(Base):
    """Dynamic configuration settings stored in the database.
    Replaces static env vars for volatile configs like LLM versions, paper flags, etc.
    """

    __tablename__ = "system_config"

    config_key = Column(String, primary_key=True)
    config_value = Column(JSON_TYPE, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class EntitlementToken(Base):
    """GTM-1 (#1840) Brick 2: a minted tier-entitlement token persisted after a paid checkout.

    One row per successful Stripe ``checkout.session.completed``. ``stripe_session_id`` is
    UNIQUE and used as the idempotency key — Stripe retries webhooks, so a duplicate delivery
    must NOT re-mint (the pre-check + unique constraint guarantee exactly one row per session).

    BORA: portable, SQLite-safe types ONLY (Integer/String/Text/DateTime) — no Postgres-only
    SERIAL/UUID/JSONB. The token itself is signed by the issuer; the raw licensee id is never
    stored (only ``issued_to_hash``). Never log ``token``.
    """

    __tablename__ = "entitlement_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    issued_to_hash = Column(String, nullable=False)
    tier = Column(String, nullable=False)
    token = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True))
    # Idempotency key: Stripe retries webhooks, so a repeated session id must be rejected.
    stripe_session_id = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True))


class UserWallet(Base):
    """User wallet metadata — broker account links, status, and risk limits.

    OSS-4 / #1085: Migrated from raw asyncpg DDL in user_wallet_store.py
    to ORM model for BORA dual-mode (SQLite + PostgreSQL).
    """

    __tablename__ = "user_wallets"

    user_id = Column(String(255), primary_key=True)
    broker_account_id = Column(String(255))
    secret_manager_id = Column(String(255), nullable=False)
    status = Column(String(50), default="inactive")  # active, inactive, halted
    risk_limits = Column(JSON_TYPE, default=dict)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))
