"""add coverage gap map to reports

Revision ID: 77f823ffc982
Revises: e144ec80d680
Create Date: 2026-09-23 11:04:30.326874

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "77f823ffc982"
down_revision: Union[str, None] = "e144ec80d680"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "portfolio_metrics_reports",
        sa.Column("coverage_gap_map", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portfolio_metrics_reports", "coverage_gap_map")
