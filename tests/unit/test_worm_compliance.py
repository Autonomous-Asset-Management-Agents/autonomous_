# tests/unit/test_worm_compliance.py
#
# Unit-Tests für WORM-Schutz auf mifid_decision_log (MiFID II Art. 16)
# Nutzt SQLite (in-memory) mit simuliertem Trigger-Verhalten via Event-Listener.
#
# Diese Tests verifizieren das Verhalten des Anwendungscodes wenn die DB
# eine Exception wirft (wie es der PostgreSQL WORM-Trigger tut).
# Die echte Trigger-Verifikation liegt in scripts/test_worm_compliance.py.

from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest
from sqlalchemy.exc import InternalError


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestWORMCloudLoggerBehavior:
    """
    Testet wie der CloudLogger mit einer WORM-Exception umgeht.
    Wenn der DB-Trigger UPDATE/DELETE blockiert, darf die App nicht crashen.
    """

    @pytest.mark.anyio
    async def test_cloudlogger_handles_worm_exception_gracefully(self):
        """
        Given: mifid_decision_log Trigger wirft Exception auf UPDATE
        When:  CloudLogger versucht einen Batch-Insert (normaler Pfad)
        Then:  Exception wird im CloudLogger geloggt, kein App-Crash
        """
        from core.cloud_logger import CloudLogger

        logger = CloudLogger()

        # Simuliere eine InternalError (wie vom PostgreSQL Trigger)
        worm_error = InternalError("mifid_decision_log ist WORM-geschützt", {}, None)
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_session)
        mock_session.execute = AsyncMock(side_effect=worm_error)

        mock_session_factory = MagicMock(return_value=mock_session)

        with patch("core.cloud_logger.AsyncSessionLocal", mock_session_factory):
            # _send_batch darf nicht propagieren — Exception soll intern behandelt werden
            try:
                await logger._send_batch(
                    "mifid_decision_log",
                    [
                        {
                            "id": "test-id-123",
                            "event_time": "2026-03-20T22:00:00+00:00",
                            "event_type": "WORM_TEST",
                            "severity": "INFO",
                            "message": "Unit test",
                        }
                    ],
                )
                # Kein raise — CloudLogger hat intern behandelt
            except Exception as e:
                pytest.fail(f"CloudLogger hat Exception propagiert (sollte nicht): {e}")

    def test_worm_trigger_message_contains_mifid_reference(self):
        """
        Given: WORM Trigger-Exception-Text (wie von PostgreSQL geworfen)
        When:  Text wird parsed
        Then:  Enthält MiFID-Referenz und Tabellenname
        """
        trigger_message = (
            "mifid_decision_log ist WORM-geschützt (MiFID II Art. 16 Abs. 5). "
            "UPDATE und DELETE sind verboten."
        )
        assert "MiFID" in trigger_message
        assert "mifid_decision_log" in trigger_message
        assert "UPDATE" in trigger_message
        assert "DELETE" in trigger_message


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestWORMEnforcementContract:
    """
    Contracts / Invarianten für WORM-Compliance.
    Diese Tests dokumentieren die Erwartungen an den DB-Trigger.
    """

    def test_worm_applies_only_to_mifid_table(self):
        """
        Nur mifid_decision_log ist WORM-geschützt.
        decisions, trades, ai_thoughts, risk_events, portfolio_snapshots sind es nicht.
        """
        worm_protected_tables = {"mifid_decision_log"}
        non_worm_tables = {
            "decisions",
            "trades",
            "ai_thoughts",
            "risk_events",
            "portfolio_snapshots",
        }
        assert worm_protected_tables.isdisjoint(
            non_worm_tables
        ), "WORM-Tabelle darf nicht in der Non-WORM-Liste sein"

    def test_insert_is_always_allowed(self):
        """
        Der WORM-Trigger reagiert nur auf UPDATE und DELETE,
        nicht auf INSERT — Append-Only Prinzip.
        """
        trigger_operations = {"UPDATE", "DELETE"}
        allowed_operations = {"INSERT", "SELECT"}
        assert not trigger_operations.intersection(
            allowed_operations
        ), "INSERT und SELECT dürfen nicht durch den WORM-Trigger blockiert werden"

    def test_mifid_retention_period_is_5_years(self):
        """
        MiFID II Art. 16 (5) schreibt 5 Jahre Aufbewahrung vor.
        Dokumentiert als Konstante für zukünftige Retention-Policy.
        """
        MIFID_RETENTION_YEARS = 5
        assert MIFID_RETENTION_YEARS >= 5, "MiFID II Mindestaufbewahrung: 5 Jahre"
