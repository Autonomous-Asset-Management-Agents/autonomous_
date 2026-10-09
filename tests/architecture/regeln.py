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
from datetime import date
from pathlib import Path

HIER = Path(__file__).resolve().parent
PAKET = HIER.parent.parent  # ai_trading_bot/
REPO = PAKET.parent  # #3837: Regel 10 misst docs/, das neben dem Paket liegt.
VERTRAG = HIER / "vertrag.toml"
IMPORT_VERTRAG = HIER / "importlinter.ini"


@dataclass(frozen=True)
class Befund:
    datei: str  # relativ zu ai_trading_bot/, mit "/"
    zeile: int
    was: str
    #: #3740: Naeheres zur Fundstelle, das NICHT in den Schluessel gehoert. ``was`` ist
    #: der Schluessel, unter dem die Ratsche einfriert (ein Name); alles, was die Stelle
    #: nur naeher beschreibt (die Funktion, in der sie steht), gehoert hierher. Steht es
    #: in ``was``, findet ``vergleiche_mit_ausnahmen`` keine einzige Ausnahme wieder.
    zusatz: str = ""

    def __str__(self) -> str:
        naeher = f" — {self.zusatz}" if self.zusatz else ""
        return f"{self.datei}:{self.zeile}: {self.was}{naeher}"


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


# ── 7. Rueckfallwert widerspricht dem Schema (#3627, Schritt 2) ───────────────

# Regel 6 prueft, ob der NAME aufloest. Diese prueft, ob der WERT dasselbe sagt.
#
# `getattr(cfg, "DECISION_CAPTURE_ENABLED", False)` neben `DECISION_CAPTURE_ENABLED:
# bool = True` im Schema sind zwei Antworten auf dieselbe Frage. Heute greift der
# Rueckfallwert nie — der Name loest ja auf (Regel 6). Aber:
#
# * Wer den Code liest, Mensch oder Agent, bekommt zwei Wahrheiten und muss raten,
#   welche gilt. Genau diese Klasse hat am 15.09.2026 die Architekturanalyse ausgeloest.
# * Faellt die Deklaration weg — ein Umbau, eine Umbenennung —, kippt das Verhalten
#   lautlos auf den anderen Wert. Gemessen am 25.09.2026 stehen 76 solcher Paare im
#   Bestand, darunter `COMPLIANCE_ALLOW_RISK_REDUCING_EXITS` (Schema an, Rueckfall aus)
#   und `DEFAULT_EQUITY` (Schema 100.000, Rueckfall 0).
#
# Verglichen werden WERTE, nicht Texte: `5400` und `5400.0` sind dasselbe, `"0.1"` und
# `"0.10"` auch. Wo einer der beiden Werte kein einfaches Literal ist, schweigt die Regel
# — lieber eine Luecke als ein Fehlalarm.

_RUECKFALL_MUSTER = re.compile(
    r'getattr\(\s*([^,]+?)\s*,\s*"([A-Z][A-Z_0-9]*)"\s*,\s*([^,()]+?)\s*\)'
)


def _wert(knoten) -> tuple | None:
    """Der Auslieferungswert eines Schema-Feldes — oder None, wenn nicht ablesbar."""
    if isinstance(knoten, ast.Constant):
        return _normwert(knoten.value)
    # `os.getenv("X", "True").lower() == "true"` — der Vergleich macht den Wahrheitswert.
    if (
        isinstance(knoten, ast.Compare)
        and len(knoten.comparators) == 1
        and isinstance(knoten.comparators[0], ast.Constant)
    ):
        rechts = knoten.comparators[0].value
        links = _getenv_default(knoten.left)
        if links is not None and isinstance(rechts, str):
            return ("bool", str(links).lower() == str(rechts).lower())
    roh = _getenv_default(knoten)
    return _normwert(roh) if roh is not None else None


