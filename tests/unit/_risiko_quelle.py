"""#4264 (H-3b) — Quelltext der Risikoverwaltung: Kern plus Themen-Module.

Plan: ``docs/4264-*/implementation_plan.md`` §2.1. Schnitt-Entscheidung #4185:
``docs/3738-arc-e6-gestalt/H3_SCHNITT_risk_manager.md`` §2.

Vier Wächter lesen ``core/risk_manager.py`` als Ganzes. Ab H-3c ziehen Methoden von
``RiskManager`` in Themen-Mixins (``core/risk_<thema>.py``), der VIX-Block mit ``resolve_vix`` ins
Skalierer-Modul. Ein Wächter, der nur den Kern liest, verlöre still Prüfumfang. Dieser Helfer
liefert deshalb:

* ``texte_risikoverwaltung`` — den Kern plus jedes **vorhandene** Modul aus ``ZIELMODULE``, je
  Datei (für ``ast``),
* ``text_risikoverwaltung`` — dieselben Texte verbunden, nur für die Suche nach Teilstrings,
* ``methoden_tabelle`` — Methodenname → (Datei, Klasse, Knoten) über ``RiskManager`` im Kern und
  jede ``*Mixin``-Klasse der Zielmodule; bei doppeltem Namen gewinnt der Kern, wie in der MRO,
* ``themen_methoden`` — dieselbe Erhebung als Rohliste je Name, ohne Vorrang (für Doppelungen),
* ``basis_module`` — die Dateien der Mixin-Basen von ``RiskManager``.

Wie ``_schleifen_quelle.py`` liest der Helfer **ohne Import**: Ein Zirkelimport zwischen Kern und
Mixin soll der Patch-Ziel-Wächter benennen, nicht alle Leser zugleich ohne Bezug rot machen.

Fehlt der Kern, **erhebt** der Helfer ``LookupError`` — leerer Text machte jeden Wächter
stillschweigend wahr.

Helfermodul — pytest sammelt es nicht. Eigentests: ``tests/unit/test_risiko_quelle.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
KERN = Path("core") / "risk_manager.py"
KLASSE = "RiskManager"
#: Die Zielmodule aus der Schnitt-Entscheidung #4185 §2, ohne den Kern — in der Reihenfolge,
#: in der ``texte_risikoverwaltung`` sie anhängt. Jede Basis des Kerns muss hier stehen
#: (``test_zielmodule_umfassen_jede_basis_des_kerns``).
ZIELMODULE = (
    "risk_vorpruefung.py",
    "risk_deckel.py",
    "risk_bemessung.py",
    "risk_konto.py",
    "risk_skalierer.py",
)

_Methode = ast.FunctionDef | ast.AsyncFunctionDef


def _kern(wurzel: Path) -> Path:
    return wurzel / KERN


def texte_risikoverwaltung(wurzel: Path = PAKET) -> dict[Path, str]:
    """Kern plus jedes vorhandene Modul aus ``ZIELMODULE``, je Datei — für ``ast``-Prüfer.

    Verbundene Modultexte sind kein gültiges Python (``from __future__`` steht nur am
    Dateianfang); wer parst, parst je Datei."""
    kern = _kern(wurzel)
    try:
        texte = {kern: kern.read_text(encoding="utf-8")}
    except OSError as fehler:
        raise LookupError(
            f"{kern} nicht lesbar: {fehler} — ohne Kern würde jeder Wächter "
            "stillschweigend wahr"
        ) from fehler
    for name in ZIELMODULE:
        datei = kern.parent / name
        if datei.is_file():
            texte[datei] = datei.read_text(encoding="utf-8")
    return texte


def text_risikoverwaltung(wurzel: Path = PAKET) -> str:
    """Die Texte aus ``texte_risikoverwaltung``, verbunden — nur für die Suche nach Teilstrings."""
    return "\n".join(texte_risikoverwaltung(wurzel).values())


def _klassen(datei: Path, text: str) -> list[ast.ClassDef]:
    """``RiskManager`` im Kern, ``*Mixin``-Klassen in den Zielmodulen."""
    baum = ast.parse(text, filename=str(datei))
    if datei.name == KERN.name:
        return [
            k for k in baum.body if isinstance(k, ast.ClassDef) and k.name == KLASSE
        ]
    return [
        k for k in baum.body if isinstance(k, ast.ClassDef) and k.name.endswith("Mixin")
    ]


def themen_methoden(wurzel: Path = PAKET) -> dict[str, list[tuple[Path, str]]]:
    """Methodenname → jede (Datei, Klasse), die ihn definiert — Kern zuerst, ohne Vorrang."""
    roh: dict[str, list[tuple[Path, str]]] = {}
    for name, (datei, klasse, _) in _alle_methoden(wurzel):
        roh.setdefault(name, []).append((datei, klasse))
    return roh


def _alle_methoden(wurzel: Path):
    for datei, text in texte_risikoverwaltung(wurzel).items():
        for klasse in _klassen(datei, text):
            for m in klasse.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield m.name, (datei, klasse.name, m)


def methoden_tabelle(wurzel: Path = PAKET) -> dict[str, tuple[Path, str, _Methode]]:
    """Methodenname → (Datei, Klasse, Knoten). Der Kern gewinnt, wie in der MRO."""
    tabelle: dict[str, tuple[Path, str, _Methode]] = {}
    for name, eintrag in _alle_methoden(wurzel):
        tabelle.setdefault(name, eintrag)
    return tabelle


def basis_module(wurzel: Path = PAKET) -> list[str]:
    """Die Dateinamen der Basen von ``RiskManager``, die der Kern per
    ``from core.<m> import <X>`` holt. Eine Basis anderer Herkunft **erhebt** ``LookupError``.
    """
    kern = _kern(wurzel)
    text = texte_risikoverwaltung(wurzel)[kern]
    baum = ast.parse(text, filename=str(kern))
    klasse = next(
        (k for k in baum.body if isinstance(k, ast.ClassDef) and k.name == KLASSE),
        None,
    )
    if klasse is None:
        raise LookupError(f"Klasse {KLASSE} nicht in {kern}")
    herkunft = {
        alias.asname or alias.name: imp.module.rpartition(".")[2]
        for imp in baum.body
        if isinstance(imp, ast.ImportFrom)
        and imp.level == 0
        and imp.module
        and imp.module.rpartition(".")[0] == "core"
        for alias in imp.names
    }
    module = []
    for basis in klasse.bases:
        if not (isinstance(basis, ast.Name) and basis.id in herkunft):
            raise LookupError(
                f"Basis {ast.unparse(basis)} von {KLASSE} wird in {kern} nicht per "
                "'from core.<m> import ...' importiert"
            )
        module.append(f"{herkunft[basis.id]}.py")
    return module
