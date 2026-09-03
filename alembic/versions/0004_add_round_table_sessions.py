"""add round_table_sessions table

Persists every RoundTable evaluation to Cloud SQL — including all 9 agent
votes with individual scores, weights, and reasoning. Enables:
  - ML training: which agent patterns lead to profitable trades?
  - Accountability: why did the bot NOT buy a specific symbol?

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── round_table_sessions ──────────────────────────────────────────────────
    # One row per symbol per trading cycle — every decision, including HOLD/NONE.
    # votes_json: list of {agent_name, score, weight, reasoning, vetoed}
    op.create_table(
        "round_table_sessions",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("session_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("consensus_score", sa.Float(), nullable=False),
        # signal_action: BUY / SELL / HOLD / NONE
        # NONE = score in neutral zone (0.35–0.65) or gatekeeper vetoed
        sa.Column("signal_action", sa.String(), nullable=True),
        sa.Column("gatekeeper_approved", sa.Boolean(), nullable=False),
        # Why the bot did NOT buy — "Position limit reached", "VIX too high", etc.
        sa.Column("gatekeeper_reason", sa.Text(), nullable=True),
        sa.Column("vote_count", sa.Integer(), nullable=True),
        # Full per-agent detail: [{agent_name, score, weight, reasoning, vetoed}, ...]
        sa.Column(
            "votes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "is_simulation", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.PrimaryKeyConstraint("session_id"),
    )

    # Indexes tuned for ML queries and HOLD-reason analysis
    op.create_index("ix_rts_symbol", "round_table_sessions", ["symbol"])
    op.create_index("ix_rts_session_time", "round_table_sessions", ["session_time"])
    op.create_index("ix_rts_signal_action", "round_table_sessions", ["signal_action"])
    op.create_index("ix_rts_consensus", "round_table_sessions", ["consensus_score"])
    op.create_index(
        "idx_rts_votes_jsonb",
        "round_table_sessions",
        ["votes_json"],
        postgresql_using="gin",
    )

    # Grant INSERT rights to app_user (matches 0002 migration pattern)
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN "
        "EXECUTE 'GRANT SELECT, INSERT ON round_table_sessions TO app_user;'; END IF; END $$;"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN "
        "EXECUTE 'REVOKE ALL ON round_table_sessions FROM app_user;'; END IF; END $$;"
    )
    op.drop_index("idx_rts_votes_jsonb", table_name="round_table_sessions")
    op.drop_index("ix_rts_consensus", table_name="round_table_sessions")
    op.drop_index("ix_rts_signal_action", table_name="round_table_sessions")
    op.drop_index("ix_rts_session_time", table_name="round_table_sessions")
    op.drop_index("ix_rts_symbol", table_name="round_table_sessions")
    op.drop_table("round_table_sessions")
