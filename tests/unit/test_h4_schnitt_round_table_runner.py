"""#4186 (H-4) — die Schnitt-Entscheidung für ``round_table/runner.py`` deckt die Datei vollständig.

Plan: ``docs/4186-*/implementation_plan.md`` §5/§6. Das Dokument
``docs/3738-arc-e6-gestalt/H4_SCHNITT_round_table_runner.md`` ordnet jede Funktion und jede
Modulvariable auf oberster Ebene von ``runner.py`` **genau einem** Zielmodul zu, nennt je
Zielmodul eine Planzahl (≤ 800) und ein Netz, je Patch-Ziel der Tests den Weg (a/b/c) und je
Umzug eine Größenklasse (S/M) samt ``mess_zuschnitt``-Aufruf.

Gelesen wird ohne Import (``ast``), Muster ``test_h3_schnitt_risk_manager.py``: ein Import
von ``core.round_table.runner`` lädt ``config`` und die Agenten. Ein Symbol gilt als
gefunden, wenn es in ``runner.py`` **oder** in seinem Zielmodul steht — so bleibt der Test
über die Umzüge hinweg wahr und prüft zugleich, dass ein Symbol nur dorthin wandert, wo das
Dokument es hinschickt.

Anders als bei H-1 bis H-3 trägt der Anker ein Verzeichnis (``round_table/runner.py::NAME``):
Es gibt zwei ``runner.py`` im Paket (``core/sim/runner.py``), und einen mehrdeutigen
Dateinamen überspringt ``scripts/check_doc_anchors.py`` stillschweigend.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
WURZEL = PAKET.parent
QUELLE = "core/round_table/runner.py"
DOKUMENT = WURZEL / "docs" / "3738-arc-e6-gestalt" / "H4_SCHNITT_round_table_runner.md"
VERTRAG = PAKET / "tests" / "architecture" / "vertrag.toml"

DATEI_GRENZE = 800
FUNKTION_GRENZE = 150

# Vor dem Umzug ``round_table/runner.py::SYMBOL``, danach ``round_table/<ziel>.py::SYMBOL``.
_ZUORDNUNG = re.compile(
    r"^\|\s*`round_table/(\w+\.py)::(\w+)`\s*\|\s*(\d+)\s*\|\s*`(core/round_table/\w+\.py)`\s*\|",
    re.M,
)
_ZIELMODUL = re.compile(
    r"^\|\s*`(core/round_table/\w+\.py)`\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|([^|]*)\|([^|]*)\|\s*$",
    re.M,
)
_UMZUG = re.compile(r"^\|\s*(H-4[a-z])\s*\|([^|]*)\|\s*([SML])\s*\|", re.M)
_PATCH_ZEILE = re.compile(
    r"^\|\s*`(\w+)`\s*\|\s*(\d+)[^|]*\|([^|]*)\|([^|]*)\|\s*$", re.M
)


def _dokument() -> str:
    if not DOKUMENT.exists():
        pytest.fail(f"{DOKUMENT.relative_to(WURZEL)} fehlt — die Schnitt-Entscheidung")
    return DOKUMENT.read_text(encoding="utf-8")


def _abschnitt(text: str, nummer: int) -> str:
    treffer = re.search(rf"^## {nummer}\.(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert treffer, f"kein Abschnitt '## {nummer}.' im Dokument"
    return treffer.group(1)


def _spannen(datei: Path) -> dict[str, int]:
    """Funktionen und Modulvariablen auf oberster Ebene → Zeilen (ab Dekorator bis
    ``end_lineno``, wie ``regeln.py``). Namen aus ``try``/``if`` und Importe zählen nicht:
    sie sind Kopf, kein Thema."""
    if not datei.exists():
        return {}
    baum = ast.parse(datei.read_text(encoding="utf-8"))
    ergebnis: dict[str, int] = {}
    for knoten in baum.body:
        start = min(
            [knoten.lineno, *(d.lineno for d in getattr(knoten, "decorator_list", []))]
        )
        zeilen = knoten.end_lineno - start + 1
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ergebnis[knoten.name] = zeilen
        elif isinstance(knoten, ast.Assign):
            for ziel in knoten.targets:
                if isinstance(ziel, ast.Name):
                    ergebnis[ziel.id] = zeilen
        elif isinstance(knoten, ast.AnnAssign) and isinstance(knoten.target, ast.Name):
            ergebnis[knoten.target.id] = zeilen
    return ergebnis


def _zuordnung(text: str) -> list[tuple[str, int, str]]:
    zeilen = _ZUORDNUNG.findall(text)
    falsch = [
        f"round_table/{datei}::{n}"
        for datei, n, _, ziel in zeilen
        if datei not in {"runner.py", Path(ziel).name}
    ]
    assert not falsch, f"Anker zeigt weder auf runner.py noch aufs Zielmodul: {falsch}"
    return [(n, int(z), ziel) for _, n, z, ziel in zeilen]


def _gemessen(zielmodule) -> dict[str, int]:
    gemessen = {**_spannen(PAKET / QUELLE)}
    for ziel in zielmodule:
        if ziel != QUELLE:
            gemessen.update(_spannen(PAKET / ziel))
    return gemessen


def _patch_namen() -> dict[str, set[str]]:
    """Name → Testdateien, die ihn über das Modulobjekt ``core.round_table.runner`` patchen
    oder zuweisen. Seit #4275 (H-4b) eine Erhebung mit dem Patch-Ziel-Wächter."""
    from tests.unit import _round_table_quelle as rq

    return rq.gepatchte_namen(
        PAKET / "tests", ausser=(*rq.BEISPIELE, Path(__file__).resolve())
    )


