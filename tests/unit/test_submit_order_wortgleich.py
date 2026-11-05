"""#4089 (ARC-E6 G-7c) — ``_submit_order_safe`` ist wortgleich in Schritte zerlegt.

Der alte Rumpf (412 Zeilen, Stand G-7b) liegt als eingecheckte Kopie in
``tests/fixtures/submit_order_safe_vor_g7c.py.txt``. Jeder Schritt in
``core/strategies/base.py`` ist genau ein Block aus dessen ``try``; erlaubt sind nur die
Abbildungen aus Plan §2.6: ``z.<feld>``, ``return _WEITER`` am Blockende und in
``_einreicher`` die Bindungszeile der freien Variablen. Der Absendeschritt ruft die Fabrik an
der Stelle, an der heute der ``KwargsAuftrag``-Import und die beiden Closures stehen. Fuer die
Closures zaehlt die Einrueckung im Docstring von ``_durchs_tor`` nicht (``inspect.cleandoc``).

Plan: ``docs/4089-g7c-submit-order-schritte/implementation_plan.md``.
"""

from __future__ import annotations

import ast
import builtins
import copy
import dataclasses
import inspect
from pathlib import Path

import pytest

from tests.helpers.wortgleich import schritt_rumpf, unterschiede

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
ALT = PAKET / "tests" / "fixtures" / "submit_order_safe_vor_g7c.py.txt"
BASE = PAKET / "core" / "strategies" / "base.py"

#: Die Bindungszeile in ``_einreicher`` (Plan §2.4) — die freien Variablen der Closures.
BINDUNG = (
    "symbol, order_qty, side, current_price, time_in_force = "
    "z.symbol, z.order_qty, z.side, z.current_price, z.time_in_force"
)
#: An der Stelle von Import und Closures ruft der Absendeschritt die Fabrik.
FABRIKAUFRUF = "_do_submit = self._einreicher(z, _schutz_exit)"


def _alt_funktion() -> ast.AsyncFunctionDef:
    (fn,) = ast.parse(ALT.read_text(encoding="utf-8")).body
    assert isinstance(fn, ast.AsyncFunctionDef) and fn.name == "_submit_order_safe"
    return fn


def _alt_try() -> ast.Try:
    rumpf = _alt_funktion().body
    # 0: Docstring, 1: das eine try
    assert len(rumpf) == 2 and isinstance(rumpf[1], ast.Try)
    return rumpf[1]


def _bloecke() -> dict[str, list[ast.stmt]]:
    """Schritt → alter Block (Plan §1), geschnitten nach obersten Anweisungen im ``try``."""
    rumpf = _alt_try().body
    assert len(rumpf) == 16
    return {
        "_schritt_client": rumpf[0:2],
        "_schritt_compliance": rumpf[2:3],
        "_schritt_markt_geschlossen": rumpf[3:5],
        "_schritt_dedup": rumpf[5:6],
        "_schritt_kaufkraft_pdt": rumpf[6:9],
        "_schritt_menge": rumpf[9:12],
        "_schritt_absenden": rumpf[12:14],
        "_schritt_nachbuchen": rumpf[14:16],
    }


def _live_zweig(block: list[ast.stmt]) -> list[ast.stmt]:
    """Der Live-Zweig des Absendeblocks: ``else`` (nicht async) → ``else`` (nicht Simulation)."""
    verzweigung = block[1]
    assert isinstance(verzweigung, ast.If)
    (innen,) = verzweigung.orelse
    assert isinstance(innen, ast.If)
    return innen.orelse


def _closure_stelle(zweig: list[ast.stmt]) -> int:
    """Index des ``KwargsAuftrag``-Imports; danach folgen ``_durchs_tor`` und ``_do_submit``."""
    (i,) = [
        i
        for i, s in enumerate(zweig)
        if isinstance(s, ast.ImportFrom)
        and s.module == "core.gateway.order_gateway"
        and [a.name for a in s.names] == ["KwargsAuftrag"]
    ]
    assert [getattr(s, "name", None) for s in zweig[i + 1 : i + 3]] == [
        "_durchs_tor",
        "_do_submit",
    ]
    return i


