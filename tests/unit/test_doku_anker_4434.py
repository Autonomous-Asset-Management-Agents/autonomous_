"""#4434 (ARC-E6 N-2b) — Fach-Doku, ADRs und Runbooks zeigen auf den heutigen Code.

Plan: ``docs/4434-arc-e6-n-2b-a-fach-doku-adrs-und-runbooks-an-die/implementation_plan.md``.

Die Umzuege H-1 bis H-5 haben die fuenf Hotspot-Dateien geschrumpft. Der Pruefer
``scripts/check_doc_anchors.py`` sieht drei Formen (``datei.py#L1``, ``datei.py:1``,
``datei.py::NAME``); diese Ratsche haelt fuer den Umfang dieses Auftrags zusaetzlich
die Formen fest, an denen er vorbeisieht (``datei.py:name``, ``modul.name``).
N-3 #4435 darf sie abloesen, sobald der Pruefer denselben Umfang hart prueft.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.vc0

REPO = Path(__file__).resolve().parents[3]


def _lade(name, pfad):
    spec = importlib.util.spec_from_file_location(name, pfad)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pruefer = _lade("check_doc_anchors", REPO / "scripts" / "check_doc_anchors.py")
gen = _lade("gen_systemkarte", REPO / "scripts" / "gen_systemkarte.py")

# Modulname -> Datei. Die fuenf Dateien, aus denen H-1 bis H-5 Funktionen verschoben haben.
HOTSPOTS = {
    "order_executor": "ai_trading_bot/core/engine/order_executor.py",
    "trading_loop": "ai_trading_bot/core/engine/trading_loop.py",
    "risk_manager": "ai_trading_bot/core/risk_manager.py",
    "portfolio_manager": "ai_trading_bot/core/portfolio_manager.py",
    "runner": "ai_trading_bot/core/round_table/runner.py",
}
_NAMEN = "|".join(HOTSPOTS)
# ``runner.py`` allein ist mehrdeutig; gemeint ist nur der Round-Table-Runner.
_DATEI = rf"(?:round_table/runner|{'|'.join(n for n in HOTSPOTS if n != 'runner')})"

# Ausnahmen — jede mit Grund.
# MODULE_MAP.md und AGENT_CATALOG.md: gehoeren zu N-2a #4433 (Plan §2.3).
FREMDER_AUFTRAG = {"MODULE_MAP.md", "AGENT_CATALOG.md"}
# Die Schnitt-Entscheidungen beschreiben den Umzug "von Ort A nach Ort B" — der alte Ort
# ist dort Inhalt, kein Fehler. Das Hauptbuch haengt nur an (Plan §2.3).
AUFZEICHNUNG = re.compile(r"^H\d+_SCHNITT_.*\.md$|^CHANGE_LEDGER\.md$")

# Zeilenanker: ``datei.py#L12``, ``datei.py:L12``, ``datei.py:12`` (auch Bereiche).
ZEILENANKER = re.compile(rf"(?<![\w-]){_DATEI}\.py(?:#L|:L?)\d+")
# Symbolverweise: ``datei.py::name``, ``datei.py:name``, ``modul.name``.
SYMBOLVERWEIS = re.compile(
    rf"(?<![\w-])(?:{_DATEI}\.py::?|(?<![./])\b({_NAMEN})\.)([A-Za-z_]\w*)"
)
_DATEINAME = re.compile(rf"({_NAMEN})\.py")


def _dokumente(ohne_aufzeichnung: bool):
    for pfad in sorted((REPO / "docs").rglob("*.md")):
        if pruefer.ist_historisch(str(pfad)) or pfad.name in FREMDER_AUFTRAG:
            continue
        if ohne_aufzeichnung and AUFZEICHNUNG.match(pfad.name):
            continue
        yield pfad


def _zeilen(pfad):
    """Zeilen eines Dokuments, ohne ```json-Bloecke.

    Ein ```json-Block gibt eine Maschinenausgabe woertlich wieder (z. B. RESULTS.json samt
    ``git_sha``). Ihn umzuschreiben hiesse, die Aufzeichnung zu faelschen; der Text stammt
    aus dem Code, der ihn erzeugt.
    """
    text = pfad.read_text(encoding="utf-8", errors="replace")
    im_json = False
    for nr, zeile in enumerate(text.splitlines(), 1):
        if zeile.strip().startswith("```"):
            im_json = zeile.strip() == "```json"
            continue
        if not im_json:
            yield nr, zeile


def _quelle(modul: str) -> str:
    return (REPO / HOTSPOTS[modul]).read_text(encoding="utf-8")


def test_keine_zeilenanker_in_hotspot_dateien():
    """Maß §2.5(a): eine Zeilennummer in eine geschrumpfte Datei veraltet beim naechsten
    Schnitt wieder. Namensanker ``datei.py::NAME`` ueberleben ihn."""
    funde = [
        f"{p.relative_to(REPO).as_posix()}:{nr}: {m.group(0)}"
        for p in _dokumente(ohne_aufzeichnung=False)
        for nr, zeile in _zeilen(p)
        for m in ZEILENANKER.finditer(zeile)
    ]
    assert not funde, f"{len(funde)} Zeilenanker in Hotspot-Dateien:\n" + "\n".join(
        funde
    )


def test_symbolverweise_auf_hotspots_sind_definiert():
    """Maß §2.5(b): ``hotspot.py:sym``, ``hotspot.py::sym`` und ``hotspot.sym`` nennen
    ein Symbol, das in dieser Datei definiert ist."""
    quellen = {m: _quelle(m) for m in HOTSPOTS}
    funde = []
    for p in _dokumente(ohne_aufzeichnung=True):
        for nr, zeile in _zeilen(p):
            for m in SYMBOLVERWEIS.finditer(zeile):
                modul = m.group(1) or _DATEINAME.search(m.group(0)).group(1)
                sym = m.group(2)
                if sym in ("py", "md") or pruefer.steht_in(sym, quellen[modul]):
                    continue
                funde.append(f"{p.relative_to(REPO).as_posix()}:{nr}: {m.group(0)}")
    assert not funde, (
        f"{len(funde)} Verweise auf ein Symbol, das dort nicht definiert ist:\n"
        + "\n".join(funde)
    )


ADR021 = (
    REPO
    / "docs"
    / "1_architecture_and_adr"
    / "ADR-021-Execution-Truth-Idempotency-Outbox-Single-Writer.md"
)


def test_adr021_ohne_nackte_folgeanker():
    """Ein nackter Folge-Anker (``#L981``) erbt die Datei aus dem Satz davor — der Pruefer
    sieht ihn nicht. Geprueft wird je Absatz (Aufzaehlungspunkt), der order_executor nennt.
    """
    text = ADR021.read_text(encoding="utf-8")
    absaetze = re.split(r"\n(?=\d+\. |\n)", text)
    nackt = [
        m.group(0)
        for absatz in absaetze
        if "order_executor" in absatz
        for m in re.finditer(r"(?<!\.py)(?<![\w/])#L\d+", absatz)
    ]
    assert not nackt, f"Nackte Folge-Anker ueber order_executor.py: {nackt}"
    assert "d7633f071" in text, "Der historische Befund nennt den Stand d7633f071."


def test_stufenspezifikation_idempotenz_und_rotation():
    text = (
        REPO / "docs" / "2_agentic_operations" / "3_trading_and_execution.md"
    ).read_text(encoding="utf-8")
    assert "decision_id" in text
    assert "core/idempotency.py::derive_client_order_id" in text
    assert "cycle_id + symbol + action" not in text
    assert "_maybe_rotate_and_trim" not in text


def test_systemkarte_kapitalschutz_gleich_pfadregel():
    regel = tomllib.loads(
        (
            REPO / "docs/5_engineering_and_devops/pfad_regeln/kapitalschutz.toml"
        ).read_text(encoding="utf-8")
    )
    soll = {
        "ai_trading_bot/core/gateway/order_gateway.py" if p.endswith("/**") else p
        for p in regel["pfade"]
        if p.endswith(".py") or p.endswith("/**")
    }
    assert set(gen.KAPITALSCHUTZ) == soll, (
        f"nur in der Pfad-Regel: {sorted(soll - set(gen.KAPITALSCHUTZ))}; "
        f"nur in der Systemkarte: {sorted(set(gen.KAPITALSCHUTZ) - soll)}"
    )
