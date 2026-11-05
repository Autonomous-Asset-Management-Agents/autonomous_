"""#4443 — ``decision_outcomes`` nicht vor ihrer Entscheidung senden.

Auf rc6 (09.10.2026) scheiterten Batches von ``decision_outcomes`` in fast jedem Zyklus am
Fremdschlüssel: 133 Zeilen in der Ersatzdatei, alle 133 Elternzeilen kamen später an. Der
Worker leerte zuerst ``_decision_queue`` und sendete sie; ein Paar, das während dieses
Sendens eintraf, brachte sein Ergebnis noch in diesem Durchlauf in die DB, seine
Entscheidung erst im nächsten.

Die DB hier ist wie in der Produktion gebaut (``core/database/session.py``): SQLite-Datei
mit ``PRAGMA foreign_keys=ON``. Der Insert wird nicht gemockt.
"""

from __future__ import annotations

import json
import logging
import queue
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

pytestmark = [pytest.mark.unit, pytest.mark.vc0]


@pytest.fixture
async def engine(tmp_path):
    from core.database.models import Base

    e = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")

    @event.listens_for(e.sync_engine, "connect")
    def _wie_in_der_produktion(dbapi_connection, _):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
def logger(engine, tmp_path):
    """Ein CloudLogger ohne Worker-Thread, mit echter SQLite-DB."""
    from core.cloud_logger import CloudLogger

    lg = object.__new__(CloudLogger)
    lg._initialized = True
    lg.is_connected = True
    lg.thread_session_maker = sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    lg._decision_queue = queue.Queue()
    lg._decision_outcome_queue = queue.Queue()
    lg.fallback_dir = str(tmp_path / "cloud_fallback_logs")
    Path(lg.fallback_dir).mkdir()
    lg.stats = {
        "decisions_logged": 0,
        "errors": 0,
        "fallback_writes": 0,
    }
    return lg


def _entscheidung(did: str) -> dict:
    return {
        "decision_id": did,
        "symbol": "COHR",
        "decision_time": "2026-10-09 14:01:31.327539+00:00",
        "action": "BUY",
    }


def _ergebnis(did: str) -> dict:
    return {
        "decision_id": did,
        "symbol": "COHR",
        "decision_time": "2026-10-09 14:01:31.327539+00:00",
        "execution_outcome": "executed",
    }


