"""decision_outcomes — durable per-decision capture row (#2113, Epic #1913 MLR)

One row per Round-Table decision, keyed + FK-joined by decision_id: Decision-Kern,
Eval-Inputs (votes_json / voted_price / cycle OHLC / vix / regime), the durable
execution outcome, nullable labels (Inc-2 attribution) and the Lineage-4
(model_version_id / git_sha / config_sha / feature_list_hash).

Deliberately a SEPARATE table (plan §3 Option A): the immutable MiFID II Art. 25
`decisions` audit record is never mutated — labels append HERE. Enterprise/
Postgres only; the desktop edition bootstraps the same ORM model via
create_all() (CLAUDE.md §5.4 — no Alembic on SQLite). Writes are gated by
DECISION_CAPTURE_ENABLED (default OFF) — the table ships dark.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decision_outcomes",
        sa.Column(
            "decision_id",
            sa.String(),
            sa.ForeignKey("decisions.decision_id"),
            primary_key=True,
        ),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("decision_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consensus_score", sa.Float(), nullable=True),
        sa.Column("signal_action", sa.String(), nullable=True),
        sa.Column("gatekeeper_approved", sa.Boolean(), nullable=True),
        sa.Column("gatekeeper_reason", sa.Text(), nullable=True),
        sa.Column("votes_json", JSONB(), nullable=True),
        sa.Column("voted_price", sa.Float(), nullable=True),
        sa.Column("cycle_open", sa.Float(), nullable=True),
        sa.Column("cycle_high", sa.Float(), nullable=True),
        sa.Column("cycle_low", sa.Float(), nullable=True),
        sa.Column("cycle_close", sa.Float(), nullable=True),
        sa.Column("vix_level", sa.Float(), nullable=True),
        sa.Column("market_regime", sa.String(), nullable=True),
        sa.Column("execution_outcome", sa.String(), nullable=True),
        sa.Column("execution_reason", sa.Text(), nullable=True),
        sa.Column("pnl", sa.Float(), nullable=True),
        sa.Column("pnl_pct", sa.Float(), nullable=True),
        sa.Column("hold_duration_hours", sa.Float(), nullable=True),
        sa.Column("exit_reason", sa.String(), nullable=True),
        sa.Column("forward_return_5d", sa.Float(), nullable=True),
        sa.Column("model_version_id", sa.String(), nullable=True),
        sa.Column("git_sha", sa.String(), nullable=True),
        sa.Column("config_sha", sa.String(), nullable=True),
        sa.Column("feature_list_hash", sa.String(), nullable=True),
        sa.Column(
            "is_simulation", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.create_index(
        "ix_decision_outcomes_session_id", "decision_outcomes", ["session_id"]
    )
    op.create_index("ix_decision_outcomes_symbol", "decision_outcomes", ["symbol"])
    op.create_index(
        "ix_decision_outcomes_decision_time", "decision_outcomes", ["decision_time"]
    )


def downgrade() -> None:
    op.drop_index("ix_decision_outcomes_decision_time", table_name="decision_outcomes")
    op.drop_index("ix_decision_outcomes_symbol", table_name="decision_outcomes")
    op.drop_index("ix_decision_outcomes_session_id", table_name="decision_outcomes")
    op.drop_table("decision_outcomes")
