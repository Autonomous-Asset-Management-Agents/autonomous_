"""decision_outcomes.regime_damp_factor + consensus_undamped — audit of the risk-off damping (#3618)

The regime conditioner now shrinks the direction consensus toward neutral in risk-off
(``consensus' = 0.5 + factor * (consensus - 0.5)``, upward only) for EVERY voter that
remains in the mean — no hand-kept name list. To measure its effect on the fills later,
the record carries the applied factor and the undamped consensus (None = no damping
this cycle). Additive & nullable; the desktop bootstrap heals the columns in place
(#3373, ``_ensure_additive_columns``).

Revision ID: 0017
Revises: 0016 (PR #3640 — this migration must land AFTER it; both are additive)
Create Date: 2026-09-24
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "decision_outcomes", sa.Column("regime_damp_factor", sa.Float(), nullable=True)
    )
    op.add_column(
        "decision_outcomes", sa.Column("consensus_undamped", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("decision_outcomes", "consensus_undamped")
    op.drop_column("decision_outcomes", "regime_damp_factor")
