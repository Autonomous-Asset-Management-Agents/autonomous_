"""iron_dome_policy_audit — queryable mirror of admin policy changes (ADR-SEC-06 #1597)

The EU AI Act Art-14 hash chain is the primary tamper-evident trail; this table is the
queryable mirror — one row per admin Iron Dome policy change.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# BORA-compatible JSON type (mirrors core/database/models.py).
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "iron_dome_policy_audit",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("old_policy", JSON_TYPE, nullable=True),
        sa.Column("new_policy", JSON_TYPE, nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_iron_dome_policy_audit_event_time"),
        "iron_dome_policy_audit",
        ["event_time"],
        unique=False,
    )
    op.create_index(
        op.f("ix_iron_dome_policy_audit_actor"),
        "iron_dome_policy_audit",
        ["actor"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_iron_dome_policy_audit_actor"),
        table_name="iron_dome_policy_audit",
    )
    op.drop_index(
        op.f("ix_iron_dome_policy_audit_event_time"),
        table_name="iron_dome_policy_audit",
    )
    op.drop_table("iron_dome_policy_audit")
