"""#3911: ``twr_pct`` / ``inception_twr_pct`` kommen aus der Vollserie, nicht aus einem Bericht.

Gemessen am 11.10.2026 (Desktop rc6, Paper): Die Kurve von ``/benchmark-equity`` lief vom
20.02. (100.000 $) bis 142.086 $ ohne Einzahlung, also +42,09 %. Die API meldete −1,32 / −1,08 %.
Das waren exakt die ``twr_pct`` zweier eingefrorener Zeilen in ``portfolio_metrics_reports``,
die ``_read_metrics_db_or_fallback`` ueber die richtige Rechnung aus den Punkten schrieb.

Plan: ``docs/3911-twr-vollserie/implementation_plan.md`` (Option A).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import config
from core.engine import metrics_builder
from core.engine.routes import benchmark_daten

pytestmark = [pytest.mark.unit, pytest.mark.vc6]

_SCHALTER = "CH6_READ_METRICS_DB_3391"


@pytest.fixture
async def speicher_db():
    from core.database.models import Base

    motor = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with motor.begin() as verbindung:
        await verbindung.run_sync(Base.metadata.create_all)
    fabrik = sessionmaker(bind=motor, class_=AsyncSession, expire_on_commit=False)
    yield fabrik
    await motor.dispose()


def _vollserie() -> list[dict]:
    """Die gemessene Kurve, verkuerzt auf Anfang und Ende: 0 Einzahlungen."""
    return [
        {"date": "2026-02-20", "equity": 100_000.0, "cashflow": 0.0},
        {"date": "2026-06-15", "equity": 121_500.0, "cashflow": 0.0},
        {"date": "2026-10-10", "equity": 142_086.06, "cashflow": 0.0},
    ]


async def _lege_bericht(fabrik, *, end_date: datetime, twr_pct: float, rid: str):
    from core.database.models import PortfolioMetricsReport

    async with fabrik() as sitzung:
        sitzung.add(
            PortfolioMetricsReport(
                id=rid,
                timestamp=end_date,
                start_date=datetime(2026, 2, 15, tzinfo=timezone.utc),
                end_date=end_date,
                baseline="ALL",
                paper_trading=True,
                initial_capital=144_070.0,
                final_equity=142_086.0,
                twr_pct=twr_pct,
                max_drawdown_pct=0.0,
                win_rate_pct=0.0,
                decision_ids=["d-beleg"],
                order_ids=["o-beleg"],
                coverage_gap_map={},
            )
        )
        await sitzung.commit()


async def _berichte(fabrik):
    from core.database.models import PortfolioMetricsReport

    async with fabrik() as sitzung:
        stmt = sa.select(PortfolioMetricsReport).order_by(
            PortfolioMetricsReport.end_date.asc()
        )
        return (await sitzung.execute(stmt)).scalars().all()


class TestReportUeberschreibtNicht:
    @pytest.mark.asyncio
    async def test_kennzahl_aus_der_kurve_belege_aus_dem_bericht(
        self, speicher_db, monkeypatch, caplog
    ):
        monkeypatch.setattr(config, _SCHALTER, True, raising=False)
        # Fenster des Berichts deckt den letzten Punkt ab: kein Neubau, nur Beleg.
        await _lege_bericht(
            speicher_db,
            end_date=datetime(2026, 10, 11, tzinfo=timezone.utc),
            twr_pct=-1.078,
            rid="report-eingefroren",
        )
        vorher = benchmark_daten.get_benchmark_counters()["report_mismatch"]

        with patch("core.database.session.AsyncSessionLocal", speicher_db):
            ergebnis, rueckfall = await benchmark_daten._read_metrics_db_or_fallback(
                _vollserie(), "ALL", True, _SCHALTER
            )

        assert rueckfall is False
        assert ergebnis["twr_pct"] == pytest.approx(42.09, abs=0.01), (
            "#3911: der eingefrorene Bericht (-1,078) darf die Kennzahl der Kurve nicht "
            "ueberschreiben."
        )
        assert ergebnis["decision_ids"] == ["d-beleg"]
        assert ergebnis["order_ids"] == ["o-beleg"]
        nachher = benchmark_daten.get_benchmark_counters()["report_mismatch"]
        assert nachher == vorher + 1
        assert any("mismatch" in r.getMessage() for r in caplog.records)


def test_zaehler_steht_in_engine_diagnostics(monkeypatch):
    from core.engine.routes import diagnose

    monkeypatch.setitem(benchmark_daten._BENCHMARK_COUNTERS, "report_mismatch", 7)
    assert diagnose._collect_benchmark() == {"report_mismatch": 7}


class TestBuilderCashIstKeinFluss:
    @pytest.mark.asyncio
    async def test_cash_bestand_ist_keine_einzahlung(self, speicher_db):
        from core.database.models import PortfolioSnapshot

        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        async with speicher_db() as sitzung:
            for i, eq in enumerate([100.0, 105.0, 110.0]):
                sitzung.add(
                    PortfolioSnapshot(
                        id=f"snap-{i}",
                        timestamp=start + timedelta(days=i),
                        total_equity=eq,
                        cash=60.0,
                        strategy_name="test",
                        is_simulation=False,
                        paper_trading=True,
                    )
                )
            await sitzung.commit()

            bericht = await metrics_builder.build_metrics_report(
                sitzung, start, start + timedelta(days=3), "ALL", True
            )

        assert bericht.twr_pct == pytest.approx(
            10.0, abs=0.01
        ), "#3911 Defekt 4: der Cash-Bestand (60) wurde als taegliche Einzahlung gerechnet."

    @pytest.mark.asyncio
    async def test_kleiner_cash_bestand_unter_dem_tagesschritt(self, speicher_db):
        """Der Fall 60 > Tagesschritt wird schon vom Verzoegerungs-Puffer (#3049) geschluckt.
        Liegt der Cash-Bestand unter dem Tagesschritt, zog der Builder ihn voll als
        Einzahlung ab: 100 → 110 → 121 mit Cash 5 ergab 10,0 statt 21,0 %."""
        from core.database.models import PortfolioSnapshot

        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        async with speicher_db() as sitzung:
            for i, eq in enumerate([100.0, 110.0, 121.0]):
                sitzung.add(
                    PortfolioSnapshot(
                        id=f"snap-k{i}",
                        timestamp=start + timedelta(days=i),
                        total_equity=eq,
                        cash=5.0,
                        strategy_name="test",
                        is_simulation=False,
                        paper_trading=True,
                    )
                )
            await sitzung.commit()

            bericht = await metrics_builder.build_metrics_report(
                sitzung, start, start + timedelta(days=3), "ALL", True
            )

        assert bericht.twr_pct == pytest.approx(21.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_echte_fluesse_werden_beruecksichtigt(self, speicher_db):
        from core.database.models import PortfolioSnapshot

        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        async with speicher_db() as sitzung:
            for i, eq in enumerate([100.0, 160.0]):
                sitzung.add(
                    PortfolioSnapshot(
                        id=f"snap-f{i}",
                        timestamp=start + timedelta(days=i),
                        total_equity=eq,
                        cash=60.0,
                        strategy_name="test",
                        is_simulation=False,
                        paper_trading=True,
                    )
                )
            await sitzung.commit()

            # 50 $ Einzahlung am zweiten Tag: Rendite (160 - 50) / 100 - 1 = 10 %.
            bericht = await metrics_builder.build_metrics_report(
                sitzung,
                start,
                start + timedelta(days=2),
                "ALL",
                True,
                cashflows={"2026-09-02": 50.0},
            )

        assert bericht.twr_pct == pytest.approx(10.0, abs=0.01)


class TestBerichtNeubauBeiNeuemFenster:
    @pytest.mark.asyncio
    async def test_veralteter_bericht_wird_neu_gebaut_alter_bleibt(
        self, speicher_db, monkeypatch
    ):
        from core.database.models import PortfolioSnapshot

        monkeypatch.setattr(config, _SCHALTER, True, raising=False)
        await _lege_bericht(
            speicher_db,
            end_date=datetime(2026, 10, 2, tzinfo=timezone.utc),
            twr_pct=-1.321,
            rid="report-alt",
        )
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        punkte = []
        async with speicher_db() as sitzung:
            for i in range(11):
                zeit = start + timedelta(days=i)
                sitzung.add(
                    PortfolioSnapshot(
                        id=f"snap-n{i}",
                        timestamp=zeit,
                        total_equity=1000.0 + i,
                        cash=10.0,
                        strategy_name="test",
                        is_simulation=False,
                        paper_trading=True,
                    )
                )
                punkte.append({"date": zeit.strftime("%Y-%m-%d"), "equity": 1000.0 + i})
            await sitzung.commit()

        with patch("core.database.session.AsyncSessionLocal", speicher_db):
            await benchmark_daten._read_metrics_db_or_fallback(
                punkte, "ALL", True, _SCHALTER
            )

        berichte = await _berichte(speicher_db)
        assert len(berichte) == 2, "Ein Bericht mit veraltetem Fenster wird neu gebaut."
        alt, neu = berichte
        assert alt.id == "report-alt" and alt.twr_pct == pytest.approx(-1.321)
        assert (
            neu.end_date.date() >= datetime(2026, 10, 11).date()
        ), "Der neue Bericht deckt den letzten Punkt (11.10.) ab."

    @pytest.mark.asyncio
    async def test_aktueller_bericht_wird_nicht_neu_gebaut(
        self, speicher_db, monkeypatch
    ):
        monkeypatch.setattr(config, _SCHALTER, True, raising=False)
        await _lege_bericht(
            speicher_db,
            end_date=datetime(2026, 10, 11, tzinfo=timezone.utc),
            twr_pct=42.0,
            rid="report-aktuell",
        )
        with patch("core.database.session.AsyncSessionLocal", speicher_db):
            await benchmark_daten._read_metrics_db_or_fallback(
                _vollserie(), "ALL", True, _SCHALTER
            )
        assert len(await _berichte(speicher_db)) == 1


class TestInvarianteNullEinzahlungen:
    @pytest.mark.asyncio
    async def test_inception_gleich_ende_durch_anfang(self, speicher_db, monkeypatch):
        """Harness INV-6 (#3912): bei net_deposits = 0 ist TWR = final/initial − 1."""
        monkeypatch.setattr(config, _SCHALTER, True, raising=False)
        await _lege_bericht(
            speicher_db,
            end_date=datetime(2026, 10, 11, tzinfo=timezone.utc),
            twr_pct=-1.321,
            rid="report-inv",
        )
        punkte = _vollserie()
        erwartet = (punkte[-1]["equity"] / punkte[0]["equity"] - 1.0) * 100.0

        with patch("core.database.session.AsyncSessionLocal", speicher_db):
            ergebnis, _ = await benchmark_daten._read_metrics_db_or_fallback(
                punkte, "ALL", True, _SCHALTER
            )

        assert ergebnis["net_deposits"] == pytest.approx(0.0)
        assert abs(ergebnis["twr_pct"] - erwartet) < 0.01