def test_jedes_symbol_hat_genau_ein_thema():
    zuordnung = _zuordnung(_dokument())
    assert (
        zuordnung
    ), "keine Zeile `round_table/runner.py::SYMBOL` | Zeilen | `Zielmodul` |"

    namen = [n for n, _, _ in zuordnung]
    doppelt = sorted({n for n in namen if namen.count(n) > 1})
    assert not doppelt, f"mehr als einem Zielmodul zugeordnet: {doppelt}"

    im_code = set(_spannen(PAKET / QUELLE))
    fehlt_im_dokument = sorted(im_code - set(namen))
    assert (
        not fehlt_im_dokument
    ), f"in {QUELLE}, aber ohne Thema im Dokument: {fehlt_im_dokument}"

    verloren = []
    for name, _, ziel in zuordnung:
        if name in im_code:
            continue
        if name not in _spannen(PAKET / ziel):
            verloren.append(f"{name} (weder in {QUELLE} noch in {ziel})")
    assert not verloren, f"im Dokument, aber nicht im Code: {verloren}"


def test_zielmodule_unter_800():
    text = _dokument()
    zuordnung = _zuordnung(text)
    zielmodule = {
        ziel: (int(plan), int(kopf))
        for ziel, plan, kopf, _, _ in _ZIELMODUL.findall(text)
    }
    assert (
        zielmodule
    ), "keine Zielmodul-Zeile `core/round_table/x.py` | Planzahl | Kopf |"

    unbekannt = sorted({ziel for _, _, ziel in zuordnung} - set(zielmodule))
    assert not unbekannt, f"zugeordnet, aber ohne Planzahl: {unbekannt}"

    gemessen = _gemessen(zielmodule)
    umzugs_inhalte = " ".join(inhalt for _, inhalt, _ in _UMZUG.findall(text))

    befunde = []
    for ziel, (plan, kopf) in sorted(zielmodule.items()):
        symbole = [n for n, _, z in zuordnung if z == ziel]
        summe = sum(gemessen[n] for n in symbole if n in gemessen)
        # eine Leerzeile je Symbol trennt es vom nächsten
        untergrenze = kopf + summe + len(symbole)
        if plan > DATEI_GRENZE:
            befunde.append(f"{ziel}: Planzahl {plan} > {DATEI_GRENZE}")
        if plan < untergrenze:
            befunde.append(
                f"{ziel}: Planzahl {plan} < Kopf {kopf} + Spannen {summe} "
                f"+ {len(symbole)} Trennzeilen = {untergrenze}"
            )
        # Über 150 bleibt eine Funktion nur mit eigenem Umzugsposten (Zerlegung in Schritte).
        for n in symbole:
            if gemessen.get(n, 0) > FUNKTION_GRENZE and f"`{n}`" not in umzugs_inhalte:
                befunde.append(
                    f"{ziel}: {n} hat {gemessen[n]} > {FUNKTION_GRENZE} "
                    "und keinen eigenen Umzugsposten"
                )
    assert not befunde, befunde


def test_jeder_patchname_hat_eine_regel():
    gemessen = _patch_namen()
    assert (
        gemessen
    ), "keine Patches auf core.round_table.runner gefunden — Erhebung kaputt?"

    regeln: dict[str, str] = {}
    for name, _, _, weg in _PATCH_ZEILE.findall(_abschnitt(_dokument(), 3)):
        regeln[name] = weg

    fehlt = {n: sorted(gemessen[n]) for n in sorted(set(gemessen) - set(regeln))}
    assert not fehlt, f"Patch-Ziele ohne Regel im Dokument (§3): {fehlt}"
    ohne_weg = sorted(n for n in gemessen if not re.search(r"\([abc]\)", regeln[n]))
    assert not ohne_weg, f"Regel ohne Weg (a), (b) oder (c): {ohne_weg}"


def test_umzuege_tragen_klasse():
    text = _dokument()
    umzuege = _UMZUG.findall(text)
    assert umzuege, "keine Umzugszeile | H-4x | … | S/M |"

    erster_kennung, erster_inhalt, _ = umzuege[0]
    assert (
        "Charakterisierung" in erster_inhalt and "`run_round_table`" in erster_inhalt
    ), f"{erster_kennung}: der erste Umzug baut nicht das Netz für run_round_table"

    for kennung, _, klasse in umzuege:
        assert klasse in {"S", "M"}, f"{kennung}: Klasse {klasse} — L wird geteilt"
        abschnitt = re.search(
            rf"^### {re.escape(kennung)}\b(.*?)(?=^### |^## |\Z)", text, re.M | re.S
        )
        assert abschnitt, f"{kennung}: kein Abschnitt '### {kennung}'"
        inhalt = abschnitt.group(1)
        assert "python scripts/mess_zuschnitt.py" in inhalt, f"{kennung}: kein Aufruf"
        assert (
            f"Groessenklasse: **{klasse}**" in inhalt
        ), f"{kennung}: Ausgabe fehlt oder nennt eine andere Klasse als {klasse}"

    # Jedes Zielmodul nennt ein Netz oder den Umzug, der es baut.
    for ziel, _, _, netz, _ in _ZIELMODUL.findall(text):
        assert netz.strip() and netz.strip() != "—", f"{ziel}: kein Netz genannt"


