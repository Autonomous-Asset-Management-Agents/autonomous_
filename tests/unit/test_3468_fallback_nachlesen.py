"""#3468 — abgelegte Entscheidungssätze kehren in die Datenbank zurück.

Scheitert ein ORM-Insert des ``CloudLogger``, landet die Charge in
``cloud_fallback_logs/<tabelle>_<datum>.jsonl`` — und blieb dort bisher für immer. Nach der
Schemadrift aus #3373 lagen auf der laufenden Installation 3 926 Entscheidungssätze nur in
dieser Ablage.

Die Engine hier ist so gebaut wie in der Produktion (``core/database/session.py``): mit
``PRAGMA foreign_keys=ON``. Das ist wichtig, denn ``INSERT OR IGNORE`` fängt einen
Fremdschlüssel-Verstoß **nicht** ab — ein einziger ``decision_outcomes``-Satz ohne zugehörige
Entscheidung ließe sonst die ganze Datei scheitern.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.unit


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
def ablage(tmp_path) -> Path:
    d = tmp_path / "cloud_fallback_logs"
    d.mkdir()
    return d


def _entscheidung(did: str, **mehr) -> dict:
    satz = {
        "decision_id": did,
        "symbol": "HAL",
        "decision_time": "2026-09-17 14:01:31.327539+00:00",
        "action": "BUY",
        "action_executed": True,
        # ein Feld, das es in der Tabelle nicht gibt — wie im echten Satz vom 17.09.
        "client_order_id": "entry-0-" + did,
    }
    satz.update(mehr)
    return satz


def _ergebnis(did: str) -> dict:
    return {
        "decision_id": did,
        "symbol": "HAL",
        "decision_time": "2026-09-17 14:01:31.327539+00:00",
    }


def _schreibe(ablage: Path, tabelle: str, tag: str, saetze) -> Path:
    datei = ablage / f"{tabelle}_{tag}.jsonl"
    with datei.open("a", encoding="utf-8") as f:
        for s in saetze:
            f.write((s if isinstance(s, str) else json.dumps(s)) + "\n")
    return datei


async def _anzahl(engine, tabelle: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT COUNT(*) FROM {tabelle}"))).scalar()


# ---------------------------------------------------------------------------


async def test_abgelegte_entscheidungen_kehren_zurueck(engine, ablage) -> None:
    from core.database.fallback_nachlesen import lies_nach

    _schreibe(
        ablage, "decisions", "20260917", [_entscheidung("d1"), _entscheidung("d2")]
    )
    _schreibe(
        ablage, "decision_outcomes", "20260917", [_ergebnis("d1"), _ergebnis("d2")]
    )

    bericht = await lies_nach(engine, ablage, heute=date(2026, 9, 18))

    assert await _anzahl(engine, "decisions") == 2
    assert await _anzahl(engine, "decision_outcomes") == 2
    assert bericht["decisions"].eingefuegt == 2
    assert bericht["decision_outcomes"].eingefuegt == 2


async def test_eine_nachgelesene_datei_wird_beiseitegelegt(engine, ablage) -> None:
    from core.database.fallback_nachlesen import lies_nach

    datei = _schreibe(ablage, "decisions", "20260917", [_entscheidung("d1")])

    await lies_nach(engine, ablage, heute=date(2026, 9, 18))

    assert not datei.exists()
    assert (
        ablage / "nachgelesen" / datei.name
    ).exists(), (
        "Beiseitegelegt heisst verschoben, nicht geloescht — die Originaldaten bleiben."
    )


async def test_nachlesen_ist_wiederholbar(engine, ablage) -> None:
    from core.database.fallback_nachlesen import lies_nach

    _schreibe(ablage, "decisions", "20260917", [_entscheidung("d1")])
    await lies_nach(engine, ablage, heute=date(2026, 9, 18))

    _schreibe(ablage, "decisions", "20260916", [_entscheidung("d1")])  # derselbe Satz
    bericht = await lies_nach(engine, ablage, heute=date(2026, 9, 18))

    assert await _anzahl(engine, "decisions") == 1
    assert bericht["decisions"].eingefuegt == 0
    assert bericht["decisions"].schon_vorhanden == 1


async def test_ein_ergebnis_ohne_entscheidung_bricht_die_datei_nicht(
    engine, ablage, caplog
) -> None:
    """Der Fall, den ``INSERT OR IGNORE`` nicht abfängt: ein Fremdschlüssel-Verstoß."""
    from core.database.fallback_nachlesen import lies_nach

    _schreibe(ablage, "decisions", "20260917", [_entscheidung("d1")])
    _schreibe(
        ablage,
        "decision_outcomes",
        "20260917",
        [_ergebnis("d1"), _ergebnis("gibt-es-nicht")],
    )

    with caplog.at_level(logging.WARNING, logger="core.database.fallback_nachlesen"):
        bericht = await lies_nach(engine, ablage, heute=date(2026, 9, 18))

    assert await _anzahl(engine, "decision_outcomes") == 1
    assert bericht["decision_outcomes"].ohne_entscheidung == 1
    assert any("ohne" in r.getMessage() for r in caplog.records)


async def test_unlesbare_zeilen_werden_gezaehlt_nicht_verschluckt(
    engine, ablage
) -> None:
    from core.database.fallback_nachlesen import lies_nach

    _schreibe(ablage, "decisions", "20260917", [_entscheidung("d1"), "{kaputt"])

    bericht = await lies_nach(engine, ablage, heute=date(2026, 9, 18))

    assert bericht["decisions"].eingefuegt == 1
    assert bericht["decisions"].unlesbar == 1


async def test_die_datei_von_heute_wird_nachgelesen_aber_nicht_verschoben(
    engine, ablage
) -> None:
    """Der Logger kann heute noch an diese Datei anhängen — verschoben ginge das verloren."""
    from core.database.fallback_nachlesen import lies_nach

    datei = _schreibe(ablage, "decisions", "20260918", [_entscheidung("d1")])

    await lies_nach(engine, ablage, heute=date(2026, 9, 18))

    assert await _anzahl(engine, "decisions") == 1
    assert datei.exists()


async def test_was_nicht_passt_bleibt_liegen_und_haelt_nichts_an(
    engine, ablage, caplog
) -> None:
    """Scheitert die Tabelle (z. B. weil das Schema noch nicht geheilt ist), bleibt die Datei."""
    from core.database.fallback_nachlesen import lies_nach

    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE decisions DROP COLUMN sizing_mode"))
    datei = _schreibe(
        ablage, "decisions", "20260917", [_entscheidung("d1", sizing_mode="b")]
    )

    with caplog.at_level(logging.WARNING, logger="core.database.fallback_nachlesen"):
        bericht = await lies_nach(engine, ablage, heute=date(2026, 9, 18))

    assert datei.exists(), "Eine gescheiterte Datei darf nicht beiseitegelegt werden."
    assert bericht["decisions"].gescheitert == 1
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_probelauf_schreibt_nichts_und_verschiebt_nichts(engine, ablage) -> None:
    from core.database.fallback_nachlesen import lies_nach

    datei = _schreibe(ablage, "decisions", "20260917", [_entscheidung("d1")])

    bericht = await lies_nach(engine, ablage, heute=date(2026, 9, 18), probelauf=True)

    assert (
        bericht["decisions"].eingefuegt == 1
    ), "Der Probelauf zaehlt, was er einfuegen wuerde."
    assert await _anzahl(engine, "decisions") == 0
    assert datei.exists()


async def test_fremde_tabellen_bleiben_unangetastet(engine, ablage) -> None:
    from core.database.fallback_nachlesen import lies_nach

    fremd = _schreibe(ablage, "portfolio_snapshots", "20260730", [{"id": "x"}])

    await lies_nach(engine, ablage, heute=date(2026, 9, 18))

    assert fremd.exists()


def test_dieselbe_ablage_wie_der_logger(monkeypatch, tmp_path) -> None:
    """Das Modul importiert den Logger bewusst nicht (Seiteneffekte) — also muss es dieselbe Regel kennen."""
    from core.cloud_logger import _resolve_fallback_dir
    from core.database.fallback_nachlesen import ablage_verzeichnis

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    assert ablage_verzeichnis() == Path(_resolve_fallback_dir())
    monkeypatch.delenv("AAA_USER_DATA_DIR")
    assert ablage_verzeichnis() == Path(_resolve_fallback_dir())


async def test_scheitern_die_entscheidungen_bleiben_die_ergebnisse_liegen(
    engine, ablage
) -> None:
    """Sonst fehlte jedem Ergebnis die Entscheidung — es würde aussortiert und die Datei
    beiseitegelegt, obwohl die Entscheidung nur *noch* nicht durch ist."""
    from core.database.fallback_nachlesen import lies_nach

    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE decisions DROP COLUMN sizing_mode"))
    _schreibe(ablage, "decisions", "20260917", [_entscheidung("d1", sizing_mode="b")])
    ergebnisse = _schreibe(ablage, "decision_outcomes", "20260917", [_ergebnis("d1")])

    bericht = await lies_nach(engine, ablage, heute=date(2026, 9, 18))

    assert ergebnisse.exists(), "Die Ergebnis-Datei wurde beiseitegelegt."
    assert bericht["decision_outcomes"].ohne_entscheidung == 0


async def test_der_start_liest_nach_der_schema_heilung_nach(
    monkeypatch, tmp_path
) -> None:
    """Die Reihenfolge ist der Kern: vorher scheiterten die Saetze wie beim ersten Mal."""
    import core.database.bootstrap as bootstrap
    import core.database.fallback_nachlesen as nachlesen
    import core.database.session as session

    reihenfolge = []

    async def _heilen(engine):
        reihenfolge.append("schema")

    async def _nachlesen(engine, verzeichnis, **_):
        reihenfolge.append("nachlesen")
        return {}

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_local_db_initialized", False)
    monkeypatch.setattr(session, "engine", create_async_engine("sqlite+aiosqlite://"))
    monkeypatch.setattr(bootstrap, "init_local_db", _heilen)
    monkeypatch.setattr(nachlesen, "lies_nach", _nachlesen)

    await session.ensure_local_db_ready()
    await session.ensure_local_db_ready()  # zweiter Aufruf: nichts mehr

    assert reihenfolge == ["schema", "nachlesen"]


async def test_ein_fehler_beim_nachlesen_haelt_den_start_nicht_an(
    monkeypatch, tmp_path
) -> None:
    import core.database.bootstrap as bootstrap
    import core.database.fallback_nachlesen as nachlesen
    import core.database.session as session

    async def _heilen(engine):
        return None

    async def _kaputt(engine, verzeichnis, **_):
        raise RuntimeError("Platte voll")

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_local_db_initialized", False)
    monkeypatch.setattr(session, "engine", create_async_engine("sqlite+aiosqlite://"))
    monkeypatch.setattr(bootstrap, "init_local_db", _heilen)
    monkeypatch.setattr(nachlesen, "lies_nach", _kaputt)

    await session.ensure_local_db_ready()  # wirft nicht

    assert session._local_db_initialized is True


async def test_ohne_desktop_verzeichnis_wird_beim_start_nichts_nachgelesen(
    monkeypatch,
) -> None:
    """Ohne AAA_USER_DATA_DIR ist die Ablage ein Arbeitsverzeichnis-Relikt.

    Im Repo lagen dort 1,3 GB Hinterlassenschaften von Testlaeufen — der erste Entwurf las
    sie beim Start komplett ein und liess einen bestehenden Test haengen.
    """
    import core.database.bootstrap as bootstrap
    import core.database.fallback_nachlesen as nachlesen
    import core.database.session as session

    gerufen = []

    async def _heilen(engine):
        return None

    async def _nachlesen(engine, verzeichnis, **_):
        gerufen.append(verzeichnis)
        return {}

    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    monkeypatch.setattr(session, "_local_db_initialized", False)
    monkeypatch.setattr(session, "engine", create_async_engine("sqlite+aiosqlite://"))
    monkeypatch.setattr(bootstrap, "init_local_db", _heilen)
    monkeypatch.setattr(nachlesen, "lies_nach", _nachlesen)

    await session.ensure_local_db_ready()

    assert gerufen == []


async def test_der_probelauf_zaehlt_wie_der_echte_lauf(engine, ablage) -> None:
    """Der Probelauf rollt die Entscheidungen zurueck, bevor er die Ergebnisse prueft.

    An einer Kopie der Live-Datenbank meldete er darum 3 926 Ergebnisse „ohne
    Entscheidung", der echte Lauf 0. Ein Probelauf, der anders zaehlt als der echte Lauf,
    ist schlimmer als keiner — man liest seine Zahlen als Befund.
    """
    from core.database.fallback_nachlesen import lies_nach

    _schreibe(ablage, "decisions", "20260917", [_entscheidung("d1")])
    _schreibe(ablage, "decision_outcomes", "20260917", [_ergebnis("d1")])

    probe = await lies_nach(engine, ablage, heute=date(2026, 9, 18), probelauf=True)
    echt = await lies_nach(engine, ablage, heute=date(2026, 9, 18))

    for tabelle in ("decisions", "decision_outcomes"):
        assert (probe[tabelle].eingefuegt, probe[tabelle].ohne_entscheidung) == (
            echt[tabelle].eingefuegt,
            echt[tabelle].ohne_entscheidung,
        ), tabelle
