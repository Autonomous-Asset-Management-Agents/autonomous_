import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from pytest_bdd import given, scenarios, then, when
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import config
from core.database.models import (
    Base,
    DecisionOutcome,
    PortfolioMetricsReport,
    PortfolioSnapshot,
    Trade,
)
from core.database.session import AsyncSessionLocal
from core.engine.api_routes import (
    _read_metrics_db_or_fallback,
    _return_metrics_from_points,
)
from core.engine.metrics_builder import build_metrics_report

scenarios("../ch6_berichte_aus_datensaetzen.feature")


def run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        import nest_asyncio

        nest_asyncio.apply()
    return loop.run_until_complete(coro)


@pytest.fixture
def memory_db_session():
    url = "sqlite+aiosqlite:///:memory:"
    engine = create_async_engine(url, echo=False)

    async def init_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    run_async(init_db())

    session_factory = sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    import core.database.session
    import core.engine.metrics_builder

    orig_session_local = core.database.session.AsyncSessionLocal

    class FakeAsyncSessionLocal:
        def __init__(self, *args, **kwargs):
            self.session = session_factory()

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            await self.session.close()

    core.database.session.AsyncSessionLocal = FakeAsyncSessionLocal
    core.engine.metrics_builder.AsyncSessionLocal = FakeAsyncSessionLocal

    session = session_factory()
    yield session

    core.database.session.AsyncSessionLocal = orig_session_local
    core.engine.metrics_builder.AsyncSessionLocal = orig_session_local
    run_async(session.close())
    run_async(engine.dispose())


@pytest.fixture
def context():
    return {}


@given("eine abgeschlossene Sitzung mit Entscheidungen, Orders und Fills")
def completed_session(memory_db_session, context):
    start = datetime.now(timezone.utc) - timedelta(days=5)
    context["start"] = start

    for i in range(3):
        snap = PortfolioSnapshot(
            id=f"snap-{i}",
            timestamp=start + timedelta(days=i),
            total_equity=10000.0 + i * 100.0,
            cash=100.0,
            strategy_name="test",
            is_simulation=False,
            paper_trading=True,
        )
        memory_db_session.add(snap)

    dec = DecisionOutcome(
        decision_id="test-dec-1",
        symbol="AAPL",
        decision_time=start + timedelta(days=1),
        is_simulation=False,  # but wait, report queries with paper_trading=True
    )
    memory_db_session.add(dec)

    trade = Trade(
        trade_id="test-trade-1",
        decision_id="test-dec-1",
        symbol="AAPL",
        side="buy",
        qty=1.0,
        price=100.0,
        executed_at=start + timedelta(days=1),
        is_simulation=True,  # Because report is built with paper_trading=True
        order_status="filled",
    )
    memory_db_session.add(trade)
    run_async(memory_db_session.commit())


@when("der Bericht erzeugt wird")
def generate_report(memory_db_session, context):
    start = context["start"]
    report = run_async(
        build_metrics_report(
            memory_db_session, start, start + timedelta(days=5), "ALL", True
        )
    )
    context["report"] = report


@then("traegt jede Kennzahl eine Verweiskette auf decision_id und Broker-Order-ID")
def metrics_traceable(context):
    report = context["report"]
    assert report is not None
    assert "test-dec-1" in report.decision_ids
    assert isinstance(report.order_ids, list)
    assert "test-trade-1" in report.order_ids


@given("derselbe abgeschlossene Zeitraum")
def same_completed_period(memory_db_session, context):
    completed_session(memory_db_session, context)
    generate_report(memory_db_session, context)


@when("der Bericht zweimal abgerufen wird")
def report_fetched_twice(context):
    config.CH6_READ_METRICS_DB_3391 = True
    points = [
        {"date": "2026-01-01", "equity": 10000.0, "cashflow": 100.0},
        {"date": "2026-01-02", "equity": 10100.0, "cashflow": 100.0},
        {"date": "2026-01-03", "equity": 10200.0, "cashflow": 100.0},
    ]
    res1, fallback1 = run_async(
        _read_metrics_db_or_fallback(points, "ALL", True, "CH6_READ_METRICS_DB_3391")
    )
    res2, fallback2 = run_async(
        _read_metrics_db_or_fallback(points, "ALL", True, "CH6_READ_METRICS_DB_3391")
    )
    context["res1"] = res1
    context["res2"] = res2
    context["fallback"] = fallback1 or fallback2


