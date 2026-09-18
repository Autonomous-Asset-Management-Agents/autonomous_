"""Initial Cloud SQL schema — all 6 ORM tables

This migration creates the full production schema for GCP Cloud SQL PostgreSQL,
replacing the previous Cloud SQL setup. Tables cover MiFID II audit requirements
and EU AI Act transparency obligations.

Revision ID: 0001
Revises: -
Create Date: 2026-03-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── decisions ─────────────────────────────────────────────────────────────
    # MiFID II Art. 25 — every trading decision must be recorded
    op.create_table(
        "decisions",
        sa.Column("decision_id", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column(
            "decision_time",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("model_version_id", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("action_executed", sa.Boolean(), nullable=True),
        sa.Column("lstm_prediction", sa.Float(), nullable=True),
        sa.Column("rl_raw_action", sa.Float(), nullable=True),
        sa.Column("rl_stabilized_action", sa.Float(), nullable=True),
        sa.Column("conviction_score", sa.Float(), nullable=True),
        sa.Column("current_price", sa.Float(), nullable=True),
        sa.Column("vix_level", sa.Float(), nullable=True),
        sa.Column("market_regime", sa.String(), nullable=True),
        sa.Column("rsi_14", sa.Float(), nullable=True),
        sa.Column("macd", sa.Float(), nullable=True),
        sa.Column("macd_signal", sa.Float(), nullable=True),
        sa.Column("adx_14", sa.Float(), nullable=True),
        sa.Column("bb_pct", sa.Float(), nullable=True),
        sa.Column("volume_ratio", sa.Float(), nullable=True),
        sa.Column("volatility_20d", sa.Float(), nullable=True),
        sa.Column("atr_14d", sa.Float(), nullable=True),
        sa.Column("in_position", sa.Boolean(), nullable=True),
        sa.Column("position_qty", sa.Float(), nullable=True),
        sa.Column("position_avg_price", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl_pct", sa.Float(), nullable=True),
        sa.Column("risk_approved", sa.Boolean(), nullable=True),
        sa.Column("risk_reason", sa.Text(), nullable=True),
        sa.Column("risk_size_scaler", sa.Float(), nullable=True),
        sa.Column("risk_sl_multiplier", sa.Float(), nullable=True),
        sa.Column("portfolio_approved", sa.Boolean(), nullable=True),
        sa.Column("portfolio_reason", sa.Text(), nullable=True),
        sa.Column("portfolio_slot_used", sa.Float(), nullable=True),
        sa.Column("portfolio_max_slots", sa.Float(), nullable=True),
        sa.Column("symbol_to_close", sa.String(), nullable=True),
        sa.Column("intelligence_approved", sa.Boolean(), nullable=True),
        sa.Column("intelligence_reason", sa.Text(), nullable=True),
        sa.Column("reasoning_summary", sa.Text(), nullable=True),
        sa.Column("reasoning_trace", sa.Text(), nullable=True),
        sa.Column("trade_id", sa.String(), nullable=True),
        sa.Column("execution_price", sa.Float(), nullable=True),
        sa.Column("execution_qty", sa.Float(), nullable=True),
        sa.Column("inference_latency_ms", sa.Float(), nullable=True),
        sa.Column("is_simulation", sa.Boolean(), nullable=True),
        sa.Column("triggered_by_stop", sa.Boolean(), nullable=True),
        sa.Column("stop_type", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("decision_id"),
    )
    op.create_index("ix_decisions_symbol", "decisions", ["symbol"])
    op.create_index("ix_decisions_decision_time", "decisions", ["decision_time"])

    # ── trades ────────────────────────────────────────────────────────────────
    # MiFID II Art. 16 — trade records must reference the authorising decision
    op.create_table(
        "trades",
        sa.Column("trade_id", sa.String(), nullable=False),
        sa.Column(
            "decision_id",
            sa.String(),
            sa.ForeignKey("decisions.decision_id"),
            nullable=True,
        ),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("total_value", sa.Float(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("position_pnl", sa.Float(), nullable=True),
        sa.Column("position_pnl_pct", sa.Float(), nullable=True),
        sa.Column("hold_duration_hours", sa.Float(), nullable=True),
        sa.Column("order_type", sa.String(), nullable=True),
        sa.Column("time_in_force", sa.String(), nullable=True),
        sa.Column("order_status", sa.String(), nullable=True),
        sa.Column("account_id", sa.String(), nullable=True),
        sa.Column("strategy_name", sa.String(), nullable=True),
        sa.Column("is_simulation", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("trade_id"),
    )
    op.create_index("ix_trades_symbol", "trades", ["symbol"])
    op.create_index("ix_trades_decision_id", "trades", ["decision_id"])
    op.create_index("ix_trades_executed_at", "trades", ["executed_at"])

    # ── ai_thoughts ───────────────────────────────────────────────────────────
    # EU AI Act — AI reasoning traces must be stored for transparency
    op.create_table(
        "ai_thoughts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("thought_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("symbol", sa.String(), nullable=True),
        sa.Column("thought_type", sa.String(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "context_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("is_simulation", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_thoughts_symbol", "ai_thoughts", ["symbol"])
    op.create_index("ix_ai_thoughts_thought_time", "ai_thoughts", ["thought_time"])
    op.create_index(
        "idx_ai_thoughts_jsonb",
        "ai_thoughts",
        ["context_json"],
        postgresql_using="gin",
    )

    # ── risk_events ───────────────────────────────────────────────────────────
    op.create_table(
        "risk_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_type", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("trigger_value", sa.Float(), nullable=True),
        sa.Column("threshold_value", sa.Float(), nullable=True),
        sa.Column("equity_at_event", sa.Float(), nullable=True),
        sa.Column(
            "details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("is_simulation", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_risk_events_event_time", "risk_events", ["event_time"])
    op.create_index("ix_risk_events_event_type", "risk_events", ["event_type"])

    # ── mifid_decision_log ────────────────────────────────────────────────────
    # MiFID II Append-Only Audit Trail (no UPDATE/DELETE — enforced via IAM)
    op.create_table(
        "mifid_decision_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_type", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("trigger_value", sa.Float(), nullable=True),
        sa.Column("threshold_value", sa.Float(), nullable=True),
        sa.Column("equity_at_event", sa.Float(), nullable=True),
        sa.Column(
            "details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("is_simulation", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mifid_decision_log_event_time", "mifid_decision_log", ["event_time"]
    )
    op.create_index(
        "ix_mifid_decision_log_event_type", "mifid_decision_log", ["event_type"]
    )

    # ── portfolio_snapshots ───────────────────────────────────────────────────
    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_equity", sa.Float(), nullable=True),
        sa.Column("cash", sa.Float(), nullable=True),
        sa.Column(
            "positions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("strategy_name", sa.String(), nullable=True),
        sa.Column("is_simulation", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_portfolio_snapshots_timestamp", "portfolio_snapshots", ["timestamp"]
    )


def downgrade() -> None:
    op.drop_table("portfolio_snapshots")
    op.drop_table("mifid_decision_log")
    op.drop_table("risk_events")
    op.drop_table("ai_thoughts")
    op.drop_table("trades")
    op.drop_table("decisions")
