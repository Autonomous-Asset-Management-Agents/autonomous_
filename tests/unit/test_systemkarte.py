"""#612 (ARC-E3) — Aus dem AST generierte Systemkarte und portabler MCP-Server.

Plan: ``docs/612-generierte-systemkarte-portabler-mcp/implementation_plan.md``.
Generator: ``scripts/gen_systemkarte.py``.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import warnings
from pathlib import Path

import pytest

pytestmark = pytest.mark.vc0

REPO = Path(__file__).resolve().parents[3]


def _lade(name, pfad):
    spec = importlib.util.spec_from_file_location(name, pfad)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses brauchen das Modul in sys.modules
    spec.loader.exec_module(mod)
    return mod


gen = _lade("gen_systemkarte", REPO / "scripts" / "gen_systemkarte.py")

# Plan 7.4: der Abgleich der eingecheckten Karte beginnt als Warnung.
ABGLEICH_MODUS = "warnen"


# ── 1. Portabilitaet: kein Nutzerpfad in Code und Navigationsdokumenten ─────

NUTZERPFAD = re.compile(
    r"[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9._-]+[\\/]|/Users/[A-Za-z0-9._-]+/|/home/[a-z0-9._-]+/"
)


def _code_und_navigation():
    for wurzel in ("ai_trading_bot", "AI Builder Agent", "scripts"):
        for pfad in (REPO / wurzel).rglob("*.py"):
            rel = pfad.relative_to(REPO).as_posix()
            if "/tests/" in f"/{rel}" or "__pycache__" in rel or "/test_" in f"/{rel}":
                continue
            yield pfad, rel
    for rel in ("CLAUDE.md", "AGENTS.md", "docs/llms.txt"):
        yield REPO / rel, rel


def test_kein_nutzerpfad_in_code_und_navigation():
    funde = []
    for pfad, rel in _code_und_navigation():
        if not pfad.exists():
            continue
        for nr, zeile in enumerate(
            pfad.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if NUTZERPFAD.search(zeile):
                funde.append(f"{rel}:{nr}: {zeile.strip()[:100]}")
    assert not funde, "Absolute Nutzerpfade:\n" + "\n".join(funde)


def test_der_detektor_erkennt_nutzerpfade():
    for probe in (
        r'Path(r"C:\Users\jemand\.gemini")',
        "/Users/jemand/Documents/x.py",
        "/home/jemand/repo",
    ):
        assert NUTZERPFAD.search(probe), probe
    assert not NUTZERPFAD.search("Path(__file__).parent / 'docs'")


def test_der_mcp_server_nimmt_den_knowledge_ordner_aus_der_umgebung():
    quelle = (REPO / "AI Builder Agent" / "core" / "mcp_server.py").read_text(
        encoding="utf-8"
    )
    assert "ANTIGRAVITY_KNOWLEDGE_DIR" in quelle
    assert "nicht konfiguriert" in quelle, (
        "Fehlt die Konfiguration, muss das Werkzeug sie benennen statt auf einen "
        "fremden Pfad zu zeigen."
    )


# ── 2. Broker-Aufrufstellen ──────────────────────────────────────────────────


def test_die_karte_nennt_jede_broker_instanziierung():
    karte = gen.erzeuge_karte(REPO)
    stellen = {(s["datei"], s["funktion"]): s for s in karte["broker_clients"]}
    # bekannter Positivfall: die vorgesehene Fabrik
    fabrik = [
        s
        for s in karte["broker_clients"]
        if s["datei"] == "ai_trading_bot/core/client_factory.py"
    ]
    assert fabrik and all(s["ueber_fabrik"] for s in fabrik)
    vorbei = {s["datei"] for s in karte["broker_clients"] if not s["ueber_fabrik"]}
    assert "ai_trading_bot/core/engine/api_routes.py" in vorbei
    assert "ai_trading_bot/research/measure_iv_fetch_cost.py" in vorbei
    assert len(stellen) >= 4


def test_ein_docstring_ist_keine_instanziierung(tmp_path):
    (tmp_path / "ai_trading_bot" / "core").mkdir(parents=True)
    (tmp_path / "ai_trading_bot" / "core" / "m.py").write_text(
        '"""TradingClient(x) im Text."""\n\ndef f():\n    return TradingClient(1)\n',
        encoding="utf-8",
    )
    karte = gen.erzeuge_karte(tmp_path, mit_zeilen=True)
    assert [(s["funktion"], s["zeile"]) for s in karte["broker_clients"]] == [("f", 4)]


# ── 3. Deckel auf einem Pfad ─────────────────────────────────────────────────


def test_die_karte_nennt_die_zeile_die_den_order_deckel_liest():
    karte = gen.erzeuge_karte(REPO, mit_zeilen=True)
    leser = [
        s
        for s in karte["deckel"]["COMPLIANCE_MAX_ORDER_VALUE"]
        if s["datei"] == "ai_trading_bot/core/compliance.py"
    ]
    assert leser, karte["deckel"]["COMPLIANCE_MAX_ORDER_VALUE"]
    zeilen = (
        (REPO / "ai_trading_bot/core/compliance.py")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    for s in leser:
        assert "COMPLIANCE_MAX_ORDER_VALUE" in zeilen[s["zeile"] - 1]


def test_abfrage_deckel_und_flags_je_modul():
    karte = gen.erzeuge_karte(REPO)
    modul = gen.modul_ansicht(karte, "ai_trading_bot/core/compliance.py")
    assert "COMPLIANCE_MAX_ORDER_VALUE" in modul["deckel"]
    assert isinstance(modul["flags"], list)


# ── 4. Determinismus und Abgleich ────────────────────────────────────────────


def test_der_generator_ist_deterministisch():
    a = gen.als_markdown(gen.erzeuge_karte(REPO))
    b = gen.als_markdown(gen.erzeuge_karte(REPO))
    assert a == b
    assert not re.search(r"20\d\d-\d\d-\d\d", a), "Kein Zeitstempel in der Karte."


def test_eine_handaenderung_faellt_auf(tmp_path):
    ziel = tmp_path / "karte.md"
    text = gen.als_markdown(gen.erzeuge_karte(REPO))
    ziel.write_text(text, encoding="utf-8")
    assert gen.abweichung(ziel, text) is False
    ziel.write_text(text + "von Hand\n", encoding="utf-8")
    assert gen.abweichung(ziel, text) is True


def test_die_eingecheckte_karte_ist_aktuell():
    soll = gen.als_markdown(gen.erzeuge_karte(REPO))
    if not gen.abweichung(gen.KARTE, soll):
        return
    text = (
        "docs/SYSTEMKARTE.md ist nicht aktuell — "
        "`python scripts/gen_systemkarte.py` laufen lassen."
    )
    if ABGLEICH_MODUS == "blockieren":
        pytest.fail(text, pytrace=False)
    warnings.warn(text, UserWarning, stacklevel=1)
