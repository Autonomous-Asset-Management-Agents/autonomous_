"""#3394 (ARC-E3) — Architekturregeln als pruefbare Befunde.

Vier Regeln, jede mit Datei und Zeile im Befund:

1. **Ratsche** gegen ``getattr(<config>, "NAME", <default>)`` im Kern — die Zahl darf nur sinken.
2. **Stufengrenzen** der Value Chain als Import-Vertraege (``importlinter.ini``).
3. **Genau ein Broker-Aufrufer** — mutierende Broker-Aufrufe nur im Gateway (#3366).
4. **Keine Editionsweiche** im Kern — die Edition wird in der Composition Root entschieden.

Was heute schon anders ist, steht eingefroren in ``vertrag.toml``. Diese Listen duerfen nur
schrumpfen: Ein Befund, der dort nicht steht, ist neu; ein Eintrag, der nicht mehr gefunden
wird, muss mit gestrichen werden. Beides meldet die Regel.
"""

from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import sys
import tokenize
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

HIER = Path(__file__).resolve().parent
PAKET = HIER.parent.parent  # ai_trading_bot/
VERTRAG = HIER / "vertrag.toml"
IMPORT_VERTRAG = HIER / "importlinter.ini"


@dataclass(frozen=True)
class Befund:
    datei: str  # relativ zu ai_trading_bot/, mit "/"
    zeile: int
    was: str

    def __str__(self) -> str:
        return f"{self.datei}:{self.zeile}: {self.was}"


def lade_vertrag(pfad: Path = VERTRAG) -> dict:
    with open(pfad, "rb") as f:
        return tomllib.load(f)


def _relativ(pfad: Path, wurzel: Path) -> str:
    return pfad.relative_to(wurzel).as_posix()


def _python_dateien(wurzel: Path, bereich: str):
    for pfad in sorted((wurzel / bereich).rglob("*.py")):
        rel = _relativ(pfad, wurzel)
        if "/tests/" in f"/{rel}" or "__pycache__" in rel:
            continue
        yield pfad, rel


# ── 1. Ratsche ────────────────────────────────────────────────────────────────

# Die Zaehlmethode aus dem Plan (docs/3394-import-linter-und-ast-fitness, Abschnitt 3):
#   grep -rnE 'getattr\([^,]*[Cc]onfig[^,]*,\s*"[A-Z_]+"\s*,' ai_trading_bot/core
#        --include=*.py | grep -v test
# Zeilenweise, wie grep. ``grep -v test`` verwirft jede Ausgabezeile, in der "test"
# vorkommt — im Pfad oder im Code; ``ratschen_fundstellen`` bildet beides nach.
RATSCHEN_MUSTER = re.compile(r'getattr\([^,]*[Cc]onfig[^,]*,\s*"[A-Z_]+"\s*,')


def ratschen_fundstellen(wurzel: Path, bereich: str) -> list[Befund]:
    """Alle Zeilen unter ``bereich`` (Datei oder Verzeichnis), die die Methode zaehlt."""
    ziel = wurzel / bereich
    dateien = [ziel] if ziel.is_file() else sorted(ziel.rglob("*.py"))
    befunde = []
    for pfad in dateien:
        rel = _relativ(pfad, wurzel)
        grep_pfad = f"ai_trading_bot/{rel}"
        with open(pfad, encoding="utf-8", errors="replace") as f:
            for nr, zeile in enumerate(f, start=1):
                if not RATSCHEN_MUSTER.search(zeile):
                    continue
                if "test" in f"{grep_pfad}:{nr}:{zeile}":
                    continue
                befunde.append(Befund(rel, nr, zeile.strip()))
    return befunde


