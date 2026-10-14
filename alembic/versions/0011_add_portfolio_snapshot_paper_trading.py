"""portfolio_snapshots.paper_trading — paper-vs-live discriminator (PR-3)

is_simulation is BACKTEST-only (a paper account is a REAL Alpaca paper account -> is_simulation
is False for BOTH paper and live), so segregating the paper equity history from the live curve
needs its own flag. This adds a NULLABLE column with NO backfill and NO server_default:

  * Nullable so the additive ALTER never rewrites existing rows.
  * NO backfill — the readers treat legacy NULL as PAPER (fail-safe). See the PR body for the
    Enterprise/Postgres caveat: a tenant already LIVE before this column has NULL rows that would
    be read as paper (their real live history hidden). That backfill is an explicit, out-of-scope
    Enterprise decision — intentionally NOT performed here.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-19
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "portfolio_snapshots",
        sa.Column("paper_trading", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portfolio_snapshots", "paper_trading")
