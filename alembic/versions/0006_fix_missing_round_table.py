"""fix missing round_table_sessions

Safely and idempotently creates the round_table_sessions table if it is missing
due to a manual stamp operation on the production database.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-20
"""

from typing import Sequence, Union
import logging

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)

    if not insp.has_table("round_table_sessions"):
        logging.info("Table round_table_sessions missing, creating it now.")
        op.create_table(
            "round_table_sessions",
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("session_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("symbol", sa.String(), nullable=False),
            sa.Column("consensus_score", sa.Float(), nullable=False),
            sa.Column("signal_action", sa.String(), nullable=True),
            sa.Column("gatekeeper_approved", sa.Boolean(), nullable=False),
            sa.Column("gatekeeper_reason", sa.Text(), nullable=True),
            sa.Column("vote_count", sa.Integer(), nullable=True),
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

        op.create_index("ix_rts_symbol", "round_table_sessions", ["symbol"])
        op.create_index("ix_rts_session_time", "round_table_sessions", ["session_time"])
        op.create_index(
            "ix_rts_signal_action", "round_table_sessions", ["signal_action"]
        )
        op.create_index("ix_rts_consensus", "round_table_sessions", ["consensus_score"])
        op.create_index(
            "idx_rts_votes_jsonb",
            "round_table_sessions",
            ["votes_json"],
            postgresql_using="gin",
        )

        op.execute("GRANT SELECT, INSERT ON round_table_sessions TO app_user;")
    else:
        logging.info("Table round_table_sessions already exists, skipping creation.")


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if insp.has_table("round_table_sessions"):
        op.execute("REVOKE ALL ON round_table_sessions FROM app_user;")
        op.drop_index("idx_rts_votes_jsonb", table_name="round_table_sessions")
        op.drop_index("ix_rts_consensus", table_name="round_table_sessions")
        op.drop_index("ix_rts_signal_action", table_name="round_table_sessions")
        op.drop_index("ix_rts_session_time", table_name="round_table_sessions")
        op.drop_index("ix_rts_symbol", table_name="round_table_sessions")
        op.drop_table("round_table_sessions")