def _getenv_default(knoten):
    """Der zweite Parameter von ``os.getenv(...)`` — der Auslieferungswert."""
    for k in ast.walk(knoten):
        if (
            isinstance(k, ast.Call)
            and isinstance(k.func, ast.Attribute)
            and k.func.attr == "getenv"
            and len(k.args) >= 2
            and isinstance(k.args[1], ast.Constant)
        ):
            return k.args[1].value
    return None


def _normwert(v) -> tuple | None:
    """Wert mit Art, damit `5400` und `5400.0` gleich sind und `"true"` ein Bool ist."""
    if isinstance(v, bool):
        return ("bool", v)
    if isinstance(v, (int, float)):
        return ("zahl", float(v))
    if v is None:
        return ("none", None)
    roh = str(v).strip()
    try:
        return ("zahl", float(roh))
    except ValueError:
        pass
    if roh.lower() in ("true", "false"):
        return ("bool", roh.lower() == "true")
    return ("text", roh)


def _literal(text: str) -> tuple | None:
    """Der Rueckfallwert aus dem Quelltext — nur einfache Literale."""
    try:
        knoten = ast.parse(text, mode="eval").body
    except SyntaxError:
        return None
    if isinstance(knoten, ast.Constant):
        return _normwert(knoten.value)
    if isinstance(knoten, ast.UnaryOp) and isinstance(knoten.op, ast.USub):
        if isinstance(knoten.operand, ast.Constant):
            return _normwert(-knoten.operand.value)
    return None


def schema_defaults(wurzel: Path) -> dict[str, tuple]:
    """Feldname -> Auslieferungswert, soweit ablesbar."""
    baum = ast.parse((wurzel / SCHEMA_DATEI).read_text(encoding="utf-8"))
    aus: dict[str, tuple] = {}
    for knoten in ast.walk(baum):
        if not (isinstance(knoten, ast.ClassDef) and knoten.name == SCHEMA_KLASSE):
            continue
        for k in knoten.body:
            if (
                isinstance(k, ast.AnnAssign)
                and isinstance(k.target, ast.Name)
                and k.value is not None
            ):
                wert = _wert(k.value)
                if wert is not None:
                    aus[k.target.id] = wert
    return aus


def widerspruch_fundstellen(wurzel: Path, bereich: str) -> list[Befund]:
    """Stellen, deren Rueckfallwert dem Schema widerspricht."""
    defaults = schema_defaults(wurzel)
    befunde = []
    for stelle in konfig_getattr_fundstellen(wurzel, bereich):
        soll = defaults.get(stelle.was)
        if soll is None:
            continue
        pfad = wurzel / stelle.datei
        zeilen = _ohne_kommentare(
            pfad.read_text(encoding="utf-8", errors="replace")
        ).splitlines()
        if stelle.zeile > len(zeilen):
            continue
        for treffer in _RUECKFALL_MUSTER.finditer(zeilen[stelle.zeile - 1]):
            if treffer.group(2) != stelle.was:
                continue
            if not _KONFIG_EMPFAENGER.search(treffer.group(1)):
                continue
            ist = _literal(treffer.group(3))
            if ist is not None and ist != soll:
                befunde.append(
                    Befund(
                        stelle.datei,
                        stelle.zeile,
                        stelle.was,
                    )
                )
    return befunde


def pruefe_getattr_widersprueche(wurzel: Path, vertrag: dict) -> list[str]:
    abschnitt = vertrag["getattr_widersprueche"]
    befunde = []
    for bereich in abschnitt["bereiche"]:
        befunde += widerspruch_fundstellen(wurzel, bereich)
    return vergleiche_mit_ausnahmen(
        "Rueckfallwert widerspricht dem Schema",
        befunde,
        erlaubt={},
        ausnahmen=vertrag.get("getattr_widersprueche.ausnahmen")
        or abschnitt.get("ausnahmen", {}),
    )