def test_vertragsbegruendung_verweist_auf_schnitt():
    vertrag = tomllib.loads(VERTRAG.read_text(encoding="utf-8"))
    groessen = vertrag["groessen"]
    # #4276 (H-4c): Die Umzüge senken die Zahl (Ratsche), keiner hebt sie über 1358.
    # #4277 (H-4d+H-4e): seit dem Signalbau-Umzug unter der Vertragsschwelle 1000, der
    # Eintrag ist gestrichen — ``regeln.pruefe_groessen`` wacht, dass er nicht zurückkehrt.
    assert (
        groessen["ausnahmen_dateien"].get(QUELLE, 0) <= 1358
    ), "über dem Ausgangswert 1358 — ein Umzug darf die Zahl nicht heben"

    # #4277 (H-4d+H-4e): _score_to_signal zerfällt in signal_bau.py in Schritte unter 150.
    assert (
        "core/round_table/signal_bau.py" not in groessen["ausnahmen_funktionen"]
    ), "keine Signalbau-Funktion über 150 — kein Zwischenschlüssel"
    # #4281 (H-4h): run_round_table in Phasen-Schritte, keine Funktion im Kern über 150 —
    # der Schlüssel des Kerns entfällt ganz.
    assert (
        QUELLE not in groessen["ausnahmen_funktionen"]
    ), "keine Funktion im Kern über 150 — kein Ausnahmeschlüssel für runner.py"


# #4281 (H-4h): die Phasen-Schritte aus der Entscheidung §5, in Aufrufreihenfolge.
PHASEN_SCHRITTE = (
    "_abstimmen",
    "_konsens_bilden",
    "_compliance_pruefen",
    "_signal_ableiten",
    "_beobachten",
    "_protokollieren",
)


def test_dirigent_in_phasen_schritten():
    """#4281 (H-4h): die sechs Schritte stehen im Kern, jede Funktion dort unter 150."""
    im_kern = _spannen(PAKET / QUELLE)
    assert [n for n in PHASEN_SCHRITTE if n not in im_kern] == []
    zu_lang = {n: z for n, z in im_kern.items() if z >= FUNKTION_GRENZE}
    assert zu_lang == {}, f"über {FUNKTION_GRENZE} Zeilen im Kern: {zu_lang}"


def test_signalbau_liegt_in_signal_bau():
    """#4277 (H-4d): die drei Signalbau-Symbole stehen im Zielmodul, nicht mehr im Kern."""
    signalbau = (
        "REASONING_TRACE_MAX_CHARS",
        "_position_fields_from_state",
        "_score_to_signal",
    )
    im_ziel = _spannen(PAKET / "core/round_table/signal_bau.py")
    im_kern = _spannen(PAKET / QUELLE)
    assert [n for n in signalbau if n not in im_ziel] == []
    assert [n for n in signalbau if n in im_kern] == []
    assert all(z <= FUNKTION_GRENZE for z in im_ziel.values()), im_ziel


def test_aufzeichnung_liegt_in_aufzeichnung():
    """#4280 (H-4g): Serialisierung und Schatten-Haken stehen im Zielmodul, nicht im Kern."""
    aufzeichnung = (
        "_AGENT_RECORD_ROLES",
        "_serialize_votes",
        "_maybe_record_shadow_tft_vote",
        "_maybe_record_shadow_specialist_vote",
    )
    im_ziel = _spannen(PAKET / "core/round_table/aufzeichnung.py")
    im_kern = _spannen(PAKET / QUELLE)
    assert [n for n in aufzeichnung if n not in im_ziel] == []
    assert [n for n in aufzeichnung if n in im_kern] == []
    assert all(z <= FUNKTION_GRENZE for z in im_ziel.values()), im_ziel


def test_kein_stummes_except_im_kern():
    """Review #4407 (FINDING-01, P1, CODING_POLICY §2.5): `except Exception: pass` ohne
    Logging ist ein Bug - auch dort, wo die Beobachtung den Ablauf nie aendern darf.
    Die vier Bloecke standen wortgleich schon auf main; sie bekommen ein WARNING."""
    baum = ast.parse((PAKET / QUELLE).read_text(encoding="utf-8"))
    stumm = [
        h.lineno
        for h in ast.walk(baum)
        if isinstance(h, ast.ExceptHandler)
        and all(isinstance(s, ast.Pass) for s in h.body)
    ]
    assert stumm == [], f"stumme except-Bloecke in {QUELLE}: Zeilen {stumm}"