def pruefe_ratsche(wurzel: Path, vertrag: dict) -> list[str]:
    meldungen = []
    for bereich, grenze in sorted(vertrag["ratsche"]["obergrenzen"].items()):
        fundstellen = ratschen_fundstellen(wurzel, bereich)
        n = len(fundstellen)
        if n > grenze:
            neu = "\n  ".join(str(b) for b in fundstellen)
            meldungen.append(
                f'Ratsche {bereich}: {n} getattr(config, "NAME", Default) — '
                f"Obergrenze {grenze}. Ein falsch geschriebener Name liefert still den "
                f"Default. Lies den Wert direkt aus der Konfiguration.\n  {neu}"
            )
        elif n < grenze:
            meldungen.append(
                f"Ratsche {bereich}: nur noch {n} Fundstellen, eingecheckt sind {grenze}. "
                f"Senke die Obergrenze in tests/architecture/vertrag.toml auf {n} — "
                f"sonst kann die Zahl unbemerkt wieder steigen."
            )
    return meldungen


# ── 2. Stufengrenzen (Import-Linter) ─────────────────────────────────────────


def pruefe_import_vertraege(
    wurzel: Path = PAKET, vertrag: Path = IMPORT_VERTRAG
) -> tuple[int, str]:
    """Faehrt den Import-Linter in einem eigenen Prozess (der Importgraph wird aus den
    Quellen gebaut, nicht aus dem Testprozess). Rueckgabe: (Exit-Code, Ausgabe)."""
    programm = (
        "import sys\n"
        "from importlinter.cli import lint_imports\n"
        f"sys.exit(lint_imports(config_filename={str(vertrag)!r}, no_cache=True,"
        " no_logo=True))\n"
    )
    umgebung = dict(os.environ, PYTHONIOENCODING="utf-8", COLUMNS="200")
    import shutil

    lauf = subprocess.run(
        [shutil.which("python") or sys.executable, "-c", programm],
        cwd=str(wurzel),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=umgebung,
        timeout=300,
    )
    return lauf.returncode, lauf.stdout + lauf.stderr


# ── 3. und 4. AST-Regeln mit eingefrorenen Ausnahmen ─────────────────────────

# Aufrufe, die beim Broker etwas veraendern. Lesende Aufrufe (get_*) sind frei.
MUTIERENDE_BROKER_AUFRUFE = frozenset(
    {
        "submit_order",
        "replace_order_by_id",
        "cancel_order_by_id",
        "cancel_orders",
        "close_position",
        "close_all_positions",
    }
)


def broker_aufrufe(quelle: str, datei: str) -> list[Befund]:
    """Jeder Zugriff auf eine mutierende Broker-Methode — nicht nur der direkte Aufruf.
    ``senden = self.client.submit_order`` und ``await senden(...)`` ist derselbe Weg zum
    Broker; ein Test, der nur ``x.submit_order(...)`` saehe, liesse ihn durch."""
    befunde = []
    for knoten in ast.walk(ast.parse(quelle)):
        if (
            isinstance(knoten, ast.Attribute)
            and isinstance(knoten.ctx, ast.Load)
            and knoten.attr in MUTIERENDE_BROKER_AUFRUFE
        ):
            befunde.append(Befund(datei, knoten.lineno, knoten.attr))
    return befunde


EDITIONS_NAMEN = frozenset({"DEPLOYMENT_MODE", "K_SERVICE", "is_local_mode"})
EDITIONS_FUNKTIONEN = frozenset({"resolve_edition"})


