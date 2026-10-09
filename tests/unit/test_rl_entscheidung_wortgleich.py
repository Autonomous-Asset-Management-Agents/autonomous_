"""#4088 (ARC-E6 G-7b) — ``_run_for_symbol_impl`` ist wortgleich in Schritte zerlegt.

Der alte Rumpf (430 Zeilen, Stand G-7a) liegt als eingecheckte Kopie in
``tests/fixtures/rl_run_for_symbol_vor_g7b.py.txt``. Jeder Schritt in
``core/strategies/rl_entscheidung.py`` ist genau ein Block daraus; erlaubt sind nur die
Abbildungen aus Plan §2.4: ``z.<feld>``, ``return _WEITER`` am Blockende und der lokale
``SimulationAdapter``-Import, den jeder Schritt wiederholt, der ihn braucht.

Plan: ``docs/4088-g7b-rl-execution-schritte/implementation_plan.md``.
"""

from __future__ import annotations

import ast
import builtins
import dataclasses
from pathlib import Path

import pytest

from tests.helpers.wortgleich import (
    import_dumps,
    ohne_importe,
    schritt_rumpf,
    unterschiede,
)

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
ALT = PAKET / "tests" / "fixtures" / "rl_run_for_symbol_vor_g7b.py.txt"
SCHRITTE = PAKET / "core" / "strategies" / "rl_entscheidung.py"

#: Der lokale Import des alten Rumpfs; jeder Schritt wiederholt ihn, wenn er ihn braucht.
LOKALE_IMPORTE = import_dumps(
    ("from core.simulation_adapter import SimulationAdapter",)
)


def _alt_funktion() -> ast.AsyncFunctionDef:
    (fn,) = ast.parse(ALT.read_text(encoding="utf-8")).body
    assert isinstance(fn, ast.AsyncFunctionDef) and fn.name == "_run_for_symbol_impl"
    return fn


def _bloecke() -> dict[str, list[ast.stmt]]:
    """Schritt → alter Block (Plan §1), geschnitten nach obersten Anweisungen."""
    rumpf = _alt_funktion().body
    # 0: Docstring, 1: lokaler SimulationAdapter-Import, 2..32: die Bloecke
    assert len(rumpf) == 33 and isinstance(rumpf[1], ast.ImportFrom)
    return {
        "_schritt_lage": rumpf[2:13],
        "_schritt_ausstieg_halten": rumpf[13:15],
        "_schritt_halten_abschliessen": rumpf[15:17],
        "_schritt_kauf_freigabe": rumpf[17:20],
        "_schritt_verkaufszaehler": rumpf[20:22],
        "_schritt_risikofilter": rumpf[22:25],
        "_schritt_tausch": rumpf[25:26],
        "_schritt_groesse": rumpf[26:29],
        "_schritt_anti_churn": rumpf[29:30],
        "_schritt_kauf_absenden": rumpf[30:31],
        "_schritt_protokoll": rumpf[31:33],
    }


def _schritte() -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    baum = ast.parse(SCHRITTE.read_text(encoding="utf-8"))
    (klasse,) = [
        k
        for k in baum.body
        if isinstance(k, ast.ClassDef) and k.name == "RLEntscheidungsSchritte"
    ]
    return {
        f.name: f
        for f in klasse.body
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _felder() -> frozenset[str]:
    from core.strategies.rl_entscheidung import SymbolEntscheidung

    return frozenset(f.name for f in dataclasses.fields(SymbolEntscheidung))


def test_die_bloecke_decken_den_alten_rumpf_vollstaendig():
    geschnitten = sum(len(b) for b in _bloecke().values())
    # alles ausser Docstring und lokalem Import
    assert geschnitten == len(_alt_funktion().body) - 2


def test_felder_sind_die_aus_plan_paragraf_1():
    assert _felder() == {
        "symbol",
        "ohlc_data",
        "market_data",
        "current_time",
        "features",
        "pred",
        "raw_rl_action",
        "rl_action",
        "in_pos",
        "qty",
        "avg",
        "curr",
        "exit_info",
        "triggered_exit",
        "signal",
        "symbol_to_close",
        "mods",
        "size",
        "conviction",
    }


def test_es_gibt_genau_die_elf_schritte_des_plans():
    assert set(_schritte()) == set(_bloecke())


@pytest.mark.parametrize("name", list(_bloecke()))
def test_jeder_schritt_ist_wortgleich_zu_seinem_block(name):
    schritt = _schritte()[name]
    assert isinstance(schritt, ast.AsyncFunctionDef)
    assert [a.arg for a in schritt.args.args] == ["self", "z"]
    neu = schritt_rumpf(
        schritt,
        objekt="z",
        felder=_felder(),
        importe=LOKALE_IMPORTE,
        sentinel="_WEITER",
    )
    alt = ohne_importe(_bloecke()[name], LOKALE_IMPORTE)
    assert unterschiede(neu, alt) == ""


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


def test_kein_schritt_liest_einen_namen_den_er_nicht_selbst_bindet():
    """Jeder Wert, der zwischen Bloecken fliesst, geht ueber ``z`` — sonst waere er frei
    und der Schritt wuerfe ``NameError`` mitten im Kapitalpfad."""
    baum = ast.parse(SCHRITTE.read_text(encoding="utf-8"))
    oben = [s for s in baum.body if not isinstance(s, ast.ClassDef)]
    modul = _gebunden(ast.Module(body=oben, type_ignores=[])) | {
        s.name
        for s in baum.body
        if isinstance(s, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name, fn in _schritte().items():
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
        assert not frei, (name, frei)


def test_jedes_feld_ist_gesetzt_bevor_ein_spaeterer_schritt_es_liest():
    gesetzt = {"symbol", "ohlc_data", "market_data", "current_time"}  # Konstruktor
    for name in _bloecke():
        zugriffe = [
            k
            for k in ast.walk(_schritte()[name])
            if isinstance(k, ast.Attribute)
            and isinstance(k.value, ast.Name)
            and k.value.id == "z"
        ]
        hier = {k.attr for k in zugriffe if isinstance(k.ctx, ast.Store)}
        gelesen = {k.attr for k in zugriffe if isinstance(k.ctx, ast.Load)}
        assert gelesen <= gesetzt | hier, (name, gelesen - gesetzt - hier)
        gesetzt |= hier


def test_keine_funktion_ueber_der_schwelle():
    for pfad in (SCHRITTE, PAKET / "core" / "strategies" / "rl_execution.py"):
        for fn in ast.walk(ast.parse(pfad.read_text(encoding="utf-8"))):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert fn.end_lineno - fn.lineno + 1 <= 150, (pfad.name, fn.name)


def test_wiederholte_importe_stammen_aus_dem_alten_rumpf():
    alt = {
        ast.dump(s)
        for s in ast.walk(_alt_funktion())
        if isinstance(s, (ast.Import, ast.ImportFrom))
    }
    assert LOKALE_IMPORTE <= alt
