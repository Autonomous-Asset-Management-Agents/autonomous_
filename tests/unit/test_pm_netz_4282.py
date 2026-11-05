"""#4282 (H-5a) — Wächter des Netzes für Zulassung, Verdrängung und Bericht.

Plan: ``docs/4282-*/implementation_plan.md`` §6. Das Netz selbst steht in
``tests/unit/_pm_netz.py``; hier wird geprüft, dass es trägt: Referenz vorhanden, jeder
Zweig gefahren, kein Teilschritt gemockt, kein Patch am Modul ``core.portfolio_manager``,
jeder Schalter gesetzt, bitgleich wiederholbar, und eine veränderte Zulassung,
Verdrängung oder ein veränderter Bericht wird rot.
"""

from __future__ import annotations

import ast
import copy
import inspect
import json
from pathlib import Path

import pytest

from tests.unit import _pm_netz as netz

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
    gefunden = netz.befunde(netz.lade_referenz(), gemessen)
    assert gefunden == [], "\n".join(" | ".join(b) for b in gefunden)


def test_jedes_szenario_misst_etwas(gemessen):
    """Leerlauf-Wächter: ein Szenario ohne einen einzigen Messwert prüft nichts."""
    leer = [name for name, wert in gemessen.items() if not netz.messwerte(wert)]
    assert not leer, f"Szenarien ohne Messwert: {leer}"


def test_zweig_wurde_gefahren(gemessen):
    """Je Szenario liefen die Zielfunktionen echt und die ausgeschlossenen nicht
    (Aufruf-Mitschnitt, kein Patch)."""
    for sz in netz.SZENARIEN:
        gelaufen = gemessen[sz.name]["gelaufen"]
        fehlt = [f for f in sz.zweige if not gelaufen.get(f)]
        assert not fehlt, f"{sz.name}: nicht gefahren {fehlt}"
        zu_viel = [f for f in sz.nicht if gelaufen.get(f)]
        assert not zu_viel, f"{sz.name}: unerwartet gefahren {zu_viel}"
        assert gelaufen.get("refresh_positions"), f"{sz.name}: kein echter refresh"


def test_zulassung_fuehrt_ueber_den_erwarteten_ausgang(gemessen):
    """Gegen ein Szenario, das grün läuft, ohne seinen Zweig zu fahren: Jeder
    Zulassungsfall endet mit dem Grund, den sein Name verspricht."""
    for sz in netz.SZENARIEN:
        if sz.grund_beginnt is None:
            continue
        erlaubt, grund, _ = gemessen[sz.name]["schritte"][-1]["ergebnis"]
        assert grund.startswith(sz.grund_beginnt), f"{sz.name}: {grund!r}"
        assert erlaubt is sz.erlaubt, f"{sz.name}: erlaubt={erlaubt}"


def _pm_methoden() -> set[str]:
    """Alle Methoden des ``PortfolioManager`` samt Basisklassen (übersteht den Umzug in
    Mixins, H-5c bis H-5k)."""
    from core.portfolio_manager import PortfolioManager

    namen = {n for n, _ in inspect.getmembers(PortfolioManager, inspect.isfunction)}
    assert len(namen) > 30, f"Methodentabelle unplausibel klein: {len(namen)}"
    return namen


def test_kein_teilschritt_gemockt():
    """Kein Szenario überdeckt eine Methode des PortfolioManager am Exemplar."""
    methoden = _pm_methoden()
    for sz in netz.SZENARIEN:
        with netz.umgebung(sz):
            pm, _client = netz.baue(sz)
            netz.fahre_schritte(sz, pm)
        ueberdeckt = sorted(methoden & set(vars(pm)))
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


def test_kein_patch_am_modul():
    """Zugriffsregel (Entscheidung §3): Der Treiber patcht an genau einer Stelle, und
    zwar das Config-Objekt — nichts am Modul ``core.portfolio_manager``."""
    aufrufe = _patch_aufrufe_im_treiber()
    assert len(aufrufe) == 1, (
        f"{len(aufrufe)} Patch-Stellen im Treiber (Zeilen: "
        f"{[a.lineno for a in aufrufe]})"
    )
    (aufruf,) = aufrufe
    assert ast.unparse(aufruf.args[0]) == "get_config()", ast.unparse(aufruf)
    # Auch kein Zugriff über einen Pfad-Text oder ein Modul-Alias: Jeder Bezug auf das
    # Modul ist ein ``from … import`` der Klasse bzw. der Eingabe.
    baum = ast.parse(_TREIBER.read_text(encoding="utf-8"))
    modul = "core.portfolio_manager"
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Constant) and isinstance(knoten.value, str):
            assert not knoten.value.startswith(modul + "."), knoten.value
        if isinstance(knoten, ast.Import):
            assert all(a.name != modul for a in knoten.names), "Modul-Alias im Treiber"
        if isinstance(knoten, ast.ImportFrom) and knoten.module == modul:
            namen = {a.name for a in knoten.names}
            assert namen <= {"OpportunityScore", "PortfolioManager"}, namen


def test_config_vollstaendig_gesetzt():
    """Jeder Schalter aus Plan §2.2 ist in jedem Szenario ausdrücklich gesetzt, und
    kein Szenario setzt einen Schalter, den die Grundlage nicht kennt (Tippfehler)."""
    for sz in netz.SZENARIEN:
        schalter = netz.schalter(sz)
        fehlt = sorted(netz.PFLICHT_SCHALTER - set(schalter))
        assert not fehlt, f"{sz.name}: nicht gesetzt {fehlt}"
        fremd = sorted(set(dict(sz.schalter)) - set(netz.GRUNDLAGE))
        assert not fremd, f"{sz.name}: unbekannte Schalter {fremd}"


def test_debattenzeit_ist_iso(gemessen):
    """Der Debatten-``timestamp`` ist Wanduhr und steht nicht in der Referenz; geprüft
    wird nur, dass er vorhanden und ISO-lesbar war."""
    zeilen = [
        z
        for wert in gemessen.values()
        for s in wert["schritte"]
        for z in s.get("debatte", [])
    ]
    assert zeilen, "keine einzige Debattenzeile gemessen"
    assert all(z["timestamp"] == "iso" for z in zeilen), zeilen


def test_dreimal_bitgleich(gemessen):
    texte = {json.dumps(gemessen, sort_keys=True)}
    for _ in range(2):
        texte.add(json.dumps(netz.messe_alle(), sort_keys=True))
    assert len(texte) == 1, "drei Messungen sind nicht bitgleich"


def test_veraenderte_zulassung_ist_rot():
    referenz = netz.lade_referenz()
    ist = copy.deepcopy(referenz)
    assert netz.veraendere_die_zulassung(ist), "kein Zuschlag in voll_debatte_gewonnen"
    assert netz.befunde(referenz, ist), "veränderte Zulassung blieb ohne Befund"


def test_veraenderte_verdraengung_ist_rot():
    referenz = netz.lade_referenz()
    ist = copy.deepcopy(referenz)
    assert netz.veraendere_die_verdraengung(ist), "keine verdrängte Position"
    assert netz.befunde(referenz, ist), "veränderte Verdrängung blieb ohne Befund"


def test_veraenderter_bericht_ist_rot():
    referenz = netz.lade_referenz()
    ist = copy.deepcopy(referenz)
    assert netz.veraendere_den_bericht(ist), "keine Summary mit Bestand"
    assert netz.befunde(referenz, ist), "veränderter Bericht blieb ohne Befund"