def pruefe_schatten_importe(wurzel: Path, vertrag: dict) -> list[str]:
    abschnitt = vertrag.get("schatten_importe", {})
    if not abschnitt:
        return []

    befunde = []
    for bereich in abschnitt.get("bereiche", []):
        for py_datei in (wurzel / bereich).rglob("*.py"):
            try:
                tree = ast.parse(py_datei.read_text(encoding="utf-8"))
            except SyntaxError:
                continue

            modul_imports = set()
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        modul_imports.add(alias.asname or alias.name)
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        modul_imports.add(alias.asname or alias.name)

            if not modul_imports:
                continue

            class ScopeVisitor(ast.NodeVisitor):
                def __init__(self, modul_imports, py_datei):
                    self.modul_imports = modul_imports
                    self.py_datei = py_datei
                    self.scopes = []

                def visit_FunctionDef(self, node):
                    self.scopes.append(node)
                    self.generic_visit(node)
                    self.scopes.pop()

                def visit_AsyncFunctionDef(self, node):
                    self.scopes.append(node)
                    self.generic_visit(node)
                    self.scopes.pop()

                def visit_ClassDef(self, node):
                    self.scopes.append(node)
                    self.generic_visit(node)
                    self.scopes.pop()

                def visit_Import(self, node):
                    if self.scopes:
                        for alias in node.names:
                            name = alias.asname or alias.name
                            if name in self.modul_imports:
                                befunde.append(
                                    Befund(
                                        datei=self.py_datei.relative_to(
                                            wurzel
                                        ).as_posix(),
                                        # #3740: der blosse Name ist der Schluessel der
                                        # Ratsche — er steht so auch im Vertrag.
                                        was=name,
                                        zeile=node.lineno,
                                        zusatz=(
                                            "Schatten-Import in "
                                            f"'{self.scopes[-1].name}'"
                                        ),
                                    )
                                )
                    self.generic_visit(node)

                def visit_ImportFrom(self, node):
                    if self.scopes:
                        for alias in node.names:
                            name = alias.asname or alias.name
                            if name in self.modul_imports:
                                befunde.append(
                                    Befund(
                                        datei=self.py_datei.relative_to(
                                            wurzel
                                        ).as_posix(),
                                        # #3740: der blosse Name ist der Schluessel der
                                        # Ratsche — er steht so auch im Vertrag.
                                        was=name,
                                        zeile=node.lineno,
                                        zusatz=(
                                            "Schatten-Import in "
                                            f"'{self.scopes[-1].name}'"
                                        ),
                                    )
                                )
                    self.generic_visit(node)

            ScopeVisitor(modul_imports, py_datei).visit(tree)

    return vergleiche_mit_ausnahmen(
        "Zweig-lokaler Import ueberschattet Modul-Import",
        befunde,
        erlaubt={},
        ausnahmen=abschnitt.get("ausnahmen", {}),
    )


# ── 9. Groessen: Datei- und Funktionslaengen (#3817, ARC-E6 G-0) ─────────────
#
# Zaehlweise wortgleich aus dem Epic-Plan (#3738, Abschnitt 3):
#
#     Umfang:   ai_trading_bot/**/*.py
#     ohne:     tests/, pandas-ta, pandas_ta_classic, __pycache__, venv
#     Zaehlung: physische Zeilen (splitlines);
#               Funktionslaenge = end_lineno - lineno + 1 (ast)
#
# Nur mit genau diesem Umfang entstehen die eingefrorenen Zahlen des Epics
# (27 Dateien, 61 Funktionen); mit dem vendorierten pandas-ta waeren es 28 und 67.
#
# Verzweigungszahl und Verschachtelungstiefe bleiben absichtlich draussen: zwei Zahlen
# fuer eine Frage sind eine Ratefrage (Plan, Abschnitt 2.6).

#: Vendorierte Pakete und virtuelle Umgebungen unter ``ai_trading_bot/`` — dieselbe
#: Liste, die ``.pre-commit-config.yaml`` seinen Hooks als Ausschluss mitgibt.
#: Fremdcode ist kein Aufraeum-Auftrag, und ein Upstream-Abgleich wuerde die Ratsche
#: in beide Richtungen reissen. ``tests/`` schliesst schon ``_python_dateien`` aus.
GROESSEN_FREMDCODE = ("pandas-ta", "pandas_ta_classic", "venv", ".venv", ".venv_verify")

