"""#4183 (H-1) — die Schnitt-Entscheidung für ``order_executor.py`` deckt die Datei vollständig.

Plan: ``docs/4183-h-1-a-schnitt-entscheidung-order-executor-py-unt/implementation_plan.md`` §5/§6.
Dokument: ``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``.

Das Dokument ordnet jedes Symbol der Datei genau einem Thema und Zielmodul zu, nennt je
Zielmodul eine Planzahl, je überlanger Funktion die Zerlegungstechnik und je Umzug die
Größenklasse samt ``mess_zuschnitt.py``-Messung. Gelesen wird der Code per ``ast``, ohne
Import (Muster ``_uebergabe_quelle.py``).

Die Zuordnung ist **lebend**: Zieht ein Umzug ein Symbol in sein Zielmodul, zeigt die Zeile
danach dorthin (``TorMixin._sende_durchs_tor`` in ``absendung_tor.py``), und die Zeilenzahl ist
wieder die gemessene. Was in ``order_executor.py`` neu entsteht, braucht eine Zeile.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.architecture import regeln

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
ENGINE = PAKET / "core" / "engine"
QUELLE = "order_executor.py"
DOKUMENT = (
    PAKET.parent / "docs" / "3738-arc-e6-gestalt" / "H1_SCHNITT_order_executor.md"
)
DIRIGENT_KLASSE = "OrderExecutorMixin"
DATEI_GRENZE = 800
FUNKTION_GRENZE = 150
UEBERLANG = ("_process_signal_event", "_sende_durchs_tor", "execute_approved_order")

_SYMBOL = re.compile(r"^`(\w+\.py)::([\w.]+)`$")
_MODUL = re.compile(r"^`(\w+\.py)`$")
_UMZUG = re.compile(r"^### (H-1[a-z]\d?)\b", re.M)


def _symbole(pfad: Path, nur_klasse: str | None = None) -> dict[str, int]:
    """Funktionen und Klassen auf Modulebene und Methoden, je mit AST-Zeilenzahl."""
    baum = ast.parse(pfad.read_text(encoding="utf-8"))
    symbole = {}
    for knoten in baum.body:
        if not isinstance(
            knoten, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        symbole[knoten.name] = knoten.end_lineno - knoten.lineno + 1
        if isinstance(knoten, ast.ClassDef) and nur_klasse in (None, knoten.name):
            for m in knoten.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbole[f"{knoten.name}.{m.name}"] = m.end_lineno - m.lineno + 1
    return symbole


def _abschnitt(text: str, kopf: str) -> str:
    """Text ab der Überschrift, die mit ``kopf`` beginnt, bis zur nächsten gleicher Ebene."""
    ebene = kopf.split(" ", 1)[0]
    treffer = re.search(rf"^{re.escape(kopf)}.*$", text, re.M)
    assert treffer, f"Abschnitt {kopf!r} fehlt in {DOKUMENT.name}"
    rest = text[treffer.end() :]
    ende = re.search(rf"^#{{1,{len(ebene)}}} ", rest, re.M)
    return rest[: ende.start()] if ende else rest


def _zeilen(text: str, kopf: str) -> list[list[str]]:
    """Die Tabelle direkt unter ``kopf``: Zeilen, deren erste Zelle in Backticks steht.

    Kopf und Trenner fallen weg; die Tabelle endet an der nächsten Überschrift jeder Ebene.
    """
    abschnitt = _abschnitt(text, kopf)
    naechste = re.search(r"^#+ ", abschnitt, re.M)
    abschnitt = abschnitt[: naechste.start()] if naechste else abschnitt
    return [
        [z.strip() for z in zeile.strip().strip("|").split("|")]
        for zeile in abschnitt.splitlines()
        if zeile.startswith("| `")
    ]


@pytest.fixture(scope="module")
def text() -> str:
    assert DOKUMENT.is_file(), f"Schnitt-Dokument fehlt: {DOKUMENT}"
    return DOKUMENT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def zuordnung(text) -> list[tuple[str, str, int, str]]:
    """(Datei, Symbol, Zeilen, Zielmodul) je Zeile der Thementabelle."""
    zeilen = []
    for zellen in _zeilen(text, "## 1."):
        sym, ziel = _SYMBOL.match(zellen[0]), _MODUL.match(zellen[3])
        assert sym and ziel, f"Zeile nicht lesbar: {zellen}"
        zeilen.append((sym.group(1), sym.group(2), int(zellen[1]), ziel.group(1)))
    return zeilen


@pytest.fixture(scope="module")
def zielmodule(text) -> dict[str, list[str]]:
    return {_MODUL.match(z[0]).group(1): z for z in _zeilen(text, "## 2.")}


def test_jedes_symbol_hat_genau_ein_thema(zuordnung, zielmodule):
    namen = [sym.rsplit(".", 1)[-1] for _, sym, _, _ in zuordnung]
    doppelt = sorted({n for n in namen if namen.count(n) > 1})
    assert not doppelt, f"mehr als einem Thema zugeordnet: {doppelt}"

    im_dokument = {sym for datei, sym, _, _ in zuordnung if datei == QUELLE}
    im_code = _symbole(ENGINE / QUELLE, nur_klasse=DIRIGENT_KLASSE)
    im_code.pop(DIRIGENT_KLASSE)  # die Klasse ist der Rahmen, ihre Methoden die Symbole
    fehlend = sorted(set(im_code) - im_dokument)
    assert not fehlend, f"in {QUELLE}, aber ohne Thema: {fehlend}"

    for datei, sym, zeilen, ziel in zuordnung:
        assert ziel in zielmodule, f"{sym}: Zielmodul {ziel} steht nicht in Abschnitt 2"
        assert datei in (
            QUELLE,
            ziel,
        ), f"{datei}::{sym} liegt weder in {QUELLE} noch in {ziel}"
        gemessen = _symbole(ENGINE / datei).get(sym)
        assert gemessen is not None, f"{datei}::{sym} steht im Dokument, nicht im Code"
        assert (
            gemessen == zeilen
        ), f"{datei}::{sym}: {gemessen} Zeilen, Dokument sagt {zeilen}"


def test_zielmodule_unter_800(zuordnung, zielmodule):
    for modul, zellen in zielmodule.items():
        summe = sum(z for _, _, z, ziel in zuordnung if ziel == modul)
        _, _, symbole, zuschlag, planzahl, begruendung = zellen
        assert (
            int(symbole) == summe
        ), f"{modul}: Symbole {symbole}, Thementabelle {summe}"
        assert int(planzahl) == summe + int(
            zuschlag
        ), f"{modul}: Planzahl rechnet nicht"
        assert begruendung, f"{modul}: Zuschlag {zuschlag} ohne Begründung"
        assert (
            int(planzahl) <= DATEI_GRENZE
        ), f"{modul}: Planzahl {planzahl} > {DATEI_GRENZE}"


def test_ueberlange_funktionen_haben_technik(text, zuordnung):
    technik = {
        _SYMBOL.match(z[0]).group(2).rsplit(".", 1)[-1]: z[2]
        for z in _zeilen(text, "### Funktionen über 150")
    }
    ueberlang = {
        sym.rsplit(".", 1)[-1] for _, sym, z, _ in zuordnung if z > FUNKTION_GRENZE
    }
    for name in sorted(ueberlang | set(UEBERLANG)):
        assert technik.get(name, "").strip(), f"{name}: keine Zerlegungstechnik genannt"


def test_umzuege_tragen_klasse(text):
    abschnitt = _abschnitt(text, "## 5.")
    teile = _UMZUG.split(abschnitt)[1:]
    assert teile, "Abschnitt 5 nennt keinen Umzug"
    for name, rumpf in zip(teile[::2], teile[1::2]):
        klasse = re.search(r"^Klasse: \*\*([SML])\*\*", rumpf, re.M)
        assert klasse and klasse.group(1) in "SM", f"{name}: Klasse S oder M fehlt"
        aufruf = re.search(r"^python scripts/mess_zuschnitt\.py .+$", rumpf, re.M)
        assert aufruf, f"{name}: mess_zuschnitt-Aufruf fehlt"
        assert "--verschoben" in aufruf.group(
            0
        ) and "--signatur-stabil" in aufruf.group(0)
        gemessen = re.search(r"^Groessenklasse: \*\*([SML])\*\*", rumpf, re.M)
        assert gemessen, f"{name}: Ausgabe von mess_zuschnitt fehlt"
        assert gemessen.group(1) == klasse.group(1), f"{name}: Klasse ≠ Messung"


def test_jedes_zielmodul_hat_ein_netz(text, zielmodule):
    umzuege = set(_UMZUG.findall(_abschnitt(text, "## 5.")))
    netze = {_MODUL.match(z[0]).group(1): z for z in _zeilen(text, "## 4.")}
    for modul in zielmodule:
        assert modul in netze, f"{modul}: kein Eintrag in Abschnitt 4"
        _, netz, _, baut = netze[modul]
        gebaut = set(re.findall(r"H-1[a-z]\d?", baut)) & umzuege
        assert (
            netz.strip("— ") or gebaut
        ), f"{modul}: weder Netz noch Umzug, der es baut"


@pytest.mark.parametrize("abschnitt", ["ausnahmen_dateien", "ausnahmen_funktionen"])
def test_vertragseintraege_des_executors_sind_gestrichen(abschnitt):
    """#4240 (H-1k): Datei (960) und Dirigent (148) liegen unter den Schwellen des Vertrags.

    Bis H-1k prüfte dieser Test, dass beide Einträge nicht steigen und auf den Schnitt
    verweisen. Gestrichen kehren sie nicht zurück: ``test_groessen_gegen_den_code`` meldet
    jede Stelle über der Schwelle ohne Eintrag.
    """
    teil = regeln.lade_vertrag()["groessen"][abschnitt]
    assert "core/engine/order_executor.py" not in teil