async def _anzahl(engine, tabelle: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT COUNT(*) FROM {tabelle}"))).scalar()


def _ersatzzeilen(lg, tabelle: str) -> list:
    zeilen = []
    for datei in Path(lg.fallback_dir).glob(f"{tabelle}_*.jsonl"):
        zeilen += [json.loads(z) for z in datei.read_text("utf-8").splitlines()]
    return zeilen


# ---------------------------------------------------------------------------


class TestWorkerDrainOrder:
    async def test_paar_waehrend_des_decisions_sendens_landet_ohne_fk_fehler(
        self, logger, engine
    ):
        """Gherkin 1: Ergebnis trifft zwischen den beiden Leerungen ein."""
        from core.cloud_logger import CloudLogger

        original = CloudLogger._send_batch
        eingereiht = []

        async def _send_mit_produzent(self, tabelle, items):
            # Der Engine-Thread reiht genau dann ein neues Paar ein, während der
            # Decisions-Batch dieses Durchlaufs unterwegs ist.
            if tabelle == "decisions" and not eingereiht:
                eingereiht.append(True)
                self._decision_queue.put(_entscheidung("d2"))
                self._decision_outcome_queue.put(_ergebnis("d2"))
            await original(self, tabelle, items)

        logger._decision_queue.put(_entscheidung("d1"))
        logger._decision_outcome_queue.put(_ergebnis("d1"))

        with patch.object(CloudLogger, "_send_batch", _send_mit_produzent):
            await logger._drain_decisions_and_outcomes()
            await logger._drain_decisions_and_outcomes()

        assert await _anzahl(engine, "decisions") == 2
        assert await _anzahl(engine, "decision_outcomes") == 2
        assert logger.stats["errors"] == 0
        assert _ersatzzeilen(logger, "decision_outcomes") == []
        # Option A allein schließt den Wettlauf: nichts musste zurückgestellt werden.
        # (Mit der alten Reihenfolge fängt erst Option C das Ergebnis auf: deferred = 1.)
        assert logger.stats.get("outcomes_deferred", 0) == 0


class TestDeferOrphanOutcome:
    async def test_ein_ergebnis_ohne_eltern_verwirft_den_batch_nicht(
        self, logger, engine
    ):
        """Gherkin 2: 1 von 5 ohne Elternzeile → 4 gespeichert, 1 zurückgestellt."""
        for i in range(4):
            logger._decision_queue.put(_entscheidung(f"d{i}"))
        for i in range(4):
            logger._decision_outcome_queue.put(_ergebnis(f"d{i}"))
        logger._decision_outcome_queue.put(_ergebnis("waise"))

        await logger._drain_decisions_and_outcomes()

        assert await _anzahl(engine, "decision_outcomes") == 4
        assert logger.stats["errors"] == 0
        assert logger.stats["outcomes_deferred"] == 1
        assert _ersatzzeilen(logger, "decision_outcomes") == []

        # Die Entscheidung kommt im nächsten Durchlauf → das Ergebnis geht mit.
        logger._decision_queue.put(_entscheidung("waise"))
        await logger._drain_decisions_and_outcomes()

        assert await _anzahl(engine, "decision_outcomes") == 5
        assert logger.stats.get("outcomes_orphaned", 0) == 0


class TestOrphanAfterDeadline:
    async def test_elternzeile_kommt_nie(self, logger, engine, caplog):
        """Gherkin 3: nach N Durchläufen Ersatzdatei, Zähler und WARNING."""
        from core.outcome_reihenfolge import OUTCOME_DEFER_MAX_CYCLES

        logger._decision_outcome_queue.put(_ergebnis("nie"))

        with caplog.at_level(logging.WARNING):
            for _ in range(OUTCOME_DEFER_MAX_CYCLES):
                await logger._drain_decisions_and_outcomes()
                assert _ersatzzeilen(logger, "decision_outcomes") == []
            await logger._drain_decisions_and_outcomes()

        assert [
            z["decision_id"] for z in _ersatzzeilen(logger, "decision_outcomes")
        ] == ["nie"]
        assert logger.stats["outcomes_orphaned"] == 1
        assert logger.stats["errors"] == 0, "kein Speicherfehler, nur verwaist"
        assert any(
            r.levelno == logging.WARNING and "nie" in r.getMessage()
            for r in caplog.records
        )
        assert await _anzahl(engine, "decision_outcomes") == 0

        # Danach ist nichts mehr zurückgestellt.
        await logger._drain_decisions_and_outcomes()
        assert logger.stats["outcomes_orphaned"] == 1


class TestHealthPersistenceCounters:
    def test_health_zeigt_zurueckgestellte_und_verwaiste_ergebnisse(self):
        from fastapi.testclient import TestClient

        import core.engine.api_routes as api_routes_mod

        lg = MagicMock()
        lg.get_stats.return_value = {
            "errors": 0,
            "fallback_writes": 1,
            "decisions_logged": 7,
            "outcomes_deferred": 3,
            "outcomes_orphaned": 1,
        }
        with (
            patch(
                "core.engine.api_routes.RedisClient.check_health",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("core.engine.api_routes.engine") as eng,
            patch("core.cloud_logger.get_cloud_logger", return_value=lg),
        ):
            eng.strategy_running.is_set.return_value = True
            data = TestClient(api_routes_mod.app).get("/health").json()

        assert data["persistence"]["outcomes_deferred"] == 3
        assert data["persistence"]["outcomes_orphaned"] == 1