@then("liefert er beide Male dieselbe Zahl aus demselben Datensatz")
def same_number_from_dataset(context):
    res1 = context["res1"]
    res2 = context["res2"]
    assert res1["twr_pct"] == res2["twr_pct"]
    assert "decision_ids" in res1
    assert "test-dec-1" in res1["decision_ids"]


@then("es findet keine Berechnung aus Rohdaten statt")
def no_raw_calculation(context):
    assert context["fallback"] is False
    assert "fallback_source" not in context["res1"]


@given("Datensaetze aus einem Papier- und einem Live-Abschnitt desselben Kontos")
def paper_and_live_records(memory_db_session, context):
    start = datetime.now(timezone.utc) - timedelta(days=5)
    context["start"] = start

    p_report = PortfolioMetricsReport(
        id="rep-paper",
        timestamp=start,
        baseline="ALL",
        paper_trading=True,
        twr_pct=5.0,
        decision_ids=["dec-paper"],
        order_ids=[],
    )
    memory_db_session.add(p_report)

    l_report = PortfolioMetricsReport(
        id="rep-live",
        timestamp=start,
        baseline="ALL",
        paper_trading=False,
        twr_pct=10.0,
        decision_ids=["dec-live"],
        order_ids=[],
    )
    memory_db_session.add(l_report)
    run_async(memory_db_session.commit())


@when("der Live-Bericht aus dem neuen Leseweg erzeugt wird")
def live_report_generated(context):
    config.CH6_READ_METRICS_DB_3391 = True
    points = [{"date": "2026-01-01", "equity": 10000.0, "cashflow": 0.0}]
    res, fallback = run_async(
        _read_metrics_db_or_fallback(points, "ALL", False, "CH6_READ_METRICS_DB_3391")
    )
    context["live_res"] = res


@then("enthaelt er ausschliesslich Live-Datensaetze")
def exclusively_live(context):
    res = context["live_res"]
    assert "dec-live" in res["decision_ids"]
    assert "dec-paper" not in res["decision_ids"]
    assert res["twr_pct"] == 10.0


@then("die Einzahlungsbereinigung wirkt wie bisher")
def deposit_adjustment_works(context):
    res = context["live_res"]
    assert "twr_pct" in res
    assert res.get("fallback_source") is not True


@given("die eigenen Datensaetze sind fuer einen Zeitraum nicht lesbar")
def records_unreadable(memory_db_session):
    """Empty database session simulates unreadable/missing records."""
    pass


@when("der Bericht ausweicht und die Broker-Historie verwendet")
def fallback_to_broker_history(context):
    config.CH6_READ_METRICS_DB_3391 = True
    points = [
        {"date": "2026-01-01", "equity": 10000.0, "cashflow": 0.0},
        {"date": "2026-01-02", "equity": 10500.0, "cashflow": 0.0},
    ]
    res, fallback = run_async(
        _read_metrics_db_or_fallback(points, "ALL", True, "CH6_READ_METRICS_DB_3391")
    )
    context["fallback_res"] = res
    context["is_fallback"] = fallback


@then("kennzeichnet er diese Zahlen als Zahlen aus einer Ersatzquelle")
def flags_fallback_source(context):
    assert context["is_fallback"] is True
    assert context["fallback_res"].get("fallback_source") is True


@then("der Bericht bleibt lieferfaehig")
def report_remains_available(context):
    assert "twr_pct" in context["fallback_res"]


@given("der bisherige Rechenweg und der neue Weg aus Datensaetzen laufen parallel")
def parallel_paths(memory_db_session, context):
    same_completed_period(memory_db_session, context)


@when("beide denselben Zeitraum auswerten")
def both_evaluate_same_period(context):
    points = [
        {"date": "2026-01-01", "equity": 10000.0, "cashflow": 100.0},
        {"date": "2026-01-02", "equity": 10100.0, "cashflow": 100.0},
        {"date": "2026-01-03", "equity": 10200.0, "cashflow": 100.0},
    ]
    old_res = _return_metrics_from_points(points)

    config.CH6_READ_METRICS_DB_3391 = True
    new_res, _ = run_async(
        _read_metrics_db_or_fallback(points, "ALL", True, "CH6_READ_METRICS_DB_3391")
    )

    context["old_res"] = old_res
    context["new_res"] = new_res


@then("weichen ihre Ergebnisse nicht voneinander ab")
def results_do_not_diverge(context):
    assert context["old_res"]["twr_pct"] == context["new_res"]["twr_pct"]