def editionsweichen(quelle: str, datei: str) -> list[Befund]:
    """Stellen, an denen Kerncode die Edition abfragt: ein Lesen von ``DEPLOYMENT_MODE``
    (Umgebung oder Konfiguration) oder ein Aufruf von ``resolve_edition``. Erwaehnungen in
    Docstrings und Kommentaren zaehlen nicht — nur Code, der den Wert liest."""
    befunde = []
    for knoten in ast.walk(ast.parse(quelle)):
        if isinstance(knoten, ast.Call):
            name = getattr(knoten.func, "attr", None) or getattr(
                knoten.func, "id", None
            )
            if name in EDITIONS_FUNKTIONEN:
                befunde.append(Befund(datei, knoten.lineno, name))
                continue
            for arg in knoten.args:
                if isinstance(arg, ast.Constant) and arg.value in EDITIONS_NAMEN:
                    befunde.append(Befund(datei, knoten.lineno, str(arg.value)))
        elif isinstance(knoten, ast.Subscript):
            s = knoten.slice
            if isinstance(s, ast.Constant) and s.value in EDITIONS_NAMEN:
                befunde.append(Befund(datei, knoten.lineno, str(s.value)))
        elif isinstance(knoten, ast.Attribute) and knoten.attr in EDITIONS_NAMEN:
            befunde.append(Befund(datei, knoten.lineno, knoten.attr))
        elif isinstance(knoten, ast.Name) and knoten.id in EDITIONS_NAMEN:
            befunde.append(Befund(datei, knoten.lineno, knoten.id))
    return befunde


def sammle(wurzel: Path, bereich: str, finder) -> list[Befund]:
    befunde = []
    for pfad, rel in _python_dateien(wurzel, bereich):
        quelle = pfad.read_text(encoding="utf-8-sig", errors="replace")
        befunde.extend(finder(quelle, rel))
    return befunde


def vergleiche_mit_ausnahmen(
    regel: str,
    befunde: list[Befund],
    erlaubt: dict[str, int],
    ausnahmen: dict[str, dict[str, int]],
) -> list[str]:
    """Befunde gegen die erlaubten Stellen und die eingefrorenen Ausnahmen.

    ``erlaubt`` sind Dateien, in denen die Regel gar nicht gilt (das Gateway, die
    Composition Root). ``ausnahmen`` zaehlt je Datei und Aufruf, was heute noch anders ist;
    gezaehlt statt nach Zeilen, damit eine verschobene Zeile keinen Befund erzeugt.
    """
    ist: dict[str, Counter] = {}
    stellen: dict[tuple[str, str], list[Befund]] = {}
    for b in befunde:
        if b.datei in erlaubt:
            continue
        ist.setdefault(b.datei, Counter())[b.was] += 1
        stellen.setdefault((b.datei, b.was), []).append(b)

    meldungen = []
    for datei in sorted(set(ist) | set(ausnahmen)):
        gefunden = ist.get(datei, Counter())
        eingefroren = ausnahmen.get(datei, {})
        for was in sorted(set(gefunden) | set(eingefroren)):
            n, grenze = gefunden.get(was, 0), eingefroren.get(was, 0)
            if n > grenze:
                orte = "\n  ".join(str(b) for b in stellen[(datei, was)])
                meldungen.append(
                    f"{regel}: {datei} — {n}× {was}, erlaubt {grenze}.\n  {orte}"
                )
            elif n < grenze:
                meldungen.append(
                    f"{regel}: {datei} — nur noch {n}× {was}, eingefroren sind {grenze}. "
                    f"Streiche den Rest in tests/architecture/vertrag.toml, damit die "
                    f"Stelle nicht zurueckkehren kann."
                )
    return meldungen


def pruefe_broker_aufrufer(wurzel: Path, vertrag: dict) -> list[str]:
    teil = vertrag["broker_aufrufer"]
    befunde = sammle(wurzel, teil["bereich"], broker_aufrufe)
    return vergleiche_mit_ausnahmen(
        "Broker-Aufruf am Gateway vorbei",
        befunde,
        dict.fromkeys(teil["gateway"], 0),
        teil.get("ausnahmen", {}),
    )


def pruefe_editionsweichen(wurzel: Path, vertrag: dict) -> list[str]:
    teil = vertrag["editionsweiche"]
    befunde = sammle(wurzel, teil["bereich"], editionsweichen)
    return vergleiche_mit_ausnahmen(
        "Editionsweiche ausserhalb der Composition Root",
        befunde,
        dict.fromkeys(teil.get("composition_root", []), 0),
        teil.get("ausnahmen", {}),
    )


