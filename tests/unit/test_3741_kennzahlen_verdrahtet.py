"""#3741 — die beiden E5-Bausteine bekommen einen Aufrufer im laufenden System.

Gemessen am 28.09.2026: ``core/value_chain_kpis.py`` (ARC-E5.6) und
``core/engine/metrics_builder.py`` (ARC-E5.4/E5.5) wurden von **keiner** Produktivdatei
importiert. ``git log -S"ValueChainMonitor"`` zeigte genau den Commit, der die Klasse
angelegt hat. Die Stufen-Kennzahlen wurden nie berechnet, die Lueckenkarte nie gefuellt.

Diese Tests fahren den **echten** Pfad:

* den echten Tagesbericht (``daily_report.run_once``) mit einer Engine-Attrappe als
  Aussenwelt — die Auswertung der Stufen-Kennzahlen laeuft darin echt;
* die echte Leseschicht ``_read_metrics_db_or_fallback`` gegen eine echte SQLite-Datenbank
  im Arbeitsspeicher, mit dem echten ``build_metrics_report``.

Die Fehlerrichtung ist in beiden Faellen dieselbe: **Eine Messung darf den Betrieb nie
anhalten.** Beide Tests halten das fest.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

import config
from core import daily_report, value_chain_kpis
from core.engine import api_routes, metrics_builder

pytestmark = [pytest.mark.unit, pytest.mark.vc6]


# ---------------------------------------------------------------------------
# A. Stufen-Kennzahlen im Tagesbericht (ARC-E5.6)
# ---------------------------------------------------------------------------


@pytest.fixture
def stiller_versand(monkeypatch):
    """Der Bericht soll nichts verschicken — geprueft wird die Auswertung davor."""
    from core import daily_report

    monkeypatch.setattr(daily_report, "dispatch_report", lambda text, cfg: {})
    monkeypatch.setattr(
        daily_report,
        "gather_metrics",
        lambda engine, now=None: {
            "equity": 50_000.0,
            "day_pl_abs": 250.0,
            "decisions": 7,
            "fills": 3,
        },
    )
    return daily_report


def test_der_tagesbericht_wertet_die_stufen_kpis_aus(stiller_versand, caplog):
    """Der Kern: ARC-E5.6 hatte keinen Aufrufer — jetzt laeuft es je Bericht."""
    import logging

    caplog.set_level(logging.INFO)
    stiller_versand.run_once(MagicMock(), datetime(2026, 9, 28, 18, 0))

    kennzahlen = stiller_versand.letzte_stufen_kennzahlen()
    assert kennzahlen["VC-2_decisions_count"] == 7
    assert kennzahlen["VC-3_filled_orders"] == 3
    assert kennzahlen["VC-5_pnl_pct"] == pytest.approx(0.5)  # 250 von 50.000


def test_fehlende_kennzahlen_werden_als_nicht_gebildet_gemeldet(stiller_versand):
    """„Wert nicht gebildet" ist die Aussage, nicht ein Ersatzwert.

    Datenabdeckung, gesperrte Orders und Auditsaetze liegen im Tagesbericht nicht vor.
    Genau das soll der Alarm sagen — und nicht eine 0, die wie eine Messung aussieht.
    """
    stiller_versand.run_once(MagicMock(), datetime(2026, 9, 28, 18, 0))

    fehlend = {a.stage for a in stiller_versand.letzte_stufen_alarme() if a.is_missing}
    assert {"VC-1", "VC-4", "VC-6"} <= fehlend, fehlend
    kennzahlen = stiller_versand.letzte_stufen_kennzahlen()
    assert kennzahlen["VC-1_data_coverage"] is None


def test_die_messung_kann_den_tagesbericht_nicht_anhalten(stiller_versand, monkeypatch):
    from core import value_chain_kpis

    monkeypatch.setattr(
        value_chain_kpis.ValueChainMonitor,
        "evaluate",
        MagicMock(side_effect=RuntimeError("Messschicht kaputt")),
    )
    # darf nicht werfen
    stiller_versand.run_once(MagicMock(), datetime(2026, 9, 28, 18, 0))


# ---------------------------------------------------------------------------
# B. Bericht aus Datensaetzen (ARC-E5.4 / E5.5)
# ---------------------------------------------------------------------------


@pytest.fixture
async def speicher_db():
    from core.database.models import Base

    motor = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with motor.begin() as verbindung:
        await verbindung.run_sync(Base.metadata.create_all)
    fabrik = sessionmaker(bind=motor, class_=AsyncSession, expire_on_commit=False)
    yield fabrik
    await motor.dispose()


async def _lege_verlauf(fabrik) -> list[dict]:
    from core.database.models import PortfolioSnapshot

    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    punkte = []
    eigenkapital = 10_000.0
    async with fabrik() as sitzung:
        for i in range(5):
            zeit = start + timedelta(days=i)
            eigenkapital += 100.0
            sitzung.add(
                PortfolioSnapshot(
                    id=f"snap-{i}",
                    timestamp=zeit,
                    total_equity=eigenkapital,
                    cash=100.0,
                    strategy_name="test",
                    is_simulation=False,
                    paper_trading=True,
                )
            )
            punkte.append(
                {
                    "date": zeit.strftime("%Y-%m-%d"),
                    "equity": eigenkapital,
                    "cashflow": 0.0,
                }
            )
        await sitzung.commit()
    return punkte


async def _anzahl_berichte(fabrik) -> int:
    import sqlalchemy as sa

    from core.database.models import PortfolioMetricsReport

    async with fabrik() as sitzung:
        return len(
            (await sitzung.execute(sa.select(PortfolioMetricsReport))).scalars().all()
        )


@pytest.mark.asyncio
async def test_ohne_bericht_wird_einer_gebaut_und_ausgeliefert(
    speicher_db, monkeypatch
):
    """Der Befund: Es gab einen Leser, aber keinen Schreiber — der Leser fiel immer zurueck."""
    import config
    from core.engine.routes import benchmark_daten

    punkte = await _lege_verlauf(speicher_db)
    monkeypatch.setattr(config, "CH6_READ_METRICS_DB_3391", True, raising=False)

    with patch("core.database.session.AsyncSessionLocal", speicher_db):
        ergebnis, rueckfall = await benchmark_daten._read_metrics_db_or_fallback(
            punkte, "ALL", True, "CH6_READ_METRICS_DB_3391"
        )

    assert await _anzahl_berichte(speicher_db) == 1, (
        "Es wurde kein Bericht abgelegt — dann bleibt coverage_gap_map leer und die "
        "Lueckenkarte aus #3405 entsteht nie."
    )
    assert rueckfall is False
    assert "decision_ids" in ergebnis and "order_ids" in ergebnis


@pytest.mark.asyncio
async def test_ein_vorhandener_bericht_wird_nicht_neu_gebaut(speicher_db, monkeypatch):
    import config
    from core.engine.routes import benchmark_daten

    punkte = await _lege_verlauf(speicher_db)
    monkeypatch.setattr(config, "CH6_READ_METRICS_DB_3391", True, raising=False)

    with patch("core.database.session.AsyncSessionLocal", speicher_db):
        await benchmark_daten._read_metrics_db_or_fallback(
            punkte, "ALL", True, "CH6_READ_METRICS_DB_3391"
        )
        await benchmark_daten._read_metrics_db_or_fallback(
            punkte, "ALL", True, "CH6_READ_METRICS_DB_3391"
        )

    assert await _anzahl_berichte(speicher_db) == 1


@pytest.mark.asyncio
async def test_ein_fehler_beim_bauen_faellt_auf_die_berechnung_zurueck(
    speicher_db, monkeypatch
):
    """Fail-soft: Die Kennzahl kommt dann aus der Berechnung, nicht gar nicht."""
    import config
    from core.engine import metrics_builder
    from core.engine.routes import benchmark_daten

    punkte = await _lege_verlauf(speicher_db)
    monkeypatch.setattr(config, "CH6_READ_METRICS_DB_3391", True, raising=False)

    monkeypatch.setattr(
        metrics_builder,
        "build_metrics_report",
        AsyncMock(side_effect=RuntimeError("Datenbank weg")),
    )

    with patch("core.database.session.AsyncSessionLocal", speicher_db):
        ergebnis, rueckfall = await benchmark_daten._read_metrics_db_or_fallback(
            punkte, "ALL", True, "CH6_READ_METRICS_DB_3391"
        )

    assert rueckfall is True
    assert "twr_pct" in ergebnis
