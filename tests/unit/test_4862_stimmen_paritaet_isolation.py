"""#4862 Paritaet: kein Import aus research/, CLI mit Exit-Code (Plan TDD-Schritt 4)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.test_3712_stimmen_sim_isolation import _importe, _verboten
from tests.unit.test_4862_stimmen_paritaet_vergleich import _schnell, _voll

pytestmark = [pytest.mark.vc1]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "sim" / "stimmen_paritaet.py"


def test_kein_import_aus_research():
    namen = _importe(MODUL)
    verboten = ["research", "ai_trading_bot.research", "core.sim.runner"]
    assert _verboten(namen, verboten) == []


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "core.sim.stimmen_paritaet", *map(str, args)],
        cwd=PAKET,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _vergleiche_cli(tmp_path, abw=0.0, strat="a", voll=None):
    s = tmp_path / "s.json"
    s.write_text(json.dumps({"strategien": [_schnell()]}), "utf-8")
    if voll is None:
        vd = _voll()
        vd["ending_equity"] *= 1 + abw
        voll = tmp_path / "v.json"
        voll.write_text(json.dumps(vd), "utf-8")
    out = tmp_path / "b.json"
    cmd = [
        "vergleiche",
        "--schnell",
        s,
        "--voll",
        voll,
        "--strategie",
        strat,
        "--out",
        out,
    ]
    return _cli(*cmd), out


@pytest.mark.parametrize("abweichung,exit_code", [(0.0, 0), (5e-4, 1)])
def test_cli_vergleiche_exitcode(tmp_path, abweichung, exit_code):
    lauf, out = _vergleiche_cli(tmp_path, abweichung)
    assert lauf.returncode == exit_code, lauf.stderr[-2000:]
    bericht = json.loads(out.read_text("utf-8"))
    assert bericht["innerhalb_toleranz"] is (exit_code == 0)
    assert bericht["n_tage"] == 3


def test_cli_unbekannte_strategie_bricht_ab(tmp_path):
    lauf, out = _vergleiche_cli(tmp_path, strat="x")
    assert lauf.returncode not in (0, 1)
    assert "x" in lauf.stderr
    assert not out.exists()


def test_cli_fehlende_datei_ist_nicht_ausserhalb(tmp_path):
    """Ein Lesefehler darf nicht als Exit 1 ("ausserhalb") gelesen werden."""
    lauf, _ = _vergleiche_cli(tmp_path, voll=tmp_path / "fehlt.json")
    assert lauf.returncode == 2, lauf.stderr[-2000:]
    assert "fehlt.json" in lauf.stderr


def test_cli_referenz(tmp_path):
    strategie = tmp_path / "a.json"
    strategie.write_text(json.dumps({"name": "a", "max_positions": 3}), "utf-8")

    lauf = _cli("referenz", "--strategie", strategie)

    assert lauf.returncode == 0, lauf.stderr[-2000:]
    assert "--set FULL_UNIVERSE_MAX_POSITIONS=3" in lauf.stdout
