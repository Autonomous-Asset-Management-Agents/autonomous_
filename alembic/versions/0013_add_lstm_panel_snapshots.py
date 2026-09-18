"""lstm_panel_snapshots — durable LSTM rank panel (#2683, Epic #1957 TRD)

One row per (snapshot_date, symbol): the engine's daily LSTM cross-section score.

WHY THE TABLE EXISTS: `core/report/lstm_panel_store.LstmPanelStore` is an in-memory
singleton whose only writer records ONE snapshot per calendar day. Since #2680 the
ranking basis of every consumer (the LSTM BUY vote, the rotation SELL trigger, the
deep-eval funnel, the report card — all via `active_cross_section`) is the per-symbol
MEDIAN over the last N snapshot DATES. A restart empties the store, so the median
collapses to a single sample — i.e. back to the point-in-time value the smoothing was
written to replace. The operations engine restarts at least daily, so without this the
smoothing is inert exactly when it matters.

`snapshot_date` is a plain calendar DATE (not a timestamp), matching the store's own
normalisation, so a same-day re-record is an upsert on the natural key instead of a
second row. Retention is enforced by the writer (the store prunes to
SNAPSHOT_HISTORY_LEN days and calls `prune_before`), keeping the table at roughly
`20 × universe` rows.

Enterprise/Postgres only; the desktop edition bootstraps the same ORM model via
create_all() (CLAUDE.md §5.4 — no Alembic on SQLite). Writes are gated by
LSTM_PANEL_PERSISTENCE_ENABLED (default ON — see ADR-RANK-02 in config.py: shipping it
off would ship the known-ineffective state) and are best-effort: a DB failure costs
telemetry, never the trading cycle.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lstm_panel_snapshots",
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_date", "symbol"),
    )
    # The read path selects the whole (bounded) table and the retention path deletes by
    # date; the composite PK already covers both, these only speed the date scan.
    op.create_index(
        "ix_lstm_panel_snapshots_snapshot_date",
        "lstm_panel_snapshots",
        ["snapshot_date"],
    )
    op.create_index(
        "ix_lstm_panel_snapshots_symbol", "lstm_panel_snapshots", ["symbol"]
    )


def downgrade() -> None:
    op.drop_index("ix_lstm_panel_snapshots_symbol", table_name="lstm_panel_snapshots")
    op.drop_index(
        "ix_lstm_panel_snapshots_snapshot_date", table_name="lstm_panel_snapshots"
    )
    op.drop_table("lstm_panel_snapshots")
