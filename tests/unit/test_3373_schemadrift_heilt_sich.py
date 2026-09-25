"""#3373 — die Entscheidungs-Erfassung stand sieben Tage still, und niemand sah es.

**Gemessen an der laufenden Desktop-Installation (18.09.2026):** ``decisions`` und
``decision_outcomes`` enden beide auf ``2026-09-10 19:59:59`` — Boersenschluss. Am 11.09.
kam nichts mehr, waehrend ``risk_events`` und ``portfolio_snapshots`` ueber **denselben**
Worker weiterwuchsen. Seit genau dem 11.09. liegen dafuer ``decisions_<datum>.jsonl`` in der
Fallback-Ablage: **jeder** Insert scheiterte.

**Ursache:** PR #3303 (10.09.) gab dem Modell ``Decision`` die Spalten ``sizing_mode`` und
``sizing_target_weight``. ``create_all()`` legt nur fehlende *Tabellen* an, es erweitert
keine bestehende. Die Spalten haetten in die Hand-Registry ``_ADDITIVE_COLUMNS`` gehoert —
und wurden dort vergessen. ``decision_outcomes`` haengt per Fremdschluessel an ``decisions``
und fiel mit.

Das ist das **dritte** Mal, dass dieselbe Falle zuschnappt (#2544, #2588, #3303). Eine
Liste, die man vergessen kann, wird vergessen. Darum prueft dieser Test nicht, ob die zwei
Spalten nachgetragen sind, sondern ob der Abgleich sie **aus dem Modell selbst** ableitet —
fuer jede Tabelle und jede Spalte, auch fuer die, die es heute noch nicht gibt.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from core.database.bootstrap import (
    CURRENT_SCHEMA_VERSION,
    _additive_ddl,
    _ensure_additive_columns,
    _set_schema_version,
    init_local_db,
)
from core.database.models import Base, Decision

pytestmark = pytest.mark.unit


@pytest.fixture
async def engine(tmp_path):
    e = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'alt.db'}", echo=False)
    yield e
    await e.dispose()


async def _spalten(engine, tabelle: str) -> set[str]:
    async with engine.begin() as conn:
        info = await conn.execute(text(f'PRAGMA table_info("{tabelle}")'))
        return {zeile[1] for zeile in info.fetchall()}


def _entfernbar(spalte: sa.Column) -> bool:
    """Spalten, die SQLite per ``DROP COLUMN`` hergibt — damit laesst sich „alt" nachstellen."""
    return not (
        spalte.primary_key
        or spalte.unique
        or spalte.index
        or spalte.foreign_keys
        or any(
            spalte.name in {c.name for c in idx.columns} for idx in spalte.table.indexes
        )
        or any(
            spalte.name in {c.name for c in con.columns}
            for con in spalte.table.constraints
            if not isinstance(con, sa.PrimaryKeyConstraint)
        )
    )


