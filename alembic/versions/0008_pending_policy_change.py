"""pending_policy_change — four-eyes loosening approvals (ADR-SEC-06 #1598)

A loosening of an Iron Dome limit needs two distinct admins (initiator + one approver) plus a
cool-off; the pending request + its approvals are persisted here so they survive a restart.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# BORA-compatible JSON type (mirrors core/database/models.py).
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "pending_policy_change",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("initiator", sa.String(), nullable=True),
        sa.Column("requested_policy", JSON_TYPE, nullable=True),
        sa.Column("approvals", JSON_TYPE, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cooloff_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pending_policy_change_initiator"),
        "pending_policy_change",
        ["initiator"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_pending_policy_change_initiator"),
        table_name="pending_policy_change",
    )
    op.drop_table("pending_policy_change")
