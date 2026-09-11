"""add system_config table

Provides a dynamic configuration table in Cloud SQL to store volatile
system parameters (like LLM version, feature flags) replacing static env vars.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import json

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── system_config ────────────────────────────────────────────────────────
    config_table = op.create_table(
        "system_config",
        sa.Column("config_key", sa.String(), nullable=False),
        sa.Column(
            "config_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("config_key"),
    )

    # Grant read/write rights to app_user
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN "
        "EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON system_config TO app_user;'; END IF; END $$;"
    )

    # Seed the initial necessary configurations
    initial_config = {"gemini_model": "gemini-2.5-flash", "alpaca_paper": True}

    op.bulk_insert(
        config_table,
        [
            {
                "config_key": "global_settings",
                "config_value": initial_config,
            }
        ],
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN "
        "EXECUTE 'REVOKE ALL ON system_config FROM app_user;'; END IF; END $$;"
    )
    op.drop_table("system_config")
