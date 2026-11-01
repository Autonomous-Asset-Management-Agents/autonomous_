"""#4075 (ARC-E6 G-4i) — ``get_benchmark_equity`` ist wortgleich in Schritte zerlegt.

Der alte Rumpf (538 Zeilen, Stand G-4h) liegt als eingecheckte Kopie in
``tests/fixtures/g4i/benchmark_kurve_alt.py.txt``. Jeder Schritt in
``core/engine/routes/benchmark_schritte.py`` ist genau ein Block daraus; erlaubt sind nur die
drei Abbildungen aus Plan §2.3 (``z.<feld>``, ``return _WEITER``, wiederholte lokale Importe).

Dazu laeuft die alte Fassung (aus der Kopie kompiliert, mit den Modul-Globalen von G-4h) gegen
den neuen Dirigenten, auf beiden Pfaden und mit denselben Mocks: Antwort und Cache-Schreibzugriffe
muessen gleich sein.

Plan: ``docs/4075-g4i-benchmark-kurve-schritte/implementation_plan.md``.
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import dataclasses
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from tests.helpers.wortgleich import (
    ausdruck_gleich,
    import_dumps,
    ohne_importe,
    schritt_rumpf,
    unterschiede,
)

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
ALT = PAKET / "tests" / "fixtures" / "g4i" / "benchmark_kurve_alt.py.txt"
SCHRITTE = PAKET / "core" / "engine" / "routes" / "benchmark_schritte.py"
ROUTER = PAKET / "core" / "engine" / "routes" / "benchmark.py"

#: Die lokalen Importe des alten Rumpfs; jeder Schritt wiederholt die, die er braucht.
LOKALE_IMPORTE = import_dumps(
    (
        "import json",
        "from datetime import timezone",
        "import sqlalchemy as sa",
        "from core.database.models import PortfolioSnapshot",
        "from core.database.session import AsyncSessionLocal",
        "from core.engine.perf_metrics import flows_signature",
    )
)


def _alt_funktion() -> ast.AsyncFunctionDef:
    (fn,) = ast.parse(ALT.read_text(encoding="utf-8")).body
    assert isinstance(fn, ast.AsyncFunctionDef) and fn.name == "get_benchmark_equity"
    return fn


def _bloecke() -> dict[str, list[ast.stmt]]:
    """Schritt → alter Block (Plan §1), geschnitten nach Anweisungen im ``try``."""
    (versuch,) = _alt_funktion().body
    oben = versuch.body
    assert len(oben) == 20 and isinstance(oben[9], ast.If)
    neu = oben[9].body
    assert len(neu) == 26
    return {
        "_schritt_cache_lesen": oben[0:9],
        "_schritt_live_kurve": neu[0:6],
        "_schritt_snapshots_laden": neu[6:7],
        "_schritt_ohne_snapshots": neu[7:8],
        "_schritt_punkte_bauen": neu[8:15],
        "_schritt_spy_kennzahlen": neu[15:26],
        "_schritt_cache_heute": oben[10:16],
        "_schritt_cache_spy": oben[16:18],
        "_schritt_cache_antwort": oben[18:20],
    }


NEUAUFBAU = (
    "_schritt_live_kurve",
    "_schritt_snapshots_laden",
    "_schritt_ohne_snapshots",
    "_schritt_punkte_bauen",
    "_schritt_spy_kennzahlen",
)
CACHE_PFAD = ("_schritt_cache_heute", "_schritt_cache_spy", "_schritt_cache_antwort")


def _modul(pfad: Path) -> ast.Module:
    return ast.parse(pfad.read_text(encoding="utf-8"))


def _funktionen(baum: ast.Module) -> dict[str, ast.AsyncFunctionDef]:
    return {
        f.name: f
        for f in baum.body
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _felder() -> frozenset[str]:
    from core.engine.routes.benchmark_schritte import BenchmarkKurve

    return frozenset(f.name for f in dataclasses.fields(BenchmarkKurve))


# ── Wortgleichheit ──────────────────────────────────────────────────────────


def test_die_bloecke_decken_den_alten_rumpf_vollstaendig():
    bloecke = _bloecke()
    (versuch,) = _alt_funktion().body
    geschnitten = sum(len(b) for b in bloecke.values())
    # 20 oberste Anweisungen minus die Bedingung (bleibt im Dirigenten) plus 26 im Neuaufbau
    assert geschnitten == len(versuch.body) - 1 + len(versuch.body[9].body)


def test_felder_sind_die_aus_plan_paragraf_1():
    assert _felder() == {
        "r",
        "_cached",
        "cfg_paper",
        "_baseline",
        "records",
        "earliest_snap",
        "initial_capital",
        "points",
        "spy_points",
        "acct_cf",
        "data",
        "today_str",
        "_cache_mode_stale",
        "_cache_baseline_stale",
    }


@pytest.mark.parametrize("name", list(_bloecke()))
def test_jeder_schritt_ist_wortgleich_zu_seinem_block(name):
    schritt = _funktionen(_modul(SCHRITTE))[name]
    assert isinstance(schritt, ast.AsyncFunctionDef)
    assert [a.arg for a in schritt.args.args] == ["z"]
    neu = schritt_rumpf(
        schritt,
        objekt="z",
        felder=_felder(),
        importe=LOKALE_IMPORTE,
        sentinel="_WEITER",
    )
    alt = ohne_importe(_bloecke()[name], LOKALE_IMPORTE)
    assert unterschiede(neu, alt) == ""


def test_wiederholte_importe_stammen_aus_dem_alten_rumpf():
    """Abbildung 3 erlaubt nur Importe, die der alte Rumpf schon lokal hatte — jeder andere
    Import bliebe beim Vergleich oben stehen und machte ihn rot."""
    alt = {
        ast.dump(s)
        for s in ast.walk(_alt_funktion())
        if isinstance(s, (ast.Import, ast.ImportFrom))
    }
    assert LOKALE_IMPORTE <= alt


def test_der_dirigent_prueft_die_unveraenderte_bedingung_und_behaelt_den_handler():
    (versuch_alt,) = _alt_funktion().body
    dirigent = _funktionen(_modul(ROUTER))["get_benchmark_equity"]
    versuche = [s for s in dirigent.body if isinstance(s, ast.Try)]
    assert len(versuche) == 1
    (versuch,) = versuche
    assert ast.dump(versuch.handlers[0]) == ast.dump(versuch_alt.handlers[0])
    bedingungen = [s.test for s in ast.walk(versuch) if isinstance(s, ast.If)]
    assert any(
        ausdruck_gleich(b, versuch_alt.body[9].test, objekt="z", felder=_felder())
        for b in bedingungen
    )


def _gebunden(fn: ast.AST) -> set[str]:
    namen = {a.arg for a in ast.walk(fn) if isinstance(a, ast.arg)}
    for k in ast.walk(fn):
        if isinstance(k, ast.Name) and isinstance(k.ctx, (ast.Store, ast.Del)):
            namen.add(k.id)
        elif isinstance(k, (ast.Import, ast.ImportFrom)):
            namen |= {(a.asname or a.name).split(".")[0] for a in k.names}
        elif isinstance(k, ast.ExceptHandler) and k.name:
            namen.add(k.name)
    return namen


#: ``timezone`` war im alten Rumpf eine Funktions-Lokale (``from datetime import timezone`` im
#: Neuaufbau), im Cache-Pfad also nie gebunden: ``datetime.now(timezone.utc)`` warf dort
#: ``UnboundLocalError`` und das ``except Exception: pass`` schluckte ihn. Der Schritt behaelt
#: das (``NameError``, ebenfalls geschluckt), verhaltensneutral — siehe Walkthrough, „Offen".
ABSICHTLICH_FREI = {"timezone"}


def test_kein_schritt_liest_einen_namen_den_er_nicht_selbst_bindet():
    """Jeder Wert, der zwischen Bloecken fliesst, geht ueber ``z`` — sonst waere er frei."""
    baum = _modul(SCHRITTE)
    modul = _gebunden(ast.Module(body=[s for s in baum.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))], type_ignores=[]))  # fmt: skip
    modul |= set(_funktionen(baum)) | {
        c.name for c in baum.body if isinstance(c, ast.ClassDef)
    }
    for name, fn in _funktionen(baum).items():
        if not name.startswith("_schritt_"):
            continue
        frei = (
            {
                k.id
                for k in ast.walk(fn)
                if isinstance(k, ast.Name) and isinstance(k.ctx, ast.Load)
            }
            - _gebunden(fn)
            - modul
            - set(dir(builtins))
        )
        assert frei <= ABSICHTLICH_FREI, (name, frei)


@pytest.mark.parametrize(
    "pfad",
    [("_schritt_cache_lesen", *NEUAUFBAU), ("_schritt_cache_lesen", *CACHE_PFAD)],
)
def test_jedes_feld_ist_gesetzt_bevor_ein_spaeterer_schritt_es_liest(pfad):
    funktionen = _funktionen(_modul(SCHRITTE))
    gesetzt: set[str] = set()
    for name in pfad:
        zugriffe = [
            k
            for k in ast.walk(funktionen[name])
            if isinstance(k, ast.Attribute)
            and isinstance(k.value, ast.Name)
            and k.value.id == "z"
        ]
        hier = {k.attr for k in zugriffe if isinstance(k.ctx, ast.Store)}
        gelesen = {k.attr for k in zugriffe if isinstance(k.ctx, ast.Load)}
        assert gelesen <= gesetzt | hier, (name, gelesen - gesetzt - hier)
        gesetzt |= hier


def test_keine_funktion_ueber_der_schwelle():
    for pfad in (ROUTER, SCHRITTE):
        for fn in ast.walk(_modul(pfad)):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert fn.end_lineno - fn.lineno + 1 <= 150, (pfad.name, fn.name)


# ── Alt gegen neu, mit denselben Mocks ──────────────────────────────────────


def _alte_fassung():
    """Die Kopie, kompiliert mit den Modul-Globalen von ``routes/benchmark.py`` (G-4h)."""
    from core.engine import api_routes as ar
    from core.engine.routes import benchmark_daten as _bd

    fn = _alt_funktion()
    fn.decorator_list = []
    code = compile(ast.Module(body=[fn], type_ignores=[]), str(ALT), "exec")
    ns = {
        "asyncio": asyncio,
        "logging": logging,
        "date": date,
        "datetime": datetime,
        "timezone": timezone,
        "ar": ar,
        "_bd": _bd,
    }
    exec(code, ns)  # noqa: S102 — eingecheckte Testkopie, kein Fremdtext
    return ns["get_benchmark_equity"]


def _snap(tag: int, equity: float):
    s = MagicMock()
    s.timestamp = datetime(2026, 9, tag, 12, tzinfo=timezone.utc)
    s.total_equity = equity
    s.strategy_name = "RLAgent"
    return s


def _sitzung(records, fehler: bool):
    ergebnis = MagicMock()
    ergebnis.scalars.return_value.all.return_value = records
    session = MagicMock()
    session.bind.dialect.name = "sqlite"
    session.execute = AsyncMock(
        side_effect=RuntimeError("schema drift") if fehler else None,
        return_value=ergebnis,
    )
    return MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=session),
            __aexit__=AsyncMock(return_value=False),
        )
    )


def _cache(paper: bool) -> str:
    return json.dumps(
        {
            "points": [
                {"date": "2026-09-01", "equity": 1000.0},
                {"date": "2026-09-02", "equity": 1010.0},
            ],
            "spy_points": [
                {"date": "2026-09-01", "equity": 1000.0},
                {"date": "2026-09-02", "equity": 1004.0},
            ],
            "spy_first_close": 500.0,
            "initial_capital": 1000.0,
            "paper_trading": paper,
            "baseline_date": None,
            "flows_sig": "none",
            "start_date": "2026-09-01",
            "end_date": "2026-09-02",
        }
    )


BACKFILL = [
    {"date": "2026-08-28", "equity": 990.0},
    {"date": "2026-08-31", "equity": 995.0},
    {"date": "2026-09-01", "equity": 1000.0},
]

FAELLE = {
    # Name: (paper, cache, records, db_fehler, inception)
    "cache_frisch": (True, "frisch", [], False, None),
    "cache_frisch_ohne_spy_daten": (True, "frisch_leer", [], False, None),
    "neuaufbau_paper_mit_snapshots": (
        True,
        None,
        [_snap(1, 1000.0), _snap(2, 1012.5)],
        False,
        ("2026-08-28", 990.0, BACKFILL),
    ),
    "neuaufbau_paper_db_kaputt": (
        True,
        None,
        [],
        True,
        ("2026-08-28", 990.0, BACKFILL),
    ),
    "neuaufbau_paper_leer": (True, None, [], False, None),
    "neuaufbau_live_broker_kurve": (
        False,
        None,
        [],
        False,
        ("2026-08-28", 990.0, BACKFILL),
    ),
    "neuaufbau_live_ohne_historie": (False, None, [], False, None),
    "neuaufbau_live_mit_snapshots": (
        False,
        None,
        [_snap(1, 1000.0), _snap(2, 1012.5)],
        False,
        None,
    ),
    "modus_wechsel_macht_cache_veraltet": (False, "frisch_paper", [], False, None),
}


def _lauf(handler, paper, cache, records, db_fehler, inception):
    from core.engine import api_routes as ar

    redis = MagicMock()
    redis.get.return_value = {
        None: None,
        "frisch": _cache(paper),
        "frisch_leer": _cache(paper),
        "frisch_paper": _cache(True),
    }[cache]
    engine = MagicMock()
    engine.api.get_account.return_value = MagicMock(equity="1020.0")
    engine.data_provider.get_data.return_value = (
        pd.DataFrame()
        if cache == "frisch_leer"
        else pd.DataFrame(
            {"close": [500.0, 502.0, 505.0]},
            index=pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"]),
        )
    )
    with patch.object(
        ar.RedisClient, "get_sync_redis", return_value=redis
    ), patch.object(ar, "engine", engine), patch.object(
        ar.config, "PAPER_TRADING", paper
    ), patch.object(
        ar, "_get_performance_baseline", AsyncMock(return_value=None)
    ), patch.object(
        ar, "_get_live_cashflows", MagicMock(return_value={"2026-09-02": 5.0})
    ), patch(
        "core.database.session.AsyncSessionLocal", _sitzung(records, db_fehler)
    ), patch(
        "core.engine.routes.benchmark_daten._get_inception_equity",
        MagicMock(return_value=inception),
    ), patch(
        "core.engine.routes.benchmark_daten._read_metrics_db_or_fallback",
        AsyncMock(return_value=({"twr_pct": 1.5, "net_deposits": 5.0}, None)),
    ):
        antwort = asyncio.run(handler())
    return (
        antwort,
        [c.args for c in redis.set.call_args_list],
        [c.args for c in redis.delete.call_args_list],
        engine.data_provider.get_data.call_count,
    )


@pytest.mark.parametrize("fall", list(FAELLE))
def test_alte_und_neue_fassung_antworten_gleich(fall):
    from core.engine.routes.benchmark import get_benchmark_equity

    alt = _lauf(_alte_fassung(), *FAELLE[fall])
    neu = _lauf(get_benchmark_equity, *FAELLE[fall])
    assert neu == alt
    assert alt[0].get("message") != "internal_error"


def test_der_cache_pfad_haengt_heute_keinen_spy_punkt_an_wie_vorher():
    """Charakterisierung, kein Wunschverhalten: Der alte Rumpf rief im Cache-Pfad nie
    ``data_provider.get_data`` (``timezone`` war dort ungebunden, siehe ``ABSICHTLICH_FREI``).
    Der Umbau aendert das nicht; die Korrektur ist ein eigenes Issue."""
    from core.engine.routes.benchmark import get_benchmark_equity

    for handler in (_alte_fassung(), get_benchmark_equity):
        antwort, _, _, abrufe = _lauf(handler, *FAELLE["cache_frisch"])
        assert abrufe == 0
        assert [p["date"] for p in antwort["spy_points"]] == [
            "2026-09-01",
            "2026-09-02",
        ]


def test_ein_fehler_mitten_im_schritt_landet_im_alten_handler():
    from core.engine import api_routes as ar
    from core.engine.routes.benchmark import get_benchmark_equity

    with patch.object(
        ar.RedisClient, "get_sync_redis", side_effect=RuntimeError("redis weg")
    ):
        assert asyncio.run(get_benchmark_equity()) == {
            "points": [],
            "spy_points": [],
            "message": "internal_error",
        }
