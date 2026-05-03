"""Add WORM protection to mifid_decision_log (MiFID II Art. 16)

Implements 2-layer WORM enforcement:
  L1: PostgreSQL BEFORE UPDATE/DELETE trigger — blocks mutation at DB level
      for ALL users including postgres superuser.
  L2: Dedicated app_user with REVOKE UPDATE, DELETE on mifid_decision_log
      for defense-in-depth.

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-20
"""

import os
from typing import Sequence, Union
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── L1: WORM Trigger ──────────────────────────────────────────────────────
    # PostgreSQL function raises an exception on any UPDATE or DELETE.
    # This fires BEFORE the mutation and works for ALL database users,
    # including the postgres superuser.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_mifid_mutation()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'mifid_decision_log ist WORM-geschützt (MiFID II Art. 16 Abs. 5). '
                'UPDATE und DELETE sind verboten. Audit-Trail darf nur erweitert werden. '
                'Operation: % auf Datensatz id=%',
                TG_OP, OLD.id;
        END;
        $$;
    """
    )

    op.execute(
        """
        CREATE TRIGGER worm_mifid_update
        BEFORE UPDATE ON mifid_decision_log
        FOR EACH ROW
        EXECUTE FUNCTION prevent_mifid_mutation();
    """
    )

    op.execute(
        """
        CREATE TRIGGER worm_mifid_delete
        BEFORE DELETE ON mifid_decision_log
        FOR EACH ROW
        EXECUTE FUNCTION prevent_mifid_mutation();
    """
    )

    # ── L2: Dedizierter App-User mit eingeschränkten Rechten ──────────────────
    # app_user hat INSERT + SELECT auf allen Tabellen,
    # aber KEIN UPDATE + DELETE auf mifid_decision_log.
    if os.environ.get("ENABLE_WORM_L2_RBAC", "false").lower() == "true":
        op.execute("CREATE USER app_user WITH PASSWORD 'app_user_trading_2026!';")
        op.execute(
            "DO $$ BEGIN EXECUTE 'GRANT CONNECT ON DATABASE ' || current_database() || ' TO app_user;'; END $$;"
        )
        op.execute("GRANT USAGE ON SCHEMA public TO app_user;")
        op.execute("GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public TO app_user;")
        op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;")

        # Explizit UPDATE + DELETE auf den nicht-WORM Tabellen erlauben
        op.execute(
            """
            GRANT UPDATE, DELETE ON
                decisions, trades, ai_thoughts, risk_events, portfolio_snapshots
            TO app_user;
        """
        )
        # mifid_decision_log: KEIN UPDATE, KEIN DELETE für app_user (Trigger greift sowieso)
        op.execute("REVOKE UPDATE, DELETE ON mifid_decision_log FROM app_user;")


def downgrade() -> None:
    # Achtung: downgrade entfernt nur die technischen Maßnahmen,
    # nicht die bereits existierenden unveränderlichen Datensätze.
    op.execute("DROP TRIGGER IF EXISTS worm_mifid_delete ON mifid_decision_log;")
    op.execute("DROP TRIGGER IF EXISTS worm_mifid_update ON mifid_decision_log;")
    op.execute("DROP FUNCTION IF EXISTS prevent_mifid_mutation();")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_user;")
    op.execute(
        "DO $$ BEGIN EXECUTE 'REVOKE ALL ON DATABASE ' || current_database() || ' FROM app_user;'; END $$;"
    )
    op.execute("DROP USER IF EXISTS app_user;")
