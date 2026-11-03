"""#3712 schnelle Sim: nur Subprozess, kein Import aus research/ (Plan TDD-Schritt 5)."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.vc1]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "sim" / "stimmen_sim.py"


def _importe(pfad: Path) -> set:
    """Alle importierten Modulnamen einer Datei, auch die spaeten im Funktionsrumpf."""
    namen = set()
    for knoten in ast.walk(ast.parse(pfad.read_text(encoding="utf-8"))):
        if isinstance(knoten, ast.Import):
            namen.update(a.name for a in knoten.names)
        elif isinstance(knoten, ast.ImportFrom) and knoten.module:
            namen.add(knoten.module)
            namen.update(f"{knoten.module}.{a.name}" for a in knoten.names)
    return namen


def _verboten(namen: set, praefixe) -> list:
    return sorted(
        n for n in namen if any(n == p or n.startswith(p + ".") for p in praefixe)
    )


def test_der_scan_findet_einen_verbotenen_import(tmp_path):
    """Positivkontrolle: ohne sie waere ein leerer Scan nicht von einem blinden zu trennen."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "def f():\n    from core.sim import stimmen_sim\n    import research.x\n",
        encoding="utf-8",
    )
    namen = _importe(probe)
    assert _verboten(namen, ["core.sim.stimmen_sim"]) == ["core.sim.stimmen_sim"]
    assert _verboten(namen, ["research"]) == ["research.x"]


def test_engine_importiert_stimmen_sim_nicht():
    funde = {
        str(p.relative_to(PAKET)): _verboten(_importe(p), ["core.sim.stimmen_sim"])
        for p in sorted((PAKET / "core" / "engine").rglob("*.py"))
    }
    assert {k: v for k, v in funde.items() if v} == {}


def test_kein_import_aus_research():
    namen = _importe(MODUL)
    assert _verboten(namen, ["research", "ai_trading_bot.research"]) == []


def test_cli_einstieg(tmp_path):
    from tests.unit.test_3712_stimmen_sim_determinismus import TAGE, mini_store

    store = mini_store(tmp_path)
    strategie = tmp_path / "s.json"
    strategie.write_text(json.dumps({"name": "s", "buy_threshold": 0.7}), "utf-8")
    out = tmp_path / "out.json"

    lauf = subprocess.run(
        [sys.executable, "-m", "core.sim.stimmen_sim", "--store", str(store)]
        + ["--strategien", str(strategie), "--von", TAGE[0], "--bis", TAGE[-1]]
        + ["--out", str(out)],
        cwd=PAKET,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert lauf.returncode == 0, lauf.stderr[-2000:]
    ergebnis = json.loads(out.read_text("utf-8"))["strategien"][0]
    assert ergebnis["strategie"] == "s"
    assert len(ergebnis["ergebnis"]["daily_equity"]) == len(TAGE)
    assert len(ergebnis["daily_returns_pct"]) == len(TAGE) - 1
