"""lemonsqueezy_licenses — offline tokens minted after a paid Lemon Squeezy order (GTM-2 #1809)

T7: the Lemon Squeezy ``order_created`` / ``subscription_created`` webhook mints a signed
Ed25519 Senior token and persists one row here. ``order_identifier`` (the LS order UUID) is
UNIQUE — it is BOTH the idempotency key that de-dupes LS webhook retries AND the unguessable
capability the success page presents to fetch the token (T6). Kept separate from
``entitlement_tokens`` (the Stripe path) so neither destabilises the other.

Portable / SQLite-safe columns ONLY (Integer PK / String / Text / DateTime) so the same DDL
runs on desktop SQLite AND Cloud SQL Postgres — no SERIAL/UUID/JSONB.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-25
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lemonsqueezy_licenses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_identifier", sa.String(), nullable=False),
        sa.Column("issued_to_hash", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_identifier", name="uq_lemonsqueezy_licenses_order_identifier"
        ),
    )


def downgrade() -> None:
    op.drop_table("lemonsqueezy_licenses")