#: Ein Modul, das ``ast`` nicht lesen kann, ist ein Befund — kein stilles
#: Ueberspringen (Plan, Abschnitt 4: fail-closed innerhalb der Regel).
UNPARSBAR = "<unparsbar>"


def _eigene_dateien(wurzel: Path, bereich: str):
    """Wie ``_python_dateien``, zusaetzlich ohne Fremdcode."""
    for pfad, rel in _python_dateien(wurzel, bereich):
        if any(teil in GROESSEN_FREMDCODE for teil in rel.split("/")[:-1]):
            continue
        yield pfad, rel


def datei_groessen(wurzel: Path, bereich: str) -> list[Befund]:
    """Je Datei ein Befund. ``was`` ist der Pfad — der Schluessel, unter dem der
    Vertrag einfriert —, ``zusatz`` die gemessene Zeilenzahl."""
    befunde = []
    for pfad, rel in _eigene_dateien(wurzel, bereich):
        quelle = pfad.read_text(encoding="utf-8-sig", errors="replace")
        befunde.append(Befund(rel, 1, rel, str(len(quelle.splitlines()))))
    return befunde


def _funktionen(knoten, praefix: str):
    """Jede Funktion unter ``knoten`` mit qualifiziertem Namen (``Klasse.methode``).

    Eine Klasse vertieft den Namen, ohne selbst gemessen zu werden; eine Funktion
    wird gemessen UND vertieft, damit verschachtelte Funktionen eigene Befunde
    bekommen. ``ast.AsyncFunctionDef`` steht gleichberechtigt neben ``FunctionDef``
    — sonst faellt jedes ``async def`` stillschweigend durch.
    """
    for kind in ast.iter_child_nodes(knoten):
        if isinstance(kind, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{praefix}{kind.name}"
            yield name, kind
            yield from _funktionen(kind, f"{name}.")
        elif isinstance(kind, ast.ClassDef):
            yield from _funktionen(kind, f"{praefix}{kind.name}.")
        else:
            yield from _funktionen(kind, praefix)


def funktions_groessen(wurzel: Path, bereich: str) -> list[Befund]:
    """Je Funktion ein Befund, ungefiltert — die Schwelle zieht erst der Vergleich.

    ``was`` ist der qualifizierte Name, ``zeile`` die ``def``-Zeile, ``zusatz`` die
    gemessene Laenge.
    """
    befunde = []
    for pfad, rel in _eigene_dateien(wurzel, bereich):
        quelle = pfad.read_text(encoding="utf-8-sig", errors="replace")
        try:
            baum = ast.parse(quelle)
        except SyntaxError as fehler:
            befunde.append(Befund(rel, fehler.lineno or 1, UNPARSBAR, str(fehler.msg)))
            continue
        for name, knoten in _funktionen(baum, ""):
            laenge = knoten.end_lineno - knoten.lineno + 1
            befunde.append(Befund(rel, knoten.lineno, name, str(laenge)))
    return befunde


def _obergrenze(eintrag) -> int:
    """Ein Vertragseintrag ist entweder die nackte Zahl — dann ist er ein
    Aufraeum-Auftrag — oder eine Tabelle ``{ zeilen = N, begruendung = "..." }``,
    dann eine bewusste Ausnahme (Plan, Abschnitt 2.2)."""
    if isinstance(eintrag, dict):
        return int(eintrag["zeilen"])
    return int(eintrag)


def _groessen_meldung(label, n, schwelle, eingetragen, wo) -> str | None:
    """Die Ratsche in beide Richtungen fuer eine gemessene Groesse.

    ``eingetragen`` ist ``None``, wenn der Vertrag die Stelle nicht kennt. Dass auch
    ein Unterschreiten meldet, ist der Kern: sonst kehrt eine geraeumte Stelle
    unbemerkt zurueck.
    """
    if eingetragen is None:
        if n > schwelle:
            return (
                f"Groessen: {label} — {n} Zeilen, Schwelle {schwelle}. Neu ueber der "
                f"Schwelle: teilen, oder mit Begruendung unter {wo} in "
                f"tests/architecture/vertrag.toml eintragen."
            )
        return None
    if n <= schwelle:
        return (
            f"Groessen: {label} — {n} Zeilen, unter der Schwelle {schwelle}. Streiche "
            f"den Eintrag unter {wo} in tests/architecture/vertrag.toml, damit die "
            f"Stelle nicht zurueckkehren kann."
        )
    if n > eingetragen:
        return (
            f"Groessen: {label} — {n} Zeilen, eingefroren sind {eingetragen}. "
            f"Die Zahl darf nur sinken."
        )
    if n < eingetragen:
        return (
            f"Groessen: {label} — nur noch {n} Zeilen, eingefroren sind {eingetragen}. "
            f"Senke die Zahl unter {wo} in tests/architecture/vertrag.toml auf {n} — "
            f"sonst kann sie unbemerkt wieder steigen."
        )
    return None


def _vergleiche_dateien(
    befunde, schwelle, ausnahmen, wo="[groessen.ausnahmen_dateien]"
) -> list[str]:
    gemessen = {b.was: int(b.zusatz) for b in befunde}
    meldungen = []
    for datei in sorted(set(gemessen) | set(ausnahmen)):
        eintrag = ausnahmen.get(datei)
        meldung = _groessen_meldung(
            datei,
            gemessen.get(datei, 0),
            schwelle,
            None if eintrag is None else _obergrenze(eintrag),
            wo,
        )
        if meldung:
            meldungen.append(meldung)
    return meldungen


def _stufe_1_ohne_begruendung(ausnahmen, grenze) -> list[str]:
    """Ueber der Stufe-1-Grenze ist eine nackte Zahl keine Entscheidung (#3835, G-8).

    Darunter darf ein Eintrag Arbeitsvorrat sein; darueber bleibt eine Datei nur mit
    gefuellter ``begruendung`` stehen — eine leere zaehlt wie keine.
    """
    meldungen = []
    for datei, eintrag in sorted(ausnahmen.items()):
        if _obergrenze(eintrag) <= grenze:
            continue
        if isinstance(eintrag, dict) and str(eintrag.get("begruendung", "")).strip():
            continue
        meldungen.append(
            f"Groessen: Stufe-1-Grenze {grenze} — {datei}: {_obergrenze(eintrag)} Zeilen "
            f"ueber der Grenze, ohne begruendung. Zerlegen, oder unter "
            f'[groessen.ausnahmen_dateien] als {{ zeilen = N, begruendung = "..." }} '
            f"begruenden."
        )
    return meldungen


def _stufe_1_funktionen_ohne_begruendung(befunde, ausnahmen, grenze) -> list[str]:
    """Stufe 1 auch fuer Funktionen (#4175, G-8c): ueber ``grenze`` bleibt eine
    gemessene Funktion nur mit gefuellter ``begruendung`` stehen. Ein fehlender
    Eintrag zaehlt wie ein unbegruendeter, eine leere Begruendung wie keine."""
    meldungen = []
    for b in befunde:
        if b.was == UNPARSBAR or int(b.zusatz) <= grenze:
            continue
        eintrag = ausnahmen.get(b.datei, {}).get(b.was)
        if isinstance(eintrag, dict) and str(eintrag.get("begruendung", "")).strip():
            continue
        meldungen.append(
            f"Groessen: Stufe-1-Grenze {grenze} — {b.datei}:{b.zeile} {b.was}: "
            f"{b.zusatz} Zeilen ueber der Grenze, ohne begruendung. Zerlegen, oder "
            f"unter [groessen.ausnahmen_funktionen] als "
            f'{{ zeilen = N, begruendung = "..." }} begruenden.'
        )
    return meldungen


def _vergleiche_funktionen(befunde, schwelle, ausnahmen) -> list[str]:
    meldungen = []
    gemessen: dict[tuple[str, str], tuple[int, int]] = {}
    for b in befunde:
        if b.was == UNPARSBAR:
            meldungen.append(
                f"Groessen: {b.datei}:{b.zeile} ist fuer ast nicht lesbar "
                f"({b.zusatz}) — die Groessen dieser Datei sind ungemessen."
            )
            continue
        gemessen[(b.datei, b.was)] = (int(b.zusatz), b.zeile)

    eingefroren = {
        (datei, name): eintrag
        for datei, stellen in ausnahmen.items()
        for name, eintrag in stellen.items()
    }
    for datei, name in sorted(set(gemessen) | set(eingefroren)):
        n, zeile = gemessen.get((datei, name), (0, 1))
        eintrag = eingefroren.get((datei, name))
        meldung = _groessen_meldung(
            f"{datei}:{zeile} {name}",
            n,
            schwelle,
            None if eintrag is None else _obergrenze(eintrag),
            "[groessen.ausnahmen_funktionen]",
        )
        if meldung:
            meldungen.append(meldung)
    return meldungen


def pruefe_groessen(wurzel: Path, vertrag: dict) -> list[str]:
    teil = vertrag["groessen"]
    meldungen = []
    for bereich in teil["bereiche"]:
        meldungen += _vergleiche_dateien(
            datei_groessen(wurzel, bereich),
            teil["datei_schwelle"],
            teil.get("ausnahmen_dateien", {}),
        )
        funktionen = funktions_groessen(wurzel, bereich)
        meldungen += _vergleiche_funktionen(
            funktionen,
            teil["funktion_schwelle"],
            teil.get("ausnahmen_funktionen", {}),
        )
        if "stufe_1_funktion_schwelle" in teil:
            meldungen += _stufe_1_funktionen_ohne_begruendung(
                funktionen,
                teil.get("ausnahmen_funktionen", {}),
                teil["stufe_1_funktion_schwelle"],
            )
    if "stufe_1_datei_schwelle" in teil:
        meldungen += _stufe_1_ohne_begruendung(
            teil.get("ausnahmen_dateien", {}), teil["stufe_1_datei_schwelle"]
        )
    return meldungen


#: Die zwei erlaubten Formen einer Begruendung (M1.2, #4199, Retro #4193): Ablauf mit
#: Zerlegungs-Issue, oder Owner-Entscheidung mit Fundstelle. Alles andere wird still
#: zur Dauerausnahme - in ARC-E6 ersetzten solche Begruendungen Zerlegungen (G-8b, G-8c).
HERKUNFT = re.compile(r"^(VORLÄUFIG bis [^:]*#\d+|Owner-Entscheidung \(#\d+)")


def pruefe_begruendungen(vertrag: dict) -> list[str]:
    teil = vertrag["groessen"]
    stellen = list(teil.get("ausnahmen_dateien", {}).items()) + [
        (f"{datei} {name}", eintrag)
        for datei, funktionen in teil.get("ausnahmen_funktionen", {}).items()
        for name, eintrag in funktionen.items()
    ]
    return [
        f"Groessen: {wo} — begruendung ohne Herkunft. Erlaubt sind "
        f'"VORLÄUFIG bis ... (#N): ..." oder "Owner-Entscheidung (#N ...): ...".'
        for wo, eintrag in sorted(stellen)
        if isinstance(eintrag, dict)
        and not HERKUNFT.match(str(eintrag.get("begruendung", "")).strip())
    ]


#: #4188 (H-K): Eine "kalt"-Begruendung traegt ihre Aenderungsrate und das Messdatum
#: (Epic #3738 §8.2: "Steigt die Rate, kommen sie nach" - ohne Zahl und Datum nicht
#: nachpruefbar). Gemessen wird mit scripts/mess_hotspots.py.
KALT_WORT = re.compile(r"\bkalt\b", re.IGNORECASE)
KALT_FORM = re.compile(
    r"kalt: (?P<commits>\d+) Commits/(?P<tage>\d+) Tage"
    r"(?:, gemessen (?P<datum>\d{4}-\d{2}-\d{2}))?"
)
#: Kriterium "kalt" aus #4188: hoechstens 3 Commits in 60 Tagen.
KALT_HOECHSTENS_COMMITS = 3


def _kalt_mangel(begruendung: str) -> str | None:
    """Prueft 'kalt'-Begruendung auf Messwert, Fenster und Datum.

    Referenzdatum ist date.today(). Da das Repository im Release-Zyklus
    2026 laeuft (Systemdatum >= 2026-10-08), ist das Messdatum 2026-10-08
    nicht in der Zukunft (gemessen <= date.today()).
    """
    form = KALT_FORM.search(begruendung)
    if form is None:
        return (
            "ohne Messwert. Erwartet "
            '"kalt: <k> Commits/<t> Tage, gemessen <JJJJ-MM-TT>".'
        )
    if form["datum"] is None:
        return (
            'ohne Datum. Erwartet ", gemessen <JJJJ-MM-TT>" nach ' '"Commits/<t> Tage".'
        )
    try:
        gemessen = date.fromisoformat(form["datum"])
    except ValueError:
        return f"Messdatum {form['datum']} ist kein gueltiges Datum."
    if gemessen > date.today():
        return f"Messdatum {form['datum']} liegt in der Zukunft."
    if int(form["commits"]) > KALT_HOECHSTENS_COMMITS:
        return (
            f"nicht kalt: {form['commits']} Commits, die Grenze ist "
            f"{KALT_HOECHSTENS_COMMITS} (#4188). Ab hier braucht die Stelle "
            "eine Zerlegung."
        )
    return None


def pruefe_kalt_begruendungen(vertrag: dict) -> list[str]:
    teil = vertrag["groessen"]
    stellen = list(teil.get("ausnahmen_dateien", {}).items()) + [
        (f"{datei} {name}", eintrag)
        for datei, funktionen in teil.get("ausnahmen_funktionen", {}).items()
        for name, eintrag in funktionen.items()
    ]
    meldungen = []
    for wo, eintrag in sorted(stellen):
        if not isinstance(eintrag, dict):
            continue
        begruendung = str(eintrag.get("begruendung", ""))
        if not KALT_WORT.search(begruendung):
            continue
        mangel = _kalt_mangel(begruendung)
        if mangel:
            meldungen.append(f'Groessen: {wo} — "kalt"-Begruendung: {mangel}')
    return meldungen


# ── 10. Doku-Groessen: keine neue Sammel-Datei unter docs/ (#3837, ARC-E6 G-9) ──


def doku_groessen(wurzel: Path, bereich: str) -> list[Befund]:
    """Je Markdown-Datei unter ``bereich`` ein Befund, gezaehlt wie Regel 9
    (``splitlines``). ``wurzel`` ist hier das Repository, nicht das Paket."""
    befunde = []
    for pfad in sorted((wurzel / bereich).rglob("*.md")):
        rel = _relativ(pfad, wurzel)
        quelle = pfad.read_text(encoding="utf-8-sig", errors="replace")
        befunde.append(Befund(rel, 1, rel, str(len(quelle.splitlines()))))
    return befunde


def pruefe_doku_groessen(wurzel: Path, vertrag: dict) -> list[str]:
    teil = vertrag["doku_groessen"]
    meldungen = []
    for bereich in teil["bereiche"]:
        meldungen += _vergleiche_dateien(
            doku_groessen(wurzel, bereich),
            teil["schwelle"],
            teil.get("ausnahmen", {}),
            "[doku_groessen.ausnahmen]",
        )
    return meldungen
