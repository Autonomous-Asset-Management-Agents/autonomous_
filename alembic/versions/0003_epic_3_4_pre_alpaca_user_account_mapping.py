"""Epic 3.4-pre: Alpaca User-Account Mapping — DB Schema Migration

Adds three new tables to Cloud SQL for secure 1:1 Alpaca account mapping:
  - user_alpaca_accounts: Firebase UID → Alpaca secret_ref mapping
  - user_roles: RBAC (admin/trader/readonly)
  - alpaca_account_audit_log: MiFID II compliant audit trail

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── user_alpaca_accounts ──────────────────────────────────────────────────
    # Secure 1:1 mapping: Firebase UID → Alpaca account metadata.
    # Credentials are NEVER stored here — only secret_ref (GCP Secret Manager key prefix).
    op.create_table(
        "user_alpaca_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("firebase_uid", sa.Text(), nullable=False),
        sa.Column(
            "account_type",
            sa.Text(),
            nullable=False,
            comment="Must be 'paper' or 'live'",
        ),
        sa.Column(
            "secret_ref",
            sa.Text(),
            nullable=False,
            comment="GCP Secret Manager prefix, e.g. 'alpaca-{uid}'. Never store actual keys here.",
        ),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "account_type IN ('paper', 'live')",
            name="ck_user_alpaca_accounts_account_type",
        ),
        sa.UniqueConstraint(
            "firebase_uid",
            "account_type",
            "is_active",
            name="uq_user_alpaca_accounts_uid_type_active",
        ),
    )
    op.create_index(
        "ix_user_alpaca_accounts_firebase_uid",
        "user_alpaca_accounts",
        ["firebase_uid"],
    )
    op.create_index(
        "ix_user_alpaca_accounts_is_active",
        "user_alpaca_accounts",
        ["is_active"],
    )

    # ── user_roles ────────────────────────────────────────────────────────────
    # RBAC: determines who can call /admin/users/{uid}/alpaca-account endpoints.
    # Default deny — absence of row means no access.
    op.create_table(
        "user_roles",
        sa.Column("firebase_uid", sa.Text(), nullable=False),
        sa.Column(
            "role",
            sa.Text(),
            nullable=False,
            comment="One of: admin, trader, readonly",
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "granted_by",
            sa.Text(),
            nullable=True,
            comment="Firebase UID of the granting admin",
        ),
        sa.PrimaryKeyConstraint("firebase_uid"),
        sa.CheckConstraint(
            "role IN ('admin', 'trader', 'readonly')",
            name="ck_user_roles_role",
        ),
    )

    # ── alpaca_account_audit_log ──────────────────────────────────────────────
    # MiFID II compliant append-only audit trail for all account mapping operations.
    # Rows must never be deleted or updated (enforce via IAM / db role).
    op.create_table(
        "alpaca_account_audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "action",
            sa.Text(),
            nullable=False,
            comment="created | revoked | accessed | access_denied",
        ),
        sa.Column("firebase_uid", sa.Text(), nullable=False),
        sa.Column("actor_uid", sa.Text(), nullable=False),
        sa.Column("account_type", sa.Text(), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "details_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_alpaca_account_audit_log_firebase_uid",
        "alpaca_account_audit_log",
        ["firebase_uid"],
    )
    op.create_index(
        "ix_alpaca_account_audit_log_timestamp",
        "alpaca_account_audit_log",
        ["timestamp"],
    )
    op.create_index(
        "ix_alpaca_account_audit_log_action",
        "alpaca_account_audit_log",
        ["action"],
    )


def downgrade() -> None:
    op.drop_table("alpaca_account_audit_log")
    op.drop_table("user_roles")
    op.drop_table("user_alpaca_accounts")
