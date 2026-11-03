"""decisions.sizing_binding_limit + sizing_cap_value — which cap bound the order (#3619 Teil 1)

The sizer already knows WHICH limit set the final size (``sizing_trace['binding_limit']``:
cash / compliance_order_value / position_cap / total_exposure_cap / kelly /
max_loss_per_trade) — but only in a log line. Since the size lever (vol targeting, and with
#3619 the settable ``VIX_SIZE_INFLUENCE``) can be neutralised by a cap, the decision record
must state the binding cap and its dollar value, so "applied == logged" (MiFID II Art. 17)
holds for the SIZE and a dead lever is visible in the data, not only in the fills.

Additive & nullable: legacy rows stay NULL; the desktop edition heals the columns in place
via the model-derived additive bootstrap (#3373, ``_ensure_additive_columns``).

Revision ID: 0016
Revises: 77f823ffc982 (Kopf der Kette: e144ec80d680 -> 77f823ffc982)
Create Date: 2026-09-24
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "77f823ffc982"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "decisions", sa.Column("sizing_binding_limit", sa.String(), nullable=True)
    )
    op.add_column("decisions", sa.Column("sizing_cap_value", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("decisions", "sizing_cap_value")
    op.drop_column("decisions", "sizing_binding_limit")
