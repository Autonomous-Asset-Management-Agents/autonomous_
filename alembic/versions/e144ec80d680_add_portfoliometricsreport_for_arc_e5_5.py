"""Add PortfolioMetricsReport for ARC-E5.5

Revision ID: e144ec80d680
Revises: 0015
Create Date: 2026-09-22 06:32:45.677296

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e144ec80d680"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy.dialects import postgresql

    op.create_table(
        "portfolio_metrics_reports",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline", sa.String(), nullable=True),
        sa.Column("paper_trading", sa.Boolean(), nullable=True),
        sa.Column("initial_capital", sa.Float(), nullable=True),
        sa.Column("final_equity", sa.Float(), nullable=True),
        sa.Column("twr_pct", sa.Float(), nullable=True),
        sa.Column("max_drawdown_pct", sa.Float(), nullable=True),
        sa.Column("win_rate_pct", sa.Float(), nullable=True),
        sa.Column(
            "decision_ids",
            sa.JSON().with_variant(postgresql.JSONB, "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "order_ids",
            sa.JSON().with_variant(postgresql.JSONB, "postgresql"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_portfolio_metrics_reports_timestamp"),
        "portfolio_metrics_reports",
        ["timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_portfolio_metrics_reports_timestamp"),
        table_name="portfolio_metrics_reports",
    )
    op.drop_table("portfolio_metrics_reports")
