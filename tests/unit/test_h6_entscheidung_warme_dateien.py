"""#4189 (H-6) — jede warme Datei aus Epic #3738 §8.2 hat genau eine Entscheidung.

Plan: ``docs/4189-*/implementation_plan.md`` §4/§5.
Dokument: ``docs/3738-arc-e6-gestalt/H6_ENTSCHEIDUNG_warme_dateien.md``.

Das Dokument trägt je Datei Zeilen, Commits, Fixes und die Commits ohne ARC-E6-Umbauten
aus einem Messpunkt in ``HOTSPOT_KENNZAHLEN.md``. Die Regel (Plan §2.1) entscheidet, nicht
der Leser: ``kalt`` genau dann, wenn höchstens 3 Commits ohne ARC-E6 bleiben, sonst
``schnitt`` mit Folge-Sub-Issue. Die Messwerte sind die vom Messtag; der Test vergleicht
sie mit dem Messpunkt, nicht mit dem Code von heute — die Umzüge der Folge-Sub-Issues
dürfen die Dateien danach verändern.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.architecture import regeln

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
GESTALT = PAKET.parent / "docs" / "3738-arc-e6-gestalt"
DOKUMENT = GESTALT / "H6_ENTSCHEIDUNG_warme_dateien.md"
KENNZAHLEN = GESTALT / "HOTSPOT_KENNZAHLEN.md"
WARM = ("core/engine/base.py", "core/compliance.py", "core/hitl_gate.py")
ANLASS = "H-6 (#4189)"
#: Plan §2.1, dieselbe Grenze wie H-K (#4188).
KALT_HOECHSTENS = regeln.KALT_HOECHSTENS_COMMITS

_DATEI = re.compile(r"^`([\w/]+\.py)`$")
_MESSPUNKT = re.compile(
    r"^Messpunkt: (\d{4}-\d{2}-\d{2}) — `origin/main` @ `([0-9a-f]{9})`$", re.M
)


def _abschnitt(text: str, kopf: str) -> str:
    """Text ab der Überschrift, die mit ``kopf`` beginnt, bis zur nächsten Überschrift."""
    treffer = re.search(rf"^{re.escape(kopf)}.*$", text, re.M)
    assert treffer, f"Abschnitt {kopf!r} fehlt in {DOKUMENT.name}"
    rest = text[treffer.end() :]
    ende = re.search(r"^#+ ", rest, re.M)
    return rest[: ende.start()] if ende else rest


def _zeilen(abschnitt: str) -> list[list[str]]:
    """Tabellenzeilen, deren erste Zelle in Backticks steht."""
    return [
        [z.strip() for z in zeile.strip().strip("|").split("|")]
        for zeile in abschnitt.splitlines()
        if zeile.startswith("| `")
    ]


@pytest.fixture(scope="module")
def text() -> str:
    assert DOKUMENT.is_file(), f"Entscheidungsdokument fehlt: {DOKUMENT}"
    return DOKUMENT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def entscheidungen(text) -> dict[str, dict]:
    """Je Datei: Zeilen, Commits, Fixes, ohne ARC-E6, Entscheidung, Folge-Sub-Issue."""
    zeilen = {}
    for zellen in _zeilen(_abschnitt(text, "## 1.")):
        datei = _DATEI.match(zellen[0])
        assert datei and len(zellen) >= 8, f"Zeile nicht lesbar: {zellen}"
        assert datei.group(1) not in zeilen, f"{datei.group(1)} steht doppelt"
        zeilen[datei.group(1)] = {
            "zellen": zellen[1:5],
            "entscheidung": zellen[5].strip("*` "),
            "folge": zellen[6],
            "begruendung": zellen[7],
        }
    return zeilen


@pytest.fixture(scope="module")
def messpunkt(text) -> tuple[str, str]:
    treffer = _MESSPUNKT.search(text)
    assert treffer, "Dokument nennt keinen Messpunkt (Datum und Commit)"
    return treffer.group(1), treffer.group(2)


def _zahlen(eintrag: dict) -> tuple[int, int, int, int]:
    zeilen, commits, fixes, ohne = eintrag["zellen"]
    return int(zeilen), int(commits), int(fixes), int(ohne)


def test_dokument_existiert_und_nennt_alle_drei_dateien(entscheidungen):
    assert sorted(entscheidungen) == sorted(WARM)


def test_jede_zeile_hat_entscheidung_und_messwerte(text, entscheidungen):
    abgezogen: dict[str, list[str]] = {}
    for zellen in _zeilen(_abschnitt(text, "## 2.")):
        datei = _DATEI.match(zellen[0])
        assert datei and re.fullmatch(r"`[0-9a-f]{9}`", zellen[1]), zellen
        abgezogen.setdefault(datei.group(1), []).append(zellen[1])

    for datei, eintrag in entscheidungen.items():
        assert eintrag["entscheidung"] in ("kalt", "schnitt"), datei
        for zelle in eintrag["zellen"]:
            assert re.fullmatch(r"\d+", zelle), f"{datei}: {zelle!r} ist keine Zahl"
        _, commits, fixes, ohne = _zahlen(eintrag)
        assert fixes <= commits, datei
        assert ohne == commits - len(
            abgezogen.get(datei, [])
        ), f"{datei}: ohne ARC-E6 {ohne}, Commits {commits}, Abschnitt 2 zieht {abgezogen.get(datei, [])} ab"
        assert eintrag["begruendung"], f"{datei}: Entscheidung ohne Begründung"


def test_entscheidung_folgt_der_regel(entscheidungen):
    for datei, eintrag in entscheidungen.items():
        ohne = _zahlen(eintrag)[3]
        erwartet = "kalt" if ohne <= KALT_HOECHSTENS else "schnitt"
        assert eintrag["entscheidung"] == erwartet, (
            f"{datei}: {ohne} Commits ohne ARC-E6, Regel sagt {erwartet}, "
            f"Dokument sagt {eintrag['entscheidung']}"
        )
        if erwartet == "schnitt":
            assert re.search(
                r"#\d+", eintrag["folge"]
            ), f"{datei}: schnitt ohne Folge-Sub-Issue"


def test_messpunkt_steht_in_hotspot_kennzahlen(entscheidungen, messpunkt):
    datum, sha = messpunkt
    kennzahlen = KENNZAHLEN.read_text(encoding="utf-8")
    kopf = f"## Messpunkt {datum} — `origin/main` @ `{sha}`"
    assert kopf in kennzahlen, f"{kopf!r} fehlt in {KENNZAHLEN.name}"
    abschnitt = kennzahlen.split(kopf, 1)[1].split("\n## ", 1)[0]
    assert (
        f"Anlass: {ANLASS}" in abschnitt
    ), f"Messpunkt {datum} hat nicht den Anlass {ANLASS}"

    gemessen = {
        _DATEI.match(z[0]).group(1): (int(z[1]), int(z[3]), int(z[4]))
        for z in _zeilen(abschnitt)
    }
    for datei, eintrag in entscheidungen.items():
        zeilen, commits, fixes, _ = _zahlen(eintrag)
        assert datei in gemessen, f"{datei} fehlt im Messpunkt {datum}"
        assert gemessen[datei] == (zeilen, commits, fixes), (
            f"{datei}: Messpunkt {gemessen[datei]} (Zeilen, Commits, Fixes), "
            f"Dokument {(zeilen, commits, fixes)}"
        )


def test_base_py_vertragszeile_begruendet(entscheidungen, messpunkt):
    eintrag = regeln.lade_vertrag()["groessen"]["ausnahmen_dateien"].get(
        "core/engine/base.py"
    )
    assert isinstance(
        eintrag, dict
    ), f"core/engine/base.py ist eine nackte Zahl ({eintrag}), keine Tabelle"
    assert isinstance(eintrag.get("zeilen"), int)
    begruendung = str(eintrag.get("begruendung", "")).strip()
    assert regeln.HERKUNFT.match(begruendung), begruendung

    entscheidung = entscheidungen["core/engine/base.py"]
    if entscheidung["entscheidung"] == "kalt":
        assert regeln.KALT_FORM.search(begruendung), begruendung
        assert messpunkt[0] in begruendung, f"Messdatum {messpunkt[0]} fehlt"
    else:
        folge = re.search(r"#\d+", entscheidung["folge"]).group(0)
        assert (
            begruendung.startswith("VORLÄUFIG bis ") and folge in begruendung
        ), f"schnitt: Begründung nennt das Folge-Sub-Issue {folge} nicht: {begruendung}"
        assert not regeln.KALT_WORT.search(
            begruendung
        ), "eine schnitt-Begründung darf das Wort 'kalt' nicht tragen (Plan §9.3)"


def test_unter_schwelle_kein_vertragseintrag_noetig(entscheidungen):
    """Über ``datei_schwelle`` braucht die warme Datei eine begründete Vertragszeile.

    Darunter reicht die Zeile im Dokument. Senkt H-A (#4190) die Schwelle auf 800, wird
    der Test für ``compliance.py`` und ``hitl_gate.py`` rot — absichtlich, damit der
    Übergang sichtbar wird statt still.
    """
    teil = regeln.lade_vertrag()["groessen"]
    schwelle = teil["datei_schwelle"]
    heute = {b.was: int(b.zusatz) for b in regeln.datei_groessen(regeln.PAKET, ".")}
    for datei in entscheidungen:
        if heute[datei] <= schwelle:
            continue
        eintrag = teil["ausnahmen_dateien"].get(datei)
        assert isinstance(eintrag, dict) and regeln.HERKUNFT.match(
            str(eintrag.get("begruendung", "")).strip()
        ), (
            f"{datei}: {heute[datei]} Zeilen über der Schwelle {schwelle}, "
            f"Vertragszeile ohne begründete Entscheidung: {eintrag}"
        )
