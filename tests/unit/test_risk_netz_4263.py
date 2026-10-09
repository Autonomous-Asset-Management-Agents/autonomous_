"""#4263 (H-3a) — Wächter des Netzes für Konto, Halt, Liquidation und Vorprüfung.

Plan: ``docs/4263-*/implementation_plan.md`` §6. Das Netz selbst steht in
``tests/unit/_risk_netz.py``; hier wird geprüft, dass es trägt: Referenz vorhanden,
kein Teilschritt gemockt, jeder Zweig gefahren, nur erlaubte Patch-Ziele, globaler Halt
unberührt, bitgleich wiederholbar, und eine veränderte Liquidation bzw. ein veränderter
Halt wird rot.
"""

from __future__ import annotations

import ast
import copy
import inspect
import json
from pathlib import Path

import pytest

from tests.unit import _risk_netz as netz

pytestmark = [pytest.mark.vc4]

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
    gefunden = netz.befunde(netz.lade_referenz(), gemessen)
    assert gefunden == [], "\n".join(" | ".join(b) for b in gefunden)


def test_jedes_szenario_misst_etwas(gemessen):
    """Leerlauf-Wächter: ein Szenario ohne einen einzigen Messwert prüft nichts."""
    leer = [name for name, wert in gemessen.items() if not netz.messwerte(wert)]
    assert not leer, f"Szenarien ohne Messwert: {leer}"


def test_zweig_wurde_gefahren(gemessen):
    """Je Szenario liefen die Zielfunktionen echt (Aufruf-Mitschnitt, kein Patch)."""
    for sz in netz.SZENARIEN:
        gelaufen = gemessen[sz.name]["gelaufen"]
        fehlt = [f for f in sz.zweige if not gelaufen.get(f)]
        assert not fehlt, f"{sz.name}: nicht gefahren {fehlt}"
    tages_limit = gemessen["tages_limit"]["gelaufen"]
    assert tages_limit.get("_liquidate_through_gateway") == 1
    assert tages_limit.get("liquidate_positions") == 1
    spans = [s for s in gemessen["ki_regel_blockiert"]["schritte"] if s["spans"]]
    assert spans, "Regelschleife ohne Span — Szenario 7 fährt die Schleife nicht"


def _risk_manager_methoden() -> set[str]:
    """Alle Methoden des ``RiskManager`` samt Basisklassen (übersteht den Umzug in
    Mixins, H-3c/H-3g)."""
    from core.risk_manager import RiskManager

    namen = {n for n, _ in inspect.getmembers(RiskManager, inspect.isfunction)}
    assert len(namen) > 15, f"Methodentabelle unplausibel klein: {len(namen)}"
    return namen


def test_kein_teilschritt_gemockt():
    """Kein Szenario überdeckt eine Methode des RiskManager am Exemplar."""
    methoden = _risk_manager_methoden()
    for sz in netz.SZENARIEN:
        rm, _client, _halt = netz.baue(sz)
        ueberdeckt = sorted(methoden & set(vars(rm)))
        assert not ueberdeckt, f"{sz.name}: Instanzattribut überdeckt {ueberdeckt}"


def _patch_aufrufe_im_treiber() -> list[ast.Call]:
    """Jeder Aufruf von ``patch``/``patch.object``/``setattr`` im Treiber."""
    baum = ast.parse(_TREIBER.read_text(encoding="utf-8"))
    aus = []
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        f = knoten.func
        if isinstance(f, ast.Name) and f.id in ("patch", "setattr"):
            aus.append(knoten)
        elif isinstance(f, ast.Attribute) and f.attr in ("object", "setattr"):
            aus.append(knoten)
    return aus


def test_nur_erlaubte_patch_ziele():
    """Der Treiber patcht an genau einer Stelle, und nur die Ziele der Tabelle."""
    aufrufe = _patch_aufrufe_im_treiber()
    assert len(aufrufe) == 1, (
        f"{len(aufrufe)} Patch-Stellen im Treiber — alle Ziele gehören in "
        "PATCH_ZIELE (Zeilen: "
        f"{[a.lineno for a in aufrufe]})"
    )
    ziele = {f"{modul}.{name}" for modul, name in netz.PATCH_ZIELE}
    assert ziele == netz.ERLAUBTE_PATCH_ZIELE, sorted(ziele ^ netz.ERLAUBTE_PATCH_ZIELE)
    verboten = sorted(
        z for z in ziele if z.startswith(("core.engine.", "core.gateway."))
    )
    assert not verboten, f"Patch im Tor oder im Executor: {verboten}"


def test_globaler_halt_unberuehrt(gemessen):
    for name, wert in gemessen.items():
        assert wert["globaler_halt"] == {
            "resets": 0,
            "trips": 0,
        }, f"{name}: globaler Kill-Switch berührt"


def test_dreimal_bitgleich(gemessen):
    texte = {json.dumps(gemessen, sort_keys=True)}
    for _ in range(2):
        texte.add(json.dumps(netz.messe_alle(), sort_keys=True))
    assert len(texte) == 1, "drei Messungen sind nicht bitgleich"


def test_veraenderte_liquidation_ist_rot():
    referenz = netz.lade_referenz()
    ist = copy.deepcopy(referenz)
    assert netz.veraendere_eine_menge(ist), "keine Breaker-Order in der Referenz"
    assert netz.befunde(referenz, ist), "veränderte Menge blieb ohne Befund"


def test_veraenderter_halt_ist_rot():
    referenz = netz.lade_referenz()
    ist = copy.deepcopy(referenz)
    assert netz.veraendere_den_halt(ist), "kein gesetzter Halt im Tages-Limit"
    assert netz.befunde(referenz, ist), "veränderter Halt blieb ohne Befund"