async def _baue_alte_installation(engine, entferne: dict[str, list[str]]) -> None:
    """Volles Schema auf aktuellem Versionsstand — nur ohne die genannten Spalten."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for tabelle, spalten in entferne.items():
            for spalte in spalten:
                await conn.execute(
                    text(f'ALTER TABLE "{tabelle}" DROP COLUMN "{spalte}"')
                )
    await _set_schema_version(engine, CURRENT_SCHEMA_VERSION)


# ---------------------------------------------------------------------------
# Der Vorfall selbst
# ---------------------------------------------------------------------------


async def test_eine_installation_vor_pr_3303_schreibt_wieder_entscheidungen(
    engine,
) -> None:
    """Genau der Zustand der Live-Installation: ``decisions`` ohne die zwei Sizing-Spalten."""
    await _baue_alte_installation(
        engine, {"decisions": ["sizing_mode", "sizing_target_weight"]}
    )
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO decisions (decision_id, symbol, decision_time, action, "
                "action_executed, is_simulation) VALUES "
                "('alt-1', 'AAPL', '2026-09-10 19:59:59', 'HOLD', 0, 0)"
            )
        )

    await init_local_db(engine)

    assert {"sizing_mode", "sizing_target_weight"} <= await _spalten(
        engine, "decisions"
    )

    # Der Ausfall selbst: ein ORM-Insert mit den neuen Feldern muss wieder gelingen …
    async with AsyncSession(engine) as session:
        async with session.begin():
            session.add(
                Decision(
                    decision_id="neu-1",
                    symbol="MSFT",
                    decision_time=datetime(2026, 9, 11, 13, 30, tzinfo=timezone.utc),
                    action="BUY",
                    sizing_mode="clean_weight",
                    sizing_target_weight=0.05,
                )
            )
    # … und die alte Zeile muss ueberlebt haben (ALTER, kein Neuaufbau).
    async with AsyncSession(engine) as session:
        ids = (await session.execute(sa.select(Decision.decision_id))).scalars().all()
    assert sorted(ids) == ["alt-1", "neu-1"]


# ---------------------------------------------------------------------------
# Die Fehlerklasse
# ---------------------------------------------------------------------------


async def test_jede_fehlende_modellspalte_wird_nachgezogen(engine) -> None:
    """Nicht „die zwei Spalten", sondern **jede** — auch die naechste, die jemand vergisst.

    Fuer jede Tabelle des Modells werden alle per ``DROP COLUMN`` entfernbaren Spalten
    entfernt. Danach muss ``init_local_db`` das Modell wiederherstellen, ohne dass irgendwo
    eine Liste gepflegt wurde.

    Ausgenommen sind Spalten, die SQLite grundsaetzlich nicht nachtraeglich anlegen kann
    (``NOT NULL`` ohne konstanten Vorgabewert) — die haelt der naechste Test fest.
    """
    dialekt = sqlite.dialect()
    entferne = {
        t.name: [
            c.name
            for c in t.columns
            if _entfernbar(c) and _additive_ddl(c, dialekt) is not None
        ]
        for t in Base.metadata.sorted_tables
    }
    entferne = {t: s for t, s in entferne.items() if s}
    assert (
        sum(len(s) for s in entferne.values()) > 50
    ), "Pruefling zu klein, um etwas zu sagen"
    await _baue_alte_installation(engine, entferne)

    await init_local_db(engine)

    fehlend = {}
    for tabelle in Base.metadata.sorted_tables:
        luecke = {c.name for c in tabelle.columns} - await _spalten(
            engine, tabelle.name
        )
        if luecke:
            fehlend[tabelle.name] = sorted(luecke)
    assert not fehlend, (
        f"Diese Modellspalten fehlen nach dem Start weiter: {fehlend}. Jeder ORM-Insert in "
        "diese Tabellen scheitert still und faellt in die JSONL-Ablage (#3373)."
    )


#: Spalten, die SQLite nicht nachtraeglich anlegen kann — ``NOT NULL`` ohne konstanten
#: Vorgabewert. Alle stammen aus der Geburt ihrer Tabelle; keine Installation kann sie
#: vermissen. **Diese Liste waechst nicht.** Wer einer bestehenden Tabelle eine Pflichtspalte
#: gibt, gibt ihr einen ``server_default`` (oder macht sie ``nullable``) — sonst steht auf
#: jeder bestehenden Desktop-Installation genau der Ausfall aus #3373 wieder da.
_VON_GEBURT_AN_PFLICHT = {
    "decisions": {"action", "symbol"},
    "entitlement_tokens": {"issued_to_hash", "stripe_session_id", "tier", "token"},
    "lemonsqueezy_licenses": {"issued_to_hash", "order_identifier", "tier", "token"},
    "lstm_panel_snapshots": {"score"},
    "round_table_sessions": {
        "consensus_score",
        "gatekeeper_approved",
        "session_time",
        "symbol",
    },
    "system_config": {"config_value", "updated_at"},
    "user_wallets": {"secret_manager_id"},
    "decision_outcomes": {"symbol"},
    "trades": {"price", "qty", "side", "symbol"},
}


def test_keine_neue_spalte_ist_unheilbar() -> None:
    """Der Waechter an der Quelle: rot beim Entwickler, nicht still beim Anwender."""
    dialekt = sqlite.dialect()
    unheilbar = {}
    for tabelle in Base.metadata.sorted_tables:
        namen = {
            c.name
            for c in tabelle.columns
            if not c.primary_key and _additive_ddl(c, dialekt) is None
        }
        neu = namen - _VON_GEBURT_AN_PFLICHT.get(tabelle.name, set())
        if neu:
            unheilbar[tabelle.name] = sorted(neu)
    assert not unheilbar, (
        f"Diese Spalten lassen sich auf einer bestehenden SQLite-Installation nicht "
        f"nachziehen: {unheilbar}. Gib ihnen einen server_default oder mach sie nullable "
        "— sonst scheitert dort jeder Insert in die Tabelle still (#3373). Eine neue "
        "TABELLE mit Pflichtspalten ist in Ordnung: dann hier eintragen."
    )


async def test_der_abgleich_ist_idempotent(engine, caplog) -> None:
    await _baue_alte_installation(engine, {"decisions": ["sizing_mode"]})
    await init_local_db(engine)
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="core.database.bootstrap"):
        await init_local_db(engine)

    assert not [
        r for r in caplog.records if "added" in r.getMessage()
    ], "Der zweite Start hat erneut Spalten angelegt."


async def test_das_nachziehen_wird_laut_gemeldet(engine, caplog) -> None:
    """WARNING, nicht DEBUG (CLAUDE.md 5.6): Eine geheilte Drift soll der Betreiber sehen."""
    await _baue_alte_installation(engine, {"decisions": ["sizing_mode"]})

    with caplog.at_level(logging.WARNING, logger="core.database.bootstrap"):
        await init_local_db(engine)

    assert any(
        "decisions.sizing_mode" in r.getMessage() and r.levelno == logging.WARNING
        for r in caplog.records
    )


async def test_eine_nicht_nachziehbare_spalte_haelt_den_start_nicht_an(
    engine, caplog
) -> None:
    """``NOT NULL`` ohne Vorgabewert kann SQLite nicht nachtraeglich anlegen.

    Das ist ein Befund fuer den Entwickler — aber kein Grund, die Engine nicht zu starten.
    Gemeldet wird auf ERROR, geworfen wird nicht.
    """
    meta = sa.MetaData()
    sa.Table("pruefling", meta, sa.Column("id", sa.String, primary_key=True))
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)

    neu = sa.MetaData()
    sa.Table(
        "pruefling",
        neu,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("pflicht", sa.Integer, nullable=False),
        sa.Column("mit_vorgabe", sa.Boolean, nullable=False, default=False),
        sa.Column("frei", sa.Float),
    )

    with caplog.at_level(logging.ERROR, logger="core.database.bootstrap"):
        await _ensure_additive_columns(engine, metadata=neu)

    spalten = await _spalten(engine, "pruefling")
    assert {"mit_vorgabe", "frei"} <= spalten
    assert "pflicht" not in spalten
    assert any("pruefling.pflicht" in r.getMessage() for r in caplog.records)