UHR_MUSTER = re.compile(r"datetime\.(now|utcnow)\(|_?time\.time\(\)")


def uhr_fundstellen(wurzel, bereich) -> list[Befund]:
    ziel = wurzel / bereich
    dateien = [ziel] if ziel.is_file() else sorted(ziel.rglob("*.py"))
    befunde = []
    for pfad in dateien:
        rel = _relativ(pfad, wurzel)
        grep_pfad = f"ai_trading_bot/{rel}"
        with open(pfad, encoding="utf-8", errors="replace") as f_in:
            for nr, zeile in enumerate(f_in, start=1):
                if not UHR_MUSTER.search(zeile):
                    continue
                if "test" in f"{grep_pfad}:{nr}:{zeile}":
                    continue
                befunde.append(Befund(rel, nr, zeile.strip()))
    return befunde


def pruefe_uhr_zugriffe(wurzel, vertrag) -> list[str]:
    erwartet = vertrag.get("uhr_zugriffe", {}).get("obergrenzen", {})
    fehler = []

    # We aggregate all occurrences by path (just like pruefe_ratsche)
    ist = {}
    from collections import Counter

    for bereich in erwartet:
        funde = uhr_fundstellen(wurzel, bereich)
        ist[bereich] = len(funde)

    for bereich, max_zahl in erwartet.items():
        if ist.get(bereich, 0) > max_zahl:
            fehler.append(
                f"Uhr-Ratsche gerissen fuer '{bereich}': {ist[bereich]} gefunden, maximal {max_zahl} erlaubt."
            )
        elif ist.get(bereich, 0) < max_zahl:
            fehler.append(
                f"Uhr-Ratsche hat sich verbessert fuer '{bereich}': {ist[bereich]} gefunden, aber {max_zahl} in vertrag.toml erlaubt. Bitte vertrag.toml senken!"
            )

    return fehler


# ── 6. getattr-Namen gegen das Schema (#3627) ────────────────────────────────

# Die Ratsche oben ZAEHLT die Umgehungen; sie prueft nicht, ob der genannte Name
# ueberhaupt existiert. Ein Name, den weder ``RuntimeConfigState`` noch die Modulebene
# von ``settings.py`` kennt, liefert IMMER den Rueckfallwert: Er ist von aussen nicht
# setzbar, steht in keinem erzeugten Register, und ein Tippfehler faellt nie auf.
#
# Diese Regel sieht mehr Stellen als die Ratsche: Die Ratsche bildet ``grep`` nach und
# verlangt "config" im Empfaenger, verfehlt also jedes ``cfg``/``_cfg`` (146 Stellen).
# Fuer eine Namenspruefung waere das eine Luecke, deshalb gilt hier jeder Empfaenger,
# dessen Ausdruck "config", "cfg" oder "settings" enthaelt — auch ``get_config()``.
SCHEMA_KLASSE = "RuntimeConfigState"
SCHEMA_DATEI = "settings.py"

_GETATTR_MUSTER = re.compile(r'getattr\(\s*([^,]+?)\s*,\s*"([A-Z][A-Z_0-9]*)"\s*,')
_KONFIG_EMPFAENGER = re.compile(r"config|cfg|settings", re.IGNORECASE)


