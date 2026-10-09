"""#4242 (H-2a) — Wächter des Zyklus-Netzes der Handelsschleife.

Plan: ``docs/4242-h-2a-a-zyklus-netz/implementation_plan.md`` §6. Das Netz selbst steht in
``tests/unit/_zyklus_netz.py``; hier wird geprüft, dass es trägt: Referenz vorhanden,
kein Teilschritt gemockt, jeder Zweig gefahren, nur erlaubte Patch-Ziele, bitgleich
wiederholbar, und eine veränderte Order wird rot.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from tests.unit import _schleifen_quelle
from tests.unit import _zyklus_netz as netz

pytestmark = [pytest.mark.vc3]

_TREIBER = Path(netz.__file__)


@pytest.fixture(scope="module")
def gemessen() -> dict:
    return netz.messe_alle()


def test_referenz_existiert():
    assert netz.REFERENZ.is_file(), f"Referenz fehlt: {netz.REFERENZ}"
    assert netz.lade_referenz(), "Referenz ist leer"


def test_jedes_szenario_hat_referenz():
    referenz = netz.lade_referenz()
    namen = {sz.name for sz in netz.SZENARIEN}
    assert namen == set(referenz), (
        f"ohne Referenz: {sorted(namen - set(referenz))}, "
        f"Referenz ohne Szenario: {sorted(set(referenz) - namen)}"
    )


def test_netz_gleich_referenz(gemessen):
    assert netz.befunde(netz.lade_referenz(), gemessen) == []


def test_jedes_szenario_misst_etwas(gemessen):
    """Leerlauf-Wächter: ein Szenario ohne einen einzigen Messwert prüft nichts."""
    leer = [name for name, wert in gemessen.items() if not netz.messwerte(wert)]
    assert not leer, f"Szenarien ohne Messwert: {leer}"


def _mixin_methoden() -> set[str]:
    """Methodennamen von TradingLoopMixin und seinen direkten Themen-Mixins, per ``ast``.

    #4248 (H-2g): Die Basen kommen aus dem Klassenkopf des Kerns (``_schleifen_quelle.basen``)
    statt aus einer festen Liste — sonst fiele jedes umgezogene Thema still aus dem Wächter.
    """
    kern = ast.parse(_schleifen_quelle.PFAD.read_text(encoding="utf-8"))
    klassen = {_schleifen_quelle.KLASSE}
    for knoten in kern.body:
        if isinstance(knoten, ast.ClassDef) and knoten.name == _schleifen_quelle.KLASSE:
            klassen |= {b.id for b in knoten.bases if isinstance(b, ast.Name)}
    namen: set[str] = set()
    for datei in (_schleifen_quelle.PFAD, *_schleifen_quelle.basen()):
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.ClassDef) and knoten.name in klassen:
                namen |= {
                    k.name
                    for k in knoten.body
                    if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
    assert len(namen) > 40, f"Methodentabelle unplausibel klein: {len(namen)}"
    return namen


def test_kein_teilschritt_gemockt():
    """Kein Szenario überdeckt eine Methode der Handelsschleife am Exemplar."""
    methoden = _mixin_methoden()
    for sz in netz.SZENARIEN:
        engine, _ = netz.baue_engine(sz)
        ueberdeckt = sorted(methoden & set(vars(engine)))
        assert not ueberdeckt, f"{sz.name}: Instanzattribut überdeckt {ueberdeckt}"


def test_zweig_wurde_gefahren(gemessen):
    """Je Szenario liefen die Zielmethoden echt (``wraps``-Spione rufen durch)."""
    for sz in netz.SZENARIEN:
        gelaufen = gemessen[sz.name]["gelaufen"]
        fehlt = [m for m in sz.zweige if not gelaufen.get(m)]
        assert not fehlt, f"{sz.name}: nicht gefahren {fehlt}"
    assert (
        gemessen["shutdown_an_der_zyklusgrenze"]["gelaufen"][
            "_perform_graceful_handover"
        ]
        == 1
    )


def _patch_ziele_im_treiber() -> tuple[set[str], set[str]]:
    """Alle Namen, die der Treiber am Modulobjekt ``core.engine.trading_loop`` patcht —
    als Zeichenkette (``"core.engine.trading_loop.X"``) oder per ``patch.object(tl, "X")``.
    Dazu jedes Patch-Ziel unter ``core.engine.`` außerhalb des Kerns."""
    baum = ast.parse(_TREIBER.read_text(encoding="utf-8"))
    am_kern: set[str] = set()
    andere_engine: set[str] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Constant) and isinstance(knoten.value, str):
            wert = knoten.value
            if wert.startswith("core.engine.trading_loop."):
                am_kern.add(wert.removeprefix("core.engine.trading_loop."))
        if (
            isinstance(knoten, ast.Call)
            and isinstance(knoten.func, ast.Attribute)
            and knoten.func.attr == "object"
            and len(knoten.args) >= 2
            and isinstance(knoten.args[0], ast.Name)
            and knoten.args[0].id == "tl"
            and isinstance(knoten.args[1], ast.Constant)
        ):
            am_kern.add(knoten.args[1].value)
    for name in netz.PATCH_ZIELE:
        am_kern.add(name)
    return am_kern, andere_engine


def test_nur_erlaubte_patch_ziele():
    am_kern, _ = _patch_ziele_im_treiber()
    assert am_kern, "Treiber patcht nichts am Kern — Tabelle oder Wächter falsch"
    verboten = {"time", "config"} & {z.split(".")[0] for z in am_kern}
    assert not verboten, f"Modulattribut-Patch am Kern: {verboten}"
    fremd = am_kern - netz.ERLAUBTE_PATCH_ZIELE
    assert not fremd, f"nicht in der Tabelle der Entscheidung §3: {sorted(fremd)}"


def test_dreimal_bitgleich(gemessen):
    texte = {json.dumps(gemessen, sort_keys=True)}
    for _ in range(2):
        texte.add(json.dumps(netz.messe_alle(), sort_keys=True))
    assert len(texte) == 1, "drei Messungen sind nicht bitgleich"


def test_veraenderte_order_ist_rot():
    referenz = netz.lade_referenz()
    ist = copy.deepcopy(referenz)
    assert netz.veraendere_eine_menge(ist), "keine submit_order in der Referenz"
    assert netz.befunde(referenz, ist), "veränderte Menge blieb ohne Befund"
