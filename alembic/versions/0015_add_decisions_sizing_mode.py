"""decisions.sizing_mode + sizing_target_weight — MiFID II Art. 17 „applied == logged" (#3284 Punkt 3)

Records WHICH sizing procedure formed each order's size ("conviction" | "a" | "b") and the
target base weight (before caps). Since clean-weight "b" became the default sizing
(#3284, Owner-Waiver 2026-09-09), ``decisions.risk_size_scaler`` alone is mode-ambiguous:
the same scaler could stem from the 7-factor conviction path or from 1/N × vol. These two
additive, nullable columns make every capital decision's sizing basis unambiguously
reconstructable — a prerequisite before real-money trading under the new default.

Additive & nullable: legacy rows stay NULL; the cloud-logger DB writer already tolerates a
schema without these columns (it drops unbacked keys), so this migration only adds durable
Enterprise/Postgres persistence. The desktop edition bootstraps the same ORM model via
create_all() (CLAUDE.md §5.4 — no Alembic on SQLite).

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("decisions", sa.Column("sizing_mode", sa.String(), nullable=True))
    op.add_column(
        "decisions", sa.Column("sizing_target_weight", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("decisions", "sizing_target_weight")
    op.drop_column("decisions", "sizing_mode")
