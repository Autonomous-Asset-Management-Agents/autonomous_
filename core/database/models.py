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
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    context_json = Column(JSONB)
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
    details_json = Column(JSONB)
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
    details_json = Column(JSONB)
    is_simulation = Column(Boolean)


class PortfolioSnapshot(Base):
    """Periodic snapshots of total equity, cash, and open positions."""

    __tablename__ = "portfolio_snapshots"

    id = Column(String, primary_key=True)
    timestamp = Column(DateTime(timezone=True), index=True)
    total_equity = Column(Float)
    cash = Column(Float)
    positions_json = Column(JSONB)
    strategy_name = Column(String)
    is_simulation = Column(Boolean)


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
    gatekeeper_approved = Column(Boolean, nullable=False)
    # Plain-text reason why the bot did NOT buy — for human review and ML labelling
    gatekeeper_reason = Column(Text)
    vote_count = Column(Integer)
    # Full per-agent detail: [{agent_name, score, weight, reasoning, vetoed}, ...]
    votes_json = Column(JSONB)
    is_simulation = Column(Boolean, nullable=False, server_default="false")


class SystemConfig(Base):
    """Dynamic configuration settings stored in the database.
    Replaces static env vars for volatile configs like LLM versions, paper flags, etc.
    """

    __tablename__ = "system_config"

    config_key = Column(String, primary_key=True)
    config_value = Column(JSONB, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
