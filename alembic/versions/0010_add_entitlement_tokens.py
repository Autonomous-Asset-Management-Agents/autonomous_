"""entitlement_tokens — minted tier tokens persisted after a paid Stripe checkout (GTM-1 #1840)

Brick 2: the Stripe ``checkout.session.completed`` webhook mints a signed tier token and
persists one row here. ``stripe_session_id`` is UNIQUE — the idempotency key that de-dupes
Stripe's webhook retries (a repeated session id must never re-mint).

Portable / SQLite-safe columns ONLY (Integer PK / String / Text / DateTime) so the same DDL
runs on desktop SQLite AND Cloud SQL Postgres — no SERIAL/UUID/JSONB.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-08
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entitlement_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("issued_to_hash", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stripe_session_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stripe_session_id", name="uq_entitlement_tokens_stripe_session_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("entitlement_tokens")