def _methoden() -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    baum = ast.parse(BASE.read_text(encoding="utf-8"))
    (klasse,) = [
        k for k in baum.body if isinstance(k, ast.ClassDef) and k.name == "BaseStrategy"
    ]
    return {
        f.name: f
        for f in klasse.body
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _felder() -> frozenset[str]:
    from core.strategies.base import OrderEinreichung

    return frozenset(f.name for f in dataclasses.fields(OrderEinreichung))


def _rumpf(name: str) -> list[ast.stmt]:
    return schritt_rumpf(
        _methoden()[name],
        objekt="z",
        felder=_felder(),
        importe=frozenset(),
        sentinel="_WEITER",
    )


def test_die_bloecke_decken_das_alte_try_vollstaendig():
    assert sum(len(b) for b in _bloecke().values()) == len(_alt_try().body)


def test_felder_sind_die_aus_plan_paragraf_2():
    assert _felder() == {
        "symbol",
        "qty",
        "side",
        "expected_cost",
        "current_price",
        "held_qty",
        "is_protective_exit",
        "is_simulation",
        "compliance_order",
        "time_in_force",
        "use_fractional",
        "order_qty",
    }


def test_es_gibt_genau_die_acht_schritte_des_plans():
    schritte = {n for n in _methoden() if n.startswith("_schritt_")}
    assert schritte == set(_bloecke())


@pytest.mark.parametrize("name", list(_bloecke()))
def test_jeder_schritt_ist_wortgleich_zu_seinem_block(name):
    schritt = _methoden()[name]
    assert isinstance(schritt, ast.AsyncFunctionDef)
    assert [a.arg for a in schritt.args.args] == ["self", "z"]
    alt = copy.deepcopy(_bloecke()[name])
    if name == "_schritt_absenden":
        zweig = _live_zweig(alt)
        i = _closure_stelle(zweig)
        zweig[i : i + 3] = ast.parse(FABRIKAUFRUF).body
    assert unterschiede(_rumpf(name), alt) == ""


def test_der_einreicher_ist_wortgleich_zu_import_und_closures():
    fabrik = _methoden()["_einreicher"]
    assert isinstance(fabrik, ast.FunctionDef)  # synchron, wie Plan §2.4
    assert [a.arg for a in fabrik.args.args] == ["self", "z", "_schutz_exit"]
    # Ohne Feld-Abbildung: Die Closures lesen die gebundenen Namen, nicht ``z``.
    neu = fabrik.body[1:] if ast.get_docstring(fabrik) else fabrik.body
    assert ast.dump(neu[0]) == ast.dump(ast.parse(BINDUNG).body[0])
    assert ast.dump(neu[-1]) == ast.dump(ast.parse("return _do_submit").body[0])
    zweig = _live_zweig(_bloecke()["_schritt_absenden"])
    i = _closure_stelle(zweig)
    assert (
        unterschiede(
            _docstrings_eingerueckt_gleich(neu[1:-1]),
            _docstrings_eingerueckt_gleich(zweig[i : i + 3]),
        )
        == ""
    )


def _docstrings_eingerueckt_gleich(anweisungen: list[ast.stmt]) -> list[ast.stmt]:
    """Die Closures stehen 12 Spalten weiter links; damit aendert sich die Einrueckung der
    Folgezeilen im Docstring von ``_durchs_tor`` — nur Leerraum, den ``inspect.cleandoc``
    (und ab Python 3.13 der Compiler) ohnehin entfernt. Jeder andere Unterschied zaehlt.
    """
    kopie = copy.deepcopy(anweisungen)
    for fn in (k for s in kopie for k in ast.walk(s)):
        if isinstance(
            fn, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and ast.get_docstring(fn, clean=False):
            fn.body[0].value.value = inspect.cleandoc(fn.body[0].value.value)
    return kopie


def test_der_dirigent_behaelt_signatur_docstring_und_handler():
    alt = _alt_funktion()
    neu = _methoden()["_submit_order_safe"]
    assert isinstance(neu, ast.AsyncFunctionDef)
    assert ast.dump(neu.args) == ast.dump(alt.args)
    assert ast.dump(neu.returns) == ast.dump(alt.returns)
    assert ast.get_docstring(neu) == ast.get_docstring(alt)
    (versuch,) = [s for s in neu.body if isinstance(s, ast.Try)]
    assert unterschiede(versuch.handlers, _alt_try().handlers) == ""
    assert not versuch.orelse and not versuch.finalbody


def _gebunden(fn: ast.AST) -> set[str]:
    namen = {a.arg for a in ast.walk(fn) if isinstance(a, ast.arg)}
    for k in ast.walk(fn):
        if isinstance(k, ast.Name) and isinstance(k.ctx, (ast.Store, ast.Del)):
            namen.add(k.id)
        elif isinstance(k, (ast.Import, ast.ImportFrom)):
            namen |= {(a.asname or a.name).split(".")[0] for a in k.names}
        elif isinstance(k, ast.ExceptHandler) and k.name:
            namen.add(k.name)
        elif isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef)):
            namen.add(k.name)
    return namen


@pytest.mark.parametrize("name", [*_bloecke(), "_einreicher"])
def test_kein_schritt_liest_einen_namen_den_er_nicht_selbst_bindet(name):
    """Jeder Wert, der zwischen Bloecken fliesst, geht ueber ``z`` — sonst waere er frei
    und der Schritt wuerfe ``NameError`` mitten im Kapitalpfad (der ``except`` des
    Dirigenten machte daraus lautlos ``False``)."""
    baum = ast.parse(BASE.read_text(encoding="utf-8"))
    oben = [s for s in baum.body if not isinstance(s, ast.ClassDef)]
    modul = _gebunden(ast.Module(body=oben, type_ignores=[])) | {
        s.name for s in baum.body if isinstance(s, ast.ClassDef)
    }
    fn = _methoden()[name]
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
    assert not frei, frei


def test_jedes_feld_ist_gesetzt_bevor_ein_spaeterer_schritt_es_liest():
    gesetzt = {
        "symbol",
        "qty",
        "side",
        "expected_cost",
        "current_price",
        "held_qty",
        "is_protective_exit",
    }  # Konstruktor: die sieben Parameter
    # Der Einreicher laeuft innerhalb des Absendeschritts.
    for name in [*_bloecke(), "_einreicher"]:
        zugriffe = [
            k
            for k in ast.walk(_methoden()[name])
            if isinstance(k, ast.Attribute)
            and isinstance(k.value, ast.Name)
            and k.value.id == "z"
        ]
        hier = {k.attr for k in zugriffe if isinstance(k.ctx, ast.Store)}
        gelesen = {k.attr for k in zugriffe if isinstance(k.ctx, ast.Load)}
        assert gelesen <= gesetzt | hier, (name, gelesen - gesetzt - hier)
        gesetzt |= hier


def test_keine_funktion_in_base_ueber_der_schwelle():
    for fn in ast.walk(ast.parse(BASE.read_text(encoding="utf-8"))):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert fn.end_lineno - fn.lineno + 1 <= 150, fn.name