def schema_felder(wurzel: Path) -> set[str]:
    """Die Namen, die die Konfiguration kennt — aus dem Quelltext, ohne Import.

    Gelesen werden die Felder von ``RuntimeConfigState`` und die Namen auf Modulebene
    derselben Datei: Beide sind ueber den ``config``-Proxy erreichbar. Die Modulebene
    ist die schwaechere Sorte (nicht ueber die Umgebung setzbar, nicht im erzeugten
    Register) — aber sie loest auf, und diese Regel prueft die Aufloesung.
    """
    baum = ast.parse((wurzel / SCHEMA_DATEI).read_text(encoding="utf-8"))
    felder: set[str] = set()

    def _namen(koerper) -> None:
        for knoten in koerper:
            if isinstance(knoten, ast.AnnAssign) and isinstance(
                knoten.target, ast.Name
            ):
                felder.add(knoten.target.id)
            elif isinstance(knoten, ast.Assign):
                for ziel in knoten.targets:
                    if isinstance(ziel, ast.Name):
                        felder.add(ziel.id)

    _namen(baum.body)
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ClassDef) and knoten.name == SCHEMA_KLASSE:
            _namen(knoten.body)
    return felder


def _ohne_kommentare(quelle: str) -> str:
    """Dieselbe Quelle, Kommentare durch Leerzeichen ersetzt — Zeilen und Spalten bleiben.

    Ohne das zaehlt jeder Kommentar mit, der die Form nennt, um sie zu erklaeren —
    auch die Kommentare dieser Regel selbst. Zeichenketten bleiben stehen: der Name
    steht als Zeichenkette IM Aufruf.
    """
    try:
        marken = list(tokenize.generate_tokens(io.StringIO(quelle).readline))
    except (tokenize.TokenError, SyntaxError):
        return quelle  # unparsbare Datei: lieber roh pruefen als gar nicht
    zeilen = quelle.splitlines(keepends=True)
    for marke in marken:
        if marke.type != tokenize.COMMENT:
            continue
        (z, von), (_, bis) = marke.start, marke.end
        zeilen[z - 1] = zeilen[z - 1][:von] + " " * (bis - von) + zeilen[z - 1][bis:]
    return "".join(zeilen)


def konfig_getattr_fundstellen(wurzel: Path, bereich: str) -> list[Befund]:
    """``getattr(<konfig>, "NAME", Default)`` unter ``bereich``, ohne Testdateien.

    Tests sind ausgenommen, weil sie absichtlich erfundene Namen abfragen, um die
    Detektoren selbst zu pruefen.
    """
    ziel = wurzel / bereich
    dateien = [ziel] if ziel.is_file() else sorted(ziel.rglob("*.py"))
    befunde = []
    for pfad in dateien:
        rel = _relativ(pfad, wurzel)
        if "/tests/" in f"/{rel}" or "__pycache__" in rel:
            continue
        quelle = pfad.read_text(encoding="utf-8", errors="replace")
        for nr, zeile in enumerate(_ohne_kommentare(quelle).splitlines(), start=1):
            for treffer in _GETATTR_MUSTER.finditer(zeile):
                if _KONFIG_EMPFAENGER.search(treffer.group(1)):
                    befunde.append(Befund(rel, nr, treffer.group(2)))
    return befunde


def unbekannte_konfigurationsnamen(wurzel: Path, bereich: str) -> list[Befund]:
    """Fundstellen, deren Name nirgends aufloest — der Rueckfallwert ist alles."""
    felder = schema_felder(wurzel)
    return [
        Befund(
            b.datei,
            b.zeile,
            f"{b.was} ist weder Feld von {SCHEMA_KLASSE} noch Name in "
            f"{SCHEMA_DATEI} — der Rueckfallwert ist die einzige Quelle",
        )
        for b in konfig_getattr_fundstellen(wurzel, bereich)
        if b.was not in felder
    ]


def pruefe_getattr_namen(wurzel: Path, vertrag: dict) -> list[str]:
    meldungen = []
    for bereich in vertrag["getattr_namen"]["bereiche"]:
        unbekannt = unbekannte_konfigurationsnamen(wurzel, bereich)
        if unbekannt:
            meldungen.append(
                f"Unbekannte Konfigurationsnamen unter {bereich}:\n  "
                + "\n  ".join(str(b) for b in unbekannt)
            )
    return meldungen
